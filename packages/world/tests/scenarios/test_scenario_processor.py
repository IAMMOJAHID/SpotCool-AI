from typing import Any

import pytest
from loguru import logger

from spotcool_contracts import Zone
from spotcool_contracts.events import ComplaintCueData
from spotcool_world.clocks import SimClock
from spotcool_world.frames import (
    CalendarFrame,
    CancelFrame,
    ComplaintCueFrame,
    DataFrame,
    ErrorFrame,
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


def chat_cue_at_the_start() -> CompiledScenario:
    """07:00-08:00 with one chat cue at 07:00 (and an audio name it must not carry)."""
    zones = {zone.value: [{"at": "07:00", "scene": "empty", "people": 0}] for zone in Zone}
    cue = {"type": "complaint", "zone": "cor", "at": "07:00", "channel": "chat"}
    cue |= {"text": "Too warm.", "audio": "ignored.wav"}
    data = {"name": "early", "day": 199, "start": "07:00", "end": "08:00", "zones": zones}
    return compile_scenario(Scenario.model_validate(data | {"events": [cue]}), step_s=STEP_S)


def at(clock: str) -> float:
    hours, minutes = clock.split(":")
    return 199 * 86_400 + int(hours) * 3600 + int(minutes) * 60


def ticks(scenario: CompiledScenario) -> list[TickFrame]:
    count = int((scenario.end_s - scenario.start_s) // STEP_S)
    return [
        TickFrame(index=i, step_s=STEP_S, sim_time_s=scenario.start_s + i * STEP_S)
        for i in range(count)
    ]


def params(start_s: float, **kwargs: Any) -> PipelineParams:
    """Run parameters whose StartFrame starts the run at ``start_s``."""
    return PipelineParams(clock=SimClock(start_s, STEP_S), run_id="t", **kwargs)


async def play(
    scenario: CompiledScenario,
    frames: list[Frame],
    *,
    start_s: float | None = None,
    **kwargs: Any,
) -> tuple[list[Frame], list[Frame]]:
    """Run a ScenarioProcessor; the run starts with the scenario unless ``start_s`` says else."""
    start = scenario.start_s if start_s is None else start_s
    processor = ScenarioProcessor(scenario)
    return await run_test(processor, frames_to_send=frames, params=params(start, **kwargs))


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
    down, up = await play(scenario, [first])
    assert kinds(down) == [ScenarioStateFrame, CalendarFrame, TickFrame]
    assert down[-1] is first
    assert {frame.sim_time_s for frame in down} == {first.sim_time_s}
    assert up == []


@pytest.mark.asyncio
async def test_the_hybrid_tuesday_tick_by_tick() -> None:
    scenario = tuesday()
    frames = ticks(scenario)
    down, up = await play(scenario, list(frames))
    assert up == []

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
    first, _ = await play(tuesday(), list(ticks(tuesday())))
    again, _ = await play(tuesday(), list(ticks(tuesday())))
    assert content(first) == content(again)


@pytest.mark.asyncio
async def test_a_chat_cue_carries_no_audio() -> None:
    scenario = chat_cue_at_the_start()
    down, _ = await play(scenario, list(ticks(scenario)[:1]))
    [frame] = [f for f in down if isinstance(f, ComplaintCueFrame)]
    assert (frame.data.channel, frame.audio) == ("chat", None)


@pytest.mark.asyncio
async def test_other_frames_pass_through_unchanged() -> None:
    other = DataFrame()
    scenario = tuesday()
    first = ticks(scenario)[0]
    down, _ = await play(scenario, [other, first])
    assert down[0] is other
    assert kinds(down[1:]) == [ScenarioStateFrame, CalendarFrame, TickFrame]

    processor = ScenarioProcessor(scenario)
    above: list[Frame] = []
    CaptureProcessor(FrameDirection.UPSTREAM, above).link(processor)
    await processor.process_frame(first, FrameDirection.UPSTREAM)
    assert above == [first]  # an upstream tick is not played


@pytest.mark.asyncio
async def test_a_new_start_frame_starts_the_calendar_and_the_cues_afresh() -> None:
    scenario = chat_cue_at_the_start()
    restart = params(scenario.start_s).start_frame()
    down, up = await play(scenario, [ticks(scenario)[0], restart, ticks(scenario)[0]])
    assert up == []
    second = down.index(restart)
    for run in (down[:second], down[second + 1 :]):  # the 07:00 cue fires once in each run
        assert kinds(run) == [ScenarioStateFrame, CalendarFrame, ComplaintCueFrame, TickFrame]


@pytest.mark.asyncio
async def test_a_run_that_starts_before_the_scenario_is_a_fatal_error() -> None:
    scenario = tuesday()
    early = scenario.start_s - 3600
    _, up = await play(scenario, [], start_s=early, fatal_error_cancels=False)
    [error] = up
    assert isinstance(error, ErrorFrame) and error.fatal
    assert (
        error.message == "the run starts at 06:00, before scenario hybrid_tuesday starts at 07:00"
    )

    down, _ = await play(scenario, list(ticks(scenario)[:1]), start_s=early)
    assert kinds(down) == [CancelFrame]  # the fatal error cancels the run before its first tick


@pytest.mark.asyncio
async def test_a_run_that_starts_later_warns_that_earlier_cues_will_not_fire() -> None:
    scenario = chat_cue_at_the_start()
    late = scenario.start_s + STEP_S
    logs: list[str] = []
    sink = logger.add(logs.append, level="WARNING", format="{message}")
    try:
        down, up = await play(
            scenario, [TickFrame(index=0, step_s=STEP_S, sim_time_s=late)], start_s=late
        )
    finally:
        logger.remove(sink)
    assert up == []
    assert kinds(down) == [ScenarioStateFrame, CalendarFrame, TickFrame]  # no 07:00 cue
    warning = "the run starts at 07:15, after scenario early starts at 07:00; complaints before "
    assert any(f"{warning}07:15 will not fire" in line for line in logs)


@pytest.mark.asyncio
async def test_a_tick_before_the_start_is_a_fatal_error_and_passes_on_alone() -> None:
    scenario = tuesday()
    early = TickFrame(index=0, step_s=STEP_S, sim_time_s=scenario.start_s - STEP_S)
    down, up = await play(scenario, [early], fatal_error_cancels=False)
    assert len(down) == 1 and down[0] is early
    [error] = up
    assert isinstance(error, ErrorFrame) and error.fatal
    assert error.message == "tick at 06:45 is before scenario hybrid_tuesday starts at 07:00"
