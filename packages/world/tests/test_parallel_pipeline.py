import asyncio

import pytest

from spotcool_world.frames import DataFrame, EndFrame, ErrorFrame, Frame, StartFrame, TickFrame
from spotcool_world.pipeline import ParallelPipeline, Pipeline
from spotcool_world.processors import FrameDirection, FrameProcessor
from spotcool_world.testing import CaptureProcessor, run_test


class FakePlant(FrameProcessor):
    """On each tick, waits ``delay_s`` (a slow plant), then emits a result frame."""

    def __init__(self, label: str, delay_s: float) -> None:
        super().__init__(name=label)
        self._delay_s = delay_s

    async def process_frame(self, frame: Frame, direction: FrameDirection) -> None:
        await super().process_frame(frame, direction)
        if isinstance(frame, TickFrame):
            await asyncio.sleep(self._delay_s)
            await self.push_frame(DataFrame(metadata={"plant": self.name}))
        await self.push_frame(frame, direction)


class Swallow(FrameProcessor):
    """Passes everything except data frames."""

    async def process_frame(self, frame: Frame, direction: FrameDirection) -> None:
        await super().process_frame(frame, direction)
        if not isinstance(frame, DataFrame):
            await self.push_frame(frame, direction)


class Complainer(FrameProcessor):
    """Reports a non-fatal error on every tick."""

    async def process_frame(self, frame: Frame, direction: FrameDirection) -> None:
        await super().process_frame(frame, direction)
        if isinstance(frame, TickFrame):
            await self.push_error("sensor glitch")
        await self.push_frame(frame, direction)


class Bouncer(FrameProcessor):
    """For every data frame going down, sends one new frame back up."""

    async def process_frame(self, frame: Frame, direction: FrameDirection) -> None:
        await super().process_frame(frame, direction)
        if isinstance(frame, DataFrame) and direction is FrameDirection.DOWNSTREAM:
            await self.push_frame(DataFrame(metadata={"reply": True}), FrameDirection.UPSTREAM)
        await self.push_frame(frame, direction)


def tick(index: int) -> TickFrame:
    return TickFrame(index=index, step_s=900.0, sim_time_s=index * 900.0)


@pytest.mark.asyncio
@pytest.mark.parametrize("delays", [(0.03, 0.0), (0.0, 0.03)])
async def test_outputs_come_in_branch_order(delays: tuple[float, float]) -> None:
    parallel = ParallelPipeline(
        [FakePlant("spotcool", delays[0])], [FakePlant("baseline", delays[1])]
    )
    ticks = [tick(0), tick(1)]
    down, _ = await run_test(parallel, frames_to_send=ticks)
    labels = [f.metadata.get("plant", f.name) for f in down]
    assert labels == ["spotcool", "baseline", ticks[0].name, "spotcool", "baseline", ticks[1].name]


@pytest.mark.asyncio
async def test_branches_run_concurrently() -> None:
    parallel = ParallelPipeline([FakePlant("a", 0.1)], [FakePlant("b", 0.1)])
    loop = asyncio.get_running_loop()
    began = loop.time()
    await run_test(parallel, frames_to_send=[tick(0)])
    assert loop.time() - began < 0.19  # two 0.1 s branches overlapped


@pytest.mark.asyncio
async def test_lifecycle_frames_leave_exactly_once() -> None:
    seen: list[Frame] = []
    parallel = ParallelPipeline([FakePlant("a", 0.0)], [FakePlant("b", 0.0)], [])
    pipeline = Pipeline([parallel, CaptureProcessor(FrameDirection.DOWNSTREAM, seen)])
    await run_test(pipeline, frames_to_send=[tick(0)])
    assert sum(isinstance(f, StartFrame) for f in seen) == 1
    assert sum(isinstance(f, TickFrame) for f in seen) == 1
    assert sum(isinstance(f, EndFrame) for f in seen) == 1


@pytest.mark.asyncio
async def test_frame_consumed_by_every_branch_does_not_leave() -> None:
    down, _ = await run_test(
        ParallelPipeline([Swallow()], [Swallow()]), frames_to_send=[DataFrame()]
    )
    assert down == []


@pytest.mark.asyncio
async def test_frame_passed_by_one_branch_leaves_once() -> None:
    frame = DataFrame()
    down, _ = await run_test(ParallelPipeline([Swallow()], []), frames_to_send=[frame])
    assert down == [frame]


@pytest.mark.asyncio
async def test_upstream_frames_entering_from_below_are_deduplicated() -> None:
    pipeline = Pipeline([ParallelPipeline([], [], []), Bouncer()])
    _, up = await run_test(pipeline, frames_to_send=[DataFrame()])
    assert len(up) == 1
    assert up[0].metadata == {"reply": True}


@pytest.mark.asyncio
async def test_branch_errors_go_upstream() -> None:
    _, up = await run_test(
        ParallelPipeline([Complainer()], [Complainer()]), frames_to_send=[tick(0)]
    )
    assert [type(f) for f in up] == [ErrorFrame, ErrorFrame]  # two distinct errors, both kept


def test_needs_a_branch() -> None:
    with pytest.raises(ValueError, match="at least one branch"):
        ParallelPipeline()
