"""Read-side API: assets, per-asset latest snapshot, snapshots history, knowledge.

These endpoints surface the deterministic fact spine (snapshots, status, trust,
coverage) as-is. They do NOT call the model.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from fastapi import APIRouter, Depends
from fastapi import Request
from pydantic import BaseModel

from ..analytics.gates import gate_snapshot_for_read
from ..core.ledger_migrate import normalize_records
from .deps import AppState
from .schemas import asset_to_dict, fail, metric_to_dict, ok, source_to_dict

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
        source = next((s for s in state.config.sources if s.asset_id == a.asset_id), None)
        d["source_kind"] = source.kind.value if source else None
        d["credential_configured"] = bool(source and source.credential_id)
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
    src = next((s for s in state.config.sources if s.asset_id == asset_id), None)
    d["source_kind"] = src.kind.value if src else None
    d["source"] = ({
        **source_to_dict(src, redact_secrets=True, include_credential_id=False),
        "credential_configured": bool(src.credential_id),
    } if src else None)
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
async def snapshots(request: Request, asset_id: str, limit: int = 50,
                    include_metrics: int = 0) -> Any:
    state: AppState = await _state(request)
    asset = state.config.asset(asset_id)
    if asset is None:
        return fail("unknown asset")
    files = state.store.list_snapshots(asset_id, limit=limit)
    # `list_snapshots` is newest-first (for "latest snapshot" reads); charts want
    # oldest-first, matching the overview sparkline convention, so reverse here.
    files = list(reversed(files))
    rows = []
    for f in files:
        s = state.store.read_persisted_snapshot(asset_id, f.name)
        if s is None:
            continue
        row = {
            "filename": f.name,
            "window_end": s.get("window_end", ""),
            "status": s.get("status", "unknown"),
            "trust": s.get("trust", "none"),
            "coverage": s.get("coverage", 0.0),
            "overall_level": s.get("overall_level", "unknown"),
            "sample_count": s.get("sample_count", 0),
            "headline": s.get("headline", ""),
        }
        # opt-in: Monitor needs per-metric series to draw history curves; the
        # default payload stays lean for list views.
        if include_metrics:
            row["metrics"] = s.get("metrics", [])
        rows.append(row)
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
                    # legacy v1 rows (demo seed pre-0.6.0) are normalized at
                    # the boundary so the HMI reads one spelling
                    rec = normalize_records([rec], cls=cls)[0]
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


# --------------------------------------------------------------------------- #
# threshold editing — the highest-frequency field change, previously requiring
# SSH + JSON edit + restart. Writes are validated, atomic, and audited.
# --------------------------------------------------------------------------- #
class ThresholdIn(BaseModel):
    warn_min: float | None = None
    warn_max: float | None = None
    crit_min: float | None = None
    crit_max: float | None = None


def _find_config_file(state: AppState) -> Path | None:
    """Locate the JSON config this state was built from (defaults._source_file)."""
    src = state.config.defaults.get("_source_file")
    if src and Path(src).exists():
        return Path(src)
    return None


_CONFIG_BACKUP_KEEP = 5


def _backup_config(cfg_path: Path) -> Path | None:
    """Copy the config aside before an HMI write mutates it.

    The threshold PATCH is the one endpoint that writes the live config file;
    a bad band edited at midnight must be one `cp` away from recovery. Keeps
    the newest 5 timestamped copies next to the config (same directory, no
    hidden dotfiles — an operator greps for *.bak- without docs).
    """
    import shutil
    import uuid as _uuid
    from datetime import datetime
    if not cfg_path.is_file():
        return None
    # second-granularity stamp + short uuid: two edits in the same second must
    # not overwrite each other's recovery point
    stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
    bak = cfg_path.with_name(f"{cfg_path.name}.bak-{stamp}-{_uuid.uuid4().hex[:6]}")
    try:
        shutil.copy2(cfg_path, bak)
    except OSError:
        return None
    # prune to the newest _CONFIG_BACKUP_KEEP copies (name sorts by timestamp)
    baks = sorted(cfg_path.parent.glob(f"{cfg_path.name}.bak-*"))
    for old in baks[:-_CONFIG_BACKUP_KEEP]:
        try:
            old.unlink()
        except OSError:
            pass
    return bak


@router.patch("/assets/{asset_id}/metrics/{key}/thresholds")
async def patch_thresholds(request: Request, asset_id: str, key: str,
                           body: ThresholdIn) -> Any:
    """Update one metric's alarm bands; persists to the config JSON atomically.

    Validation: values must be finite, warn band inside crit band, and both
    sides of each band ordered — a bad band would silently disable alarming or
    flip the chart zones. The edit lands in runlog (what changed, from → to) so
    a midnight threshold tweak is attributable.
    """
    import math
    import json as _json
    import time as _time
    state: AppState = await _state(request)
    asset = state.config.asset(asset_id)
    if asset is None:
        return fail("unknown asset")
    metric = asset.metric(key)
    if metric is None:
        return fail(f"unknown metric: {key}")

    vals = {"warn_min": body.warn_min, "warn_max": body.warn_max,
            "crit_min": body.crit_min, "crit_max": body.crit_max}
    for name, v in vals.items():
        if v is not None and not math.isfinite(v):
            return fail(f"{name} must be a finite number")

    # validate ordering against the merged (new) band set
    merged = {
        "warn_min": vals["warn_min"] if vals["warn_min"] is not None else metric.warn_min,
        "warn_max": vals["warn_max"] if vals["warn_max"] is not None else metric.warn_max,
        "crit_min": vals["crit_min"] if vals["crit_min"] is not None else metric.crit_min,
        "crit_max": vals["crit_max"] if vals["crit_max"] is not None else metric.crit_max,
    }
    if merged["warn_min"] is not None and merged["warn_max"] is not None \
            and merged["warn_min"] > merged["warn_max"]:
        return fail("warn_min must be <= warn_max")
    if merged["crit_min"] is not None and merged["crit_max"] is not None \
            and merged["crit_min"] > merged["crit_max"]:
        return fail("crit_min must be <= crit_max")
    # warn/crit overlap: the normal layout is crit ABOVE warn (high-side trip),
    # but a low-side trip (crit band below warn, e.g. pressure collapse) is a
    # legitimate configuration — reject only genuine overlap, where crit and
    # warn bands intersect, which would make the level derivation ambiguous.
    if merged["warn_min"] is not None and merged["crit_max"] is not None \
            and merged["crit_max"] > merged["warn_min"] \
            and merged["crit_min"] is not None and merged["warn_max"] is not None \
            and merged["crit_min"] < merged["warn_max"]:
        return fail("crit band overlaps warn band; use crit above or below warn")

    cfg_path = _find_config_file(state)
    if cfg_path is None:
        return fail("config file not found; threshold editing unavailable "
                    "(config loaded from memory or demo)")

    started = _time.time()
    raw = _json.loads(cfg_path.read_text(encoding="utf-8"))
    assets = raw.get("assets", [])
    target = next((a for a in assets if a.get("asset_id") == asset_id), None)
    if target is None:
        return fail("asset not found in config file")
    metrics = target.get("metrics", [])
    mrec = next((m for m in metrics if m.get("key") == key), None)
    if mrec is None:
        return fail("metric not found in config file")

    changes: dict[str, list[Any]] = {}
    # guardrail 2: type fidelity — an int input (68) must not drift to 68.0;
    # json.dumps would otherwise rewrite every future diff as a type change
    def _typed(name: str, v: float) -> float:
        old = mrec.get(name)
        if isinstance(old, int) and not isinstance(old, bool) \
                and float(v).is_integer():
            return int(v)
        return v

    for name, v in vals.items():
        if v is None:
            continue
        old = mrec.get(name)
        if old == v:
            continue
        changes[name] = [old, v]
        mrec[name] = _typed(name, v)

    if not changes:
        return ok(data={"changed": {}, "metric": metric_to_dict(metric)})

    # guardrail 1: snapshot the config aside before the write lands (only when
    # something actually changes — a no-op edit must not churn backups)
    _backup_config(cfg_path)

    # atomic write: tmp + os.replace — a crash mid-write must not lose the config
    tmp = cfg_path.with_suffix(".json.tmp")
    tmp.write_text(_json.dumps(raw, ensure_ascii=False, indent=2), encoding="utf-8")
    import os
    os.replace(tmp, cfg_path)

    # apply to the in-memory spec so the running process picks it up immediately
    for name, v in vals.items():
        if v is not None:
            setattr(metric, name, _typed(name, v))

    from ..core.runlog import log_run_event
    log_run_event(state.store, asset_id, "threshold_edit", "ok", actor="hmi",
                  reason=f"{key}: " + ", ".join(
                      f"{k} {old}->{new}" for k, (old, new) in changes.items())[:200],
                  elapsed_s=round(_time.time() - started, 3))
    return ok(data={"changed": changes, "metric": metric_to_dict(metric)})