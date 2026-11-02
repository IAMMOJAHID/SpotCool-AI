"""RunStatusProcessor: running after the start, then finished or failed before the end frame."""

import pytest

from spotcool_contracts.events import RunStatusData
from spotcool_world.frames import (
    CancelFrame,
    EndFrame,
    Frame,
    RunStatusFrame,
    StartFrame,
    TickFrame,
)
from spotcool_world.pipeline import Pipeline
from spotcool_world.processors import FrameDirection, FrameProcessor
from spotcool_world.processors.run_status import RunStatusProcessor
from spotcool_world.testing import run_test

TICK = TickFrame(index=0, step_s=900.0, sim_time_s=0.0)


def status() -> RunStatusProcessor:
    return RunStatusProcessor("run-1", "rc", "hybrid_tuesday")


def kinds(frames: list[Frame]) -> list[str]:
    """Each frame's class, with a run status's status and message."""
    shown = []
    for frame in frames:
        if isinstance(frame, RunStatusFrame):
            shown.append(f"{frame.data.status}: {frame.data.message}")
        else:
            shown.append(type(frame).__name__)
    return shown


class FailOn(FrameProcessor):
    """Reports a fatal error when a frame of the given type passes."""

    def __init__(self, kind: type[Frame]) -> None:
        super().__init__()
        self._kind = kind

    async def process_frame(self, frame: Frame, direction: FrameDirection) -> None:
        await super().process_frame(frame, direction)
        if isinstance(frame, self._kind) and direction is FrameDirection.DOWNSTREAM:
            await self.push_error("the plant broke", fatal=True)
        await self.push_frame(frame, direction)


@pytest.mark.asyncio
async def test_a_run_that_reaches_its_end_is_running_then_finished() -> None:
    end = EndFrame(reason="end time reached", sim_time_s=900.0)
    down, _ = await run_test(status(), frames_to_send=[TICK, end], send_end=False)
    assert kinds(down) == ["running: None", "TickFrame", "finished: None", "EndFrame"]
    running, finished = down[0], down[2]
    assert isinstance(running, RunStatusFrame) and isinstance(finished, RunStatusFrame)
    assert running.data == RunStatusData(
        run_id="run-1", status="running", plant="rc", scenario="hybrid_tuesday"
    )
    assert (running.sim_time_s, finished.sim_time_s) == (0.0, 900.0)


@pytest.mark.asyncio
async def test_a_cancelled_run_is_failed_with_the_cancels_reason() -> None:
    down, _ = await run_test(
        status(), frames_to_send=[CancelFrame(reason="SIGINT")], send_end=False
    )
    assert kinds(down) == ["running: None", "failed: cancelled: SIGINT", "CancelFrame"]


@pytest.mark.asyncio
async def test_a_fatal_error_fails_the_run_with_its_message() -> None:
    down, up = await run_test(
        Pipeline([status(), FailOn(TickFrame)]), frames_to_send=[TICK], send_end=False
    )
    assert kinds(down) == ["running: None", "TickFrame", "failed: the plant broke", "CancelFrame"]
    assert kinds(up) == ["ErrorFrame"]


@pytest.mark.asyncio
async def test_a_run_that_fails_to_start_is_never_running() -> None:
    down, _ = await run_test(
        Pipeline([status(), FailOn(StartFrame)]), frames_to_send=[], send_end=False
    )
    assert kinds(down) == ["failed: the plant broke", "CancelFrame"]
