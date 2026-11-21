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
from ..core.bus import KIND_ALERT, KIND_SNAPSHOT, KIND_TASK, make_snapshot_event
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
        started = time.time()
        snap = await state.supervisor.run_once(asset)
        if isinstance(snap, dict) and snap.get("error"):
            task.mark_failed()
            return fail(f"run not OK: {snap.get('error')}", data={"token": task.token})
        from .alerts import publish_alert
        await _publish_snapshot(state, asset_id, snap)
        await publish_alert(state, asset_id, snap)
        # audit the manual run so it is distinguishable from scheduler ticks
        from ..core.runlog import log_run_event
        log_run_event(state.store, asset_id, "run_now", "ok", actor="hmi",
                      elapsed_s=round(time.time() - started, 3),
                      window_end=snap.get("window_end", ""))
        snap["token"] = task.token
        return ok(data=snap)


@router.post("/api/assets/{asset_id}/report")
async def report(request: Request, asset_id: str,
                 background: BackgroundTasks) -> Any:
    """Trigger a deep report; returns the task token IMMEDIATELY.

    A deep report can hold the event loop for minutes (LLM timeouts × provider
    chain). Inline awaiting it made the browser time out and retry → double
    execution. The token + /stream/task/{token} SSE already exist; the client
    watches the stream for phase=done/failed instead of holding the HTTP call.
    """
    state: AppState = await _state(request)
    asset = state.config.asset(asset_id)
    if asset is None:
        return fail("unknown asset")

    async def _run() -> None:
        async with _task(state, asset_id, "report") as task:
            started = time.time()
            try:
                res = await state.supervisor.run_deep_report(asset)
                from ..core.runlog import log_run_event
                if isinstance(res, dict) and res.get("error"):
                    log_run_event(state.store, asset_id, "deep_report", "gated",
                                  reason=res["error"][:200], actor="hmi",
                                  elapsed_s=round(time.time() - started, 3))
                    task.mark_failed()
                    return
                log_run_event(state.store, asset_id, "deep_report", "ok",
                              reason=f"provider={res.get('provider', '')}",
                              actor="hmi",
                              elapsed_s=round(time.time() - started, 3),
                              detail=str(res.get("report_dir", ""))[:200])
            except Exception as exc:  # noqa: BLE001 — must not kill the worker
                task.mark_failed()

    background.add_task(_run)
    token = uuid4().hex  # announce the token so the client can open its stream
    await state.bus.publish(KIND_TASK, asset_id,
                            payload={"token": token, "kind": "report", "phase": "queued"})
    return ok(data={"token": token, "queued": True})


@router.post("/api/assets/{asset_id}/learn")
async def learn(request: Request, asset_id: str,
                background: BackgroundTasks) -> Any:
    """Trigger a learn pass; returns the task token IMMEDIATELY (same rationale
    as /report — a learn call can hold the loop for minutes)."""
    state: AppState = await _state(request)
    asset = state.config.asset(asset_id)
    if asset is None:
        return fail("unknown asset")

    async def _run() -> None:
        async with _task(state, asset_id, "learn") as task:
            started = time.time()
            try:
                res = await state.supervisor.run_learn(asset)
                from ..core.runlog import log_run_event
                if res.error:
                    err = res.error if res.error.startswith(("no_", "insufficient_data")) \
                        else f"provider_failed: {res.error}"
                    log_run_event(state.store, asset_id, "learn", "gated",
                                  reason=err[:200], actor="hmi",
                                  elapsed_s=round(time.time() - started, 3))
                    task.mark_failed()
                    return
                log_run_event(state.store, asset_id, "learn", "ok", actor="hmi",
                              elapsed_s=round(time.time() - started, 3))
            except Exception:  # noqa: BLE001
                task.mark_failed()

    background.add_task(_run)
    token = uuid4().hex
    await state.bus.publish(KIND_TASK, asset_id,
                            payload={"token": token, "kind": "learn", "phase": "queued"})
    return ok(data={"token": token, "queued": True})


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
    started = time.time()
    # B3: freshness gate lives in QAEngine.ask itself; we surface its refusal
    # without driving the model when there is no fresh snapshot.
    res = await state.qa.ask(asset, body.question, role=ModelRole.ROUTINE,
                             allow_stale=body.allow_stale)
    elapsed = round(time.time() - started, 3)
    # QAEngine.ask returns a Chinese *explanation* content even on a gate refusal,
    # but always sets error="no_fresh_snapshot: ...". The refusal must not look
    # like a real answer, so the HMI surfaces it as ok=false and does NOT render it
    # as an LLM answer. The model was never called in this path.
    if res.error:
        # audit the refusal too: a spike of gate refusals is an ops signal
        from ..core.runlog import log_run_event
        log_run_event(state.store, asset_id, "qa", "gated",
                      reason=res.error[:200], actor="hmi", elapsed_s=elapsed,
                      detail=body.question[:150])
        # QAEngine returns error="no_fresh_snapshot: ..." for gate refusals;
        # anything else is a provider outage. Pass the code through untouched so
        # the UI can distinguish "no data to assess" from "model unavailable".
        return fail(res.error if res.error.startswith("no_fresh_snapshot")
                    else f"provider_failed: {res.error}")
    # audit every QA call: without this row, "who asked what and what did the
    # system answer" is unanswerable — a core requirement for an audited HMI.
    from ..core.runlog import log_run_event
    log_run_event(state.store, asset_id, "qa", "ok",
                  reason=f"provider={res.used_provider}", actor="hmi",
                  elapsed_s=elapsed, detail=body.question[:150])
    return ok(data={
        "content": res.content,
        "used_provider": res.used_provider,
        "used_role": res.used_role.value if res.used_role else None,
        "degraded": res.degraded,
        "elapsed_s": elapsed,
    })


# A tiny context manager so write actions are traceable in the task stream.
class _task:
    def __init__(self, state: AppState, asset_id: str, kind: str):
        self.state = state
        self.asset_id = asset_id
        self.kind = kind
        self.token = uuid4().hex  # unique per action so /stream/task/{token} can isolate it
        self.failed = False  # set by handlers returning fail() so __aexit__ reports it

    async def __aenter__(self):
        await self.state.bus.publish(KIND_TASK, self.asset_id,
                                     payload={"token": self.token, "kind": self.kind,
                                              "phase": "start"})
        return self

    def mark_failed(self) -> None:
        self.failed = True

    async def __aexit__(self, exc_type, exc, tb):
        phase = "failed" if (exc or self.failed) else "done"
        payload = {"token": self.token, "kind": self.kind, "phase": phase}
        if exc_type is not None:
            payload["error"] = str(exc)
        await self.state.bus.publish(KIND_TASK, self.asset_id, payload=payload)