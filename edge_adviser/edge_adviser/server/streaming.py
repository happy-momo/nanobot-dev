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
from typing import Any, Iterable

from sse_starlette.sse import EventSourceResponse

from ..core.bus import EventBus


def _to_sse(ev: dict[str, Any]) -> dict[str, str]:
    return {
        "id": str(ev.get("seq", "")),
        "event": ev.get("kind", "message"),
        "data": json.dumps(ev.get("payload") or {}, ensure_ascii=False),
    }


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
    queue: asyncio.Queue[dict[str, Any]] = asyncio.Queue()

    async def relay(ev: dict[str, Any]) -> None:
        if predicate is not None and not predicate(ev):
            return
        await queue.put(ev)

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