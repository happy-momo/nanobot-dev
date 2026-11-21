"""UX-workflow backend tests (0.4.3): ack loop, audit, pagination, CSV,
threshold editing, mock failure injection, honest health.

Covers the M-A/M-B/M-D milestone backend work:
  * MB1  — ack with note (persisted + echoed to feedback), bulk ack per asset
           and fleet-wide, ack/feedback/qa land in runlog
  * MD1  — runlog cursor pagination + CSV export; actor/elapsed fields;
           /api/health reports scheduler liveness honestly
  * MA4  — report/learn return a task token immediately (background execution)
  * MD4  — threshold PATCH: validation, atomic config write, runlog audit
  * F7   — MockGateway deterministic failure injection
"""

from __future__ import annotations

import asyncio
import json
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

_HERE = Path(__file__).resolve().parent.parent / "edge_adviser"
if str(_HERE) not in sys.path:
    sys.path.insert(0, str(_HERE))

import pytest  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402

from edge_adviser.config.model import AdviserConfig, AssetSpec, MetricSpec  # noqa: E402
from edge_adviser.server.alerts import acknowledge, acknowledge_many, derive_alert  # noqa: E402
from edge_adviser.llm.mock import MockGateway  # noqa: E402
from edge_adviser.config.model import ModelRole  # noqa: E402


def _asset(tmp: Path) -> tuple[AdviserConfig, AssetSpec]:
    asset = AssetSpec(
        asset_id="pump1", name="Test Pump", window_s=60, sample_freq_s=1.0,
        metrics=[MetricSpec(key="temp", unit="degC", warn_max=68, crit_max=73,
                            persist=1)],
    )
    cfg = AdviserConfig(workspace=tmp, assets=[asset])
    return cfg, asset


def _seed_alerts(tmp: Path, n: int = 3, subject: str = "warn") -> None:
    """Seed alert rows into the SAME workspace the config points at (tmp/ws)."""
    from edge_adviser.core.asset_store import AssetStore
    store = AssetStore(tmp / "ws")
    now = time.time()
    for i in range(n):
        rec = {
            "asset_id": "pump1", "state": "healthy", "subject": subject,
            "reason": "over warn band", "overall_level": subject,
            "headline": f"alert {i}", "window_end": "", "ts": now + i,
            "acked": False, "acked_at": None, "dedup_key": f"pump1:{subject}",
        }
        p = store.asset_dir("pump1") / "alerts.jsonl"
        with open(p, "a", encoding="utf-8") as f:
            f.write(json.dumps(rec, ensure_ascii=False) + "\n")


import time  # noqa: E402


# --------------------------------------------------------------------------- #
# MB1: ack workflow
# --------------------------------------------------------------------------- #
def _state_for(tmp: Path) -> Any:
    """AppState over the SAME workspace the HTTP config uses (tmp/ws)."""
    from edge_adviser.server.deps import AppState
    cfg, asset = _asset(tmp / "ws")
    from edge_adviser.core.asset_store import AssetStore
    store = AssetStore(tmp / "ws")
    return AppState(cfg, store, gateway=_Stub())


class _Stub:
    async def astart(self): pass
    async def aclose(self): pass

    async def run(self, *a, **k):
        from edge_adviser.llm.gateway import LLMResult
        return LLMResult(content="stub", used_provider="stub", degraded=False)


def test_ack_with_note_persists_and_feeds_feedback(tmp_path: Path) -> None:
    _seed_alerts(tmp_path, n=1)
    c = _client(tmp_path)
    # read the seeded alert's ts via the API
    alerts = c.get("/api/assets/pump1/alerts").json()["data"]
    assert len(alerts) == 1
    ts_val = alerts[0]["ts"]
    # ack through the HTTP endpoint with a handling note
    r = c.post(f"/api/assets/pump1/alerts/{ts_val}/ack", json={"ts": ts_val,
                                                               "note": "replaced coupling"})
    assert r.json()["ok"] is True and r.json()["data"]["acked"] is True
    # note persisted on the alert
    alerts = c.get("/api/assets/pump1/alerts", params={"include_acked": "true"}).json()["data"]
    assert alerts[0]["acked"] is True
    assert alerts[0]["ack_note"] == "replaced coupling"
    # the note must reach the learn loop via the feedback ledger
    fb = c.get("/api/assets/pump1/feedback").json()["data"]
    assert any("告警处理" in f.get("note", "") and "coupling" in f.get("note", "")
               for f in fb)


def test_ack_writes_runlog(tmp_path: Path) -> None:
    _seed_alerts(tmp_path, n=1)
    c = _client(tmp_path)
    alerts = c.get("/api/assets/pump1/alerts").json()["data"]
    ts_val = alerts[0]["ts"]
    c.post(f"/api/assets/pump1/alerts/{ts_val}/ack", json={"ts": ts_val, "note": "ok"})
    rl = c.get("/api/assets/pump1/runlog", params={"limit": 10}).json()["data"]["rows"]
    assert any(e.get("kind") == "ack" for e in rl)
    ack_rows = [e for e in rl if e.get("kind") == "ack"]
    assert ack_rows[-1].get("elapsed_s") is not None


def test_ack_all_fleet_endpoint(tmp_path: Path) -> None:
    _seed_alerts(tmp_path, n=3, subject="warn")
    c = _client(tmp_path)
    r = c.post("/api/alerts/ack-all", json={"subject": "warn", "note": "storm passed"})
    assert r.json()["ok"] is True
    assert r.json()["data"]["acked"] == 3
    alerts = c.get("/api/assets/pump1/alerts").json()["data"]
    assert alerts == [], "all unacked warn alerts must be acked"
    # audited + feedback echo
    rl = c.get("/api/assets/pump1/runlog", params={"limit": 10}).json()["data"]["rows"]
    assert any(e.get("kind") == "ack" for e in rl)
    fb = c.get("/api/assets/pump1/feedback").json()["data"]
    assert any("批量确认" in f.get("note", "") for f in fb)


def test_ack_many_per_subject(tmp_path: Path) -> None:
    _seed_alerts(tmp_path, n=2, subject="warn")
    _seed_alerts(tmp_path, n=2, subject="critical")
    state = _state_for(tmp_path)
    n = acknowledge_many(state, "pump1", subject="critical", note="bulk")
    assert n == 2
    p = state.store.asset_dir("pump1") / "alerts.jsonl"
    recs = [json.loads(x) for x in p.read_text(encoding="utf-8").splitlines() if x.strip()]
    acked_subjects = {r["subject"] for r in recs if r["acked"]}
    assert acked_subjects == {"critical"}
    unacked = [r for r in recs if not r["acked"]]
    assert len(unacked) == 2 and all(r["subject"] == "warn" for r in unacked)


# --------------------------------------------------------------------------- #
# MD1: pagination + CSV + honest health (HTTP level)
# --------------------------------------------------------------------------- #
def _write_config(tmp: Path) -> Path:
    cfg = {
        "workspace": str(tmp / "ws"),
        "assets": [{"asset_id": "pump1", "name": "p", "window_s": 60,
                    "metrics": [{"key": "temp", "unit": "degC",
                                 "warn_max": 68, "crit_max": 73}]}],
        "sources": [{"kind": "simulator", "asset_id": "pump1"}],
    }
    p = tmp / "config.json"
    p.write_text(json.dumps(cfg), encoding="utf-8")
    return p


class _Stub2:
    async def astart(self): pass
    async def aclose(self): pass

    async def run(self, *a, **k):
        from edge_adviser.llm.gateway import LLMResult
        return LLMResult(content="stub", used_provider="stub", degraded=False)


def _client(tmp: Path) -> TestClient:
    from edge_adviser.server.app import create_app
    return TestClient(create_app(_write_config(tmp), gateway=_Stub2()))


def test_runlog_pagination_and_csv(tmp_path: Path) -> None:
    c = _client(tmp_path)
    # generate >5 audit rows via repeated feedback posts (distinct notes so
    # same-second rows stay distinguishable)
    for i in range(6):
        r = c.post("/api/assets/pump1/feedback", json={"note": f"unique note {i}"})
        assert r.json()["ok"] is True
    r = c.get("/api/assets/pump1/runlog", params={"limit": 3})
    data = r.json()["data"]
    assert len(data["rows"]) == 3
    assert data["total"] >= 6
    assert data["next_before"] == 3, "full page must carry the offset cursor"
    # follow the cursor: next page, no overlap with page 1 (offset cursors are
    # positional; identical same-second rows are fine, positions are not)
    r2 = c.get("/api/assets/pump1/runlog",
               params={"limit": 3, "before": data["next_before"]})
    rows2 = r2.json()["data"]["rows"]
    assert len(rows2) >= 3
    # identify rows by their feedback detail (each note is unique)
    details1 = {x.get("detail", "") for x in data["rows"]}
    details2 = {x.get("detail", "") for x in rows2}
    assert not (details1 & details2), "pages must not overlap"
    # CSV export includes header + the audited rows
    rc = c.get("/api/assets/pump1/runlog.csv")
    assert rc.status_code == 200
    assert rc.text.startswith("timestamp,date,kind,outcome")
    assert "feedback" in rc.text


def test_feedback_and_qa_audited(tmp_path: Path) -> None:
    c = _client(tmp_path)
    c.post("/api/assets/pump1/feedback", json={"note": "coupling loose"})
    r = c.get("/api/assets/pump1/runlog", params={"limit": 10})
    kinds = [x.get("kind") for x in r.json()["data"]["rows"]]
    assert "feedback" in kinds
    # QA is audited even when refused (no fresh snapshot in this cold workspace)
    c.post("/api/assets/pump1/ask", json={"question": "status?"})
    r = c.get("/api/assets/pump1/runlog", params={"limit": 10})
    kinds = [x.get("kind") for x in r.json()["data"]["rows"]]
    assert "qa" in kinds


def test_health_reports_scheduler_liveness(tmp_path: Path) -> None:
    c = _client(tmp_path)
    r = c.get("/api/health")
    data = r.json()["data"]
    # TestClient never runs the scheduler loop: status must be degraded, not ok
    assert data["status"] == "degraded"
    assert data["scheduler"]["running"] is False
    assert data["gateway"] == "_Stub2"


def test_feedback_note_length_bounded(tmp_path: Path) -> None:
    c = _client(tmp_path)
    r = c.post("/api/assets/pump1/feedback", json={"note": "x" * 3000})
    assert r.status_code == 422  # pydantic max_length


# --------------------------------------------------------------------------- #
# MA4: report/learn return tokens immediately
# --------------------------------------------------------------------------- #
def test_report_returns_token_without_blocking(tmp_path: Path) -> None:
    c = _client(tmp_path)
    import time as _t
    t0 = _t.time()
    r = c.post("/api/assets/pump1/report")
    dt = _t.time() - t0
    assert r.status_code == 200
    data = r.json()["data"]
    assert data.get("token"), "token must be returned immediately"
    assert dt < 5.0, f"report endpoint must not block ({dt:.1f}s)"


def test_learn_returns_token_without_blocking(tmp_path: Path) -> None:
    c = _client(tmp_path)
    r = c.post("/api/assets/pump1/learn")
    assert r.json()["data"].get("token")


# --------------------------------------------------------------------------- #
# MD4: threshold editing
# --------------------------------------------------------------------------- #
def test_threshold_patch_validates_and_persists(tmp_path: Path) -> None:
    c = _client(tmp_path)
    r = c.patch("/api/assets/pump1/metrics/temp/thresholds",
                json={"warn_max": 70, "crit_max": 75})
    assert r.status_code == 200
    data = r.json()["data"]
    assert data["changed"]["warn_max"] == [68, 70]
    # persisted to the config file
    raw = json.loads((tmp_path / "config.json").read_text(encoding="utf-8"))
    m = raw["assets"][0]["metrics"][0]
    assert m["warn_max"] == 70 and m["crit_max"] == 75
    # audited
    rl = c.get("/api/assets/pump1/runlog", params={"limit": 5})
    assert any(x.get("kind") == "threshold_edit" for x in rl.json()["data"]["rows"])


def test_threshold_patch_rejects_inverted_band(tmp_path: Path) -> None:
    c = _client(tmp_path)
    r = c.patch("/api/assets/pump1/metrics/temp/thresholds",
                json={"warn_min": 80, "warn_max": 60})
    assert r.json()["ok"] is False
    assert "warn_min" in r.json()["error"]


def test_threshold_patch_rejects_crit_below_warn(tmp_path: Path) -> None:
    c = _client(tmp_path)
    r = c.patch("/api/assets/pump1/metrics/temp/thresholds",
                json={"crit_min": 50})  # below warn_max=68
    assert r.json()["ok"] is False


def test_threshold_patch_unknown_metric(tmp_path: Path) -> None:
    c = _client(tmp_path)
    r = c.patch("/api/assets/pump1/metrics/nosuch/thresholds", json={"warn_max": 1})
    assert r.json()["ok"] is False


# --------------------------------------------------------------------------- #
# F7: MockGateway deterministic failure injection
# --------------------------------------------------------------------------- #
def test_mock_gateway_failure_injection_deterministic() -> None:
    cfg = AdviserConfig(workspace=Path("."), demo=True)
    gw = MockGateway(cfg, failure_spec={
        "assets": ["pump1"], "modes": ["qa"], "every": 2,
        "error": "simulated outage"})
    asyncio.run(gw.astart())

    async def two_calls():
        r1 = await gw.run("q", role=ModelRole.ROUTINE, session_key="asset:pump1:qa")
        r2 = await gw.run("q", role=ModelRole.ROUTINE, session_key="asset:pump1:qa")
        return r1, r2

    r1, r2 = asyncio.run(two_calls())
    assert r1.error is None       # call 1: passes
    assert r2.error == "simulated outage"  # call 2: every=2 fires
    # a different mode is untouched
    async def learn_call():
        return await gw.run("q", role=ModelRole.ONLINE, session_key="asset:pump1:learn")
    assert asyncio.run(learn_call()).error is None
