"""Pipeline: processors chained in order, itself usable as one processor."""

from collections.abc import Awaitable, Callable, Sequence

from spotcool_world.frames import Frame
from spotcool_world.processors.frame_processor import (
    FrameDirection,
    FrameProcessor,
    FrameProcessorSetup,
)

FrameCallback = Callable[[Frame], Awaitable[None]]
"""Receives a frame that left a chain of processors."""


class PipelineSource(FrameProcessor):
    """Top edge of a chain: passes downstream frames in, hands upstream frames out.

    Args:
        upstream: Receives every frame that leaves the top of the chain going upstream.
        name: Display name.
    """

    def __init__(self, upstream: FrameCallback, *, name: str | None = None) -> None:
        super().__init__(name=name)
        self._upstream = upstream

    async def process_frame(self, frame: Frame, direction: FrameDirection) -> None:
        """Forward downstream frames into the chain; hand upstream frames to the callback."""
        await super().process_frame(frame, direction)
        if direction is FrameDirection.DOWNSTREAM:
            await self.push_frame(frame, direction)
        else:
            await self._upstream(frame)


class PipelineSink(FrameProcessor):
    """Bottom edge of a chain: passes upstream frames in, hands downstream frames out.

    Args:
        downstream: Receives every frame that leaves the bottom of the chain going downstream.
        name: Display name.
    """

    def __init__(self, downstream: FrameCallback, *, name: str | None = None) -> None:
        super().__init__(name=name)
        self._downstream = downstream

    async def process_frame(self, frame: Frame, direction: FrameDirection) -> None:
        """Forward upstream frames into the chain; hand downstream frames to the callback."""
        await super().process_frame(frame, direction)
        if direction is FrameDirection.UPSTREAM:
            await self.push_frame(frame, direction)
        else:
            await self._downstream(frame)


def link_all(processors: Sequence[FrameProcessor]) -> None:
    """Link each processor to the next one in order.

    Args:
        processors: The chain, upstream first.
    """
    for upstream, downstream in zip(processors, processors[1:], strict=False):
        upstream.link(downstream)


class Pipeline(FrameProcessor):
    """Processors run in order; the pipeline behaves as a single processor.

    A frame entering from upstream goes through every processor in order and leaves at the
    bottom to the pipeline's own downstream neighbour; frames pushed upstream inside leave at
    the top to the pipeline's upstream neighbour. ``setup`` and ``cleanup`` reach every child.

    Args:
        processors: The processors, upstream first.
        name: Display name.
    """

    def __init__(self, processors: Sequence[FrameProcessor], *, name: str | None = None) -> None:
        super().__init__(name=name)
        self._source = PipelineSource(self._leave_upstream, name=f"{self.name}::Source")
        self._sink = PipelineSink(self._leave_downstream, name=f"{self.name}::Sink")
        self._processors: list[FrameProcessor] = list(processors)
        link_all([self._source, *self._processors, self._sink])

    @property
    def processors(self) -> list[FrameProcessor]:
        """The pipeline's processors, upstream first (without the internal edges)."""
        return list(self._processors)

    async def setup(self, setup: FrameProcessorSetup) -> None:
        """Set up the pipeline and every processor in it."""
        await super().setup(setup)
        for processor in (self._source, *self._processors, self._sink):
            await processor.setup(setup)

    async def cleanup(self) -> None:
        """Clean up the pipeline and every processor in it."""
        await super().cleanup()
        for processor in (self._source, *self._processors, self._sink):
            await processor.cleanup()

    async def process_frame(self, frame: Frame, direction: FrameDirection) -> None:
        """Send a downstream frame in at the top, an upstream frame in at the bottom."""
        await super().process_frame(frame, direction)
        if direction is FrameDirection.DOWNSTREAM:
            await self._source.process_frame(frame, direction)
        else:
            await self._sink.process_frame(frame, direction)

    async def _leave_upstream(self, frame: Frame) -> None:
        await self.push_frame(frame, FrameDirection.UPSTREAM)

    async def _leave_downstream(self, frame: Frame) -> None:
        await self.push_frame(frame, FrameDirection.DOWNSTREAM)
