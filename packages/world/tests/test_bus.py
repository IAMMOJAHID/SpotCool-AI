import asyncio

import pytest

from spotcool_contracts.events import EventEnvelope, EventSource, EventType, MetricsData
from spotcool_world.bus import InProcessBus
from spotcool_world.frames import DataFrame, EventFrame
from spotcool_world.processors import BusPublisher
from spotcool_world.testing import run_test


@pytest.mark.asyncio
async def test_every_subscriber_of_a_topic_gets_every_message() -> None:
    bus = InProcessBus()
    first, second = bus.subscribe("events"), bus.subscribe("events")
    other = bus.subscribe("other")
    await bus.publish("events", 1)
    await bus.publish("events", 2)
    assert [await anext(first), await anext(first)] == [1, 2]
    assert [await anext(second), await anext(second)] == [1, 2]
    other.close()
    assert [m async for m in other] == []


@pytest.mark.asyncio
async def test_full_queue_drops_the_oldest_and_counts_it() -> None:
    bus = InProcessBus()
    slow = bus.subscribe("events", maxsize=2)
    for message in (1, 2, 3):
        await bus.publish("events", message)
    assert slow.dropped == 1
    assert [await anext(slow), await anext(slow)] == [2, 3]


@pytest.mark.asyncio
async def test_close_ends_iteration_and_wakes_a_waiting_reader() -> None:
    bus = InProcessBus()
    subscription = bus.subscribe("events")
    received: list[int] = []

    async def read() -> None:
        async for message in subscription:
            received.append(message)

    reader = asyncio.ensure_future(read())
    await bus.publish("events", 1)
    await asyncio.sleep(0.01)
    subscription.close()
    await asyncio.wait_for(reader, timeout=1.0)
    assert received == [1]
    assert subscription.closed
    assert bus.subscriber_count("events") == 0
    await bus.publish("events", 2)  # no subscribers left: nothing happens


def test_maxsize_must_be_positive() -> None:
    with pytest.raises(ValueError, match="maxsize"):
        InProcessBus().subscribe("events", maxsize=0)


@pytest.mark.asyncio
async def test_bus_publisher_publishes_envelopes_and_passes_frames_on() -> None:
    bus = InProcessBus()
    subscription = bus.subscribe("events")
    envelope = EventEnvelope.build(
        EventType.METRICS, EventSource.WORLD, MetricsData(name="latency", value=0.1, unit="s")
    )
    frames = [EventFrame(envelope=envelope), DataFrame()]
    down, _ = await run_test(BusPublisher(bus), frames_to_send=frames)
    assert down == frames
    assert await anext(subscription) is envelope
    assert bus.subscriber_count("events") == 1
