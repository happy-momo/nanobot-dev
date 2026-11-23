"""Fleet-level observability endpoint.

Answers the three questions field engineers actually ask about a deployed
adviser:
  * is the model layer doing its job — provider call counts, success rate,
    latency (from the audit runlog, so no extra state to persist);
  * how fast do we respond to trouble — alert→ack MTTA per asset;
  * is the disk story under control — workspace size + retention headroom.

All figures are derived from files already on disk (runlog, alerts, snapshots);
the endpoint is read-only and cheap enough to poll from the Settings page.
"""

from __future__ import annotations

import json
import time
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from fastapi import APIRouter, Request

from .deps import AppState
from .schemas import ok

router = APIRouter(prefix="/api", tags=["stats"])


async def _state(request: Request) -> AppState:
    return request.app.state.edge


def _read_jsonl(path: Path, limit_rows: int = 0) -> list[dict[str, Any]]:
    if not path.exists():
        return []
    out: list[dict[str, Any]] = []
    try:
        for line in path.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if not line:
                continue
            try:
                out.append(json.loads(line))
            except json.JSONDecodeError:
                continue
            if limit_rows and len(out) >= limit_rows:
                break
    except OSError:
        return []
    return out


def _dir_size(path: Path) -> int:
    total = 0
    try:
        for p in path.rglob("*"):
            if p.is_file():
                total += p.stat().st_size
    except OSError:
        pass
    return total


def _provider_stats(rows: list[dict[str, Any]]) -> dict[str, Any]:
    """Aggregate provider success/latency from audited runlog rows.

    Kinds audited with a provider reason: qa, deep_report, learn, monitor.
    'gated'/'no_data' outcomes are excluded from the provider success rate —
    they mean the deterministic gate refused, not that a provider was called.
    """
    called = [r for r in rows
              if r.get("kind") in ("qa", "deep_report", "learn", "monitor")
              and r.get("outcome") in ("ok", "provider_failed")]
    ok_rows = [r for r in called if r.get("outcome") == "ok"]
    latencies = [r["elapsed_s"] for r in ok_rows
                 if isinstance(r.get("elapsed_s"), (int, float))]
    gated = [r for r in rows
             if r.get("kind") in ("qa", "deep_report", "learn", "monitor")
             and r.get("outcome") in ("gated", "no_data")]
    return {
        "calls": len(called),
        "ok": len(ok_rows),
        "success_rate": round(len(ok_rows) / len(called), 3) if called else None,
        "avg_elapsed_s": round(sum(latencies) / len(latencies), 3) if latencies else None,
        "max_elapsed_s": round(max(latencies), 3) if latencies else None,
        "gated_refusals": len(gated),
    }


def _mtta_s(rows: list[dict[str, Any]]) -> float | None:
    """Mean time-to-acknowledge over acked alerts (seconds).

    Each acked alert carries ts (fired) and acked_at (handled) — the gap is the
    responsiveness of the operation, the number a plant manager asks about.
    """
    gaps = [r["acked_at"] - r["ts"] for r in rows
            if r.get("acked") and isinstance(r.get("ts"), (int, float))
            and isinstance(r.get("acked_at"), (int, float))
            and r["acked_at"] >= r["ts"]]
    return round(sum(gaps) / len(gaps), 1) if gaps else None


@router.get("/stats")
async def fleet_stats(request: Request) -> Any:
    state: AppState = await _state(request)
    started = time.time()

    assets_out: list[dict[str, Any]] = []
    total_bytes = 0
    per_kind: Counter[str] = Counter()
    provider_rows: list[dict[str, Any]] = []
    all_alerts: list[dict[str, Any]] = []

    for asset in state.config.assets:
        adir = state.store.asset_dir(asset.asset_id)
        size = _dir_size(adir)
        total_bytes += size
        snap_count = len(state.store.list_snapshots(asset.asset_id, limit=100000))
        runlog = _read_jsonl(adir / "runlog.jsonl")
        alerts = _read_jsonl(adir / "alerts.jsonl")
        all_alerts.extend(alerts)
        for r in runlog:
            per_kind[(r.get("kind") or "?") + ":" + (r.get("outcome") or "?")] += 1
        provider_rows.extend(runlog)
        assets_out.append({
            "asset_id": asset.asset_id,
            "snapshot_count": snap_count,
            "snapshot_cap": state.config.max_snapshots,
            "runlog_rows": len(runlog),
            "runlog_cap": state.config.max_runlog_rows,
            "alert_count": len(alerts),
            "unacked_alerts": sum(1 for a in alerts if not a.get("acked")),
            "mtta_s": _mtta_s(alerts),
            "disk_bytes": size,
        })

    import shutil
    usage = shutil.disk_usage(state.config.workspace)

    return ok(data={
        "generated_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "assets": assets_out,
        "providers": _provider_stats(provider_rows),
        "alerts": {
            "total": len(all_alerts),
            "unacked": sum(1 for a in all_alerts if not a.get("acked")),
            "mtta_s": _mtta_s(all_alerts),
        },
        "activity": dict(per_kind.most_common(20)),
        "storage": {
            "workspace_bytes": total_bytes,
            "workspace_mb": round(total_bytes / (1024 * 1024), 2),
            "disk_free_bytes": usage.free,
            "disk_total_bytes": usage.total,
        },
        "elapsed_s": round(time.time() - started, 3),
    })
