"""Read-side API: cross-asset overview + read-only config view + health."""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Request

from ..analytics.gates import gate_snapshot_for_read
from .deps import AppState
from .schemas import asset_to_dict, ok

router = APIRouter(prefix="/api", tags=["overview"])


async def _state(request: Request) -> AppState:
    return request.app.state.edge


@router.get("/overview")
async def overview(request: Request) -> Any:
    """One bird's-eye snapshot of every asset for the Dashboard card wall.

    Each card carries: status + trust + coverage + worst metric level + latest
    values + alert count + whether a fresh snapshot exists. This is the "①结论 →
    ②事实" top layer of the HMI.
    """
    state: AppState = await _state(request)
    cards: list[dict[str, Any]] = []
    for a in state.config.assets:
        gate = gate_snapshot_for_read(state.store, a, state.config)
        snap = gate.snapshot or {}
        latest_values: dict[str, float | None] = {}
        worst = "info"
        for m in snap.get("metrics", []):
            latest_values[m.get("key", "")] = m.get("max", m.get("mean"))
            lv = m.get("level", "info")
            if lv in ("critical", "warn"):
                worst = lv if lv == "critical" or worst == "info" else worst
        alerts = _unacked_alert_count(state, a.asset_id)
        cards.append({
            "asset_id": a.asset_id,
            "name": a.name,
            "kind": a.kind,
            "config": asset_to_dict(a),
            "runtime": {
                "ok": gate.ok,
                "state": gate.state.value,
                "state_reason": gate.reason or snap.get("state_reason", ""),
                "trust": snap.get("trust", "none"),
                "coverage": snap.get("coverage", 0.0),
                "overall_level": snap.get("overall_level", "unknown"),
                "worst_metric_level": worst,
                "sample_count": snap.get("sample_count", 0),
                "window_end": snap.get("window_end", ""),
                "headline": snap.get("headline", ""),
                "latest_values": latest_values,
                "alerts": alerts,
            },
        })
    return ok(data={"cards": cards, "assets_count": len(cards)})


def _unacked_alert_count(state: AppState, asset_id: str) -> int:
    """Count open alert records for an asset (no ACK file present = all open).

    Alerts are derived and stored under the asset dir as an append-only JSONL
    `alerts.jsonl` (see the write-side alert emitter); entries carry acked flag.
    """
    import json
    p = state.store.asset_dir(asset_id) / "alerts.jsonl"
    if not p.exists():
        return 0
    n = 0
    try:
        for line in p.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if not line:
                continue
            try:
                rec = json.loads(line)
            except json.JSONDecodeError:
                continue
            if not rec.get("acked"):
                n += 1
    except OSError:
        return 0
    return n


@router.get("/config")
async def config_view(request: Request) -> Any:
    """Read-only view of the tuning knobs + provider status for the Settings page."""
    state: AppState = await _state(request)
    c = state.config
    providers = {}
    for role, spec in c.providers.items():
        providers[role] = {
            "role": role,
            "model_preset": spec.model_preset,
            "model": spec.model,
            "api_base": spec.api_base,
            "timeout_s": spec.timeout_s,
            "max_tokens": spec.max_tokens,
        }
    return ok(data={
        "workspace": str(c.workspace),
        "monitor_interval_s": c.monitor_interval_s,
        "deep_report_cron": c.deep_report_cron,
        "learn_cron": c.learn_cron,
        "data_freshness_s": c.data_freshness_s,
        "coverage_threshold": c.coverage_threshold,
        "max_empty_windows": c.max_empty_windows,
        "analysis_regime": c.analysis_regime,
        "learn_gate": c.learn_gate,
        "memory_index": c.memory_index,
        "memory_conflict_policy": c.memory_conflict_policy,
        "memory_decay_s": c.memory_decay_s,
        "runlog_enabled": c.runlog_enabled,
        "hmi_pin_enabled": c.hmi_pin != "none",
        "providers": providers,
    })


@router.get("/version")
async def version(request: Request) -> Any:
    return ok(data={"service": "edge-adviser-hmi"})


@router.get("/health")
async def health(request: Request) -> Any:
    state: AppState = await _state(request)
    snapshots = sum(1 for a in state.config.assets
                    if state.store.list_snapshots(a.asset_id, limit=1))
    return ok(data={
        "status": "ok",
        "assets": len(state.config.assets),
        "assets_with_snapshots": snapshots,
    })