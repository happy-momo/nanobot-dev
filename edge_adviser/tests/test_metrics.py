"""/metrics — Prometheus text exposition (no new dependency).

The gauges are aggregates over runlog/snapshots that /api/stats already
computes; the format is hand-rolled 0.0.4 text so the edge image does not
carry prometheus-client. The endpoint is PIN-protected like every other
surface; scrapers use the existing ?token= GET allowance (EventSource
precedent).
"""
from __future__ import annotations

import asyncio
import json
import sys
from pathlib import Path

_HERE = Path(__file__).resolve().parent.parent / "edge_adviser"
if str(_HERE) not in sys.path:
    sys.path.insert(0, str(_HERE))

from edge_adviser.server.router_metrics import render_metrics  # noqa: E402


def test_render_metrics_shape() -> None:
    text = render_metrics(
        assets=2,
        snapshots={"pump1": 12, "feeder1": 3},
        provider_calls=10, provider_ok=9, gated=1,
        unacked=2, tick_age=12.5, sse_active_n=3, pin_failures=0)
    assert "# TYPE edge_adviser_assets gauge" in text
    assert "edge_adviser_assets 2" in text
    assert 'edge_adviser_snapshots_total{asset_id="pump1"} 12' in text
    assert 'edge_adviser_snapshots_total{asset_id="feeder1"} 3' in text
    assert "edge_adviser_provider_calls_total 10" in text
    assert "edge_adviser_provider_ok_total 9" in text
    assert "edge_adviser_gated_refusals_total 1" in text
    assert "edge_adviser_alerts_unacked 2" in text
    assert "edge_adviser_scheduler_last_tick_age_seconds 12.5" in text
    assert "edge_adviser_sse_streams_active 3" in text
    assert "edge_adviser_pin_failed_attempts_total 0" in text


def test_tick_age_none_reports_minus_one() -> None:
    """No scheduler tick yet — a scrape must not crash, and -1 is the
    Prometheus convention for 'not applicable'."""
    text = render_metrics(
        assets=1, snapshots={}, provider_calls=0, provider_ok=0, gated=0,
        unacked=0, tick_age=None, sse_active_n=0, pin_failures=0)
    assert "edge_adviser_scheduler_last_tick_age_seconds -1" in text


def test_watcher_count_tracks_subscribers() -> None:
    from edge_adviser.core.bus import EventBus

    async def _main() -> tuple[int, int]:
        bus = EventBus(replay=1)

        async def sink1(ev): pass

        async def sink2(ev): pass

        await bus.subscribe({"snapshot"}, set(), on_event=sink1)
        await bus.subscribe({"alert"}, set(), on_event=sink2)
        opened = bus.watcher_count()
        await bus.unsubscribe(sink1)
        return opened, bus.watcher_count()

    opened, after = asyncio.run(_main())
    assert opened == 2 and after == 1


class _Stub:
    async def astart(self): pass
    async def aclose(self): pass
    async def run(self, prompt, **kw): return {}


def test_metrics_endpoint_requires_pin(tmp_path: Path) -> None:
    from fastapi.testclient import TestClient
    from edge_adviser.server.app import create_app

    cfg = {"workspace": str(tmp_path / "ws"), "hmi_pin": "1357",
           "assets": [{"asset_id": "pump1", "name": "p", "window_s": 60,
                       "sample_freq_s": 1.0,
                       "metrics": [{"key": "temp", "unit": "degC",
                                    "norm_max": 68, "persist": 5}]}],
           "sources": [{"kind": "simulator", "asset_id": "pump1"}]}
    p = tmp_path / "config.json"
    p.write_text(json.dumps(cfg), encoding="utf-8")
    client = TestClient(create_app(p, gateway=_Stub()))
    r = client.post("/api/auth/pin", json={"pin": "1357"})
    token = r.json()["data"]["token"]
    assert client.get("/api/metrics").status_code == 401
    ok = client.get("/api/metrics", params={"token": token})
    assert ok.status_code == 200
    assert "edge_adviser_assets" in ok.text
    assert "text/plain" in ok.headers["content-type"]
