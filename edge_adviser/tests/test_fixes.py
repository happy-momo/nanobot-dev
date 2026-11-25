"""Tests for the bug-fix batch + demo mode. All offline & deterministic."""

from __future__ import annotations

import asyncio
import json
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

_HERE = Path(__file__).resolve().parent.parent / "edge_adviser"
if str(_HERE) not in sys.path:
    sys.path.insert(0, str(_HERE))

from edge_adviser import __version__  # noqa: E402
from edge_adviser.config.model import SourceSpec, SourceKind  # noqa: E402
from edge_adviser.ingest.simulator import SimulatorSource  # noqa: E402


# --------------------------------------------------------------------------- #
#  B4: simulator anchors every pull to the requested window
# --------------------------------------------------------------------------- #
def _spec(params: dict) -> SourceSpec:
    return SourceSpec(kind=SourceKind.SIMULATOR, asset_id="t", params=params)


def test_simulator_pull_anchored_to_window() -> None:
    src = SimulatorSource(_spec({"sample_freq_s": 1.0}))
    now = datetime.now(timezone.utc)
    b1 = asyncio.run(src.pull(now - timedelta(seconds=60), now))
    later = now + timedelta(seconds=5)
    b2 = asyncio.run(src.pull(later - timedelta(seconds=60), later))
    assert len(b1) == 60 and len(b2) == 60
    last_ts = datetime.fromisoformat(b2[-1]["ts"])
    assert last_ts <= later + timedelta(seconds=1)  # no future-dated samples
    assert abs((later - last_ts).total_seconds()) <= 2.0


def test_simulator_offline_and_fault_regimes() -> None:
    off = SimulatorSource(_spec({"regime": "offline"}))
    now = datetime.now(timezone.utc)
    assert asyncio.run(off.pull(now - timedelta(seconds=60), now)) == []

    bad = SimulatorSource(_spec({"regime": "fault"}))
    with pytest.raises(RuntimeError):
        asyncio.run(bad.pull(now - timedelta(seconds=60), now))


def test_simulator_seed_param_varies_noise() -> None:
    now = datetime.now(timezone.utc)
    a = asyncio.run(SimulatorSource(_spec({"seed": 11})).pull(
        now - timedelta(seconds=60), now))
    b = asyncio.run(SimulatorSource(_spec({"seed": 99})).pull(
        now - timedelta(seconds=60), now))
    assert len(a) == len(b) == 60
    assert [s["temp"] for s in a] != [s["temp"] for s in b]


# --------------------------------------------------------------------------- #
#  B1: alerts actually broadcast on the bus
# --------------------------------------------------------------------------- #
def _write_config(tmp: Path, *, warn_bands: bool = False, pin: str = "none") -> Path:
    """One-asset config; warn_bands=True puts the seeded snapshot into warn level."""
    metric = {
        "key": "temp", "unit": "degC", "norm_max": 68,
        "warn_max": 68 if warn_bands else 90, "crit_max": 73 if warn_bands else 95,
        "persist": 5, "min_samples": 30, "description": "bearing temp",
    }
    cfg = {
        "workspace": str(tmp / "ws"),
        "hmi_pin": pin,
        "assets": [{"asset_id": "pump1", "name": "test pump", "window_s": 60,
                    "sample_freq_s": 1.0, "metrics": [metric]}],
        "sources": [{"kind": "simulator", "asset_id": "pump1"}],
        "data_freshness_s": 1800,
    }
    p = tmp / "config.json"
    p.write_text(json.dumps(cfg), encoding="utf-8")
    return p


def _seed_snapshot(ws: Path, asset_id: str, mean: float = 62.0) -> None:
    end = datetime.now(timezone.utc)
    start = end - timedelta(minutes=1)
    snap = {
        "asset_id": asset_id,
        "window_start": start.isoformat(),
        "window_end": end.isoformat(),
        "source": "test", "regime": "clean",
        "overall_level": "warn" if mean >= 70 else "info",
        "status": "healthy", "trust": "full", "coverage": 1.0,
        "expected_samples": 60, "sample_count": 60,
        "headline": "warn threshold exceeded" if mean >= 70 else "Nominal",
        "metrics": [{"key": "temp", "unit": "degC", "n": 60, "mean": mean,
                     "min": mean - 2, "max": mean + 2, "p95": mean + 1,
                     "level": "warn" if mean >= 70 else "info"}],
    }
    d = ws / "assets" / asset_id / "snapshots"
    d.mkdir(parents=True, exist_ok=True)
    safe = end.isoformat().replace(":", "-")
    (d / f"{safe}.json").write_text(json.dumps(snap), encoding="utf-8")


class RecGateway:
    calls = 0

    async def astart(self): pass
    async def aclose(self): pass

    async def run(self, prompt, **kw):
        RecGateway.calls += 1
        from edge_adviser.llm.gateway import LLMResult
        return LLMResult(content="stub", used_provider="stub", degraded=False)


def test_alert_broadcast_on_bus(tmp_path: Path) -> None:
    from edge_adviser.server.app import create_app

    cfg = _write_config(tmp_path, warn_bands=True)
    _seed_snapshot(tmp_path / "ws", "pump1", mean=72.0)  # warn level
    app = create_app(cfg, gateway=RecGateway())
    state = app.state.edge

    got: list[dict] = []
    stop = asyncio.new_event_loop()

    async def scenario():
        task = await state.bus.subscribe({"alert"}, on_event=got.append)
        # publish_alert = derive + dedup + persist + broadcast; with the fixed
        # KIND_ALERT import the broadcast must reach the subscriber.
        from edge_adviser.server.alerts import publish_alert
        snap = {"status": "healthy", "overall_level": "warn",
                "state_reason": "", "headline": "h", "window_end": "x",
                "metrics": []}
        await publish_alert(state, "pump1", snap)
        await asyncio.sleep(0.05)
        task.cancel()

    asyncio.run(scenario())
    stop.close()
    assert any(ev["kind"] == "alert" for ev in got)


# --------------------------------------------------------------------------- #
#  B2: PIN enforcement middleware
# --------------------------------------------------------------------------- #
def test_pin_enforced_and_sse_token_query(tmp_path: Path) -> None:
    from edge_adviser.server.app import create_app

    cfg = _write_config(tmp_path, pin="1234")
    app = create_app(cfg, gateway=RecGateway())
    c = TestClient(app)

    # protected without token → 401
    assert c.get("/api/assets").status_code == 401
    # bad pin → 401
    assert c.post("/api/auth/pin", json={"pin": "wrong"}).status_code == 401
    # correct pin → token
    r = c.post("/api/auth/pin", json={"pin": "1234"})
    assert r.status_code == 200
    token = r.json()["data"]["token"]
    assert token
    # bearer works
    r2 = c.get("/api/assets", headers={"Authorization": f"Bearer {token}"})
    assert r2.status_code == 200
    # health/version exempt
    assert c.get("/api/health").status_code == 200
    assert c.get("/api/version").status_code == 200
    # SSE surface is covered by the middleware too: without a token it must be
    # rejected (401) instead of opening an endless stream. (A *successful* SSE
    # request never terminates, so we don't open one under TestClient here.)
    r4 = c.get("/stream/telemetry")
    assert r4.status_code == 401


# --------------------------------------------------------------------------- #
#  B3: SPA deep-link fallback
# --------------------------------------------------------------------------- #
def test_spa_deeplink_fallback(tmp_path: Path, monkeypatch) -> None:
    import edge_adviser.server.app as app_module
    from edge_adviser.server.app import create_app

    dist = tmp_path / "dist"
    dist.mkdir()
    (dist / "index.html").write_text("<html>shell</html>", encoding="utf-8")
    (dist / "app.js").write_text("console.log(1)", encoding="utf-8")
    monkeypatch.setattr(app_module, "_DIST", dist)

    cfg = _write_config(tmp_path)
    c = TestClient(create_app(cfg, gateway=RecGateway()))

    r = c.get("/dist/monitor/pump1")   # extension-less miss → shell
    assert r.status_code == 200
    assert "shell" in r.text
    r2 = c.get("/dist/missing.js")     # real asset miss stays 404
    assert r2.status_code == 404


# --------------------------------------------------------------------------- #
#  B6/B7: snapshot metrics opt-in + overview history + version fields
# --------------------------------------------------------------------------- #
def test_snapshots_include_metrics_param(tmp_path: Path) -> None:
    from edge_adviser.server.app import create_app

    cfg = _write_config(tmp_path)
    _seed_snapshot(tmp_path / "ws", "pump1")
    c = TestClient(create_app(cfg, gateway=RecGateway()))

    plain = c.get("/api/assets/pump1/snapshots").json()["data"]
    assert plain and "metrics" not in plain[0]
    rich = c.get("/api/assets/pump1/snapshots",
                 params={"include_metrics": 1}).json()["data"]
    assert rich[0]["metrics"][0]["key"] == "temp"


def test_overview_history_series(tmp_path: Path) -> None:
    from edge_adviser.server.app import create_app

    cfg = _write_config(tmp_path)
    ws = tmp_path / "ws"
    for _ in range(3):
        _seed_snapshot(ws, "pump1", mean=62.0)
    c = TestClient(create_app(cfg, gateway=RecGateway()))

    r = c.get("/api/overview", params={"history": 5}).json()["data"]
    rt = r["cards"][0]["runtime"]
    assert len(rt["series"]) == 3
    assert {"window_end", "value", "level"} <= set(rt["series"][0])


def test_version_and_config_demo_fields(tmp_path: Path) -> None:
    from edge_adviser.server.app import create_app

    cfg = _write_config(tmp_path)
    c = TestClient(create_app(cfg, gateway=RecGateway()))
    v = c.get("/api/version").json()["data"]
    assert v["service"] == "edge-adviser-hmi"
    assert v["demo"] is False
    assert v["assets"] == 1
    assert v["version"] == __version__
    cfgv = c.get("/api/config").json()["data"]
    assert "demo" in cfgv


def test_task_stream_token_filter_and_unique_tokens(tmp_path: Path) -> None:
    from edge_adviser.server.app import create_app

    cfg = _write_config(tmp_path)
    app = create_app(cfg, gateway=RecGateway())
    from edge_adviser.config.model import SourceSpec
    app.state.edge.supervisor._sources["pump1"] = SourceSpec(
        kind=SourceKind.SIMULATOR, asset_id="pump1")
    app.state.edge.supervisor._sources["pump1"] = __import__(
        "edge_adviser.ingest", fromlist=["create_source"]).create_source(
        SourceSpec(kind=SourceKind.SIMULATOR, asset_id="pump1"))
    c = TestClient(app)

    r = c.post("/api/assets/pump1/run-now").json()
    assert r["ok"] is True
    token = r["data"].get("token")
    assert token and len(token) >= 16  # uuid4 hex, not the legacy kind string


# --------------------------------------------------------------------------- #
#  0.5.1: config loader honors retention fields
# --------------------------------------------------------------------------- #
def test_loader_reads_retention_fields(tmp_path: Path) -> None:
    from edge_adviser.config.loader import load_config

    cfg = {"workspace": str(tmp_path / "ws"),
           "max_snapshots": 50, "max_runlog_rows": 500, "assets": []}
    p = tmp_path / "config.json"
    p.write_text(json.dumps(cfg), encoding="utf-8")
    c = load_config(p)
    assert c.max_snapshots == 50
    assert c.max_runlog_rows == 500


def test_loader_rejects_absurd_retention_values(tmp_path: Path) -> None:
    from edge_adviser.config.loader import load_config

    for field, bad in (("max_snapshots", 5), ("max_runlog_rows", 10)):
        cfg = {"workspace": str(tmp_path / "ws"), "assets": [], field: bad}
        p = tmp_path / f"config-{field}.json"
        p.write_text(json.dumps(cfg), encoding="utf-8")
        with pytest.raises(ValueError, match=field):
            load_config(p)


# --------------------------------------------------------------------------- #
#  0.5.1: snapshot / MEMORY.md writes are atomic (tmp + os.replace)
# --------------------------------------------------------------------------- #
def test_snapshot_and_memory_writes_are_atomic(tmp_path: Path) -> None:
    from edge_adviser.core.asset_store import AssetStore

    store = AssetStore(tmp_path)
    snap = {"window_end": "2026-11-25T00:00:00+00:00", "state": "healthy"}
    p = store.save_snapshot("a1", snap)
    assert json.loads(p.read_text(encoding="utf-8"))["state"] == "healthy"
    # no tmp residue next to the snapshot
    assert list(p.parent.glob("*.tmp")) == []

    mp = store.write_memory_md("a1", "# knowledge v1")
    assert mp.read_text(encoding="utf-8") == "# knowledge v1"
    store.write_memory_md("a1", "# knowledge v2 — longer content over same path")
    assert "v2" in mp.read_text(encoding="utf-8")
    assert list(mp.parent.glob("*.tmp")) == []


# --------------------------------------------------------------------------- #
#  0.5.1: headless serve mode derives alerts (parity with the web loop)
# --------------------------------------------------------------------------- #
def test_serve_mode_ingests_alerts(tmp_path: Path) -> None:
    import asyncio
    from edge_adviser.core.asset_store import AssetStore
    from edge_adviser.cli import _ServeAlertSink
    from edge_adviser.server.alerts import ingest_alert

    store = AssetStore(tmp_path)
    sink = _ServeAlertSink(store)
    now = datetime.now(timezone.utc)
    # a critical snapshot must produce exactly one alert (dedup on subject)
    snap = {"asset_id": "pump1", "status": "healthy", "overall_level": "critical",
            "state_reason": "over crit band", "headline": "temp critical",
            "window_end": now.isoformat(timespec="seconds")}
    a1 = ingest_alert(sink, "pump1", snap)
    assert a1 and a1["subject"] == "critical"
    assert not ingest_alert(sink, "pump1", snap), "second critical run must dedup"
    p = store.asset_dir("pump1") / "alerts.jsonl"
    rows = [json.loads(x) for x in p.read_text(encoding="utf-8").splitlines() if x.strip()]
    assert len(rows) == 1
    # planned downtime stays exempt, same as the HMI path
    snap2 = {"asset_id": "pump1", "status": "planned_downtime", "overall_level": "info",
             "state_reason": "", "headline": "", "window_end": now.isoformat(timespec="seconds")}
    assert not ingest_alert(sink, "pump1", snap2)
    assert len(rows) == 1


# --------------------------------------------------------------------------- #
#  0.5.2: scheduler start() isolates a bad source instead of dying
# --------------------------------------------------------------------------- #
def test_scheduler_start_isolates_bad_source(tmp_path: Path) -> None:
    """A ValueError from a bad connector config must not kill supervisor.start()."""
    import asyncio
    from edge_adviser.config.loader import load_config
    from edge_adviser.core.asset_store import AssetStore
    from edge_adviser.core.scheduler import Supervisor

    cfg = {"workspace": str(tmp_path / "ws"),
           "assets": [{"asset_id": "bad1", "name": "b", "window_s": 60},
                      {"asset_id": "ok1", "name": "o", "window_s": 60}],
           "sources": [
               {"kind": "mqtt", "asset_id": "bad1", "url": "not-a-url", "topic": "x"},
               {"kind": "simulator", "asset_id": "ok1"}]}
    p = tmp_path / "config.json"
    p.write_text(json.dumps(cfg), encoding="utf-8")
    c = load_config(p)

    class Gw:
        async def astart(self): pass
        async def aclose(self): pass
        async def run(self, *a, **k):
            from edge_adviser.llm.gateway import LLMResult
            return LLMResult(content="ok", used_provider="stub", degraded=False)

    from edge_adviser.report.generator import ReportGenerator
    from edge_adviser.learn.learner import Learner
    store = AssetStore(c.workspace)
    sup = Supervisor(config=c, store=store, gateway=Gw(),
                     report_gen=ReportGenerator(c, store, Gw()),
                     learner=Learner(c, store, Gw()))
    asyncio.run(sup.start())  # must NOT raise
    try:
        assert "ok1" in sup._sources, "healthy asset must still be wired"
        assert "bad1" not in sup._sources, "bad asset must be isolated"
        rows = store.read_runlog("bad1", limit=5)
        assert any(r.get("reason", "").startswith("source_failed") for r in rows)
    finally:
        asyncio.run(sup.stop())
