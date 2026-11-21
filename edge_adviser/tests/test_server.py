"""HMI server tests — offline, no model, exercising the real FastAPI app with a
stubbed gateway and a temp workspace. The app's lifespan is NOT started by the
TestClient lifespan, so no scheduler loop runs; we drive write-side actions that
only touch deterministic paths or a stub model.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

_HERE = Path(__file__).resolve().parent.parent / "edge_adviser"
if str(_HERE) not in sys.path:
    sys.path.insert(0, str(_HERE))

from edge_adviser.config.model import AdviserConfig, AssetSpec, MetricSpec  # noqa: E402
from edge_adviser.config.loader import asset_from_dict  # noqa: E402
from edge_adviser.server.app import create_app  # noqa: E402


# --------------------------------------------------------------------------- #
#  stub gateway — records calls, never hits the network
# --------------------------------------------------------------------------- #
class StubGateway:
    def __init__(self):
        self.calls = 0

    async def astart(self):
        pass

    async def aclose(self):
        pass

    async def run(self, prompt, *, role=None, session_key=None, blocks=None,
                  parse_json=False, model_preset=None, attributes=None):
        self.calls += 1
        from edge_adviser.llm.gateway import LLMResult
        return LLMResult(content="stub answer", used_provider="stub", degraded=False)


def _write_config(tmp: Path, *, fresh_snapshot: bool, min_samples: int = 30) -> Path:
    """Write a minimal config into tmp and optionally seed a fresh snapshot."""
    cfg = {
        "workspace": str(tmp / "ws"),
        "assets": [{
            "asset_id": "pump1",
            "name": "test pump",
            "window_s": 60,
            "sample_freq_s": 1.0,
            "metrics": [{
                "key": "temp", "unit": "degC", "norm_max": 68,
                "warn_max": 68, "crit_max": 73, "persist": 5,
                "min_samples": min_samples, "description": "bearing temp",
            }],
        }],
        "sources": [{"kind": "simulator", "asset_id": "pump1"}],
        "data_freshness_s": 1800,
    }
    cfg_path = tmp / "config.json"
    cfg_path.write_text(json.dumps(cfg), encoding="utf-8")
    if fresh_snapshot:
        _seed_fresh_snapshot(tmp / "ws", "pump1")
    return cfg_path


def _now_window() -> tuple[str, str]:
    """Now-relative (window_start, window_end) so the freshness gate accepts it."""
    from datetime import datetime, timedelta, timezone
    end = datetime.now(timezone.utc)
    start = end - timedelta(minutes=1)
    fmt = lambda d: d.strftime("%Y-%m-%dT%H:%M:%SZ")
    return fmt(start), fmt(end)


def _seed_fresh_snapshot(ws: Path, asset_id: str) -> None:
    """Seed one healthy, fresh snapshot so read-gates pass (now-relative window)."""
    snap = {
        "asset_id": asset_id,
        "window_start": "2026-01-01T00:00:00Z",
        "window_end": "2026-01-01T00:01:00Z",
        "source": "test",
        "regime": "clean",
        "overall_level": "info",
        "status": "healthy",
        "trust": "full",
        "coverage": 1.0,
        "expected_samples": 60,
        "sample_count": 60,
        "headline": "Nominal",
        "metrics": [{
            "key": "temp", "unit": "degC", "n": 60, "mean": 62.0,
            "min": 60.0, "max": 64.0, "p95": 63.0, "level": "info",
        }],
    }
    start, end = _now_window()
    snap["window_start"], snap["window_end"] = start, end
    d = ws / "assets" / asset_id / "snapshots"
    d.mkdir(parents=True, exist_ok=True)
    safe = snap["window_end"].replace(":", "-").replace("/", "-")
    (d / f"{safe}.json").write_text(json.dumps(snap), encoding="utf-8")


def _make_app(tmp: Path, *, fresh: bool, stub: StubGateway,
              with_source: bool = True) -> TestClient:
    cfg = _write_config(tmp, fresh_snapshot=fresh)
    app = create_app(cfg, gateway=stub)
    if with_source:
        # inject a simulator source so run-now/monitor paths have data to pull
        # (the lifespan that normally builds sources doesn't run under TestClient)
        from edge_adviser.ingest import create_source
        from edge_adviser.config.model import SourceSpec, SourceKind
        spec = SourceSpec(kind=SourceKind.SIMULATOR, asset_id="pump1")
        app.state.edge.supervisor._sources["pump1"] = create_source(spec)
    return TestClient(app)  # no lifespan → scheduler loop does not run


# --------------------------------------------------------------------------- #
#  read side
# --------------------------------------------------------------------------- #
def test_health_and_assets(tmp_path: Path) -> None:
    stub = StubGateway()
    c = _make_app(tmp_path, fresh=False, stub=stub)
    r = c.get("/api/health")
    assert r.status_code == 200
    data = r.json()["data"]
    # health now reports scheduler liveness honestly: the web loop has not
    # ticked yet under TestClient, so status must NOT claim ok
    assert data["status"] == "degraded"
    assert data["scheduler"]["running"] is False
    assert "gateway" in data

    r = c.get("/api/assets")
    assert r.status_code == 200
    cards = r.json()["data"]
    assert len(cards) == 1
    assert cards[0]["asset_id"] == "pump1"
    assert cards[0]["metrics"][0]["crit_max"] == 73
    assert cards[0]["runtime"]["status"] in ("unknown", "insufficient")


def test_overview(tmp_path: Path) -> None:
    stub = StubGateway()
    c = _make_app(tmp_path, fresh=True, stub=stub)
    r = c.get("/api/overview")
    assert r.status_code == 200
    cards = r.json()["data"]["cards"]
    assert len(cards) == 1
    rt = cards[0]["runtime"]
    assert rt["state"] in ("healthy", "stale")  # seeded snapshot present
    assert "coverage" in rt


def test_latest_snapshot_read(tmp_path: Path) -> None:
    stub = StubGateway()
    c = _make_app(tmp_path, fresh=True, stub=stub)
    r = c.get("/api/assets/pump1/latest-snapshot")
    assert r.status_code == 200
    body = r.json()["data"]
    assert body["ok"] is True
    assert body["snapshot"]["status"] == "healthy"


# --------------------------------------------------------------------------- #
#  gate: no fresh snapshot → ask refuses to drive the model
# --------------------------------------------------------------------------- #
def test_ask_refuses_without_fresh_snapshot(tmp_path: Path) -> None:
    stub = StubGateway()
    c = _make_app(tmp_path, fresh=False, stub=stub)
    r = c.post("/api/assets/pump1/ask", json={"question": "状态如何?"})
    body = r.json()
    # refusal is a 200 envelope with ok=false and no model call
    assert body["ok"] is False
    assert "no_fresh_snapshot" in (body["error"] or "")
    assert stub.calls == 0


def test_ask_allowed_with_fresh_snapshot(tmp_path: Path) -> None:
    stub = StubGateway()
    c = _make_app(tmp_path, fresh=True, stub=stub)
    r = c.post("/api/assets/pump1/ask",
               json={"question": "状态如何?", "allow_stale": True})
    body = r.json()
    # seeded snapshot is "fresh" relative to test clock? window_end is 2026-01-01
    # which is stale vs now() — so allow_stale must be set to pass the gate.
    assert body["ok"] is True
    assert "stub answer" in body["data"]["content"]
    assert stub.calls == 1


# --------------------------------------------------------------------------- #
#  report / runlog / feedback
# --------------------------------------------------------------------------- #
def test_reports_listing_empty(tmp_path: Path) -> None:
    stub = StubGateway()
    c = _make_app(tmp_path, fresh=False, stub=stub)
    r = c.get("/api/assets/pump1/reports")
    assert r.status_code == 200
    assert r.json()["data"] == []


def test_runlog_roundtrip(tmp_path: Path) -> None:
    stub = StubGateway()
    c = _make_app(tmp_path, fresh=False, stub=stub)
    # a run-now against simulator source succeeds and writes a runlog entry
    r = c.post("/api/assets/pump1/run-now")
    assert r.status_code == 200
    assert r.json()["ok"] is True
    rl = c.get("/api/assets/pump1/runlog", params={"limit": 10})
    rows = rl.json()["data"]["rows"]
    assert any(x.get("kind") == "run_once" for x in rows)
    # manual runs are audited as actor=hmi (distinct from scheduler ticks)
    assert any(x.get("actor") == "hmi" for x in rows)


def test_feedback_roundtrip(tmp_path: Path) -> None:
    stub = StubGateway()
    c = _make_app(tmp_path, fresh=False, stub=stub)
    r = c.post("/api/assets/pump1/feedback", json={"note": "coupling loose yesterday"})
    assert r.status_code == 200 and r.json()["ok"] is True
    out = c.get("/api/assets/pump1/feedback").json()["data"]
    assert out and "coupling" in out[0]["note"]


def test_knowledge_view(tmp_path: Path) -> None:
    stub = StubGateway()
    c = _make_app(tmp_path, fresh=False, stub=stub)
    r = c.get("/api/assets/pump1/knowledge")
    assert r.status_code == 200
    assert "memory_md" in r.json()["data"]