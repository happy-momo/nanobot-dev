"""Alert derivation + persistence for the HMI.

The core contract: an alert is a **real state change worth a human**, not every
snapshot. We derive alerts from deterministic snapshot transitions, persist them
append-only per asset (`alerts.jsonl`), and let the operator acknowledge them.

Rules:
  * Only fire when the *state* (health/trust) actually changed vs the last alert —
    a steady warn that keeps re-warning produces a single alert (dedup).
  * Planed-downtime and pure data-coverage verdicts (unknown/insufficient) are
    exempt from alarming (they are expected / informational).
  * Each alert records the snapshot it was derived from so the operator can revisit it.
"""

from __future__ import annotations

import json
import time
from pathlib import Path
from typing import Any

from .deps import AppState
from ..core.bus import KIND_ALERT

# An alert is warranted by one of two facts:
#   * a REAL alarm  — overall_level is warn/critical (the engine's band verdict),
#   * a DATA fault  — status is offline / stale (disconnection / expired data).
# It is NOT warranted by a pure data-coverage verdict (unknown/insufficient — those
# mean "we have no confident facts", not "something is wrong") nor by planned
# downtime (the outage is expected). So a machine sitting in `healthy` with a
# warn-level metric DOES alert; the same machine with only `insufficient` data does not.
_FAULT_LEVELS = {"warn", "critical"}
_FAULT_STATES = {"offline", "stale"}
_EXEMPT = {"planned_downtime", "unknown", "insufficient"}


def derive_alert(asset_id: str, snapshot: dict[str, Any]) -> dict[str, Any] | None:
    """Return an alert record for this snapshot, or None if it is not alert-worthy."""
    state = snapshot.get("status", "unknown")
    level = snapshot.get("overall_level", "info")
    reason = snapshot.get("state_reason", "")
    if state in _EXEMPT:
        return None  # planned/informational — no operator alarm
    if level not in _FAULT_LEVELS and state not in _FAULT_STATES:
        return None
    # what the operator should act on — prefer the real alarm if present, else the fault
    subject = level if level in _FAULT_LEVELS else ("offline" if state == "offline" else "stale")
    return {
        "asset_id": asset_id,
        "state": state,
        "subject": subject,
        "reason": reason,
        "overall_level": level,
        "headline": snapshot.get("headline", ""),
        "window_end": snapshot.get("window_end", ""),
        "ts": time.time(),
        "acked": False,
        "acked_at": None,
        # dedup on the actionable subject, not the data-integrity status, so a
        # vibration-warn keeps warning once until acknowledged even as status stays healthy.
        "dedup_key": f"{asset_id}:{subject}",
    }


def last_alert_key(store, asset_id: str) -> dict[str, Any]:
    """The most recent stored alert per asset (used for dedup against a new one)."""
    p = _alerts_path(store, asset_id)
    recs = _read(p)
    return recs[-1] if recs else {}


def ingest_alert(state: AppState, asset_id: str, snapshot: dict[str, Any]) -> dict[str, Any] | None:
    """Dedup + persist a derived alert. Returns the new alert or None if suppressed.

    Dedup rule: only fire when that asset+state has no **active (unacked)** alert
    yet. So a machine that stays in critical across many runs produces exactly one
    alert until the operator acknowledges it. A fresh transition to a different
    fault state creates a new alert.
    """
    alert = derive_alert(asset_id, snapshot)
    if alert is None:
        return None
    for existing in _read(_alerts_path(state.store, asset_id)):
        if existing.get("dedup_key") == alert["dedup_key"] and not existing.get("acked"):
            return None  # already fireing + unacked → dedup
    _append(state.store, asset_id, alert)
    return alert


def list_alerts(state: AppState, asset_id: str, *, include_acked: bool = False) -> list[dict[str, Any]]:
    all_alerts = _read(_alerts_path(state.store, asset_id))
    if include_acked:
        return all_alerts
    return [a for a in all_alerts if not a.get("acked")]


def acknowledge(state: AppState, asset_id: str, ts: float, note: str = "") -> bool:
    """Acknowledge an alert by its timestamp. Rewrites the JSONL in place.

    ``note`` records HOW the alert was handled — this is the raw material the
    learn loop turns into maintenance knowledge, so it is persisted on the alert
    itself and echoed into the feedback ledger.
    """
    p = _alerts_path(state.store, asset_id)
    recs = _read(p)
    changed = False
    for r in recs:
        if abs(r.get("ts", 0) - ts) < 1e-6 and not r.get("acked"):
            r["acked"] = True
            r["acked_at"] = time.time()
            if note:
                r["ack_note"] = note[:2000]
            changed = True
    if changed:
        _write(p, recs)
    return changed


def acknowledge_many(state: AppState, asset_id: str, subject: str | None = None,
                     note: str = "") -> int:
    """Acknowledge every unacked alert of an asset (optionally one subject).

    Returns the number of alerts acknowledged. After a plant-wide outage an
    operator faces 20+ alerts; per-row acking alone makes the center unusable.
    """
    p = _alerts_path(state.store, asset_id)
    recs = _read(p)
    n = 0
    for r in recs:
        if r.get("acked"):
            continue
        if subject and r.get("subject") != subject:
            continue
        r["acked"] = True
        r["acked_at"] = time.time()
        if note:
            r["ack_note"] = note[:2000]
        n += 1
    if n:
        _write(p, recs)
    return n


def _alerts_path(store, asset_id: str) -> Path:
    return store.asset_dir(asset_id) / "alerts.jsonl"


def _read(path: Path) -> list[dict[str, Any]]:
    if not path.exists():
        return []
    out: list[dict[str, Any]] = []
    try:
        for line in path.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if line:
                try:
                    out.append(json.loads(line))
                except json.JSONDecodeError:
                    continue
    except OSError:
        return []
    return out


def _append(store, asset_id: str, rec: dict[str, Any]) -> None:
    p = _alerts_path(store, asset_id)
    with open(p, "a", encoding="utf-8") as f:
        f.write(json.dumps(rec, ensure_ascii=False) + "\n")


def _write(path: Path, recs: list[dict[str, Any]]) -> None:
    with open(path, "w", encoding="utf-8") as f:
        for r in recs:
            f.write(json.dumps(r, ensure_ascii=False) + "\n")


async def publish_alert(state: AppState, asset_id: str, snapshot: dict[str, Any]) -> None:
    """Derive + dedup + persist + broadcast an alert, if any."""
    alert = ingest_alert(state, asset_id, snapshot)
    if alert is None:
        return
    # retention: keep the alerts ledger bounded with the same cap as the runlog
    try:
        state.store.truncate_jsonl(_alerts_path(state.store, asset_id),
                                   state.config.max_runlog_rows)
    except Exception:  # noqa: BLE001 — pruning must never break alerting
        pass
    try:
        await state.bus.publish(KIND_ALERT, asset_id, payload=alert)
    except Exception:  # noqa: BLE001
        pass