"""Read-side API: archived reports + knowledge, plus report download."""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Request
from fastapi.responses import PlainTextResponse

from .deps import AppState
from .schemas import fail, ok

router = APIRouter(prefix="/api", tags=["reports"])


async def _state(request: Request) -> AppState:
    return request.app.state.edge


@router.get("/assets/{asset_id}/reports")
async def list_reports(request: Request, asset_id: str) -> Any:
    state: AppState = await _state(request)
    if state.config.asset(asset_id) is None:
        return fail("unknown asset")
    reports = state.store.list_reports(asset_id)
    # frontend needs summary meta for each day (badge + degraded), not full markdown
    out = [{
        "date": r["date"],
        "degraded": r["degraded"],
        "provider": r["provider"],
        "meta": r["meta"],
        "has_report": bool(r.get("report_md")),
    } for r in reports]
    return ok(data=out)


@router.get("/assets/{asset_id}/reports/{date}")
async def read_report(request: Request, asset_id: str, date: str) -> Any:
    state: AppState = await _state(request)
    if state.config.asset(asset_id) is None:
        return fail("unknown asset")
    r = state.store.read_report(asset_id, date)
    if r is None:
        return fail("report not found", status=404)
    return ok(data={
        "date": r["date"],
        "report_md": r.get("report_md"),
        "meta": r.get("meta"),
        "snapshot": r.get("snapshot"),
        "degraded": r.get("degraded"),
        "provider": r.get("provider"),
    })


@router.get("/assets/{asset_id}/reports/{date}/download.md")
async def download_report_md(request: Request, asset_id: str, date: str) -> Any:
    state: AppState = await _state(request)
    r = state.store.read_report(asset_id, date)
    if r is None or not r.get("report_md"):
        return fail("report not found", status=404)
    md = r["report_md"]
    return PlainTextResponse(md, media_type="text/markdown",
                             headers={"Content-Disposition": f'attachment; filename="{asset_id}-{date}.md"'})


@router.get("/assets/{asset_id}/reports/{date}/download.json")
async def download_report_json(request: Request, asset_id: str, date: str) -> Any:
    state: AppState = await _state(request)
    r = state.store.read_report(asset_id, date)
    if r is None or r.get("snapshot") is None:
        return fail("report not found", status=404)
    import json
    from fastapi.responses import JSONResponse
    return JSONResponse({
        "asset_id": asset_id, "date": date,
        "meta": r.get("meta"), "snapshot": r.get("snapshot"),
        "degraded": r.get("degraded"), "provider": r.get("provider"),
    })