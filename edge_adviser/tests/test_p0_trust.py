"""P0 trustworthiness fixes (0.4.2): sample hygiene, real-time slope, gates.

Covers the three P0 "lying system" defects found in the 0.4.1 audit:
  1. duplicate / out-of-window / unordered samples inflated coverage and
     corrupted window bounds (analyze-level sample hygiene pipeline);
  2. slope was fitted over sample indices (assumed uniform 1 s cadence),
     inflating ~30x on sparse windows (real elapsed-time fit);
  3. future window_end clamped its age to 0 -> "eternally fresh" (fail closed);
     plus missing/unrecognized snapshot status promoted to healthy (fail closed).
"""

from __future__ import annotations

import json
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

_HERE = Path(__file__).resolve().parent.parent / "edge_adviser"
if str(_HERE) not in sys.path:
    sys.path.insert(0, str(_HERE))

import pytest  # noqa: E402

from edge_adviser.config.model import (  # noqa: E402
    AdviserConfig,
    AlertLevel,
    AssetSpec,
    AssetState,
    MetricSpec,
)
from edge_adviser.analytics import analyze, sanitize_samples  # noqa: E402
from edge_adviser.analytics.gates import (  # noqa: E402
    classify_snapshot_state,
    gate_snapshot_for_read,
    is_fresh,
    snapshot_age_s,
)
from edge_adviser.core import AssetStore  # noqa: E402


def make_asset() -> AssetSpec:
    return AssetSpec(
        asset_id="pump1", name="Test Pump", window_s=60, sample_freq_s=1.0,
        metrics=[
            MetricSpec(key="temp", unit="degC", warn_max=68, crit_max=73, persist=5),
        ],
    )


def _iso(offset_s: float, base: datetime | None = None) -> str:
    base = base or datetime.now(timezone.utc) - timedelta(seconds=60)
    return (base + timedelta(seconds=offset_s)).isoformat(timespec="milliseconds")


# --------------------------------------------------------------------------- #
# 1. sample hygiene: sort / dedup / window clip / coverage semantics
# --------------------------------------------------------------------------- #
def test_duplicate_timestamps_do_not_inflate_coverage() -> None:
    """60 copies of one row must NOT read as coverage=1.0 / healthy."""
    base = datetime.now(timezone.utc) - timedelta(seconds=60)
    one = {"ts": _iso(30.0, base), "temp": 65.0}
    samples = [dict(one) for _ in range(60)]
    snap = analyze(make_asset(), samples)
    assert snap.coverage <= 0.05, "duplicates must not fake full coverage"
    assert snap.coverage * 60 == pytest.approx(1.0, abs=0.05)  # 1 unique timestamp
    assert snap.status in (AssetState.INSUFFICIENT, AssetState.PARTIAL)


def test_out_of_window_samples_are_clipped() -> None:
    """Rows outside [start, end) are dropped, not counted toward coverage."""
    base = (datetime.now(timezone.utc) - timedelta(seconds=60)).replace(
        microsecond=0)
    start, end = base, base + timedelta(seconds=60)
    in_window = [_iso(float(i) + 1, base) for i in range(30)]     # start+1s .. +30s
    out_window = [_iso(float(i) + 3600, base) for i in range(30)]  # +1h
    samples = [{"ts": t, "temp": 60.0} for t in (in_window + out_window)]
    clean, stats = sanitize_samples(samples,
                                    window_start=start.isoformat(),
                                    window_end=end.isoformat())
    assert len(clean) == 30
    assert stats["dropped_out_of_window"] == 30


def test_unordered_samples_are_sorted() -> None:
    """First/last window bounds and persistence runs need chronological order."""
    base = datetime.now(timezone.utc) - timedelta(seconds=60)
    samples = [{"ts": _iso(float(59 - i), base), "temp": 60.0 + (59 - i)}
               for i in range(60)]  # reversed
    snap = analyze(make_asset(), samples)
    assert snap.window_start < snap.window_end
    # temp climbs 60 -> 119 over 60 s; mean must reflect the full climb
    m = snap.metrics[0]
    assert m.mean is not None and m.mean > 88.0


def test_unparseable_ts_dropped_not_crash() -> None:
    samples = [{"ts": "not-a-date", "temp": 60.0},
               {"ts": _iso(1.0), "temp": 61.0}]
    snap = analyze(make_asset(), samples)
    assert snap.metrics[0].n == 1


def test_hygiene_dropped_is_audited_in_state_reason() -> None:
    base = datetime.now(timezone.utc) - timedelta(seconds=60)
    samples = [{"ts": _iso(1.0, base), "temp": 60.0},
               {"ts": "bad", "temp": 61.0}]
    snap = analyze(make_asset(), samples)
    assert "sample_hygiene_dropped=1" in snap.state_reason


# --------------------------------------------------------------------------- #
# 2. slope over real elapsed time
# --------------------------------------------------------------------------- #
def test_slope_uses_real_time_not_sample_index() -> None:
    """3 samples at 0/30/60 s: index-fit would inflate the slope 30x."""
    base = datetime.now(timezone.utc) - timedelta(seconds=90)
    samples = [
        {"ts": _iso(0.0, base), "temp": 50.0},
        {"ts": _iso(30.0, base), "temp": 55.0},
        {"ts": _iso(60.0, base), "temp": 60.0},
    ]
    snap = analyze(make_asset(), samples)
    slope = snap.metrics[0].slope
    assert slope is not None
    # 10 degC/min real slope = 600 degC/hour; index-fit would give ~20
    assert 400.0 < slope < 800.0, f"real-time slope expected, got {slope}"


def test_slope_nan_filter_keeps_time_alignment() -> None:
    """analyze() must drop a NaN value AND its timestamp together."""
    base = datetime.now(timezone.utc) - timedelta(seconds=90)
    samples = [
        {"ts": _iso(0.0, base), "temp": 50.0},
        {"ts": _iso(10.0, base), "temp": float("nan")},
        {"ts": _iso(30.0, base), "temp": 55.0},
        {"ts": _iso(60.0, base), "temp": 60.0},
    ]
    snap = analyze(make_asset(), samples)
    m = snap.metrics[0]
    assert m.n == 3
    # 50 -> 55 -> 60 degC over real 60 s => ~600 degC/hour;
    # a misaligned axis would compress 60 s of data into 30 s (~1200) or
    # treat the remaining 3 points as 0/1/2 s (~20)
    assert m.slope is not None and 400.0 < m.slope < 800.0


# --------------------------------------------------------------------------- #
# 3. gates: future timestamps + fail-closed status
# --------------------------------------------------------------------------- #
def test_future_window_end_is_not_fresh() -> None:
    """A future-dated snapshot must be treated as stale, not eternally fresh."""
    future = (datetime.now(timezone.utc) + timedelta(hours=24)).isoformat(
        timespec="seconds")
    snap = {"window_end": future}
    assert snapshot_age_s(snap) == float("inf")
    assert not is_fresh(snap, datetime.now(timezone.utc), 3600)


def test_gate_refuses_future_dated_snapshot(tmp_path: Path) -> None:
    store = AssetStore(tmp_path)
    asset = make_asset()
    config = AdviserConfig(workspace=tmp_path, assets=[asset])
    store.save_snapshot(asset.asset_id, {
        "asset_id": "pump1",
        "window_end": (datetime.now(timezone.utc) + timedelta(hours=1))
        .isoformat(timespec="seconds"),
        "status": "healthy", "coverage": 1.0, "sample_count": 10,
    })
    gate = gate_snapshot_for_read(store, asset, config)
    assert not gate.ok, "future-dated snapshot must fail the read gate"
    assert gate.reason == "stale"


def test_unknown_status_fails_closed_in_classify() -> None:
    asset = make_asset()
    config = AdviserConfig(workspace=Path("."), assets=[asset])
    now = datetime.now(timezone.utc)
    snap = {"window_end": now.isoformat(timespec="seconds"),
            "sample_count": 10, "status": "insufficent"}  # typo'd status
    assert classify_snapshot_state(asset, snap, 0, now, config) == AssetState.UNKNOWN


def test_missing_status_fails_closed_in_gate(tmp_path: Path) -> None:
    store = AssetStore(tmp_path)
    asset = make_asset()
    config = AdviserConfig(workspace=tmp_path, assets=[asset])
    store.save_snapshot(asset.asset_id, {
        "asset_id": "pump1",
        "window_end": (datetime.now(timezone.utc) - timedelta(seconds=30))
        .isoformat(timespec="seconds"),
        "coverage": 1.0, "sample_count": 10,  # no status at all
    })
    gate = gate_snapshot_for_read(store, asset, config)
    assert gate.ok is False or gate.state != AssetState.HEALTHY, \
        "missing status must not read as healthy"
