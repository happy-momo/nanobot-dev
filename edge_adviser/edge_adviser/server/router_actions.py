"""Write-side actions + SSE streams.

Write actions (run-now / report / learn / ask) reuse the exact same deterministic
services the CLI drives, and every one goes through the freshness gate — a missing
or stale snapshot is refused with ``ok=false`` and a human-readable reason, never
used to drive the model. This keeps the "no data → refuse to guess" guarantee the
industrial contract demands.

The SSE streams:
  * ``/stream/telemetry``  — every new snapshot (Monitor page live curves).
  * ``/stream/alerts``    — real state changes worth the operator's attention
                            (deduped + debounced; planned-downtime exempt).
  * ``/stream/task/{token}`` — progress/result of a write action the UI triggered.
"""

from __future__ import annotations

import asyncio
import json
import time
from typing import Any
from uuid import uuid4

from fastapi import APIRouter, BackgroundTasks, Request
from pydantic import BaseModel

from ..analytics.gates import gate_snapshot_for_read
from ..config.model import ModelRole
from ..core.bus import KIND_ALERT, KIND_SCHEDULE, KIND_SNAPSHOT, KIND_TASK, make_snapshot_event
from .deps import AppState
from .schemas import fail, ok
from .streaming import stream_bus

router = APIRouter(tags=["actions"])


async def _state(request: Request) -> AppState:
    return request.app.state.edge


# --------------------------------------------------------------------------- #
#  SSE streams
# --------------------------------------------------------------------------- #
@router.get("/stream/telemetry")
async def stream_telemetry(request: Request) -> Any:
    state: AppState = await _state(request)
    return await stream_bus(state.bus, [KIND_SNAPSHOT], replay=True)


@router.get("/stream/alerts")
async def stream_alerts(request: Request) -> Any:
    state: AppState = await _state(request)
    return await stream_bus(state.bus, [KIND_ALERT], replay=True)


@router.get("/stream/task/{token}")
async def stream_task(request: Request, token: str) -> Any:
    state: AppState = await _state(request)
    # Only deliver task events whose token matches — each action gets a unique
    # token from _task, so a client subscribed to its own stream never sees
    # unrelated background tasks. (Legacy tokens were the bare kind string, which
    # still matches for old clients since start/done used the same value.)
    def _match(ev: dict) -> bool:
        payload = ev.get("payload") or {}
        return payload.get("token") == token
    return await stream_bus(state.bus, [KIND_TASK], replay=True, predicate=_match)


# --------------------------------------------------------------------------- #
#  Write-side actions
# --------------------------------------------------------------------------- #
async def _publish_snapshot(state: AppState, asset_id: str, snap: dict[str, Any]) -> None:
    try:
        await state.bus.publish(KIND_SNAPSHOT, asset_id, payload=make_snapshot_event(asset_id, snap))
    except Exception:  # noqa: BLE001
        pass


@router.post("/api/assets/{asset_id}/run-now")
async def run_now(request: Request, asset_id: str) -> Any:
    state: AppState = await _state(request)
    asset = state.config.asset(asset_id)
    if asset is None:
        return fail("unknown asset")
    async with _task(state, asset_id, "run-now") as task:
        snap = await state.supervisor.run_once(asset)
        if isinstance(snap, dict) and snap.get("error"):
            return fail(f"run not OK: {snap.get('error')}", data={"token": task.token})
        from .alerts import publish_alert
        await _publish_snapshot(state, asset_id, snap)
        await publish_alert(state, asset_id, snap)
        snap["token"] = task.token
        return ok(data=snap)


@router.post("/api/assets/{asset_id}/report")
async def report(request: Request, asset_id: str) -> Any:
    state: AppState = await _state(request)
    asset = state.config.asset(asset_id)
    if asset is None:
        return fail("unknown asset")
    async with _task(state, asset_id, "report") as task:
        res = await state.supervisor.run_deep_report(asset)
        if isinstance(res, dict) and res.get("error"):
            return fail(f"report gated: {res.get('error')}", data={"token": task.token})
        res["token"] = task.token
        return ok(data=res)


@router.post("/api/assets/{asset_id}/learn")
async def learn(request: Request, asset_id: str) -> Any:
    state: AppState = await _state(request)
    asset = state.config.asset(asset_id)
    if asset is None:
        return fail("unknown asset")
    async with _task(state, asset_id, "learn") as task:
        res = await state.supervisor.run_learn(asset)
        if res.error:
            return fail(f"learn gated: {res.error}", data={"token": task.token})
        return ok(data={"content": res.content[:400], "token": task.token})


class AskIn(BaseModel):
    question: str
    # allow the caller to request a stale snapshot explicitly (operator override);
    # default False keeps the strict fresh-data contract.
    allow_stale: bool = False


@router.post("/api/assets/{asset_id}/ask")
async def ask(request: Request, asset_id: str, body: AskIn) -> Any:
    state: AppState = await _state(request)
    asset = state.config.asset(asset_id)
    if asset is None:
        return fail("unknown asset")
    if not body.question.strip():
        return fail("question is required")
    # B3: freshness gate lives in QAEngine.ask itself; we surface its refusal
    # without driving the model when there is no fresh snapshot.
    res = await state.qa.ask(asset, body.question, role=ModelRole.ROUTINE,
                             allow_stale=body.allow_stale)
    # QAEngine.ask returns a Chinese *explanation* content even on a gate refusal,
    # but always sets error="no_fresh_snapshot: ...". The refusal must not look
    # like a real answer, so the HMI surfaces it as ok=false and does NOT render it
    # as an LLM answer. The model was never called in this path.
    if res.error:
        return fail(f"no_fresh_snapshot: {res.error}")
    return ok(data={
        "content": res.content,
        "used_provider": res.used_provider,
        "used_role": res.used_role.value if res.used_role else None,
        "degraded": res.degraded,
    })


# A tiny context manager so write actions are traceable in the task stream.
class _task:
    def __init__(self, state: AppState, asset_id: str, kind: str):
        self.state = state
        self.asset_id = asset_id
        self.kind = kind
        self.token = uuid4().hex  # unique per action so /stream/task/{token} can isolate it

    async def __aenter__(self):
        await self.state.bus.publish(KIND_TASK, self.asset_id,
                                     payload={"token": self.token, "kind": self.kind,
                                              "phase": "start"})
        return self

    async def __aexit__(self, exc_type, exc, tb):
        phase = "failed" if exc else "done"
        await self.state.bus.publish(KIND_TASK, self.asset_id,
                                     payload={"token": self.token, "kind": self.kind,
                                              "phase": phase})