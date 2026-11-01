from typing import Any

import pytest

from spotcool_contracts import Zone
from spotcool_contracts.events import ComplaintCueData
from spotcool_world.frames import (
    CalendarFrame,
    ComplaintCueFrame,
    DataFrame,
    Frame,
    ScenarioStateFrame,
    TickFrame,
)
from spotcool_world.pipeline import PipelineParams
from spotcool_world.processors import FrameDirection, ScenarioProcessor
from spotcool_world.scenarios import CompiledScenario, Scenario, compile_scenario, load_scenario
from spotcool_world.testing import CaptureProcessor, run_test

STEP_S = 900.0


def tuesday() -> CompiledScenario:
    return compile_scenario(load_scenario("hybrid_tuesday").scenario, step_s=STEP_S)


def at(clock: str) -> float:
    hours, minutes = clock.split(":")
    return 199 * 86_400 + int(hours) * 3600 + int(minutes) * 60


def ticks(scenario: CompiledScenario) -> list[TickFrame]:
    count = int((scenario.end_s - scenario.start_s) // STEP_S)
    return [
        TickFrame(index=i, step_s=STEP_S, sim_time_s=scenario.start_s + i * STEP_S)
        for i in range(count)
    ]


def kinds(frames: list[Frame]) -> list[type[Frame]]:
    return [type(frame) for frame in frames]


def content(frames: list[Frame]) -> list[tuple[str, float | None, Any]]:
    """Everything a frame says, without its process-wide id and wall-clock creation time."""

    def payload(frame: Frame) -> Any:
        if isinstance(frame, ScenarioStateFrame):
            return frame.state.model_dump()
        if isinstance(frame, CalendarFrame):
            return frame.data.model_dump()
        if isinstance(frame, ComplaintCueFrame):
            return frame.data.model_dump(), frame.audio
        return frame.index if isinstance(frame, TickFrame) else None

    return [(type(f).__name__, f.sim_time_s, payload(f)) for f in frames]


@pytest.mark.asyncio
async def test_the_first_tick_brings_state_then_calendar_then_the_tick() -> None:
    scenario = tuesday()
    first = ticks(scenario)[0]
    down, up = await run_test(ScenarioProcessor(scenario), frames_to_send=[first])
    assert kinds(down) == [ScenarioStateFrame, CalendarFrame, TickFrame]
    assert down[-1] is first
    assert {frame.sim_time_s for frame in down} == {first.sim_time_s}
    assert up == []


@pytest.mark.asyncio
async def test_the_hybrid_tuesday_tick_by_tick() -> None:
    scenario = tuesday()
    frames = ticks(scenario)
    down, _ = await run_test(ScenarioProcessor(scenario), frames_to_send=frames)

    assert [f for f in down if isinstance(f, TickFrame)] == frames
    states = [f for f in down if isinstance(f, ScenarioStateFrame)]
    assert [f.sim_time_s for f in states] == [f.sim_time_s for f in frames]
    assert all(f.state == scenario.state_at(f.sim_time_s or 0) for f in states)

    calendars = [f for f in down if isinstance(f, CalendarFrame)]
    assert [(f.sim_time_s, f.data.room) for f in calendars] == [
        (at("07:00"), "meeting-south"),
        (at("13:45"), "meeting-south"),
    ]
    assert [len(f.data.items) for f in calendars] == [0, 1]

    [cue] = [f for f in down if isinstance(f, ComplaintCueFrame)]
    assert cue.sim_time_s == at("14:15")
    assert cue.audio == "south_complaint.wav"
    assert cue.data == ComplaintCueData(
        zone=Zone.SOU, text="It's freezing in this meeting room.", channel="voice", delivered=False
    )
    tick = down.index(frames[29])  # 14:15
    assert kinds(down[tick - 2 : tick + 1]) == [ScenarioStateFrame, ComplaintCueFrame, TickFrame]
    tick = down.index(frames[27])  # 13:45
    assert kinds(down[tick - 2 : tick + 1]) == [ScenarioStateFrame, CalendarFrame, TickFrame]


@pytest.mark.asyncio
async def test_the_same_ticks_give_the_same_frames() -> None:
    first, _ = await run_test(ScenarioProcessor(tuesday()), frames_to_send=ticks(tuesday()))
    again, _ = await run_test(ScenarioProcessor(tuesday()), frames_to_send=ticks(tuesday()))
    assert content(first) == content(again)


@pytest.mark.asyncio
async def test_a_chat_cue_carries_no_audio() -> None:
    zones = {zone.value: [{"at": "07:00", "scene": "empty", "people": 0}] for zone in Zone}
    cue = {"type": "complaint", "zone": "cor", "at": "07:00", "channel": "chat"}
    cue |= {"text": "Too warm.", "audio": "ignored.wav"}
    data = {"name": "chat", "day": 199, "start": "07:00", "end": "08:00", "zones": zones}
    scenario = compile_scenario(Scenario.model_validate(data | {"events": [cue]}), step_s=STEP_S)
    down, _ = await run_test(ScenarioProcessor(scenario), frames_to_send=ticks(scenario)[:1])
    [frame] = [f for f in down if isinstance(f, ComplaintCueFrame)]
    assert (frame.data.channel, frame.audio) == ("chat", None)


@pytest.mark.asyncio
async def test_other_frames_pass_through_unchanged() -> None:
    other = DataFrame()
    scenario = tuesday()
    first = ticks(scenario)[0]
    down, _ = await run_test(ScenarioProcessor(scenario), frames_to_send=[other, first])
    assert down[0] is other
    assert kinds(down[1:]) == [ScenarioStateFrame, CalendarFrame, TickFrame]

    processor = ScenarioProcessor(scenario)
    above: list[Frame] = []
    CaptureProcessor(FrameDirection.UPSTREAM, above).link(processor)
    await processor.process_frame(first, FrameDirection.UPSTREAM)
    assert above == [first]  # an upstream tick is not played


@pytest.mark.asyncio
async def test_a_new_start_frame_starts_the_calendar_and_cues_afresh() -> None:
    scenario = tuesday()
    first = ticks(scenario)[0]
    again = TickFrame(index=0, step_s=STEP_S, sim_time_s=scenario.start_s)
    restart = PipelineParams(run_id="again").start_frame()
    down, _ = await run_test(ScenarioProcessor(scenario), frames_to_send=[first, restart, again])
    calendars = [f for f in down if isinstance(f, CalendarFrame)]
    assert len(calendars) == 2
