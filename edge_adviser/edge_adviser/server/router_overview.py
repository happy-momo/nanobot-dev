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
async def overview(request: Request, history: int = 0) -> Any:
    """One bird's-eye snapshot of every asset for the Dashboard card wall.

    Each card carries: status + trust + coverage + worst metric level + latest
    values + alert count + whether a fresh snapshot exists. This is the "①结论 →
    ②事实" top layer of the HMI.

    `?history=N` embeds a sparkline series (first metric, last N snapshots) in
    each card's runtime so the Dashboard can draw trend thumbnails in one request
    instead of N+1 snapshot fetches.
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
        runtime: dict[str, Any] = {
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
        }
        if history > 0:
            runtime["series"] = _sparkline_series(state, a.asset_id, history)
        cards.append({
            "asset_id": a.asset_id,
            "name": a.name,
            "kind": a.kind,
            "config": asset_to_dict(a),
            "runtime": runtime,
        })
    return ok(data={"cards": cards, "assets_count": len(cards)})


def _sparkline_series(state: AppState, asset_id: str, n: int) -> list[dict[str, Any]]:
    """Trend points for the card's first metric: oldest → newest [{window_end, value, level}]."""
    first_key = next((m.key for m in state.config.asset(asset_id).metrics if m), "")
    out: list[dict[str, Any]] = []
    if not first_key:
        return out
    for f in state.store.list_snapshots(asset_id, limit=n):
        s = state.store.read_persisted_snapshot(asset_id, f.name)
        if not s:
            continue
        m = next((mm for mm in s.get("metrics", []) if mm.get("key") == first_key), None)
        if m is None:
            continue
        out.append({
            "window_end": s.get("window_end", ""),
            "value": m.get("mean", m.get("max")),
            "level": m.get("level", "info"),
        })
    out.reverse()  # persisted filenames are newest-first; charts want oldest-first
    return out


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
        "demo": c.demo,
        "providers": providers,
    })


@router.get("/version")
async def version(request: Request) -> Any:
    from importlib.metadata import PackageNotFoundError, version as _pkg_version
    try:
        ver = _pkg_version("edge-adviser")
    except PackageNotFoundError:
        from .. import __version__ as ver  # fallback: repo checkout
    state: AppState = await _state(request)
    return ok(data={
        "service": "edge-adviser-hmi",
        "version": ver,
        "demo": state.config.demo,
        "pin_enabled": state.config.hmi_pin != "none",
        "assets": len(state.config.assets),
        "monitor_interval_s": state.config.monitor_interval_s,
    })


@router.get("/health")
async def health(request: Request) -> Any:
    """Liveness that means something: scheduler heartbeat age + per-asset data.

    ``status`` is "ok" only while the scheduler loop is alive (tick age within
    2× the monitor interval); "degraded" otherwise — a watchdog probing this
    endpoint can now detect a wedged scheduler instead of a green lie.
    """
    state: AppState = await _state(request)
    snapshots = sum(1 for a in state.config.assets
                    if state.store.list_snapshots(a.asset_id, limit=1))
    tick_age = state.tick_age_s()
    max_age = max(state.config.monitor_interval_s * 2, 30)
    scheduler_ok = tick_age <= max_age
    return ok(data={
        "status": "ok" if scheduler_ok else "degraded",
        "scheduler": {
            "running": scheduler_ok,
            "last_tick_age_s": round(tick_age, 1) if tick_age != float("inf") else None,
        },
        "gateway": type(state.gateway).__name__,
        "assets": len(state.config.assets),
        "assets_with_snapshots": snapshots,
    })