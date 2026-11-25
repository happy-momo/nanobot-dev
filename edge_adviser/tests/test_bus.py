"""EventBus tests — publish/subscribe, dedup replay, unsubscribe, filtering."""

from __future__ import annotations

import asyncio
import sys
from pathlib import Path

import pytest

_HERE = Path(__file__).resolve().parent.parent / "edge_adviser"
if str(_HERE) not in sys.path:
    sys.path.insert(0, str(_HERE))

from edge_adviser.core.bus import EventBus  # noqa: E402


@pytest.mark.asyncio
async def test_publish_delivers_to_subscriber() -> None:
    bus = EventBus()
    got: list[dict] = []

    async def _cb(ev):
        got.append(ev)

    task = await bus.subscribe({"snapshot"}, None, include_replay=False, on_event=_cb)
    await bus.publish("snapshot", "pump1", payload={"a": 1})
    await asyncio.sleep(0.05)
    task.cancel()
    assert len(got) == 1 and got[0]["kind"] == "snapshot"


@pytest.mark.asyncio
async def test_publish_filters_by_kind_and_asset() -> None:
    bus = EventBus()
    got: list[dict] = []

    async def _cb(ev):
        got.append(ev)

    t1 = await bus.subscribe({"snapshot"}, {"pump1"}, on_event=_cb)
    await bus.publish("snapshot", "pump1")
    await bus.publish("snapshot", "pump2")   # asset filter excludes
    await bus.publish("alert", "pump1")       # kind filter excludes
    await asyncio.sleep(0.05)
    t1.cancel()
    assert len(got) == 1 and got[0]["asset_id"] == "pump1"


@pytest.mark.asyncio
async def test_replay_joins_without_missing_prior() -> None:
    """A late-joining client can recover the latest events via snapshot_replay()."""
    bus = EventBus(replay=1)
    await bus.publish("snapshot", "pump1", payload={"v": 1})
    await bus.publish("snapshot", "pump2", payload={"v": 2})
    replay = bus.snapshot_replay()
    # latest (asset_id, kind) wins — exactly one event per key
    by_asset = {ev["asset_id"]: ev for ev in replay}
    assert by_asset["pump1"]["payload"]["v"] == 1
    assert by_asset["pump2"]["payload"]["v"] == 2


@pytest.mark.asyncio
async def test_unsubscribe_stops_delivery() -> None:
    bus = EventBus()
    got: list[dict] = []

    async def _cb(ev):
        got.append(ev)

    t = await bus.subscribe({"alert"}, None, include_replay=False, on_event=_cb)
    await bus.publish("alert", "pump1")
    await asyncio.sleep(0.02)
    assert len(got) == 1
    await bus.unsubscribe(_cb)
    t.cancel()
    await bus.publish("alert", "pump1")
    await asyncio.sleep(0.02)
    assert len(got) == 1  # unchanged after unsubscribe

# --------------------------------------------------------------------------- #
#  0.5.2: SSE slow-consumer backpressure (bounded queue, disconnect on overflow)
# --------------------------------------------------------------------------- #
def test_slow_stream_disconnects_on_queue_overflow(monkeypatch) -> None:
    """A slow SSE consumer is disconnected instead of silently losing events."""
    from edge_adviser.core.bus import KIND_SNAPSHOT
    from edge_adviser.server import streaming

    async def scenario():
        bus = EventBus()
        response = await streaming.stream_bus(bus, [KIND_SNAPSHOT], replay=False)
        body = response.body_iterator
        first_event = asyncio.create_task(body.__anext__())
        await asyncio.sleep(0)
        await bus.publish(KIND_SNAPSHOT, "pump1", payload={"sample_count": 1})
        first = await first_event
        assert '"sample_count": 1' in first["data"]

        await bus.publish(KIND_SNAPSHOT, "pump1", payload={"sample_count": 2})
        await bus.publish(KIND_SNAPSHOT, "pump1", payload={"sample_count": 3})
        await bus.publish(KIND_SNAPSHOT, "pump1", payload={"sample_count": 4})
        with pytest.raises(StopAsyncIteration):
            await body.__anext__()
        assert bus._watchers == []

    monkeypatch.setattr(streaming, "STREAM_QUEUE_MAX", 2)
    asyncio.run(scenario())
