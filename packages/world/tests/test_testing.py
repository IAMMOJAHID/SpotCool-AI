import pytest

from spotcool_world.frames import DataFrame, EndFrame, Frame, StartFrame
from spotcool_world.pipeline import PipelineParams
from spotcool_world.processors import FrameDirection, FrameProcessor
from spotcool_world.testing import run_test


class Echo(FrameProcessor):
    """Passes frames on, and answers every data frame with a frame upstream."""

    async def process_frame(self, frame: Frame, direction: FrameDirection) -> None:
        await super().process_frame(frame, direction)
        if isinstance(frame, DataFrame):
            await self.push_frame(DataFrame(metadata={"echo": frame.id}), FrameDirection.UPSTREAM)
        await self.push_frame(frame, direction)


class Ender(FrameProcessor):
    """Ends the run itself on the first data frame."""

    async def process_frame(self, frame: Frame, direction: FrameDirection) -> None:
        await super().process_frame(frame, direction)
        await self.push_frame(frame, direction)
        if isinstance(frame, DataFrame):
            await self.push_frame(EndFrame(reason="processor ended"))


@pytest.mark.asyncio
async def test_returns_both_directions_without_injected_frames() -> None:
    frames = [DataFrame(), DataFrame()]
    down, up = await run_test(Echo(), frames_to_send=frames)
    assert down == frames
    assert [f.metadata["echo"] for f in up] == [f.id for f in frames]


@pytest.mark.asyncio
async def test_own_start_frame_is_returned() -> None:
    start = PipelineParams(run_id="own").start_frame()
    down, _ = await run_test(Echo(), frames_to_send=[start], send_start=False)
    assert down == [start]


@pytest.mark.asyncio
async def test_processor_may_end_the_run() -> None:
    data = DataFrame()
    down, _ = await run_test(Ender(), frames_to_send=[data], send_end=False)
    assert down[0] is data
    assert isinstance(down[1], EndFrame) and down[1].reason == "processor ended"


@pytest.mark.asyncio
async def test_params_reach_the_processor() -> None:
    echo = Echo()
    params = PipelineParams(run_id="p", scenario="hybrid_tuesday")
    await run_test(echo, frames_to_send=[], params=params)
    assert echo.clock is params.clock
    assert isinstance(echo.start_frame, StartFrame)
    assert echo.start_frame.scenario == "hybrid_tuesday"
