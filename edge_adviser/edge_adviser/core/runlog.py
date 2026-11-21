"""Append-only audit ledger for what actually happened per asset per window/day.

Industrial trust requires that a missing report day is *traceable* — we must be
able to tell "didn't run" from "ran but had no data" from "provider failed" from
"gated for low trust". Reports only exist on success; the runlog records every
outcome (including failures) so the two can be reconciled.

Each asset owns ``assets/<id>/runlog.jsonl`` (sibling of ``reports/``). Entries
are append-only JSON lines; reads are best-effort with a row limit.

Every entry carries ``elapsed_s`` (how long the action took) and, when the
caller knows it, ``actor`` (who/what triggered it: "scheduler", "hmi", "cron").
Without these, an auditor cannot tell a scheduled 06:00 report from a manual
one, or a 0.2 s gate refusal from a 6-minute model call.
"""

from __future__ import annotations

import time
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
    *,
    detail: str = "",
    actor: str = "",
    elapsed_s: float | None = None,
    window_end: str = "",
    **extra: Any,
) -> None:
    """Append one audit record for an asset.

    ``detail`` (explicit string) wins over legacy kwargs; ``actor`` names the
    trigger ("hmi" for HTTP actions, "cron"/"scheduler" for loop jobs);
    ``elapsed_s`` captures duration so a stuck provider is visible in the audit.
    """
    now = datetime.now(timezone.utc)
    entry = RunLogEntry(
        asset_id=asset_id,
        date=now.strftime("%Y-%m-%d"),
        kind=kind,
        outcome=outcome,
        reason=reason or "",
        detail=str(detail or extra.get("reason", "")),
        timestamp=now.isoformat(timespec="seconds"),
        actor=actor or extra.get("actor", ""),
        elapsed_s=round(elapsed_s, 3) if elapsed_s is not None else None,
        window_end=window_end or "",
    )
    store.append_runlog(asset_id, entry.to_dict())


def read_runlog(store: AssetStore, asset_id: str, limit: int = 100) -> list[dict[str, Any]]:
    """Return the most recent ``limit`` runlog entries for an asset."""
    return store.read_runlog(asset_id, limit=limit)


class _Timer:
    """Context helper capturing elapsed wall time for an audited action."""

    def __init__(self) -> None:
        self.started = time.time()

    def elapsed(self) -> float:
        return round(time.time() - self.started, 3)
