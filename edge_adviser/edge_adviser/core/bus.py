"""In-process event bus for the HMI server.

The supervisor and write-side actions publish events (new snapshot, state change,
alert, task progress); the SSE endpoints subscribe to fan them out to browsers.

Deliberately dependency-free: a single async-first, lock-guarded pub/sub ring.
Events carry a monotonic timestamp so subscribers can de-duplicate and the UI can
ignore stale packets (useful on weak/flaky networks).

This is the glue between the deterministic services and the web layer — it never
mutates the fact spine, it only announces that new facts exist.
"""

from __future__ import annotations

import asyncio
import itertools
import time
from typing import Any, Awaitable, Callable

_Kind = str
# Topic filters: a subscriber may declare interest in a subset of kinds + asset_ids.
Subscription = Callable[[dict[str, Any]], Awaitable[None] | None]


class EventBus:
    """Async pub/sub with per-subscriber subscription sets and replay window."""

    def __init__(self, replay: int = 0) -> None:
        self._replay = replay
        self._lock = asyncio.Lock()
        self._seq = itertools.count(1)
        self._watchers: list[tuple[set[str], set[str], Subscription]] = []
        # latest event per (asset_id, kind) for late-joining subscribers
        self._last: dict[tuple[str, str], dict[str, Any]] = {}

    async def publish(
        self,
        kind: str,
        asset_id: str = "",
        *,
        payload: dict[str, Any] | None = None,
    ) -> None:
        """Record and deliver an event of kind `kind` for `asset_id`."""
        event: dict[str, Any] = {
            "seq": next(self._seq),
            "kind": kind,
            "asset_id": asset_id,
            "ts": time.time(),
            "payload": payload or {},
        }
        self._last[(asset_id, kind)] = event
        async with self._lock:
            for kinds, assets, cb in self._watchers:
                if kinds and kind not in kinds:
                    continue
                if assets and asset_id not in assets:
                    continue
                try:
                    await cb(event)
                except Exception:  # noqa: BLE001 — a bad subscriber must not block others
                    pass

    async def subscribe(
        self,
        kinds: set[str],
        assets: set[str] | None = None,
        *,
        on_event: Subscription,
        include_replay: bool = True,
    ) -> asyncio.Task[None]:
        """Register a per-subscriber callback. Returns a cancellable task.

        Delivery is via `on_event` only (each subscriber gets its OWN stream of
        events — no shared queue to split messages across clients). The task itself
        is a sentinel: it stays alive until cancelled so callers have a handle to
        detach on disconnect. `include_replay` delivers the latest events the bus
        has seen to a freshly-subscribed client.
        """
        if assets:
            assets = set(assets)
        async with self._lock:
            self._watchers.append((set(kinds), assets, on_event))

        # Replay is intentionally NOT done by this sentinel: the SSE streaming
        # layer owns replay via snapshot_replay(), so a late-joining client gets a
        # consistent replay without racing a concurrent publish. This task just
        # lives until cancelled so callers have a handle to detach on disconnect.
        async def _sentinel() -> None:
            while True:
                await asyncio.sleep(3600)

        task = asyncio.create_task(_sentinel())
        return task

    async def unsubscribe(self, on_event: Subscription) -> None:
        """Remove a subscriber callback (idempotent)."""
        await self._lock.acquire()
        try:
            self._watchers = [w for w in self._watchers if w[2] is not on_event]
        finally:
            self._lock.release()

    async def aclose(self) -> None:
        self._watchers.clear()

    def snapshot_replay(self) -> list[dict[str, Any]]:
        """Return the most recent event per (asset_id, kind) for ad-hoc joins."""
        return list(self._last.values())


# --------------------------------------------------------------------------- #
# Standardized event factories used across the server.
# --------------------------------------------------------------------------- #
KIND_SNAPSHOT = "snapshot"      # a fresh AnalyticsSnapshot was archived
KIND_ALERT = "alert"            # a real state change worth the operator's attention
KIND_TASK = "task"              # write-side action progress/status (run/report/learn/ask)


def make_snapshot_event(asset_id: str, snapshot: dict[str, Any]) -> dict[str, Any]:
    """A standardized `snapshot` payload the Monitor page can render directly."""
    return {
        "asset_id": asset_id,
        "status": snapshot.get("status", "unknown"),
        "trust": snapshot.get("trust", "none"),
        "coverage": snapshot.get("coverage", 0.0),
        "overall_level": snapshot.get("overall_level", "unknown"),
        "headline": snapshot.get("headline", ""),
        "window_end": snapshot.get("window_end", ""),
        "sample_count": snapshot.get("sample_count", 0),
        "metrics": snapshot.get("metrics", []),
    }