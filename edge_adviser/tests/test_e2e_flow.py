"""End-to-end business-flow tests (0.4.4).

These walk the FULL pipeline the way a plant shift actually uses it, against
the real HTTP surface with a real simulator source and the real analysis
engine — no mocked analytics:

  Flow 1  collect→analyze→alert→ack(note)→feedback→audit
          a failing pump (spike regime) produces a real critical snapshot,
          a real alert, the operator's handling note lands in the feedback
          ledger and the audit runlog, and a re-run does NOT re-alert while
          the ack is active (dedup).
  Flow 2  QA gate: no data → deterministic refusal, model untouched;
          with a fresh snapshot → answer returned with snapshot provenance.
  Flow 3  threshold PATCH → next analysis honors the new band → audited.
  Flow 4  offline machine: consecutive empty windows → OFFLINE status →
          data-fault alert → fleet stats reflect the MTTA/alerts.
"""

from __future__ import annotations

import json
import sys
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path

_HERE = Path(__file__).resolve().parent.parent / "edge_adviser"
if str(_HERE) not in sys.path:
    sys.path.insert(0, str(_HERE))

import pytest  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402

from edge_adviser.config.model import AdviserConfig, AssetSpec, MetricSpec, SourceKind, SourceSpec  # noqa: E402
from edge_adviser.core.asset_store import AssetStore  # noqa: E402
from edge_adviser.core.scheduler import Supervisor  # noqa: E402
from edge_adviser.ingest import create_source  # noqa: E402


def _config(tmp: Path, *, regime: str = "clean") -> dict:
    return {
        "workspace": str(tmp / "ws"),
        "data_freshness_s": 300,
        "assets": [{
            "asset_id": "pump1", "name": "Feed Pump", "kind": "pump",
            "window_s": 60, "sample_freq_s": 1.0,
            "metrics": [{"key": "temp", "unit": "degC", "warn_max": 68,
                         "crit_max": 73, "persist": 2, "min_samples": 5},
                        {"key": "vibration", "unit": "mm/s", "warn_max": 7.1,
                         "crit_max": 9.0, "persist": 2}],
        }],
        "sources": [{
            "kind": "simulator", "asset_id": "pump1",
            "params": {"sample_freq_s": 1.0, "regime": regime, "seed": 7,
                         "base_temp": 78.0},
        }],
    }


class _StubGateway:
    async def astart(self): pass
    async def aclose(self): pass

    async def run(self, *a, **k):
        from edge_adviser.llm.gateway import LLMResult
        return LLMResult(content="stub interpretation", used_provider="stub",
                         degraded=False)


def _client(tmp: Path, cfg: dict) -> TestClient:
    from edge_adviser.server.app import create_app

    p = tmp / "config.json"
    p.write_text(json.dumps(cfg), encoding="utf-8")
    return TestClient(create_app(p, gateway=_StubGateway()))


def _supervisor(tmp: Path, cfg: dict) -> tuple[Supervisor, AssetSpec]:
    """A Supervisor wired against the SAME workspace as the HTTP config."""
    ws = tmp / "ws"
    asset = AssetSpec(
        asset_id="pump1", name="Feed Pump", kind="pump", window_s=60,
        sample_freq_s=1.0,
        metrics=[MetricSpec(key="temp", unit="degC", warn_max=68, crit_max=73,
                            persist=2, min_samples=5),
                 MetricSpec(key="vibration", unit="mm/s", warn_max=7.1,
                            crit_max=9.0, persist=2)],
    )
    config = AdviserConfig(workspace=ws, assets=[asset],
                           sources=[SourceSpec(kind=SourceKind.SIMULATOR,
                                               asset_id="pump1",
                                               params={"sample_freq_s": 1.0,
                                                       "regime": "clean",
                                                       "seed": 7,
                                                       "base_temp": 78.0})])
    store = AssetStore(ws)
    sup = Supervisor(config=config, store=store,  # type: ignore[arg-type]
                     gateway=_StubGateway(), report_gen=None,  # type: ignore[arg-type]
                     learner=type("_L", (), {"buffer_snapshot": lambda *a, **k: None})())
    return sup, asset


def _snapshot_via_pipeline(sup: Supervisor, asset: AssetSpec, regime: str,
                           base_temp: float = 78.0) -> dict:
    """One real collect→analyze→archive round with the given simulator regime."""
    src = create_source(SourceSpec(kind=SourceKind.SIMULATOR, asset_id="pump1",
                                   params={"sample_freq_s": 1.0, "regime": regime,
                                           "seed": 7, "base_temp": base_temp}))
    import asyncio
    return asyncio.run(sup.run_once(asset, src))


# --------------------------------------------------------------------------- #
# Flow 1: collect→analyze→alert→ack(note)→feedback→audit + dedup
# --------------------------------------------------------------------------- #
def test_flow_collect_alert_ack_feedback_audit(tmp_path: Path) -> None:
    tmp = tmp_path
    cfg = _config(tmp, regime="spike")
    c = _client(tmp, cfg)

    # 1. a real spike window through the real engine → critical snapshot
    sup, asset = _supervisor(tmp, cfg)
    snap = _snapshot_via_pipeline(sup, asset, regime="spike")
    assert snap["asset_id"] == "pump1"
    assert snap["sample_count"] > 0
    assert snap["status"] in ("healthy", "partial")
    # the spike regime drives temp above crit_max=73 → critical level
    assert snap["overall_level"] == "critical", snap["headline"]

    # 2. the alert loop derives + persists one critical alert
    from edge_adviser.server.alerts import publish_alert
    import asyncio
    state = c.app.state.edge
    asyncio.run(publish_alert(state, "pump1", snap))
    alerts = c.get("/api/assets/pump1/alerts").json()["data"]
    assert len(alerts) == 1 and alerts[0]["subject"] == "critical"

    # 3. run the same snapshot again → dedup holds (one active critical alert)
    asyncio.run(publish_alert(state, "pump1", snap))
    assert len(c.get("/api/assets/pump1/alerts").json()["data"]) == 1

    # 4. operator acks WITH a handling note
    ts_val = alerts[0]["ts"]
    r = c.post(f"/api/assets/pump1/alerts/{ts_val}/ack",
               json={"ts": ts_val, "note": "更换轴承后振动恢复正常"})
    assert r.json()["data"]["acked"] is True

    # 5. the note reached the feedback ledger (learn-loop raw material)…
    fb = c.get("/api/assets/pump1/feedback").json()["data"]
    assert any("告警处理" in f["note"] and "轴承" in f["note"] for f in fb)
    # …and the ack is audited with actor + elapsed
    rl = c.get("/api/assets/pump1/runlog", params={"limit": 50}).json()["data"]["rows"]
    ack_rows = [e for e in rl if e["kind"] == "ack"]
    assert ack_rows and ack_rows[0]["outcome"] == "ok"

    # 6. a NEW critical alert may now fire (ack freed the dedup key)…
    snap2 = _snapshot_via_pipeline(sup, asset, regime="spike")
    asyncio.run(publish_alert(state, "pump1", snap2))
    fresh = [a for a in c.get("/api/assets/pump1/alerts").json()["data"]
             if a["subject"] == "critical"]
    assert len(fresh) == 1, "after ack, a new critical must alert exactly once"


# --------------------------------------------------------------------------- #
# Flow 2: QA gate — refusal without data, provenance with data
# --------------------------------------------------------------------------- #
def test_flow_qa_gate_then_answer_with_provenance(tmp_path: Path) -> None:
    tmp = tmp_path
    cfg = _config(tmp)
    c = _client(tmp, cfg)
    import asyncio
    state = c.app.state.edge
    asset = state.config.asset("pump1")

    # cold workspace → deterministic refusal, model untouched
    r = c.post("/api/assets/pump1/ask", json={"question": "当前状态如何？"}).json()
    assert r["ok"] is False
    assert r["error"].startswith("no_fresh_snapshot")
    # refusal is audited
    rl = c.get("/api/assets/pump1/runlog").json()["data"]["rows"]
    assert any(e["kind"] == "qa" and e["outcome"] == "gated" for e in rl)

    # seed a fresh snapshot via the app's real pipeline → answer with provenance
    src = create_source(SourceSpec(kind=SourceKind.SIMULATOR, asset_id="pump1",
                                   params={"sample_freq_s": 1.0, "regime": "clean",
                                           "seed": 7, "base_temp": 78.0}))
    snap = asyncio.run(state.supervisor.run_once(asset, src))
    assert snap["sample_count"] > 0
    r = c.post("/api/assets/pump1/ask", json={"question": "当前状态如何？"}).json()
    assert r["ok"] is True
    assert r["data"]["content"]
    # the answer carries the snapshot it was built from — the badge truth
    assert r["data"]["snapshot"]["window_end"] == snap["window_end"]
    assert r["data"]["snapshot"]["trust"] == snap["trust"]

    # allow_stale path: ONLY stale data on disk + allow → answered (not refused)
    stale_end = (datetime.now(timezone.utc) - timedelta(hours=2)).isoformat(
        timespec="seconds")
    state.store.save_snapshot("pump1", {"asset_id": "pump1", "window_end": stale_end,
                                        "status": "healthy", "trust": "full",
                                        "overall_level": "info", "sample_count": 30,
                                        "coverage": 1.0, "metrics": []})
    # the stale snapshot is now the ONLY one the gate can pick (fresh one was
    # saved earlier — prune everything newer to simulate a long outage)
    for f in list(state.store.list_snapshots("pump1", limit=100)):
        if json.loads(f.read_text(encoding="utf-8")).get("window_end") != stale_end:
            f.unlink()
    r = c.post("/api/assets/pump1/ask",
               json={"question": "历史状态？", "allow_stale": True}).json()
    assert r["ok"] is True
    assert r["data"]["snapshot"]["window_end"] == stale_end
    # strict default still refuses the same workspace
    r2 = c.post("/api/assets/pump1/ask", json={"question": "历史状态？"}).json()
    assert r2["ok"] is False and r2["error"].startswith("no_fresh_snapshot")


# --------------------------------------------------------------------------- #
# Flow 3: threshold PATCH → next analysis honors the new band → audited
# --------------------------------------------------------------------------- #
def test_flow_threshold_edit_changes_verdict(tmp_path: Path) -> None:
    tmp = tmp_path
    cfg = _config(tmp, regime="spike")
    c = _client(tmp, cfg)
    import asyncio
    state = c.app.state.edge
    asset = state.config.asset("pump1")
    src = create_source(SourceSpec(kind=SourceKind.SIMULATOR, asset_id="pump1",
                                   params={"sample_freq_s": 1.0, "regime": "spike",
                                           "seed": 7, "base_temp": 78.0}))
    snap = asyncio.run(state.supervisor.run_once(asset, src))
    assert snap["overall_level"] == "critical"  # temp above 73

    # raise crit_max above the spike → same data, no critical anymore
    r = c.patch("/api/assets/pump1/metrics/temp/thresholds",
                json={"crit_max": 200}).json()
    assert r["ok"] is True and "crit_max" in r["data"]["changed"]
    # the edit landed on the LIVE spec the supervisor's next analysis reads
    snap2 = asyncio.run(state.supervisor.run_once(asset, src))
    assert snap2["overall_level"] != "critical"

    # audited with from→to
    rl = c.get("/api/assets/pump1/runlog").json()["data"]["rows"]
    row = next(e for e in rl if e["kind"] == "threshold_edit")
    assert "crit_max" in row["reason"]


# --------------------------------------------------------------------------- #
# Flow 4: disconnection → OFFLINE → data-fault alert → stats
# --------------------------------------------------------------------------- #
def test_flow_offline_alert_and_stats(tmp_path: Path) -> None:
    tmp = tmp_path
    cfg = _config(tmp)
    c = _client(tmp, cfg)
    sup, asset = _supervisor(tmp, cfg)
    import asyncio

    state = c.app.state.edge
    from edge_adviser.server.alerts import publish_alert

    # 4 consecutive empty windows (max_empty_windows default 3) → OFFLINE
    statuses = []
    for _ in range(4):
        src = create_source(SourceSpec(kind=SourceKind.SIMULATOR,
                                       asset_id="pump1",
                                       params={"regime": "offline"}))
        snap = asyncio.run(sup.run_once(asset, src))
        statuses.append(snap["status"])
        asyncio.run(publish_alert(state, "pump1", snap))
    assert statuses[-1] == "offline"
    # the offline data-fault produced exactly one alert
    alerts = c.get("/api/assets/pump1/alerts").json()["data"]
    assert len(alerts) == 1 and alerts[0]["subject"] == "offline"

    # operator acknowledges → MTTA shows up in /api/stats
    ts_val = alerts[0]["ts"]
    c.post(f"/api/assets/pump1/alerts/{ts_val}/ack", json={"ts": ts_val, "note": "网线松动已插回"})
    stats = c.get("/api/stats").json()["data"]
    assert stats["alerts"]["unacked"] == 0
    assert stats["alerts"]["mtta_s"] is not None
    a = next(x for x in stats["assets"] if x["asset_id"] == "pump1")
    assert a["alert_count"] == 1
    # the offline windows were archived (audit trail of the outage)
    assert a["snapshot_count"] >= 4
