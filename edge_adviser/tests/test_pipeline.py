"""End-to-end deterministic tests for the EdgeAdviser pipeline.

These run fully offline: they exercise the config loader, asset store, simulator
source, analytics engine, and report archiver with a stub LLM gateway — no model
API is called. The goal is to prove the fact-extraction and archiving layers work
before any provider is configured.
"""

from __future__ import annotations

import asyncio
import json
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

# ensure the inner package is importable regardless of CWD
_HERE = Path(__file__).resolve().parent.parent / "edge_adviser"
if str(_HERE) not in sys.path:
    sys.path.insert(0, str(_HERE))

import pytest  # noqa: E402

from edge_adviser.config.model import (  # noqa: E402
    AssetSpec,
    MetricSpec,
    ProviderSpec,
    SourceKind,
    SourceSpec,
)
from edge_adviser.core import AssetStore  # noqa: E402
from edge_adviser.analytics import analyze  # noqa: E402
from edge_adviser.ingest import create_source  # noqa: E402


def make_asset() -> AssetSpec:
    return AssetSpec(
        asset_id="pump1",
        name="Test Pump",
        window_s=60,
        sample_freq_s=1.0,
        metrics=[
            MetricSpec(key="temp", unit="degC", warn_max=68, crit_max=73, persist=5),
            MetricSpec(key="vibration", unit="mm/s", warn_max=3.0, crit_max=4.5, persist=5),
            MetricSpec(key="rpm", unit="rpm", norm_min=1200, norm_max=1600),
        ],
    )


@pytest.mark.parametrize("regime", ["clean", "shift", "spike", "degrade"])
def test_analytics_detects_regimes(regime: str) -> None:
    asset = make_asset()
    src = create_source(SourceSpec(
        kind=SourceKind.SIMULATOR, asset_id="pump1",
        params={"regime": regime, "sample_freq_s": 1.0, "uptime_h": 200},
    ))
    end = datetime.now(timezone.utc)
    start = end - timedelta(seconds=60)
    samples = asyncio.run(src.pull(start, end))
    assert len(samples) == 60
    snap = analyze(asset, samples, source="sim", regime=regime)
    # every metric must produce a fact snapshot
    assert len(snap.metrics) == 3
    for m in snap.metrics:
        assert m.n == 60
        assert m.mean is not None
    # degrade regime must raise temp slope and/or vibration warning
    if regime == "degrade":
        assert snap.overall_level.value in ("warn", "critical")


def test_snapshot_json_roundtrip() -> None:
    asset = make_asset()
    src = create_source(SourceSpec(
        kind=SourceKind.SIMULATOR, asset_id="pump1", params={"regime": "clean"},
    ))
    end = datetime.now(timezone.utc)
    start = end - timedelta(seconds=60)
    samples = asyncio.run(src.pull(start, end))
    snap = analyze(asset, samples, source="sim", regime="clean")
    d = snap.to_dict()
    # JSON-serializable (no numpy types leaking)
    json.dumps(d)
    assert d["asset_id"] == "pump1"
    assert d["overall_level"] in ("info", "warn", "critical")


def test_asset_store_materializes_profile(tmp_path: Path) -> None:
    store = AssetStore(tmp_path)
    asset = make_asset()
    p = store.ensure_asset(asset)
    assert p.exists()
    md = p.read_text(encoding="utf-8")
    assert "Test Pump" in md
    assert "vibration" in md


def test_report_archive_without_llm(tmp_path: Path) -> None:
    """Archiving writes report.md + snapshot.json even with a stub gateway."""
    from edge_adviser.report.generator import ReportGenerator
    from edge_adviser.llm.gateway import LLMResult
    from edge_adviser.config.model import AdviserConfig

    store = AssetStore(tmp_path)
    asset = make_asset()
    config = AdviserConfig(workspace=tmp_path, assets=[asset])

    class _Stub:
        async def run(self, *a, **k):  # pragma: no cover
            return LLMResult(content="# Test Pump — Operational Report\n\n## Executive Summary\nNominal.")

    gateway = _Stub()  # type: ignore
    gen = ReportGenerator(config, store, gateway)
    snap = {"asset_id": "pump1", "overall_level": "info", "headline": "ok"}
    out = gen.archive(asset, snap, "# Test Pump — Operational Report\n\n## Executive Summary\nNominal.")
    assert (out / "report.md").exists()
    assert (out / "snapshot.json").exists()