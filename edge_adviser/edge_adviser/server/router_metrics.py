"""/metrics — Prometheus text exposition for plant monitoring stacks.

Hand-rolled metrics exposition 0.0.4 rather than prometheus-client: the gauges
here are aggregates over runlog/snapshots that /api/stats already computes, and
a client library would drag a dependency into an edge image that does not need
it. The endpoint mounts at /api/metrics so the shared PIN gate covers it
(wrapped separately it would need its own auth path); prometheus scrapes set
`metrics_path: /api/metrics` and use the ?token= GET allowance (the
EventSource precedent) when PIN mode is on.
"""

from __future__ import annotations

import json
import time
from typing import Any

from fastapi import APIRouter, Request
from fastapi.responses import PlainTextResponse

from .deps import AppState

router = APIRouter(prefix="/api", tags=["metrics"])


def render_metrics(*, assets: int, snapshots: dict[str, int],
                   provider_calls: int, provider_ok: int, gated: int,
                   unacked: int, tick_age: float | None,
                   sse_active_n: int, pin_failures: int) -> str:
    lines: list[str] = []

    def gauge(name: str, help_: str, val: Any) -> None:
        lines.append(f"# HELP {name} {help_}")
        lines.append(f"# TYPE {name} gauge")
        lines.append(f"{name} {val}")

    def counter(name: str, help_: str, val: Any) -> None:
        lines.append(f"# HELP {name} {help_}")
        lines.append(f"# TYPE {name} counter")
        lines.append(f"{name} {val}")

    gauge("edge_adviser_assets", "enabled assets", assets)
    # per-asset snapshot archive depth (a gauge: restarts reset nothing, but
    # the cap prunes — it is a level, not an append-only total)
    lines.append("# HELP edge_adviser_snapshots_total archived snapshots per asset")
    lines.append("# TYPE edge_adviser_snapshots_total gauge")
    for aid, n in sorted(snapshots.items()):
        lines.append(f'edge_adviser_snapshots_total{{asset_id="{aid}"}} {n}')
    counter("edge_adviser_provider_calls_total", "LLM provider calls audited", provider_calls)
    counter("edge_adviser_provider_ok_total", "LLM provider calls that returned ok", provider_ok)
    counter("edge_adviser_gated_refusals_total", "deterministic gate refusals (no data → no model)", gated)
    gauge("edge_adviser_alerts_unacked", "alerts awaiting acknowledgement", unacked)
    # -1 = no tick yet (Prometheus convention for 'not applicable'); otherwise
    # a healthy tick is < monitor_interval_s and a dead loop grows unbounded
    gauge("edge_adviser_scheduler_last_tick_age_seconds", "age of last scheduler tick",
          -1 if tick_age is None else round(tick_age, 1))
    gauge("edge_adviser_sse_streams_active", "open SSE streams (bus subscriptions)", sse_active_n)
    counter("edge_adviser_pin_failed_attempts_total", "wrong PIN exchanges since process start", pin_failures)
    return "\n".join(lines) + "\n"


async def _state(request: Request) -> AppState:
    return request.app.state.edge


@router.get("/metrics")  # → /api/metrics (prefix above)
async def metrics(request: Request) -> Any:
    state: AppState = await _state(request)
    from .router_stats import _provider_stats  # same package; the aggregate of record

    snap_counts: dict[str, int] = {}
    provider_rows: list[dict[str, Any]] = []
    unacked = 0
    for asset in state.config.assets:
        adir = state.store.asset_dir(asset.asset_id)
        snap_counts[asset.asset_id] = len(
            state.store.list_snapshots(asset.asset_id, limit=100000))
        for name, use in (("runlog.jsonl", True), ("alerts.jsonl", False)):
            path = adir / name
            if not path.exists():
                continue
            try:
                rows = [json.loads(l) for l in path.read_text(encoding="utf-8").splitlines()
                        if l.strip()]
            except Exception:  # noqa: BLE001 — a corrupt ledger must not kill the scrape
                continue
            if use:
                provider_rows.extend(rows)
            else:
                unacked += sum(1 for r in rows if not r.get("acked"))
    prov = _provider_stats(provider_rows)
    tick_age = (time.time() - state.last_tick) if state.last_tick else None
    limiter = getattr(state, "pin_limiter", None)
    body = render_metrics(
        assets=len([a for a in state.config.assets if a.enabled]),
        snapshots=snap_counts,
        provider_calls=prov["calls"], provider_ok=prov["ok"],
        gated=prov["gated_refusals"],
        unacked=unacked, tick_age=tick_age,
        sse_active_n=state.bus.watcher_count(),
        pin_failures=getattr(limiter, "total_failures", 0) if limiter else 0)
    return PlainTextResponse(body, media_type="text/plain; version=0.0.4; charset=utf-8")
