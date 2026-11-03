import asyncio
from collections.abc import Iterator

import pytest
from loguru import logger

from spotcool_world.clocks import SimClock, WallPacer
from spotcool_world.frames import DataFrame, ErrorFrame, Frame, TickFrame
from spotcool_world.observers import LogObserver, StepTimingObserver
from spotcool_world.pipeline import Pipeline, PipelineParams
from spotcool_world.processors import FrameDirection, FrameProcessor, TickSource
from spotcool_world.testing import run_test


class SlowStep(FrameProcessor):
    """Takes 20 ms of wall time per tick."""

    async def process_frame(self, frame: Frame, direction: FrameDirection) -> None:
        await super().process_frame(frame, direction)
        if isinstance(frame, TickFrame):
            await asyncio.sleep(0.02)
        await self.push_frame(frame, direction)


@pytest.fixture
def warnings() -> Iterator[list[str]]:
    messages: list[str] = []
    sink = logger.add(lambda message: messages.append(str(message)), level="INFO")
    yield messages
    logger.remove(sink)


@pytest.mark.asyncio
async def test_step_timing_measures_each_tick(warnings: list[str]) -> None:
    timing = StepTimingObserver(warn_above_s=0.01)
    clock = SimClock(0.0, 900.0)
    pipeline = Pipeline([TickSource(clock, WallPacer(None), end_time_s=2700.0), SlowStep()])
    params = PipelineParams(clock=clock, observers=[timing])
    await run_test(pipeline, frames_to_send=[], send_end=False, params=params)
    assert timing.count == 3
    assert timing.min_s is not None and timing.mean_s is not None and timing.max_s is not None
    assert 0.015 <= timing.min_s <= timing.mean_s <= timing.max_s < 1.0
    assert sum("took" in m for m in warnings) == 3


def test_step_timing_is_empty_before_the_first_tick() -> None:
    timing = StepTimingObserver()
    assert (timing.count, timing.min_s, timing.mean_s, timing.max_s) == (0, None, None, None)


class Complainer(FrameProcessor):
    async def process_frame(self, frame: Frame, direction: FrameDirection) -> None:
        await super().process_frame(frame, direction)
        if isinstance(frame, DataFrame):
            await self.push_error("glitch")
        await self.push_frame(frame, direction)


@pytest.mark.asyncio
async def test_log_observer_logs_each_chosen_frame_once(warnings: list[str]) -> None:
    observer = LogObserver((ErrorFrame,))
    pipeline = Pipeline([Pipeline([]), Complainer(), Pipeline([])])  # several hops
    await run_test(
        pipeline, frames_to_send=[DataFrame()], params=PipelineParams(observers=[observer])
    )
    logged = [m for m in warnings if "ErrorFrame#" in m and "upstream from" in m]
    assert len(logged) == 1
