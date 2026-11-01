from typing import Any

import pytest

from spotcool_contracts import Zone
from spotcool_contracts.events import CalendarData, ComplaintCueData, ScenarioStateData
from spotcool_world.bus import InProcessBus, InProcessSubscription
from spotcool_world.frames import (
    CalendarFrame,
    ComplaintCueFrame,
    Frame,
    ScenarioStateFrame,
    TickFrame,
)
from spotcool_world.processors import WORLD_FRAMES_TOPIC, FrameDirection, FramePublisher
from spotcool_world.testing import CaptureProcessor, run_test


async def published(bus: InProcessBus, subscription: InProcessSubscription) -> list[Any]:
    await bus.publish(subscription.topic, "end")  # everything before it has been delivered
    messages: list[Any] = []
    async for message in subscription:
        if message == "end":
            return messages
        messages.append(message)
    return messages


def calendar() -> CalendarFrame:
    return CalendarFrame(data=CalendarData(room="meeting-south", items=[]), sim_time_s=0.0)


@pytest.mark.asyncio
async def test_it_publishes_the_chosen_frames_and_passes_every_frame_on() -> None:
    bus = InProcessBus()
    world = bus.subscribe(WORLD_FRAMES_TOPIC)
    cue_data = ComplaintCueData(zone=Zone.SOU, text="Cold.", channel="chat", delivered=False)
    frames: list[Frame] = [
        calendar(),
        ScenarioStateFrame(state=ScenarioStateData(zones=[])),
        TickFrame(index=0, step_s=900.0, sim_time_s=0.0),
        ComplaintCueFrame(data=cue_data, audio=None),
    ]
    publisher = FramePublisher(bus, (CalendarFrame, ComplaintCueFrame))
    down, up = await run_test(publisher, frames_to_send=frames)
    assert [id(f) for f in down] == [id(f) for f in frames]
    assert up == []
    messages = await published(bus, world)
    assert [id(m) for m in messages] == [id(frames[0]), id(frames[3])]  # the frames themselves


@pytest.mark.asyncio
async def test_upstream_frames_are_published_and_passed_on_too() -> None:
    bus = InProcessBus()
    world = bus.subscribe("custom")
    publisher = FramePublisher(bus, (CalendarFrame,), "custom")
    above: list[Frame] = []
    CaptureProcessor(FrameDirection.UPSTREAM, above).link(publisher)
    frame = calendar()
    await publisher.process_frame(frame, FrameDirection.UPSTREAM)
    assert above == [frame]
    assert await published(bus, world) == [frame]


def test_the_world_topic_is_named_world() -> None:
    assert WORLD_FRAMES_TOPIC == "world"
