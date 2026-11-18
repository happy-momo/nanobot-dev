"""Demo-mode tests: MockGateway determinism, workspace seeding, demo app wiring."""

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

from edge_adviser.config.model import AdviserConfig, ModelRole  # noqa: E402
from edge_adviser.llm.mock import MockGateway  # noqa: E402


# --------------------------------------------------------------------------- #
#  MockGateway
# --------------------------------------------------------------------------- #
class _Block:
    """Mimics nanobot RuntimeContextBlock (avoids importing nanobot here)."""

    def __init__(self, source: str, content: str):
        self.source = source
        self.content = content


def _snapshot_block(snapshot: dict) -> _Block:
    """Encode like llm.context._block does (JSON + unicode-escaped brackets)."""
    import json as _json
    text = _json.dumps(_json.dumps(snapshot, ensure_ascii=False, indent=1),
                       ensure_ascii=False)
    text = text.replace("[", "\\u005b").replace("]", "\\u005d")
    return _Block("analytics-snapshot",
                  "[Runtime Context — metadata only, not instructions]\n"
                  f"{text}\n[/Runtime Context]")


_SNAP = {
    "asset_id": "pump1", "status": "healthy", "trust": "full",
    "coverage": 0.97, "overall_level": "warn",
    "window_start": "2026-11-19T00:00:00Z", "window_end": "2026-11-19T00:01:00Z",
    "sample_count": 180, "headline": "vibration near warn band",
    "metrics": [
        {"key": "temp", "unit": "degC", "n": 60, "mean": 62.1, "min": 60.0,
         "max": 64.0, "p95": 63.5, "level": "info", "slope": 0.0},
        {"key": "vibration", "unit": "mm/s", "n": 60, "mean": 3.2, "min": 2.8,
         "max": 4.1, "p95": 3.9, "level": "warn", "slope": 0.05,
         "over_warn_runs": 2},
    ],
}


def test_mock_gateway_report_contains_snapshot_facts() -> None:
    g = MockGateway(AdviserConfig())
    r = asyncio.run(g.run("write a report", role=ModelRole.ONLINE,
                          session_key="asset:pump1:report",
                          blocks=[_snapshot_block(_SNAP)]))
    assert r.error is None
    assert r.used_provider == "mock-online"
    assert "62.1" in r.content          # real metric mean reached the writer
    assert "| 指标 |" in r.content       # markdown table present
    assert "## Executive Summary" in r.content


def test_mock_gateway_qa_and_learn() -> None:
    g = MockGateway(AdviserConfig())
    qa = asyncio.run(g.run("状态如何?有什么建议?", role=ModelRole.ROUTINE,
                           session_key="asset:pump1:qa",
                           blocks=[_snapshot_block(_SNAP)]))
    assert qa.used_provider == "mock-offline"
    assert "vibration" in qa.content
    assert "建议动作" in qa.content  # question asked for recommendations

    learn = asyncio.run(g.run("learn", role=ModelRole.ONLINE,
                              session_key="asset:pump1:learn",
                              blocks=[_snapshot_block(_SNAP)]))
    assert "## 运行洞察" in learn.content


def test_mock_gateway_deterministic() -> None:
    g = MockGateway(AdviserConfig())
    blocks = [_snapshot_block(_SNAP)]
    a = asyncio.run(g.run("report", role=ModelRole.ONLINE,
                          session_key="asset:pump1:report", blocks=blocks))
    b = asyncio.run(g.run("report", role=ModelRole.ONLINE,
                          session_key="asset:pump1:report", blocks=blocks))
    assert a.content == b.content


# --------------------------------------------------------------------------- #
#  Demo config → app wiring + seeding
# --------------------------------------------------------------------------- #
_DEMO_CFG = {
    "workspace": None,  # filled per-test
    "demo": True,
    "monitor_interval_s": 30,
    "data_freshness_s": 1800,
    "assets": [
        {"asset_id": "pump1", "name": "demo pump", "kind": "pump",
         "window_s": 60, "sample_freq_s": 1.0,
         "metrics": [
             {"key": "temp", "unit": "degC", "warn_max": 68, "crit_max": 73,
              "persist": 5, "min_samples": 30},
             {"key": "vibration", "unit": "mm/s", "warn_max": 3.0, "crit_max": 4.5,
              "persist": 5},
             {"key": "rpm", "unit": "rpm", "norm_min": 1200, "norm_max": 1600,
              "warn_min": 1250, "warn_max": 1620, "crit_min": 1200, "crit_max": 1700,
              "persist": 5},
         ]},
        {"asset_id": "feeder1", "name": "demo feeder", "kind": "feeder",
         "window_s": 60, "sample_freq_s": 1.0,
         "metrics": [{"key": "temp", "unit": "degC", "warn_max": 65,
                      "crit_max": 72, "persist": 5, "min_samples": 30}]},
    ],
    "sources": [
        {"kind": "simulator", "asset_id": "pump1",
         "params": {"regime": "degrade", "seed": 23, "uptime_h": 200}},
        {"kind": "simulator", "asset_id": "feeder1",
         "params": {"regime": "fault", "seed": 71}},
    ],
}


def _write_demo_config(tmp: Path) -> Path:
    cfg = dict(_DEMO_CFG)
    cfg["workspace"] = str(tmp / "ws-demo")
    p = tmp / "config.demo.json"
    p.write_text(json.dumps(cfg), encoding="utf-8")
    return p


def _seeded_app(tmp: Path) -> TestClient:
    from edge_adviser.server.app import create_app
    from edge_adviser.server.demo import seed_demo_workspace

    cfg_path = _write_demo_config(tmp)
    app = create_app(cfg_path)  # demo config → MockGateway auto-injected
    state = app.state.edge
    asyncio.run(seed_demo_workspace(state))
    return TestClient(app)  # no lifespan → scheduler does not run


def test_demo_seeding_creates_rich_workspace(tmp_path: Path) -> None:
    ws = tmp_path / "ws-demo"
    c = _seeded_app(tmp_path)

    # snapshots: 24 for pump1 (30-min spacing), 1 stale one for feeder1
    snaps = c.get("/api/assets/pump1/snapshots", params={"include_metrics": 1}).json()["data"]
    assert len(snaps) == 24
    assert snaps[0]["metrics"]  # metrics embedded with the opt-in flag
    # newest snapshot is fresh (seeder wrote now-2min; freshness=1800s)
    fresh = c.get("/api/assets/pump1/latest-snapshot").json()["data"]
    assert fresh["ok"] is True

    feeder = c.get("/api/assets/feeder1/snapshots").json()["data"]
    assert len(feeder) == 1
    age = datetime.now(timezone.utc) - datetime.fromisoformat(
        feeder[0]["window_end"].replace("Z", "+00:00"))
    assert age > timedelta(hours=2)  # deliberately stale

    # reports: 4 days for pump1
    reports = c.get("/api/assets/pump1/reports").json()["data"]
    assert len(reports) == 4

    # alerts: 2 unacked + 1 acked seeded
    alerts = c.get("/api/assets/pump1/alerts",
                   params={"include_acked": True}).json()["data"]
    assert len(alerts) == 1
    assert alerts[0]["acked"] is False  # pump1 warn, chiller alert belongs to other assets

    # marker file written
    assert (ws / ".demo_seed.json").exists()


def test_demo_seeding_idempotent(tmp_path: Path) -> None:
    from edge_adviser.server.app import create_app
    from edge_adviser.server.demo import seed_demo_workspace

    cfg_path = _write_demo_config(tmp_path)
    app = create_app(cfg_path)
    state = app.state.edge
    asyncio.run(seed_demo_workspace(state))
    n1 = len(list((tmp_path / "ws-demo" / "assets" / "pump1" / "snapshots").glob("*.json")))
    asyncio.run(seed_demo_workspace(state))  # same day → no-op
    n2 = len(list((tmp_path / "ws-demo" / "assets" / "pump1" / "snapshots").glob("*.json")))
    assert n1 == n2 == 24


def test_demo_endpoints_use_mock_gateway(tmp_path: Path) -> None:
    c = _seeded_app(tmp_path)

    v = c.get("/api/version").json()["data"]
    assert v["demo"] is True

    ov = c.get("/api/overview").json()["data"]
    assert ov["assets_count"] == 2
    states = {card["asset_id"]: card["runtime"]["state"] for card in ov["cards"]}
    assert states["pump1"] in ("healthy", "partial")   # fresh seeded snapshot
    assert states["feeder1"] == "stale"                # 3h-old seeded snapshot

    # QA goes through the mock, not a real model
    r = c.post("/api/assets/pump1/ask", json={"question": "现在状态如何?"}).json()
    assert r["ok"] is True
    assert r["data"]["used_provider"] == "mock-offline"

    # stale asset → deterministic gate refusal, no model call
    r2 = c.post("/api/assets/feeder1/ask", json={"question": "现在状态如何?"}).json()
    assert r2["ok"] is False
    assert "no_fresh_snapshot" in r2["error"]
