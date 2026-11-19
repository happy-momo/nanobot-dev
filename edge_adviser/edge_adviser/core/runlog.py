"""Append-only audit ledger for what actually happened per asset per window/day.

Industrial trust requires that a missing report day is *traceable* — we must be
able to tell "didn't run" from "ran but had no data" from "provider failed" from
"gated for low trust". Reports only exist on success; the runlog records every
outcome (including failures) so the two can be reconciled.

Each asset owns ``assets/<id>/runlog.jsonl`` (sibling of ``reports/``). Entries
are append-only JSON lines; reads are best-effort with a row limit.
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

from ..config.model import RunLogEntry
from ..core.asset_store import AssetStore


def log_run_event(
    store: AssetStore,
    asset_id: str,
    kind: str,
    outcome: str,
    reason: str = "",
    **detail: Any,
) -> None:
    """Append one audit record for an asset."""
    now = datetime.now(timezone.utc)
    entry = RunLogEntry(
        asset_id=asset_id,
        date=now.strftime("%Y-%m-%d"),
        kind=kind,
        outcome=outcome,
        reason=reason or "",
        detail=str(detail.get("detail", detail.get("reason", ""))),
        timestamp=now.isoformat(timespec="seconds"),
    )
    store.append_runlog(asset_id, entry.to_dict())


def read_runlog(store: AssetStore, asset_id: str, limit: int = 100) -> list[dict[str, Any]]:
    """Return the most recent ``limit`` runlog entries for an asset."""
    p = store.runlog_path(asset_id)
    if not p.exists():
        return []
    import json

    out: list[dict[str, Any]] = []
    with open(p, encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            try:
                out.append(json.loads(line))
            except json.JSONDecodeError:
                continue
    return out[-limit:]