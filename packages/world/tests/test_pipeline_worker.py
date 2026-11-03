import asyncio
from typing import Any

import pytest

from spotcool_world.clocks import SimClock, WallPacer
from spotcool_world.frames import (
    CancelFrame,
    DataFrame,
    EndFrame,
    ErrorFrame,
    Frame,
    StartFrame,
    TickFrame,
)
from spotcool_world.pipeline import ParallelPipeline, Pipeline, PipelineParams
from spotcool_world.processors import FrameDirection, FrameProcessor, TickSource
from spotcool_world.runner import WorkerRunner
from spotcool_world.workers import PipelineWorker, WorkerState


class Recorder(FrameProcessor):
    """Records every frame it sees going down and passes it on."""

    def __init__(self) -> None:
        super().__init__()
        self.frames: list[Frame] = []

    async def process_frame(self, frame: Frame, direction: FrameDirection) -> None:
        await super().process_frame(frame, direction)
        if direction is FrameDirection.DOWNSTREAM:
            self.frames.append(frame)
        await self.push_frame(frame, direction)


class ErrorOnData(FrameProcessor):
    """Reports an error for each data frame whose metadata says so."""

    async def process_frame(self, frame: Frame, direction: FrameDirection) -> None:
        await super().process_frame(frame, direction)
        if isinstance(frame, DataFrame) and "error" in frame.metadata:
            await self.push_error("bad step", fatal=frame.metadata["error"] == "fatal")
        await self.push_frame(frame, direction)


class CancelRequester(FrameProcessor):
    """Asks the worker to cancel (an upstream CancelFrame) when it sees a data frame."""

    async def process_frame(self, frame: Frame, direction: FrameDirection) -> None:
        await super().process_frame(frame, direction)
        if isinstance(frame, DataFrame):
            await self.push_frame(CancelFrame(reason="requested"), FrameDirection.UPSTREAM)
            return
        await self.push_frame(frame, direction)


class Boom(FrameProcessor):
    async def process_frame(self, frame: Frame, direction: FrameDirection) -> None:
        await super().process_frame(frame, direction)
        if isinstance(frame, DataFrame):
            raise RuntimeError("bug in process_frame")
        await self.push_frame(frame, direction)


def attach_event_log(worker: PipelineWorker) -> list[tuple[str, Any]]:
    events: list[tuple[str, Any]] = []
    for name in ("on_pipeline_started", "on_pipeline_finished", "on_pipeline_error"):

        def handler(obj: PipelineWorker, frame: Frame, name: str = name) -> None:
            events.append((name, frame))

        worker.add_event_handler(name, handler)
    return events


async def run(worker: PipelineWorker) -> None:
    await WorkerRunner(handle_sigint=False).run(worker)


@pytest.mark.asyncio
async def test_runs_from_start_to_end() -> None:
    recorder = Recorder()
    worker = PipelineWorker(Pipeline([recorder]), params=PipelineParams(run_id="r1"))
    events = attach_event_log(worker)
    data = DataFrame()
    await worker.queue_frames([data, EndFrame(reason="done")])
    await run(worker)

    assert worker.state is WorkerState.FINISHED
    kinds = [type(f) for f in recorder.frames]
    assert kinds == [StartFrame, DataFrame, EndFrame]
    start = recorder.frames[0]
    assert isinstance(start, StartFrame) and start.run_id == "r1"
    assert [name for name, _ in events] == ["on_pipeline_started", "on_pipeline_finished"]
    assert isinstance(events[1][1], EndFrame)


@pytest.mark.asyncio
async def test_first_queued_start_frame_is_used() -> None:
    recorder = Recorder()
    worker = PipelineWorker(recorder)
    start = PipelineParams(run_id="mine").start_frame()
    await worker.queue_frames([start, EndFrame()])
    await run(worker)
    assert recorder.frames[0] is start


@pytest.mark.asyncio
async def test_stop_cancels_the_run() -> None:
    recorder = Recorder()
    worker = PipelineWorker(recorder)
    events = attach_event_log(worker)

    async def stop_soon() -> None:
        await asyncio.sleep(0.05)
        await worker.stop("operator")

    await asyncio.gather(run(worker), stop_soon())
    assert worker.state is WorkerState.CANCELLED
    final = events[-1]
    assert final[0] == "on_pipeline_finished"
    assert isinstance(final[1], CancelFrame) and final[1].reason == "operator"
    assert isinstance(recorder.frames[-1], CancelFrame)  # the cancel went through the pipeline


@pytest.mark.asyncio
async def test_queued_cancel_jumps_the_queue() -> None:
    recorder = Recorder()
    worker = PipelineWorker(recorder)
    await worker.queue_frames([DataFrame(), DataFrame(), CancelFrame(reason="now")])
    await run(worker)
    assert worker.state is WorkerState.CANCELLED
    assert [type(f) for f in recorder.frames] == [StartFrame, CancelFrame]


@pytest.mark.asyncio
async def test_non_fatal_error_is_reported_and_the_run_goes_on() -> None:
    recorder = Recorder()
    worker = PipelineWorker(Pipeline([ErrorOnData(), recorder]))
    events = attach_event_log(worker)
    await worker.queue_frames([DataFrame(metadata={"error": "soft"}), DataFrame(), EndFrame()])
    await run(worker)
    assert worker.state is WorkerState.FINISHED
    errors = [frame for name, frame in events if name == "on_pipeline_error"]
    assert len(errors) == 1 and isinstance(errors[0], ErrorFrame) and not errors[0].fatal
    assert sum(isinstance(f, DataFrame) for f in recorder.frames) == 2


@pytest.mark.asyncio
async def test_fatal_error_cancels_the_run() -> None:
    recorder = Recorder()
    worker = PipelineWorker(Pipeline([ErrorOnData(), recorder]))
    events = attach_event_log(worker)
    fatal = DataFrame(metadata={"error": "fatal"})
    await worker.queue_frames([fatal, DataFrame(), EndFrame()])
    await run(worker)
    assert worker.state is WorkerState.CANCELLED
    assert [type(f) for f in recorder.frames] == [StartFrame, DataFrame, CancelFrame]
    finished = events[-1][1]
    assert isinstance(finished, CancelFrame) and finished.reason == "bad step"


@pytest.mark.asyncio
async def test_fatal_error_can_be_left_to_the_application() -> None:
    worker = PipelineWorker(ErrorOnData(), params=PipelineParams(fatal_error_cancels=False))
    events = attach_event_log(worker)
    await worker.queue_frames([DataFrame(metadata={"error": "fatal"}), EndFrame()])
    await run(worker)
    assert worker.state is WorkerState.FINISHED
    assert any(name == "on_pipeline_error" for name, _ in events)


@pytest.mark.asyncio
async def test_processor_can_request_cancel() -> None:
    recorder = Recorder()
    worker = PipelineWorker(Pipeline([recorder, CancelRequester()]))
    await worker.queue_frames([DataFrame(), DataFrame(), EndFrame()])
    await run(worker)
    assert worker.state is WorkerState.CANCELLED
    assert [type(f) for f in recorder.frames] == [StartFrame, DataFrame, CancelFrame]


class CancelMidTick(FrameProcessor):
    """Asks for a cancel while it handles a tick, then passes the tick on."""

    async def process_frame(self, frame: Frame, direction: FrameDirection) -> None:
        await super().process_frame(frame, direction)
        if isinstance(frame, TickFrame):
            await self.push_frame(CancelFrame(reason="mid-tick"), FrameDirection.UPSTREAM)
        await self.push_frame(frame, direction)


@pytest.mark.parametrize("parallel", [True, False], ids=["below-parallel-branches", "plain"])
@pytest.mark.asyncio
async def test_a_cancel_requested_mid_tick_reaches_the_worker(parallel: bool) -> None:
    # The request travels up inside the tick task (below a ParallelPipeline, inside one of its
    # gathered branch tasks), so nothing on its way up may cancel that task.
    clock = SimClock(0.0, 900.0)
    branches = [ParallelPipeline([Pipeline([])], [Pipeline([])])] if parallel else []
    recorder = Recorder()
    pipeline = Pipeline(
        [TickSource(clock, WallPacer(None), 1000 * 900.0), *branches, CancelMidTick(), recorder]
    )
    worker = PipelineWorker(pipeline, params=PipelineParams(clock=clock))
    await asyncio.wait_for(run(worker), 2)
    assert worker.state is WorkerState.CANCELLED
    assert isinstance(recorder.frames[-1], CancelFrame) and recorder.frames[-1].reason == "mid-tick"
    assert {t for t in asyncio.all_tasks() if t is not asyncio.current_task()} == set()


@pytest.mark.asyncio
async def test_exception_in_process_frame_fails_the_run() -> None:
    worker = PipelineWorker(Boom())
    await worker.queue_frames([DataFrame(), EndFrame()])
    with pytest.raises(RuntimeError, match="bug in process_frame"):
        await run(worker)
    assert worker.state is WorkerState.FAILED


@pytest.mark.asyncio
async def test_a_worker_runs_once() -> None:
    worker = PipelineWorker(Recorder())
    await worker.queue_frame(EndFrame())
    await worker.run()
    with pytest.raises(RuntimeError, match="already run"):
        await worker.run()
