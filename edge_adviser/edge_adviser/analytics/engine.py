"""Deterministic time-series analysis — the "fact layer".

The whole point of this module is precision and auditability: every number the
LLM is later handed comes from a pure, reproducible calculation over the raw
window. Nothing here calls a model. Output is a plain JSON-serializable
AnalyticsSnapshot; the LLM only interprets it.

Algorithms are deliberately simple and transparent (mean/percentiles/min-max,
persistence-aware band exceedance, linear-regression trend, change-point by
segmented means) so field engineers can trust and re-verify them.
"""

from __future__ import annotations

import math
from dataclasses import asdict, dataclass, field
from datetime import datetime
from typing import Any

import numpy as np

from ..config.model import AlertLevel, AssetSpec, AssetState, MetricSpec, TrustLevel


# --------------------------------------------------------------------------- #
# Per-metric analysis result
# --------------------------------------------------------------------------- #
@dataclass
class MetricResult:
    key: str
    unit: str = ""
    n: int = 0
    mean: float | None = None
    std: float | None = None
    min: float | None = None
    max: float | None = None
    p50: float | None = None
    p95: float | None = None
    # persist-aware band exceedance (contiguous runs and total samples)
    over_warn_runs: int = 0
    under_warn_runs: int = 0
    over_crit_runs: int = 0
    under_crit_runs: int = 0
    over_warn_samples: int = 0
    over_crit_samples: int = 0
    # trend (units per hour) via linear fit over the window
    slope: float | None = None
    slope_r2: float | None = None
    # change-point summary (segmented means) if a jump is detected
    change_point: dict[str, Any] | None = None
    moving_mean_std: float | None = None
    level: AlertLevel = AlertLevel.INFO
    # human-readable reason for a non-nominal level (e.g. "n=5<min_samples=10")
    state_reason: str = ""

    def to_dict(self) -> dict[str, Any]:
        d = asdict(self)
        d["level"] = self.level.value
        return d


@dataclass
class AnalyticsSnapshot:
    asset_id: str
    window_start: str
    window_end: str
    source: str = ""
    regime: str = "clean"
    metrics: list[MetricResult] = field(default_factory=list)
    overall_level: AlertLevel = AlertLevel.INFO
    duration_s: float = 0.0
    # free summary fields rendered for the LLM
    headline: str = ""
    # ---- trustworthiness verdicts (filled by analyze / scheduler) ----
    status: AssetState = AssetState.HEALTHY
    trust: TrustLevel = TrustLevel.FULL
    coverage: float = 0.0           # fraction of expected samples actually seen
    expected_samples: int = 0
    actual_span_s: float = 0.0      # wall-clock span actually covered by the data
    state_reason: str = ""

    def overall_metric_dicts(self) -> list[dict[str, Any]]:
        return [m.to_dict() for m in self.metrics]

    def to_dict(self) -> dict[str, Any]:
        d = {
            "asset_id": self.asset_id,
            "window_start": self.window_start,
            "window_end": self.window_end,
            "source": self.source,
            "regime": self.regime,
            "duration_s": self.duration_s,
            "overall_level": self.overall_level.value,
            "headline": self.headline,
            "metrics": self.overall_metric_dicts(),
            # data-integrity fields: pure additions, always present so downstream
            # consumers and the report badge can rely on them.
            "status": self.status.value,
            "trust": self.trust.value,
            "coverage": self.coverage,
            "expected_samples": self.expected_samples,
            "sample_count": sum(m.n for m in self.metrics),
            "actual_span_s": self.actual_span_s,
            "state_reason": self.state_reason,
        }
        return d


# --------------------------------------------------------------------------- #
# helpers
# --------------------------------------------------------------------------- #
def _as_float_array(values: list[float]) -> np.ndarray:
    return np.asarray(values, dtype=np.float64)


def _band_exceedance(values: np.ndarray, lo: float | None, hi: float | None,
                     persist: int, crit: bool) -> tuple[int, int, int]:
    """Return (runs_over, runs_under, total_exceed_samples).

    A run is a contiguous span of `persist`+ samples beyond the band — this is
    the "persistence" that keeps fleeting single-sample noise from alarming.
    """
    vals = np.asarray(values, dtype=np.float64)
    over = np.zeros(vals.shape, dtype=bool) if hi is None else np.greater(vals, hi)
    under = np.zeros(vals.shape, dtype=bool) if lo is None else np.less(vals, lo)
    over_samples = int(over.sum())
    under_samples = int(under.sum())
    runs_over = _contiguous_runs(over, persist)
    runs_under = _contiguous_runs(under, persist)
    return runs_over, runs_under, over_samples, under_samples


def _contiguous_runs(cond: Any, persist: int) -> int:
    cond = np.asarray(cond, dtype=bool)
    if cond.size == 0:
        return 0
    # find runs of True; count runs with length >= persist
    d = np.diff(np.concatenate(([0], cond.view(np.int8), [0])))
    starts = np.where(d == 1)[0]
    ends = np.where(d == -1)[0]
    lengths = ends - starts
    return int((lengths >= max(1, persist)).sum())


def _linear_slope(x: np.ndarray, y: np.ndarray) -> tuple[float | None, float | None]:
    if y.size < 3:
        return None, None
    # x normalized to hours
    xh = (x - x[0]) / 3600.0
    denom = float(((xh - xh.mean()) ** 2).sum())
    if denom <= 0:
        return None, None
    slope = float(((xh - xh.mean()) * (y - y.mean())).sum() / denom)
    r2 = None
    if y.std() > 0:
        pred = xh * slope + (y.mean() - xh.mean() * slope)
        ss_res = float(((y - pred) ** 2).sum())
        ss_tot = float(((y - y.mean()) ** 2).sum())
        if ss_tot > 0:
            r2 = float(1.0 - ss_res / ss_tot)
    return slope, r2


def _parse_ts_minutes(ts_strings: list[str]) -> list[float] | None:
    """Parse ISO timestamps to minutes-since-epoch using only stdlib."""
    if not ts_strings or not ts_strings[0]:
        return None
    import datetime as _dt
    minutes: list[float] = []
    for raw in ts_strings:
        try:
            ts = _dt.datetime.fromisoformat(raw)
            if ts.tzinfo is None:
                ts = ts.replace(tzinfo=_dt.timezone.utc)
            minutes.append(ts.timestamp() / 60.0)
        except Exception:
            return None
    return minutes


def _change_point(y: np.ndarray) -> dict[str, Any] | None:
    """Split-point that maximizes between-group variance; report segment means."""
    n = y.size
    if n < 8:
        return None
    best = (0.0, None)
    for split in range(2, n - 2):
        left, right = y[:split], y[split:]
        if len(left) == 0 or len(right) == 0:
            continue
        gap = abs(float(left.mean()) - float(right.mean()))
        # normalized by pooled std to be scale-free
        pooled = math.sqrt((left.var() + right.var()) / 2.0) + 1e-9
        score = gap / pooled
        if score > best[0]:
            best = (score, split)
    score, split = best
    if split is None or score < 2.0:
        return None
    left, right = y[:split], y[split:]
    return {
        "at_sample": int(split),
        "frac": round(split / n, 3),
        "left_mean": round(float(left.mean()), 3),
        "right_mean": round(float(right.mean()), 3),
        "gap": round(abs(float(left.mean()) - float(right.mean())), 3),
        "score": round(score, 3),
    }


# --------------------------------------------------------------------------- #
# the engine
# --------------------------------------------------------------------------- #
def analyze_metric(values: list[float], ts_minutes: list[float] | None,
                   metric: MetricSpec) -> MetricResult:
    y = _as_float_array(values)
    res = MetricResult(key=metric.key, unit=metric.unit, n=int(y.size))

    # N1 / N3: data-coverage gate BEFORE computing statistics so that a sparse or
    # empty window is never reported as "nominal". We still compute the raw stats
    # below for the audit trail, but the level carries a coverage verdict instead.
    if y.size == 0:
        res.level = AlertLevel.UNKNOWN
        res.state_reason = "no samples for metric"
        return res
    if metric.min_samples > 0 and y.size < metric.min_samples:
        res.level = AlertLevel.INSUFFICIENT
        res.state_reason = f"n={y.size}<min_samples={metric.min_samples}"

    res.mean = round(float(y.mean()), 4)
    res.std = round(float(y.std()), 4)
    res.min = round(float(y.min()), 4)
    res.max = round(float(y.max()), 4)
    res.p50 = round(float(np.percentile(y, 50)), 4)
    res.p95 = round(float(np.percentile(y, 95)), 4)

    # band exceedance, persistence-aware
    ro, ru, ros, rus = _band_exceedance(y, metric.warn_min, metric.warn_max, metric.persist, False)
    res.over_warn_runs, res.under_warn_runs = ro, ru
    res.over_warn_samples = ros
    rco, rcu, rcos, rcus = _band_exceedance(y, metric.crit_min, metric.crit_max, metric.persist, True)
    res.over_crit_runs, res.under_crit_runs = rco, rcu
    res.over_crit_samples = rcos
    res.slope, res.slope_r2 = _linear_slope(np.arange(y.size, dtype=np.float64), y)
    res.change_point = _change_point(y)
    if ts_minutes and len(ts_minutes) > 1:
        res.moving_mean_std = round(float(np.std(np.convolve(y, np.ones(5)/5, mode='valid'))), 4)

    # aggregate level (crit > warn > info). If a data-coverage verdict
    # (INSUFFICIENT/UNKNOWN) was set above, do NOT override it with band logic —
    # band exceedance on sparse data is itself untrustworthy, so the gated level
    # wins.
    if res.level in (AlertLevel.INSUFFICIENT, AlertLevel.UNKNOWN):
        return res
    if res.over_crit_runs or res.under_crit_runs:
        res.level = AlertLevel.CRITICAL
    elif res.over_warn_runs or res.under_warn_runs:
        res.level = AlertLevel.WARN
    return res


def analyze(asset: AssetSpec, samples: list[dict[str, Any]], *,
            source: str = "", regime: str = "clean") -> AnalyticsSnapshot:
    """Analyze a window of samples into an AnalyticsSnapshot."""
    ts_strings = [s.get("ts", "") for s in samples]
    ts_min = _parse_ts_minutes(ts_strings)

    win_start = ts_strings[0] if ts_strings else ""
    win_end = ts_strings[-1] if ts_strings else ""

    results: list[MetricResult] = []
    for m in asset.metrics:
        values = [float(s[m.key]) for s in samples if m.key in s and s[m.key] is not None]
        results.append(analyze_metric(values, ts_min, m))

    overall = _overall_level(results)
    headline = _build_headline(asset, overall)
    duration_s = 0.0
    if ts_min and len(ts_min) > 1:
        duration_s = round((ts_min[-1] - ts_min[0]) * 60.0, 2)

    # N2: measure how much of the expected window the sample batch actually covers.
    expected, coverage, span = _compute_coverage(asset, samples, ts_min)
    status = _infer_data_state(results, coverage)

    snap = AnalyticsSnapshot(
        asset_id=asset.asset_id,
        window_start=win_start,
        window_end=win_end,
        source=source,
        regime=regime,
        metrics=results,
        overall_level=overall,
        duration_s=duration_s,
        headline=headline,
        coverage=coverage,
        expected_samples=expected,
        actual_span_s=span,
        status=status,
        trust=_trust_from_state(status),
        state_reason=_state_reason(results),
    )
    return snap


def _overall_level(results: list[MetricResult]) -> AlertLevel:
    """Aggregate level honoring data-coverage verdicts (N3).

    Real info/warn/critical verdicts take precedence; only when NO metric reached
    a confident verdict do we fall back to INSUFFICIENT / UNKNOWN so the headline
    never claims "nominal" on sparse or empty data.
    """
    real = [r.level for r in results
            if r.level in (AlertLevel.INFO, AlertLevel.WARN, AlertLevel.CRITICAL)]
    if AlertLevel.CRITICAL in real:
        return AlertLevel.CRITICAL
    if AlertLevel.WARN in real:
        return AlertLevel.WARN
    if real:
        return AlertLevel.INFO
    if any(r.level == AlertLevel.INSUFFICIENT for r in results):
        return AlertLevel.INSUFFICIENT  # some data but below min_samples
    if results:  # all UNKNOWN (n==0 every metric) -> genuinely no data
        return AlertLevel.UNKNOWN
    return AlertLevel.UNKNOWN           # no metrics at all


def _compute_coverage(asset: AssetSpec, samples: list[dict[str, Any]],
                      ts_min: list[float] | None) -> tuple[int, float, float]:
    """Return (expected_samples, coverage, actual_span_s).

    ``expected`` is the ideal sample count for the window at the configured
    frequency; ``coverage`` is the fraction of it actually received; ``span`` is
    the wall-clock time the data really covers (may be far less than ``window_s``).
    """
    if asset.sample_freq_s > 0:
        expected = max(0, round(asset.window_s / asset.sample_freq_s))
    else:
        expected = len(samples)
    actual = len(samples)
    coverage = round(min(1.0, actual / expected), 3) if expected > 0 else 1.0
    span = 0.0
    if ts_min and len(ts_min) > 1:
        span = round((ts_min[-1] - ts_min[0]) * 60.0, 2)
    elif samples and "ts" in samples[0]:
        span = 0.0  # single sample: no measurable span
    return expected, coverage, span


def _infer_data_state(results: list[MetricResult], coverage: float) -> AssetState:
    """Deterministic data-state from the analysis results alone (no context)."""
    if not results:
        return AssetState.UNKNOWN
    if all(r.level == AlertLevel.UNKNOWN for r in results):
        return AssetState.UNKNOWN
    if any(r.level in (AlertLevel.INSUFFICIENT, AlertLevel.UNKNOWN) for r in results):
        return AssetState.INSUFFICIENT
    if coverage < 1.0:
        return AssetState.PARTIAL
    return AssetState.HEALTHY


def _state_reason(results: list[MetricResult]) -> str:
    reasons = [r.state_reason for r in results if r.state_reason]
    return "; ".join(dict.fromkeys(reasons))[:200]  # dedupe, keep order


def _trust_from_state(state: AssetState) -> TrustLevel:
    """Deterministic trust mapping used by snapshots and badges."""
    if state == AssetState.HEALTHY:
        return TrustLevel.FULL
    if state == AssetState.PARTIAL:
        return TrustLevel.PARTIAL
    if state == AssetState.INSUFFICIENT:
        return TrustLevel.LOW
    return TrustLevel.NONE


def empty_snapshot(asset: AssetSpec, now: datetime | None = None,
                   *, state: AssetState = AssetState.INSUFFICIENT) -> dict[str, Any]:
    """A snapshot-like dict for an empty window.

    Used by the scheduler when a pull returns no samples: it still archives an
    OFFICE-style callable record (for audit + feeding the state machine) instead
    of silently dropping the window. No metrics, overall UNKNOWN, coverage 0.
    """
    now = now or datetime.now()
    iso = now.isoformat(timespec="seconds")
    return {
        "asset_id": asset.asset_id,
        "window_start": iso,
        "window_end": iso,
        "source": "",
        "regime": "",
        "duration_s": 0.0,
        "overall_level": AlertLevel.UNKNOWN.value,
        "headline": f"{asset.name}: no data in window — status unknown.",
        "metrics": [],
        "status": state.value,
        "trust": _trust_from_state(state).value,
        "coverage": 0.0,
        "expected_samples": max(0, round(asset.window_s / asset.sample_freq_s))
        if asset.sample_freq_s > 0 else 0,
        "sample_count": 0,
        "actual_span_s": 0.0,
        "state_reason": "no samples in window",
    }


def _build_headline(asset: AssetSpec, level: AlertLevel) -> str:
    if level == AlertLevel.CRITICAL:
        return f"{asset.name}: one or more parameters exceed critical bands."
    if level == AlertLevel.WARN:
        return f"{asset.name}: some parameters approach warning bands."
    if level == AlertLevel.INSUFFICIENT:
        return (f"{asset.name}: insufficient data in window — some metrics below "
                f"their minimum sample count; results are low-confidence.")
    if level == AlertLevel.UNKNOWN:
        return f"{asset.name}: no data in window — status unknown."
    return f"{asset.name}: nominal conditions."