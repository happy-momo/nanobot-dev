"""Read-side API: feedback + audit runlog for an asset."""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Request
from pydantic import BaseModel

from .deps import AppState
from .schemas import fail, ok

router = APIRouter(prefix="/api", tags=["runlog"])


async def _state(request: Request) -> AppState:
    return request.app.state.edge


@router.get("/assets/{asset_id}/feedback")
async def list_feedback(request: Request, asset_id: str) -> Any:
    state: AppState = await _state(request)
    if state.config.asset(asset_id) is None:
        return fail("unknown asset")
    return ok(data=state.store.read_feedback(asset_id))


class FeedbackIn(BaseModel):
    note: str


@router.post("/assets/{asset_id}/feedback")
async def add_feedback(request: Request, asset_id: str, body: FeedbackIn) -> Any:
    state: AppState = await _state(request)
    if state.config.asset(asset_id) is None:
        return fail("unknown asset")
    if not body.note.strip():
        return fail("note is required")
    state.feedback.record(asset_id, body.note.strip())
    await state.bus.publish("feedback", asset_id, payload={"note": body.note})
    return ok(data={"recorded": True})


@router.get("/assets/{asset_id}/runlog")
async def list_runlog(request: Request, asset_id: str, limit: int = 200) -> Any:
    state: AppState = await _state(request)
    if state.config.asset(asset_id) is None:
        return fail("unknown asset")
    return ok(data=state.store.read_runlog(asset_id, limit=limit))


@router.get("/assets/{asset_id}/alerts")
async def list_alerts(request: Request, asset_id: str, include_acked: bool = False) -> Any:
    state: AppState = await _state(request)
    if state.config.asset(asset_id) is None:
        return fail("unknown asset")
    from .alerts import list_alerts as _list
    return ok(data=_list(state, asset_id, include_acked=include_acked))


class AckIn(BaseModel):
    ts: float


@router.post("/assets/{asset_id}/alerts/{ts}/ack")
async def ack_alert(request: Request, asset_id: str, ts: float) -> Any:
    state: AppState = await _state(request)
    if state.config.asset(asset_id) is None:
        return fail("unknown asset")
    from .alerts import acknowledge
    ok_res = acknowledge(state, asset_id, ts)
    return ok(data={"acked": ok_res})