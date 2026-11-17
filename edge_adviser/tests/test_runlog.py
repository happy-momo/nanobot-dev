"""Audit ledger: empty-window archiving + runlog, learn-gate keeps memory intact."""

from __future__ import annotations

import asyncio
import sys
from pathlib import Path

_HERE = Path(__file__).resolve().parent.parent / "edge_adviser"
if str(_HERE) not in sys.path:
    sys.path.insert(0, str(_HERE))

import pytest  # noqa: E402

from edge_adviser.config.model import (  # noqa: E402
    AdviserConfig,
    AssetSpec,
    MetricSpec,
    SourceKind,
    SourceSpec,
)
from edge_adviser.core import AssetStore  # noqa: E402


def make_asset() -> AssetSpec:
    return AssetSpec(
        asset_id="pump1", name="Test Pump", window_s=60, sample_freq_s=1.0,
        metrics=[MetricSpec(key="temp", unit="degC", warn_max=68, crit_max=73, persist=1)],
    )


async def _run_once_with_empty_source(tmp_path: Path) -> tuple[dict, AssetStore]:
    config = AdviserConfig(
        workspace=tmp_path, assets=[make_asset()],
        analysis_regime="clean",
        sources=[SourceSpec(kind=SourceKind.SIMULATOR, asset_id="pump1",
                            params={"regime": "clean"})],
    )
    store = AssetStore(tmp_path)
    from edge_adviser.ingest import create_source

    class _Learn:
        def buffer_snapshot(self, *a, **k):
            pass

    from edge_adviser.llm.gateway import LLMResult
    from edge_adviser.llm.context import ContextBuilder

    class _Gw:
        async def astart(self): pass
        async def aclose(self): pass
        async def run(self, *a, **k): return LLMResult(content="")

    from edge_adviser.core.scheduler import Supervisor
    ctx = ContextBuilder(config, store)
    sup = Supervisor(config=config, store=store, gateway=_Gw(),  # type: ignore
                     report_gen=None, learner=_Learn(),  # type: ignore
                     default_regime=config.analysis_regime)
    src = create_source(config.sources[0])
    # simulate an always-empty source by pulling a zero-length window
    snap = await sup.run_once(config.assets[0], _EmptySource(src))
    return snap, store


class _EmptySource:
    def __init__(self, wrapped):
        self.wrapped = wrapped
        self.kind = wrapped.kind

    async def pull(self, ws, we):
        return []  # no data

    async def aclose(self):  # pragma: no cover
        pass


def test_empty_window_archives_and_runlog(tmp_path: Path) -> None:
    snap, store = asyncio.run(_run_once_with_empty_source(tmp_path))
    # an offline/incomplete snapshot is archived, not dropped
    assert snap.get("sample_count") == 0
    assert snap.get("status") in ("insufficient", "offline")
    assert store.list_snapshots("pump1", limit=1)  # file persisted
    # runlog records the no_data outcome
    recs = store.read_runlog("pump1")
    assert recs and any(r.get("kind") == "run_once" and r.get("outcome") == "no_data"
                        for r in recs)


def test_deep_report_no_snapshot_audited(tmp_path: Path) -> None:
    store = AssetStore(tmp_path)
    asset = make_asset()
    config = AdviserConfig(workspace=tmp_path, assets=[asset], data_freshness_s=100)
    from edge_adviser.core.scheduler import Supervisor
    from edge_adviser.llm.gateway import LLMResult

    class _Learn:
        def buffer_snapshot(self, *a, **k): pass

    class _Gw:
        async def astart(self): pass
        async def aclose(self): pass
        async def run(self, *a, **k): return LLMResult(content="")

    sup = Supervisor(config=config, store=store, gateway=_Gw(),  # type: ignore
                     report_gen=None, learner=_Learn())  # type: ignore
    res = asyncio.run(sup.run_deep_report(asset))
    assert "no_fresh_snapshot" in str(res.get("error", ""))
    recs = store.read_runlog("pump1")
    assert any(r.get("kind") == "deep_report" and r.get("outcome") == "no_data"
               for r in recs)


def test_learn_gate_strict_keeps_memory_untouched(tmp_path: Path) -> None:
    store = AssetStore(tmp_path)
    asset = make_asset()
    config = AdviserConfig(workspace=tmp_path, assets=[asset], learn_gate="strict",
                           data_freshness_s=100)
    from edge_adviser.learn.learner import Learner
    from edge_adviser.llm.gateway import LLMResult

    calls = {"n": 0}

    class _Gw:
        async def astart(self): pass
        async def aclose(self): pass
        async def run(self, *a, **k):
            calls["n"] += 1
            return LLMResult(content="# New\nunsupported fact")

    learner = Learner(config, store, _Gw())  # type: ignore
    # feed an untrustworthy snapshot (insufficient)
    bad = {"asset_id": "pump1", "status": "insufficient", "coverage": 0.2,
           "overall_level": "insufficient", "window_end": "2026-01-01T00:00:00Z"}
    learner.buffer_snapshot("pump1", bad)
    res = asyncio.run(learner.learn_asset(asset))
    assert res.error and "insufficient_data" in res.error
    assert calls["n"] == 0  # model not invoked under strict gate
    assert not store.memory_md("pump1").exists()  # MEMORY.md never written