"""SSE helpers for the HMI — weak-network-friendly one-way push on top of the EventBus.

For every `GET /stream/*` connection we allocate a dedicated ``asyncio.Queue``,
subscribe the EventBus to it, and stream the queue as SSE. On client disconnect
(detected by the generator's finally) we unsubscribe. Late-joining clients get a
replay of the latest matching events so a tab opened mid-shift starts with the
current picture rather than a blank screen until the next publish.
"""

from __future__ import annotations

import asyncio
import json
import logging
from typing import Any, Iterable

from sse_starlette.sse import EventSourceResponse

from ..core.bus import EventBus


def _to_sse(ev: dict[str, Any]) -> dict[str, str]:
    return {
        "id": str(ev.get("seq", "")),
        "event": ev.get("kind", "message"),
        "data": json.dumps(ev.get("payload") or {}, ensure_ascii=False),
    }


# Slow-consumer backpressure: a stalled client must not accumulate an unbounded
# in-memory backlog. Close the stream on overflow so EventSource reconnects and
# the client can resync from REST plus the bus replay cache; never silently drop
# an alert or task terminal event.
STREAM_QUEUE_MAX = 256
_STREAM_OVERFLOW = object()
logger = logging.getLogger(__name__)


def _replay_payloads(bus: EventBus, kinds: set[str], assets: set[str] | None) -> list[dict[str, Any]]:
    """Latest event per (asset_id, kind) the bus has seen, filtered, newest-first."""
    got: list[dict[str, Any]] = []
    for ev in bus.snapshot_replay():
        if kinds and ev["kind"] not in kinds:
            continue
        if assets and ev["asset_id"] not in assets:
            continue
        got.append(ev)
    got.sort(key=lambda e: e.get("seq", 0), reverse=True)
    return got


async def stream_bus(
    bus: EventBus,
    kinds: Iterable[str],
    *,
    assets: Iterable[str] | None = None,
    replay: bool = True,
    predicate: Any | None = None,
) -> EventSourceResponse:
    """Return an SSE stream of live bus events for the given kinds (and assets).

    `kinds` empty → all kinds. `assets` empty/None → all assets.
    `predicate(ev)` — optional extra filter applied to both replay and live
    delivery (e.g. a task-token match for /stream/task/{token}).
    """
    kset: set[str] = set(kinds)
    assetset: set[str] = set(assets or [])
    queue: asyncio.Queue[Any] = asyncio.Queue(maxsize=STREAM_QUEUE_MAX)
    overflowed = False

    async def relay(ev: dict[str, Any]) -> None:
        nonlocal overflowed
        if overflowed:
            return
        if predicate is not None and not predicate(ev):
            return
        try:
            queue.put_nowait(ev)
        except asyncio.QueueFull:
            # A bounded queue cannot preserve an arbitrarily long backlog.
            # End the stream instead of dropping an event; reconnecting clients
            # resync current state and replay the latest event per asset/kind.
            overflowed = True
            try:
                while True:
                    queue.get_nowait()
            except asyncio.QueueEmpty:
                pass
            queue.put_nowait(_STREAM_OVERFLOW)
            logger.warning("SSE slow consumer exceeded queue limit; closing stream")

    def _pass(ev: dict[str, Any]) -> bool:
        return predicate is None or bool(predicate(ev))

    async def _generator():
        sub_task: asyncio.Task[None] | None = None
        try:
            sub_task = await bus.subscribe(
                kset if kset else set(),
                assetset if assetset else None,
                on_event=relay,
            )
            if replay:
                for ev in _replay_payloads(bus, kset, assetset if assetset else None)[:20]:
                    if _pass(ev):
                        yield _to_sse(ev)
            while True:
                try:
                    ev = await asyncio.wait_for(queue.get(), timeout=25.0)
                except asyncio.TimeoutError:
                    yield _to_sse({"kind": "ping", "asset_id": "", "payload": {}})
                    continue
                if ev is _STREAM_OVERFLOW:
                    return
                yield _to_sse(ev)
        finally:
            if sub_task is not None:
                sub_task.cancel()
                try:
                    await sub_task
                except (asyncio.CancelledError, Exception):  # noqa: BLE001
                    pass
            # Detach the relay from the bus too: cancelling the sentinel task does
            # not remove the watcher, so a disconnected client would otherwise stay
            # subscribed forever (a ghost whose queue grows on every publish).
            await bus.unsubscribe(relay)

    return EventSourceResponse(_generator())