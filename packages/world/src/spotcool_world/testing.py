"""Test helpers: run one processor in a real pipeline and see what comes out of it."""

from collections.abc import Sequence

from spotcool_world.frames import EndFrame, Frame
from spotcool_world.pipeline.params import PipelineParams
from spotcool_world.pipeline.pipeline import Pipeline
from spotcool_world.processors.frame_processor import FrameDirection, FrameProcessor
from spotcool_world.runner.runner import WorkerRunner
from spotcool_world.workers.pipeline_worker import PipelineWorker


class CaptureProcessor(FrameProcessor):
    """Records the frames travelling in one direction and passes every frame on.

    Args:
        direction: The direction to record.
        frames: List the recorded frames are appended to.
        name: Display name.
    """

    def __init__(
        self, direction: FrameDirection, frames: list[Frame], *, name: str | None = None
    ) -> None:
        super().__init__(name=name)
        self._direction = direction
        self._frames = frames

    async def process_frame(self, frame: Frame, direction: FrameDirection) -> None:
        """Record the frame if it travels the recorded direction, then pass it on."""
        await super().process_frame(frame, direction)
        if direction is self._direction:
            self._frames.append(frame)
        await self.push_frame(frame, direction)


async def run_test(
    processor: FrameProcessor,
    *,
    frames_to_send: Sequence[Frame],
    send_start: bool = True,
    send_end: bool = True,
    params: PipelineParams | None = None,
) -> tuple[list[Frame], list[Frame]]:
    """Run ``processor`` between two capture processors and return what came out.

    The processor runs inside a real ``PipelineWorker`` and ``WorkerRunner``, so the lifecycle
    is the production one. ``frames_to_send`` are pushed downstream in order.

    Args:
        processor: The processor (or pipeline) under test.
        frames_to_send: Frames to push downstream after the ``StartFrame``.
        send_start: Send a ``StartFrame`` built from ``params`` first. When False, start
            ``frames_to_send`` with your own ``StartFrame`` (it is then returned like any
            other frame); otherwise the worker builds a default one.
        send_end: Send an ``EndFrame`` last and finish when it comes out. When False, the
            processor must end the run itself (an ``EndFrame`` or a cancel).
        params: Run parameters (clock, observers, ...).

    Returns:
        ``(downstream, upstream)``: the frames that left the processor at its bottom and at
        its top, in order, without the Start and End frames this function sent.
    """
    params = params or PipelineParams(run_id="test")
    downstream: list[Frame] = []
    upstream: list[Frame] = []
    pipeline = Pipeline(
        [
            CaptureProcessor(FrameDirection.UPSTREAM, upstream, name="test::CaptureUp"),
            processor,
            CaptureProcessor(FrameDirection.DOWNSTREAM, downstream, name="test::CaptureDown"),
        ]
    )
    worker = PipelineWorker(pipeline, params=params, name="test::Worker")
    sent: set[int] = set()
    if send_start:
        start = params.start_frame()
        sent.add(start.id)
        await worker.queue_frame(start)
    await worker.queue_frames(frames_to_send)
    if send_end:
        end = EndFrame(reason="test finished")
        sent.add(end.id)
        await worker.queue_frame(end)
    await WorkerRunner(handle_sigint=False).run(worker)
    return (
        [f for f in downstream if f.id not in sent],
        [f for f in upstream if f.id not in sent],
    )
