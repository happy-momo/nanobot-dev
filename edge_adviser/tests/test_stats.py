"""Fleet observability endpoint (/api/stats, 0.4.4).

Verifies the three derived views against seeded on-disk state:
  * provider success rate / latency — from audited runlog rows, with gate
    refusals excluded from the success rate;
  * alert MTTA — mean(acked_at - ts) over acked alerts;
  * storage — workspace byte size + retention caps reported per asset.
"""

from __future__ import annotations

import json
import sys
import time
from pathlib import Path

_HERE = Path(__file__).resolve().parent.parent / "edge_adviser"
if str(_HERE) not in sys.path:
    sys.path.insert(0, str(_HERE))

from edge_adviser.core.asset_store import AssetStore  # noqa: E402


def _seed_runlog(store: AssetStore, asset_id: str, rows: list[dict]) -> None:
    p = store.asset_dir(asset_id) / "runlog.jsonl"
    p.parent.mkdir(parents=True, exist_ok=True)
    with open(p, "a", encoding="utf-8") as f:
        for r in rows:
            f.write(json.dumps(r, ensure_ascii=False) + "\n")


def _seed_alerts(store: AssetStore, asset_id: str, rows: list[dict]) -> None:
    p = store.asset_dir(asset_id) / "alerts.jsonl"
    p.parent.mkdir(parents=True, exist_ok=True)
    with open(p, "a", encoding="utf-8") as f:
        for r in rows:
            f.write(json.dumps(r, ensure_ascii=False) + "\n")


def _client(tmp: Path):
    from fastapi.testclient import TestClient
    from edge_adviser.server.app import create_app

    cfg = {
        "workspace": str(tmp / "ws"),
        "assets": [{"asset_id": "pump1", "name": "p", "window_s": 60,
                    "metrics": [{"key": "temp", "unit": "degC"}]}],
        "sources": [{"kind": "simulator", "asset_id": "pump1"}],
    }
    (tmp / "config.json").write_text(json.dumps(cfg), encoding="utf-8")

    class _Stub:
        async def astart(self): pass
        async def aclose(self): pass
        async def run(self, *a, **k):
            from edge_adviser.llm.gateway import LLMResult
            return LLMResult(content="stub")

    return TestClient(create_app(tmp / "config.json", gateway=_Stub()))


def test_stats_provider_success_and_latency(tmp_path: Path) -> None:
    store = AssetStore(tmp_path / "ws")
    now = time.time()
    _seed_runlog(store, "pump1", [
        {"timestamp": "2026-11-23T10:00:00+00:00", "date": "2026-11-23",
         "kind": "qa", "outcome": "ok", "elapsed_s": 1.2, "actor": "hmi"},
        {"timestamp": "2026-11-23T10:01:00+00:00", "date": "2026-11-23",
         "kind": "qa", "outcome": "ok", "elapsed_s": 2.4, "actor": "hmi"},
        {"timestamp": "2026-11-23T10:02:00+00:00", "date": "2026-11-23",
         "kind": "deep_report", "outcome": "provider_failed", "reason": "boom",
         "actor": "hmi"},
        # gate refusal — must NOT count into provider success rate
        {"timestamp": "2026-11-23T10:03:00+00:00", "date": "2026-11-23",
         "kind": "qa", "outcome": "gated", "reason": "no_fresh_snapshot: stale",
         "actor": "hmi"},
    ])
    c = _client(tmp_path)
    d = c.get("/api/stats").json()["data"]
    p = d["providers"]
    assert p["calls"] == 3            # gated row excluded
    assert p["ok"] == 2
    assert p["success_rate"] == round(2 / 3, 3)
    assert p["avg_elapsed_s"] == 1.8  # (1.2 + 2.4) / 2
    assert d["providers"]["gated_refusals"] == 1


def test_stats_mtta(tmp_path: Path) -> None:
    store = AssetStore(tmp_path / "ws")
    t0 = 1000.0
    _seed_alerts(store, "pump1", [
        {"ts": t0, "acked": True, "acked_at": t0 + 60, "subject": "warn"},
        {"ts": t0 + 100, "acked": True, "acked_at": t0 + 260, "subject": "critical"},
        {"ts": t0 + 500, "acked": False, "acked_at": None, "subject": "warn"},
    ])
    c = _client(tmp_path)
    d = c.get("/api/stats").json()["data"]
    # acked gaps: 60s and 160s → MTTA 110; unacked row ignored
    assert d["alerts"]["mtta_s"] == 110.0
    assert d["alerts"]["unacked"] == 1
    a = next(x for x in d["assets"] if x["asset_id"] == "pump1")
    assert a["unacked_alerts"] == 1
    assert a["alert_count"] == 3


def test_stats_storage_and_caps(tmp_path: Path) -> None:
    store = AssetStore(tmp_path / "ws")
    store.save_snapshot("pump1", {"window_end": "2026-11-23T10:00:00+00:00",
                                  "sample_count": 5})
    c = _client(tmp_path)
    d = c.get("/api/stats").json()["data"]
    a = next(x for x in d["assets"] if x["asset_id"] == "pump1")
    assert a["snapshot_count"] == 1
    assert a["snapshot_cap"] > 0 and a["runlog_cap"] > 0
    assert d["storage"]["workspace_bytes"] > 0
    assert d["storage"]["disk_free_bytes"] > 0


def test_stats_empty_workspace(tmp_path: Path) -> None:
    """A cold deployment must return a well-formed skeleton, not an error."""
    c = _client(tmp_path)
    d = c.get("/api/stats").json()
    assert d["ok"] is True
    assert d["data"]["providers"]["calls"] == 0
    assert d["data"]["providers"]["success_rate"] is None
    assert d["data"]["alerts"]["mtta_s"] is None
