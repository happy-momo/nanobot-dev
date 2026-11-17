"""Read-gate freshness, offline/downtime state machine, QA refusal, regime inject."""

from __future__ import annotations

import asyncio
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

_HERE = Path(__file__).resolve().parent.parent / "edge_adviser"
if str(_HERE) not in sys.path:
    sys.path.insert(0, str(_HERE))

import pytest  # noqa: E402

from edge_adviser.config.model import (  # noqa: E402
    AdviserConfig,
    AssetSpec,
    AssetState,
    MetricSpec,
    SourceKind,
    SourceSpec,
)
from edge_adviser.core import AssetStore  # noqa: E402
from edge_adviser.analytics.engine import empty_snapshot  # noqa: E402
from edge_adviser.analytics.gates import (  # noqa: E402
    classify_snapshot_state,
    gate_snapshot_for_read,
)
from edge_adviser.llm.gateway import LLMResult  # noqa: E402
from edge_adviser.qa.engine import QAEngine  # noqa: E402
from edge_adviser.core.scheduler import Supervisor  # noqa: E402


def make_asset() -> AssetSpec:
    return AssetSpec(
        asset_id="pump1", name="Test Pump", window_s=60, sample_freq_s=1.0,
        metrics=[MetricSpec(key="temp", unit="degC", warn_max=68, crit_max=73, persist=1)],
    )


def make_config(**kw) -> AdviserConfig:
    base = AdviserConfig(
        workspace=_tmp_root(),
        assets=[make_asset()],
        data_freshness_s=100,
        max_empty_windows=3,
        analysis_regime="clean",
    )
    for k, v in kw.items():
        setattr(base, k, v)
    return base


_tmp_root = lambda: Path(__file__).resolve().parent.parent / "_tmp_gates"  # noqa: E731


def test_freshness_gate_stale(tmp_path: Path) -> None:
    store = AssetStore(tmp_path)
    asset = make_asset()
    config = make_config(data_freshness_s=100)
    # write an old snapshot
    old = empty_snapshot(asset, state=AssetState.OFFLINE)
    old["window_end"] = (datetime.now(timezone.utc) - timedelta(seconds=5000)).isoformat()
    old["status"] = "healthy"
    store.save_snapshot(asset.asset_id, old)
    g = gate_snapshot_for_read(store, asset, config)
    assert not g.ok
    assert g.state == AssetState.STALE


def test_freshness_gate_ok(tmp_path: Path) -> None:
    store = AssetStore(tmp_path)
    asset = make_asset()
    config = make_config(data_freshness_s=100)
    snap = empty_snapshot(asset, state=AssetState.HEALTHY)
    store.save_snapshot(asset.asset_id, snap)  # fresh (window_end ~ now)
    g = gate_snapshot_for_read(store, asset, config)
    assert g.ok
    assert g.state == AssetState.HEALTHY


def test_qa_ask_no_snapshot_no_llm(tmp_path: Path) -> None:
    store = AssetStore(tmp_path)
    asset = make_asset()
    config = AdviserConfig(workspace=tmp_path, assets=[asset])

    calls = {"n": 0}

    class _Stub:
        async def astart(self):
            pass

        async def run(self, *a, **k):
            calls["n"] += 1
            return LLMResult(content="fake")

    qa = QAEngine(config, store, _Stub())  # type: ignore
    res = asyncio.run(qa.ask(asset, "current status?"))
    assert res.error and "no_fresh_snapshot" in res.error
    assert calls["n"] == 0  # model never invoked without fresh data


def test_classify_offline_after_empty_windows() -> None:
    asset = make_asset()
    config = make_config(max_empty_windows=3)
    now = datetime.now(timezone.utc)
    empty = {"sample_count": 0}
    assert classify_snapshot_state(asset, empty, 1, now, config) == AssetState.INSUFFICIENT
    assert classify_snapshot_state(asset, empty, 2, now, config) == AssetState.INSUFFICIENT
    assert classify_snapshot_state(asset, empty, 3, now, config) == AssetState.OFFLINE


def test_classify_planned_downtime() -> None:
    now = datetime.now(timezone.utc)
    asset = AssetSpec(
        asset_id="pump1", name="p", window_s=60, sample_freq_s=1.0,
        metrics=[], downtime_windows=[
            {"start": (now - timedelta(minutes=1)).isoformat(),
             "end": (now + timedelta(minutes=1)).isoformat()},
        ],
    )
    config = make_config()
    assert classify_snapshot_state(asset, {"sample_count": 0}, 10, now, config) \
        == AssetState.PLANNED_DOWNTIME


async def _regime_injected_once(tmp_path: Path) -> str:
    config = AdviserConfig(
        workspace=tmp_path, assets=[make_asset()],
        analysis_regime="clean",
        sources=[SourceSpec(kind=SourceKind.SIMULATOR, asset_id="pump1",
                            params={"regime": "clean"})],
    )
    store = AssetStore(tmp_path)
    from edge_adviser.core.runlog import log_run_event
    class _Learn:
        def buffer_snapshot(self, *a, **k):
            pass
    from edge_adviser.report.generator import ReportGenerator
    from edge_adviser.llm.gateway import LLMGateway
    from edge_adviser.llm.context import ContextBuilder
    ctx = ContextBuilder(config, store)

    class _Gw:
        async def astart(self): pass
        async def aclose(self): pass
        async def run(self, *a, **k): return LLMResult(content="")
    gw = _Gw()  # type: ignore
    from edge_adviser.ingest import create_source
    sup = Supervisor(config=config, store=store, gateway=gw,  # type: ignore
                     report_gen=None, learner=_Learn(),  # type: ignore
                     default_regime=config.analysis_regime)
    src = create_source(config.sources[0])
    snap = await sup.run_once(config.assets[0], src)
    return snap["regime"]


def test_regime_injected() -> None:
    regime = asyncio.run(_regime_injected_once(Path(__file__).resolve().parent / "_tmp_regime"))
    assert regime == "clean"  # not the legacy hardcoded "degrade"