"""Read-side API: assets, per-asset latest snapshot, snapshots history, knowledge.

These endpoints surface the deterministic fact spine (snapshots, status, trust,
coverage) as-is. They do NOT call the model.
"""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Depends
from fastapi import Request

from ..analytics.gates import gate_snapshot_for_read
from .deps import AppState
from .schemas import asset_to_dict, fail, metric_to_dict, ok

router = APIRouter(prefix="/api", tags=["assets"])


async def _state(request: Request) -> AppState:
    return request.app.state.edge


def _client_assets(state: AppState) -> list[dict[str, Any]]:
    out = []
    for a in state.config.assets:
        d = asset_to_dict(a)
        # runtime facts glued onto the static profile
        gate = gate_snapshot_for_read(state.store, a, state.config)
        snap = gate.snapshot or {}
        d["runtime"] = {
            "status": gate.state.value,
            "trust": snap.get("trust", "none"),
            "coverage": snap.get("coverage", 0.0),
            "overall_level": snap.get("overall_level", "unknown"),
            "sample_count": snap.get("sample_count", 0),
            "window_end": snap.get("window_end", ""),
            "state_reason": gate.reason or snap.get("state_reason", ""),
        }
        out.append(d)
    return out


@router.get("/assets")
async def list_assets(request: Request) -> Any:
    state: AppState = await _state(request)
    return ok(data=_client_assets(state))


@router.get("/assets/{asset_id}")
async def get_asset(request: Request, asset_id: str) -> Any:
    state: AppState = await _state(request)
    asset = state.config.asset(asset_id)
    if asset is None:
        return fail("unknown asset")
    d = asset_to_dict(asset)
    d["asset_md"] = state.store.read_asset_md(asset_id)
    d["downtime_windows"] = asset.downtime_windows
    return ok(data=d)


@router.get("/assets/{asset_id}/latest-snapshot")
async def latest_snapshot(request: Request, asset_id: str) -> Any:
    state: AppState = await _state(request)
    asset = state.config.asset(asset_id)
    if asset is None:
        return fail("unknown asset")
    gate = gate_snapshot_for_read(state.store, asset, state.config)
    return ok(data={
        "ok": gate.ok,
        "state": gate.state.value,
        "reason": gate.reason,
        "snapshot": gate.snapshot,
    })


@router.get("/assets/{asset_id}/snapshots")
async def snapshots(request: Request, asset_id: str, limit: int = 50) -> Any:
    state: AppState = await _state(request)
    asset = state.config.asset(asset_id)
    if asset is None:
        return fail("unknown asset")
    files = state.store.list_snapshots(asset_id, limit=limit)
    rows = []
    for f in files:
        s = state.store.read_persisted_snapshot(asset_id, f.name)
        if s is None:
            continue
        rows.append({
            "filename": f.name,
            "window_end": s.get("window_end", ""),
            "status": s.get("status", "unknown"),
            "trust": s.get("trust", "none"),
            "coverage": s.get("coverage", 0.0),
            "overall_level": s.get("overall_level", "unknown"),
            "sample_count": s.get("sample_count", 0),
            "headline": s.get("headline", ""),
        })
    return ok(data=rows)


@router.get("/assets/{asset_id}/snapshots/{filename}")
async def snapshot_detail(request: Request, asset_id: str, filename: str) -> Any:
    state: AppState = await _state(request)
    s = state.store.read_persisted_snapshot(asset_id, filename)
    if s is None:
        return fail("snapshot not found", status=404)
    return ok(data=s)


@router.get("/assets/{asset_id}/knowledge")
async def knowledge(request: Request, asset_id: str) -> Any:
    """MEMORY.md as rendered + claims/conflicts ledger for the Knowledge page."""
    state: AppState = await _state(request)
    asset = state.config.asset(asset_id)
    if asset is None:
        return fail("unknown asset")
    mem = state.store.read_memory_md(asset_id)
    claims: list[dict[str, Any]] = []
    conflicts: list[dict[str, Any]] = []
    for cls, path_fn in (("claim", "memory_claims"), ("conflict", "memory_conflicts")):
        p = state.store.asset_dir(asset_id) / f"{path_fn}.jsonl"
        if not p.exists():
            continue
        try:
            for line in p.read_text(encoding="utf-8").splitlines():
                line = line.strip()
                if line:
                    import json
                    rec = json.loads(line)
                    rec.setdefault("cls", cls)
                    (claims if cls == "claim" else conflicts).append(rec)
        except Exception:  # noqa: BLE001
            pass
    return ok(data={
        "memory_md": mem,
        "claims": claims,
        "conflicts": conflicts,
        "learn_gate": state.config.learn_gate,
    })


@router.get("/assets/{asset_id}/metadata")
async def asset_metadata(request: Request, asset_id: str) -> Any:
    """Latest snapshot's MetricResult rows (for the Monitor multi-curve view)."""
    state: AppState = await _state(request)
    asset = state.config.asset(asset_id)
    if asset is None:
        return fail("unknown asset")
    gate = gate_snapshot_for_read(state.store, asset, state.config)
    if not gate.ok:
        return ok(data={"ok": False, "reason": gate.reason, "metrics": []})
    snap = gate.snapshot or {}
    metrics = []
    for m in snap.get("metrics", []):
        spec = state.config.asset(asset_id).metric(m["key"])
        metrics.append({"metric": m, "spec": metric_to_dict(spec) if spec else None})
    return ok(data={"ok": True, "metrics": metrics})