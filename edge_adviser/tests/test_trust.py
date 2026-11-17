"""Deterministic trust verdicts: data lower bound, coverage, empty-metric fix."""

from __future__ import annotations

import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

_HERE = Path(__file__).resolve().parent.parent / "edge_adviser"
if str(_HERE) not in sys.path:
    sys.path.insert(0, str(_HERE))

import pytest  # noqa: E402

from edge_adviser.config.model import (  # noqa: E402
    AlertLevel,
    AssetSpec,
    AssetState,
    MetricSpec,
    TrustLevel,
)
from edge_adviser.analytics.engine import (  # noqa: E402
    analyze,
    analyze_metric,
    empty_snapshot,
    _infer_data_state,
    _trust_from_state,
)
from edge_adviser.analytics.gates import trust_from_state  # noqa: E402


def make_asset(min_samples: int = 0) -> AssetSpec:
    return AssetSpec(
        asset_id="pump1", name="Test Pump", window_s=60, sample_freq_s=1.0,
        metrics=[
            MetricSpec(key="temp", unit="degC", warn_max=68, crit_max=73, persist=5,
                       min_samples=min_samples),
        ],
    )


def test_metric_insufficient_below_min_samples() -> None:
    asset = make_asset(min_samples=10)
    snap = analyze(asset, _samples(5), source="sim")
    m = snap.metrics[0]
    assert m.level == AlertLevel.INSUFFICIENT
    assert m.mean is not None  # raw stats still computed for the audit trail
    assert "min_samples" in m.state_reason


def test_metric_unknown_when_empty() -> None:
    asset = make_asset()  # min_samples defaults to 0 -> only n==0 triggers gate
    snap = analyze(asset, [], source="sim")
    m = snap.metrics[0]
    assert m.level == AlertLevel.UNKNOWN
    assert snap.overall_level == AlertLevel.UNKNOWN
    assert "unknown" in snap.headline  # NOT "nominal"


def test_overall_ignores_insufficient_when_real_warn() -> None:
    asset = AssetSpec(
        asset_id="pump1", name="Test Pump", window_s=60, sample_freq_s=1.0,
        metrics=[
            MetricSpec(key="temp", unit="degC", warn_max=68, crit_max=73, persist=1),
            MetricSpec(key="vib", unit="mm/s", warn_max=3.0, crit_max=4.5, persist=1,
                       min_samples=50),
        ],
    )
    # temp very high (real warn), vib only 5 samples (insufficient)
    samples = [{"ts": _ts(1.0, i), "temp": 70.0, "vib": 1.0 if i < 5 else 1.0}
               for i in range(60)]
    snap = analyze(asset, samples, source="sim")
    assert snap.overall_level == AlertLevel.WARN  # real signal beats coverage gate


def test_snapshot_coverage_and_span() -> None:
    asset = make_asset()
    full = analyze(asset, _samples(60), source="sim")
    assert full.expected_samples == 60
    assert full.coverage == 1.0
    half = analyze(asset, _samples(30), source="sim")
    assert 0.45 <= half.coverage <= 0.55
    assert half.actual_span_s > 0


def test_trust_mapping_deterministic() -> None:
    assert trust_from_state(AssetState.HEALTHY) == TrustLevel.FULL
    assert trust_from_state(AssetState.PARTIAL) == TrustLevel.PARTIAL
    assert trust_from_state(AssetState.INSUFFICIENT) == TrustLevel.LOW
    assert trust_from_state(AssetState.OFFLINE) == TrustLevel.NONE


def test_empty_snapshot_dict() -> None:
    asset = make_asset()
    d = empty_snapshot(asset, state=AssetState.OFFLINE)
    assert d["status"] == "offline"
    assert d["overall_level"] == AlertLevel.UNKNOWN.value
    assert d["sample_count"] == 0
    assert d["trust"] == TrustLevel.NONE.value


def _ts(freq_s: float, i: int) -> str:
    base = datetime.now(timezone.utc) - timedelta(seconds=60)
    return (base + timedelta(seconds=i)).isoformat(timespec="milliseconds")


def _samples(n: int) -> list[dict]:
    return [{"ts": _ts(1.0, i), "temp": 60.0 + i} for i in range(n)]