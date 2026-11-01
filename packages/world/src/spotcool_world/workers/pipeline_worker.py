"""PipelineWorker: runs one pipeline from its StartFrame to its EndFrame."""

import asyncio
import itertools
from collections.abc import Iterable

from loguru import logger

from spotcool_world.bus import InProcessBus
from spotcool_world.frames import CancelFrame, EndFrame, ErrorFrame, Frame, StartFrame
from spotcool_world.pipeline.params import PipelineParams
from spotcool_world.pipeline.pipeline import PipelineSink, PipelineSource, link_all
from spotcool_world.processors.frame_processor import (
    FrameDirection,
    FrameProcessor,
    FrameProcessorSetup,
)
from spotcool_world.utils.task_manager import TaskManager
from spotcool_world.workers.base_worker import BaseWorker, WorkerState

_URGENT = 0
_NORMAL = 1

_QueueItem = tuple[int, int, Frame | None, FrameDirection]
"""(priority, sequence, frame, direction); a ``None`` frame means an EndFrame left the end."""


class PipelineWorker(BaseWorker):
    """Runs a pipeline: setup, ``StartFrame``, queued frames, then ``EndFrame`` or cancel.

    ``run()`` sets every processor up, pushes the ``StartFrame`` (the first queued frame if it
    is one, otherwise built from ``params``), then pushes queued frames in order. The run
    finishes when an ``EndFrame`` leaves the bottom of the pipeline, or is cancelled when a
    ``CancelFrame`` is queued, pushed up to the top, sent by ``stop()``, or follows a fatal
    ``ErrorFrame`` reaching the top. On cancel the ``CancelFrame`` is pushed through the whole
    pipeline so every processor stops its background work. Queued frames left over are dropped.

    Events:
        on_pipeline_started(worker, start_frame): The ``StartFrame`` went through.
        on_pipeline_finished(worker, frame): The run ended; ``frame`` is the ``EndFrame`` or
            ``CancelFrame`` that ended it.
        on_pipeline_error(worker, error_frame): An ``ErrorFrame`` reached the top.

    Args:
        pipeline: The processor to run, usually a ``Pipeline``.
        params: Run parameters.
        name: Unique worker name.
    """

    def __init__(
        self,
        pipeline: FrameProcessor,
        *,
        params: PipelineParams | None = None,
        name: str | None = None,
    ) -> None:
        super().__init__(name=name)
        self._pipeline = pipeline
        self._params = params or PipelineParams()
        self._source = PipelineSource(self._on_upstream, name=f"{self.name}::Source")
        self._sink = PipelineSink(self._on_downstream, name=f"{self.name}::Sink")
        link_all([self._source, pipeline, self._sink])
        self._queue: asyncio.PriorityQueue[_QueueItem] = asyncio.PriorityQueue()
        self._sequence = itertools.count()
        self._start: StartFrame | None = None
        self._end: EndFrame | None = None
        for event in ("on_pipeline_started", "on_pipeline_finished", "on_pipeline_error"):
            self.register_event_handler(event)

    @property
    def pipeline(self) -> FrameProcessor:
        """The pipeline this worker runs."""
        return self._pipeline

    @property
    def params(self) -> PipelineParams:
        """The run parameters."""
        return self._params

    async def queue_frame(
        self, frame: Frame, direction: FrameDirection = FrameDirection.DOWNSTREAM
    ) -> None:
        """Queue a frame to push once the run has started.

        Downstream frames enter at the top, upstream frames at the bottom. A ``CancelFrame``
        jumps the queue.

        Args:
            frame: The frame.
            direction: Where it enters.
        """
        first = self._start is None and self._queue.empty() and self._state is WorkerState.IDLE
        if first and isinstance(frame, StartFrame):
            self._start = frame
            return
        self._put(_URGENT if isinstance(frame, CancelFrame) else _NORMAL, frame, direction)

    async def queue_frames(
        self, frames: Iterable[Frame], direction: FrameDirection = FrameDirection.DOWNSTREAM
    ) -> None:
        """Queue several frames in order.

        Args:
            frames: The frames.
            direction: Where they enter.
        """
        for frame in frames:
            await self.queue_frame(frame, direction)

    async def stop(self, reason: str = "") -> None:
        """Cancel the run: a ``CancelFrame`` goes through the pipeline ahead of queued frames.

        Args:
            reason: Carried on the ``CancelFrame``.
        """
        self._put(_URGENT, CancelFrame(reason=reason), FrameDirection.DOWNSTREAM)

    async def run(self) -> None:
        """Run the pipeline to completion. See the class docstring for the lifecycle."""
        if self._state is not WorkerState.IDLE:
            raise RuntimeError(f"{self.name} has already run")
        self._state = WorkerState.RUNNING
        setup = FrameProcessorSetup(
            clock=self._params.clock,
            bus=self.bus or InProcessBus(),
            observers=list(self._params.observers),
            task_manager=TaskManager(),
        )
        processors = (self._source, self._pipeline, self._sink)
        try:
            for processor in processors:
                await processor.setup(setup)
            start = self._start or self._params.start_frame()
            await self._source.process_frame(start, FrameDirection.DOWNSTREAM)
            await self._call_event_handler("on_pipeline_started", start)
            final = await self._feed()
            self._state = (
                WorkerState.CANCELLED if isinstance(final, CancelFrame) else WorkerState.FINISHED
            )
            logger.info(f"{self.name} {self._state.value}: {getattr(final, 'reason', '')}")
            await self._call_event_handler("on_pipeline_finished", final)
        except asyncio.CancelledError:
            self._state = WorkerState.CANCELLED
            raise
        except Exception:
            self._state = WorkerState.FAILED
            raise
        finally:
            for processor in processors:
                await processor.cleanup()
            await setup.task_manager.cancel_all()
            await self.cleanup()

    async def _feed(self) -> EndFrame | CancelFrame:
        while True:
            _, _, frame, direction = await self._queue.get()
            if frame is None:
                assert self._end is not None
                return self._end
            if isinstance(frame, CancelFrame):
                await self._source.process_frame(frame, FrameDirection.DOWNSTREAM)
                return frame
            entry = self._source if direction is FrameDirection.DOWNSTREAM else self._sink
            await entry.process_frame(frame, direction)

    def _put(self, priority: int, frame: Frame | None, direction: FrameDirection) -> None:
        self._queue.put_nowait((priority, next(self._sequence), frame, direction))

    async def _on_downstream(self, frame: Frame) -> None:
        if isinstance(frame, EndFrame) and self._end is None:
            self._end = frame
            self._put(_URGENT, None, FrameDirection.DOWNSTREAM)

    async def _on_upstream(self, frame: Frame) -> None:
        if isinstance(frame, ErrorFrame):
            await self._call_event_handler("on_pipeline_error", frame)
            if frame.fatal and self._params.fatal_error_cancels:
                self._put(_URGENT, CancelFrame(reason=frame.message), FrameDirection.DOWNSTREAM)
        elif isinstance(frame, CancelFrame):
            self._put(_URGENT, frame, FrameDirection.DOWNSTREAM)
