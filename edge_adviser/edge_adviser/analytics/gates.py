"""Deterministic data-integrity gates — pure logic, no LLM.

These functions decide, from the data itself and the scheduler context, whether a
snapshot / sample batch is trustworthy enough to drive model interpretation:
  * snapshot freshness (P1) used by the read gates (ask/monitor/report),
  * coverage / data-state classification (N2/N3),
  * the machine/run state machine (N4): planned downtime vs transient gap vs
    abnormal disconnection (OFFLINE).

Every verdict here is reproducible and unit-testable offline. The LLM only ever
sees the *outputs* of these gates; it never decides trust.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any

from ..config.model import AdviserConfig, AssetSpec, AssetState, TrustLevel
from ..core.asset_store import AssetStore


# --------------------------------------------------------------------------- #
# gate result
# --------------------------------------------------------------------------- #
@dataclass
class GateResult:
    ok: bool
    snapshot: dict[str, Any] | None
    state: AssetState
    reason: str


# --------------------------------------------------------------------------- #
# freshness helpers
# --------------------------------------------------------------------------- #
def _safe_iso(s: Any) -> str:
    if not isinstance(s, str):
        return str(s) if s is not None else ""
    return s


def snapshot_age_s(snapshot: dict[str, Any], now: datetime | None = None) -> float:
    """Age in seconds of a snapshot's window_end vs ``now``.

    Returns inf when the window_end is absent/unparseable so it is treated as
    stale. A FUTURE window_end (clock-skewed device, out-of-window samples) is
    also inf: clamping a negative age to 0 would let a bad snapshot read as
    "eternally fresh" and bypass the freshness gate entirely (P0 fix).
    """
    end = _safe_iso(snapshot.get("window_end"))
    if not end:
        return float("inf")
    now = now or datetime.now(timezone.utc)
    try:
        ts = datetime.fromisoformat(end)
        if ts.tzinfo is None:
            ts = ts.replace(tzinfo=timezone.utc)
        age = (now - ts).total_seconds()
        if age < 0:
            return float("inf")  # future-dated: cannot be trusted as fresh
        return age
    except ValueError:
        return float("inf")


def is_fresh(snapshot: dict[str, Any], now: datetime, freshness_s: int) -> bool:
    return snapshot_age_s(snapshot, now) <= freshness_s


# --------------------------------------------------------------------------- #
# state machine (N4 + scheduler context)
# --------------------------------------------------------------------------- #
def in_planned_downtime(asset: AssetSpec, now: datetime) -> bool:
    """True when ``now`` falls inside a configured planned-downtime window."""
    for w in asset.downtime_windows:
        try:
            start = datetime.fromisoformat(_safe_iso(w.get("start")))
            end = datetime.fromisoformat(_safe_iso(w.get("end")))
        except ValueError:
            continue
        if start.tzinfo is None:
            start = start.replace(tzinfo=timezone.utc)
        if end.tzinfo is None:
            end = end.replace(tzinfo=timezone.utc)
        if start <= now <= end:
            return True
    return False


def classify_snapshot_state(
    asset: AssetSpec,
    snap: dict[str, Any],
    consecutive_empty: int,
    now: datetime,
    config: AdviserConfig,
) -> AssetState:
    """Classify an asset's runtime state from data + scheduler context.

    Precedence: planned downtime > offline (consecutive empties) > freshness
    > data-state. Planned downtime counts as the only "expected empty" state and
    is therefore NOT an anomaly.
    """
    if in_planned_downtime(asset, now):
        return AssetState.PLANNED_DOWNTIME
    sample_count = int(snap.get("sample_count", snap.get("expected_samples", 0)))
    if snap.get("metrics") is None and snap.get("status") is None:
        sample_count = 0
    if sample_count == 0:
        # a truly empty window: offline once we've seen enough consecutive ones
        if consecutive_empty >= config.max_empty_windows:
            return AssetState.OFFLINE
        return AssetState.INSUFFICIENT
    if snapshot_age_s(snap, now) > config.data_freshness_s:
        return AssetState.STALE
    # else trust the data-state already computed into the snapshot — but a
    # missing or unrecognizable status must FAIL CLOSED (unknown), never be
    # promoted to healthy.
    state = snap.get("status")
    try:
        return AssetState(state) if state else AssetState.UNKNOWN
    except ValueError:
        return AssetState.UNKNOWN


def trust_from_state(state: AssetState) -> TrustLevel:
    """Deterministic trust mapping (kept here as the single public source)."""
    if state == AssetState.HEALTHY:
        return TrustLevel.FULL
    if state == AssetState.PARTIAL:
        return TrustLevel.PARTIAL
    if state == AssetState.INSUFFICIENT:
        return TrustLevel.LOW
    return TrustLevel.NONE


# --------------------------------------------------------------------------- #
# read gate (P1): what consumers ask before interpreting a snapshot
# --------------------------------------------------------------------------- #
def gate_snapshot_for_read(
    store: AssetStore,
    asset: AssetSpec,
    config: AdviserConfig,
    now: datetime | None = None,
) -> GateResult:
    """Return whether the latest snapshot is fresh enough to interpret.

    The read gates (ask/monitor/report) call this FIRST and refuse to drive the
    model when it returns ok=False, so "no fresh data" is decided in the
    deterministic layer rather than left to LLM honesty.
    """
    now = now or datetime.now(timezone.utc)
    files = store.list_snapshots(asset.asset_id, limit=1)
    if not files:
        return GateResult(False, None, AssetState.UNKNOWN, "no_snapshot")
    import json

    try:
        snap = json.loads(files[0].read_text(encoding="utf-8"))
    except Exception:
        return GateResult(False, None, AssetState.UNKNOWN, "unreadable_snapshot")
    if not _safe_iso(snap.get("window_end")):
        return GateResult(False, snap, AssetState.UNKNOWN, "empty_window_end")
    if not is_fresh(snap, now, config.data_freshness_s):
        return GateResult(False, snap, AssetState.STALE, "stale")
    state = snap.get("status")
    try:
        st = AssetState(state) if state else AssetState.UNKNOWN
    except ValueError:
        st = AssetState.UNKNOWN  # fail closed: unrecognized status ≠ healthy
    return GateResult(True, snap, st, "")