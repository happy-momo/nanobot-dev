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
from datetime import datetime, timezone
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


def _finite_floats(values: list[Any]) -> list[float]:
    """Convert to floats, dropping None/non-numeric/NaN/Inf entries.

    NaN or Inf would poison every statistic (numpy propagates them) and leak
    into JSON as invalid literals, so the fact layer refuses them up front.
    """
    out: list[float] = []
    for v in values:
        if v is None:
            continue
        try:
            f = float(v)
        except (TypeError, ValueError):
            continue
        if math.isfinite(f):
            out.append(f)
    return out


def _band_exceedance(values: np.ndarray, lo: float | None, hi: float | None,
                     persist: int, crit: bool) -> tuple[int, int, int, int]:
    """Return (runs_over, runs_under, over_samples, under_samples).

    A run is a contiguous span of `persist`+ samples beyond the band — this is
    the "persistence" that keeps fleeting single-sample noise from alarming.
    ``crit`` only marks the caller's band tier (kept for readability at call
    sites); band logic itself is identical.
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
    """Linear fit slope over real elapsed time.

    ``x`` is per-sample elapsed SECONDS (aligned with ``y`` after value
    filtering). Units of the returned slope are per-hour. Callers must pass the
    real timestamps of the values that survived filtering — using sample
    indices instead silently assumes a uniform 1 s cadence and inflates the
    slope by the true sample period when data is sparse (P0 fix).
    """
    if y.size < 3 or x.size != y.size:
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
                   metric: MetricSpec, *,
                   elapsed_s: list[float] | None = None) -> MetricResult:
    """Analyze one metric's filtered values.

    ``elapsed_s`` is the per-sample elapsed time (seconds since window start)
    ALIGNED with ``values`` — i.e. entries survive the same finite-value filter
    as the values themselves. Slope is then fitted over real time instead of
    sample indices; without it slope would silently assume a uniform 1 s
    cadence and inflate ~30x on sparse windows.
    """
    y = _as_float_array(_finite_floats(values))
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
    # slope over REAL elapsed time (aligned per-sample), not sample indices
    x_s = np.asarray(elapsed_s, dtype=np.float64) if elapsed_s is not None \
        else np.arange(y.size, dtype=np.float64)  # legacy fallback (uniform 1s)
    res.slope, res.slope_r2 = _linear_slope(x_s[: y.size], y)
    res.change_point = _change_point(y)
    if ts_minutes and len(ts_minutes) > 1 and y.size >= 5:
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


def sanitize_samples(samples: list[dict[str, Any]],
                     window_start: str | None = None,
                     window_end: str | None = None) -> tuple[
        list[dict[str, Any]], dict[str, int]]:
    """Sample hygiene: sort by ts, dedup identical timestamps, clip to window.

    Data sources cannot be trusted to return ordered, unique, in-window rows.
    Without this pipeline: unordered rows corrupt first/last-derived window
    bounds and persistence runs; duplicates inflate coverage (N repeated rows
    read as N samples of real observation); out-of-window rows (a source that
    ignores start/end, or a device clock skew) count toward coverage and can
    even place window_end in the future (which the freshness gate would then
    read as "eternally fresh").

    Returns (clean_samples, stats) where stats counts what was dropped so the
    hygiene pass itself is auditable.
    """
    import datetime as _dt
    stats = {"dropped_unparseable_ts": 0, "dropped_out_of_window": 0,
             "dropped_duplicates": 0, "sorted": 0}
    if not samples:
        return [], stats

    def _ts_of(s: dict[str, Any]) -> _dt.datetime | None:
        raw = s.get("ts")
        if not isinstance(raw, str) or not raw:
            return None
        try:
            ts = _dt.datetime.fromisoformat(raw)
            return ts if ts.tzinfo else ts.replace(tzinfo=_dt.timezone.utc)
        except ValueError:
            return None

    w_lo = w_hi = None
    if window_start:
        try:
            w_lo = _dt.datetime.fromisoformat(window_start)
            w_lo = w_lo if w_lo.tzinfo else w_lo.replace(tzinfo=_dt.timezone.utc)
        except ValueError:
            w_lo = None
    if window_end:
        try:
            w_hi = _dt.datetime.fromisoformat(window_end)
            w_hi = w_hi if w_hi.tzinfo else w_hi.replace(tzinfo=_dt.timezone.utc)
        except ValueError:
            w_hi = None

    kept: list[tuple[_dt.datetime, dict[str, Any]]] = []
    for s in samples:
        ts = _ts_of(s)
        if ts is None:
            stats["dropped_unparseable_ts"] += 1
            continue
        if (w_lo and ts < w_lo) or (w_hi and ts >= w_hi):
            stats["dropped_out_of_window"] += 1
            continue
        kept.append((ts, s))
    # sort by real time — first/last and persistence runs depend on it
    kept.sort(key=lambda p: p[0])
    if kept and kept != sorted(kept, key=lambda p: p[0], reverse=True):
        stats["sorted"] = 1
    deduped: list[tuple[_dt.datetime, dict[str, Any]]] = []
    seen_ts: set[_dt.datetime] = set()
    for ts, s in kept:
        if ts in seen_ts:
            stats["dropped_duplicates"] += 1
            continue
        seen_ts.add(ts)
        deduped.append((ts, s))
    return [s for _, s in deduped], stats


def analyze(asset: AssetSpec, samples: list[dict[str, Any]], *,
            source: str = "", regime: str = "clean",
            window_start: str | None = None,
            window_end: str | None = None) -> AnalyticsSnapshot:
    """Analyze a window of samples into an AnalyticsSnapshot.

    Samples pass through the hygiene pipeline first (sort / dedup / window
    clip); all statistics, coverage and window bounds are computed over the
    sanitized batch. Window bounds default to the sanitized data's own first
    and last timestamps when not supplied by the caller.

    ``window_start``/``window_end`` — when the caller knows the requested pull
    window (the scheduler always does), passing it makes the clip real: rows
    outside the window (a source that ignores start/end, device clock skew)
    are dropped instead of inflating coverage or pushing window_end into the
    future. Without them, clipping is a no-op and hygiene still sorts/dedups.
    """
    # hygiene first: sort by real time, dedup timestamps, clip out-of-window
    # rows. All statistics / coverage / window bounds below use the clean batch.
    samples, hygiene = sanitize_samples(samples, window_start, window_end)
    dropped = (hygiene["dropped_unparseable_ts"] + hygiene["dropped_duplicates"]
               + hygiene["dropped_out_of_window"])
    ts_strings = [s.get("ts", "") for s in samples]
    ts_min = _parse_ts_minutes(ts_strings)

    win_start = ts_strings[0] if ts_strings else ""
    win_end = ts_strings[-1] if ts_strings else ""

    # per-sample elapsed seconds aligned with the sanitized batch, so slope is
    # fitted over real time. When timestamps are unparseable we fall back to
    # sample indices (uniform-1s assumption) — flagged in state_reason.
    elapsed_s: list[float] | None = None
    if ts_min:
        t0 = ts_min[0]
        elapsed_s = [(t - t0) * 60.0 for t in ts_min]

    results: list[MetricResult] = []
    for m in asset.metrics:
        # non-numeric / None / NaN values are dropped instead of raising, so a
        # single malformed field cannot abort the whole window. The elapsed-time
        # axis is filtered WITH the values so the pair stays aligned — dropping
        # a value must drop its timestamp, not shift every later sample.
        values: list[float] = []
        m_elapsed: list[float] | None = [] if elapsed_s is not None else None
        for i, s in enumerate(samples):
            v = s.get(m.key)
            if v is None:
                continue
            try:
                f = float(v)
            except (TypeError, ValueError):
                continue
            if not math.isfinite(f):
                continue
            values.append(f)
            if m_elapsed is not None:
                m_elapsed.append(elapsed_s[i])
        results.append(analyze_metric(values, ts_min, m, elapsed_s=m_elapsed))

    overall = _overall_level(results)
    headline = _build_headline(asset, overall)
    duration_s = 0.0
    if ts_min and len(ts_min) > 1:
        duration_s = round((ts_min[-1] - ts_min[0]) * 60.0, 2)

    # N2: coverage over the SANITIZED batch — unique timestamps / expected, so
    # duplicate or out-of-window rows can no longer fake full coverage.
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
        state_reason=_state_reason(results, hygiene_dropped=dropped),
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


def _state_reason(results: list[MetricResult], hygiene_dropped: int = 0) -> str:
    reasons = [r.state_reason for r in results if r.state_reason]
    if hygiene_dropped:
        reasons.append(f"sample_hygiene_dropped={hygiene_dropped}")
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
    auditable record (for audit + feeding the state machine) instead
    of silently dropping the window. No metrics, overall UNKNOWN, coverage 0.
    """
    now = now or datetime.now(timezone.utc)
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