"""Read-side API: feedback + audit runlog for an asset.

Auditing is a first-class contract: every state-changing HTTP action (ack,
feedback, ask, run-now, threshold edit) lands in runlog.jsonl with actor +
duration, and the runlog itself supports cursor pagination + CSV export so an
auditor can reconcile "what happened" beyond the newest N rows.
"""

from __future__ import annotations

import json
import time
from typing import Any

from fastapi import APIRouter, Request
from fastapi.responses import PlainTextResponse
from pydantic import BaseModel, Field

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
    # bounded + sanitized: a note flows into the learn prompt (injection
    # surface) and onto every SSE subscriber; an unbounded 10MB note is a
    # memory/disk/bandwidth lever. 2000 chars covers any real operator note.
    note: str = Field(..., min_length=1, max_length=2000)


@router.post("/assets/{asset_id}/feedback")
async def add_feedback(request: Request, asset_id: str, body: FeedbackIn) -> Any:
    state: AppState = await _state(request)
    if state.config.asset(asset_id) is None:
        return fail("unknown asset")
    started = time.time()
    note = body.note.strip()
    if not note:
        return fail("note is required")
    state.feedback.record(asset_id, note)
    await state.bus.publish("feedback", asset_id, payload={"note": note})
    from ..core.runlog import log_run_event
    log_run_event(state.store, asset_id, "feedback", "ok",
                  detail=note[:200], elapsed_s=round(time.time() - started, 3))
    return ok(data={"recorded": True})


@router.get("/assets/{asset_id}/runlog")
async def list_runlog(request: Request, asset_id: str, limit: int = 200,
                      before: int = 0) -> Any:
    """Audit rows, newest-first, offset-cursor paginated.

    ``before`` is the number of rows already consumed from the newest end
    (page N returns rows[N*limit:(N+1)*limit] of the newest-first sequence).
    A timestamp cursor breaks when several rows share one second
    (``timespec='seconds'``); an offset against the retention-bounded ledger
    is stable for audit browsing. ``limit`` is clamped to [1, 1000] — an
    unbounded limit on a 10k-row ledger is a memory + loop-stall lever.
    """
    state: AppState = await _state(request)
    if state.config.asset(asset_id) is None:
        return fail("unknown asset")
    limit = max(1, min(1000, limit))
    offset = max(0, int(before))
    all_rows = list(reversed(state.store.read_runlog(asset_id, limit=0)))  # newest-first
    rows = all_rows[offset: offset + limit]
    next_before = offset + limit if offset + limit < len(all_rows) else None
    return ok(data={"rows": rows, "next_before": next_before, "total": len(all_rows)})


@router.get("/assets/{asset_id}/runlog.csv")
async def export_runlog_csv(request: Request, asset_id: str) -> Any:
    """Full audit ledger as CSV for external reconciliation (audit requirement)."""
    state: AppState = await _state(request)
    if state.config.asset(asset_id) is None:
        return fail("unknown asset")
    rows = state.store.read_runlog(asset_id, limit=0)
    import csv
    import io
    buf = io.StringIO()
    writer = csv.writer(buf)
    writer.writerow(["timestamp", "date", "kind", "outcome", "reason", "detail", "actor"])
    for r in rows:
        writer.writerow([_csv_safe(r.get("timestamp", "")), _csv_safe(r.get("date", "")),
                         _csv_safe(r.get("kind", "")),
                         _csv_safe(r.get("outcome", "")), _csv_safe(r.get("reason", "")),
                         _csv_safe((r.get("detail") or "").replace("\n", " ")),
                         _csv_safe(r.get("actor", ""))])
    return PlainTextResponse(
        buf.getvalue(), media_type="text/csv; charset=utf-8",
        headers={"Content-Disposition":
                 f'attachment; filename="{asset_id}-runlog.csv"'})


def _csv_safe(v: Any) -> Any:
    """Neutralize CSV formula injection.

    Note/feedback fields are operator free-text that lands in the audit ledger;
    opened in Excel/WPS, a cell starting with ``=``/``+``/``-``/``@`` evaluates
    as a formula (=cmd() etc). Prefixing a quote renders it inert while normal
    text passes through untouched.
    """
    s = str(v)
    if s[:1] in ("=", "+", "-", "@"):
        return "'" + s
    return s


@router.get("/assets/{asset_id}/alerts")
async def list_alerts(request: Request, asset_id: str, include_acked: bool = False) -> Any:
    state: AppState = await _state(request)
    if state.config.asset(asset_id) is None:
        return fail("unknown asset")
    from .alerts import list_alerts as _list
    return ok(data=_list(state, asset_id, include_acked=include_acked))


class AckIn(BaseModel):
    ts: float
    # how the alert was handled — persisted on the alert AND fed to the learn
    # loop via the feedback ledger, closing the alert→action→knowledge chain
    note: str = Field(default="", max_length=2000)


@router.post("/assets/{asset_id}/alerts/{ts}/ack")
async def ack_alert(request: Request, asset_id: str, ts: float, body: AckIn | None = None) -> Any:
    state: AppState = await _state(request)
    if state.config.asset(asset_id) is None:
        return fail("unknown asset")
    started = time.time()
    note = (body.note.strip() if body and body.note else "")
    from .alerts import acknowledge
    ok_res = await acknowledge(state, asset_id, ts, note=note)
    # audit: who acknowledged what, when, and (optionally) how it was handled.
    # Without this row, "who confirmed the 03:00 critical" is unanswerable.
    from ..core.runlog import log_run_event
    log_run_event(state.store, asset_id, "ack", "ok" if ok_res else "no_data",
                  reason=f"ts={ts}" + (f" note={note[:150]}" if note else ""),
                  elapsed_s=round(time.time() - started, 3))
    if ok_res and note:
        # the operator's handling note is exactly the correction the learner
        # consumes — record it as feedback so the next learn pass sees it
        state.feedback.record(asset_id, f"【告警处理】{note}")
    return ok(data={"acked": ok_res})


class AckAllIn(BaseModel):
    subject: str | None = Field(default=None,
                                description="optional filter: ack only this subject (warn/critical/offline/stale)")
    note: str = Field(default="", max_length=2000)


@router.post("/assets/{asset_id}/alerts/ack-all")
async def ack_all(request: Request, asset_id: str, body: AckAllIn | None = None) -> Any:
    """Bulk-acknowledge unacked alerts of one asset, optionally filtered by subject."""
    state: AppState = await _state(request)
    if state.config.asset(asset_id) is None:
        return fail("unknown asset")
    started = time.time()
    subject = body.subject if body and body.subject else None
    note = (body.note.strip() if body and body.note else "")
    from .alerts import acknowledge_many
    n = await acknowledge_many(state, asset_id, subject=subject, note=note)
    from ..core.runlog import log_run_event
    log_run_event(state.store, asset_id, "ack", "ok" if n else "no_data",
                  reason=f"bulk n={n}" + (f" subject={subject}" if subject else "")
                  + (f" note={note[:150]}" if note else ""),
                  elapsed_s=round(time.time() - started, 3))
    if n and note:
        state.feedback.record(asset_id, f"【告警处理】批量确认 {n} 条{('[' + subject + ']') if subject else ''}：{note}")
    return ok(data={"acked": n})


@router.post("/alerts/ack-all")
async def ack_all_fleet(request: Request, body: AckAllIn | None = None) -> Any:
    """Fleet-wide bulk ack — the post-outage sweep across every asset at once."""
    state: AppState = await _state(request)
    started = time.time()
    subject = body.subject if body and body.subject else None
    note = (body.note.strip() if body and body.note else "")
    from .alerts import acknowledge_many
    total = 0
    from ..core.runlog import log_run_event
    for asset in state.config.assets:
        n = await acknowledge_many(state, asset.asset_id, subject=subject, note=note)
        total += n
        # the handling note must only feed the learn loop for assets that
        # actually had alerts acknowledged — a blanket record would inject
        # unrelated "handling" text into every asset's knowledge base
        if n and note:
            state.feedback.record(
                asset.asset_id,
                f"【告警处理】批量确认 {n} 条{('[' + subject + ']') if subject else ''}：{note}")
        log_run_event(state.store, asset.asset_id, "ack",
                      "ok" if n else "no_data",
                      reason=f"fleet-bulk n={n}",
                      elapsed_s=round(time.time() - started, 3))
    return ok(data={"acked": total})
