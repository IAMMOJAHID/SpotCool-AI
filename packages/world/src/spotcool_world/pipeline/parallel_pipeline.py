"""ParallelPipeline: branches that handle the same frame side by side.

The two building plants (SpotCool and baseline) are branches: each tick steps both at once,
and what they produce comes out in branch order, so every run is identical.
"""

import asyncio
import functools
from collections.abc import Sequence
from contextvars import ContextVar

from spotcool_world.frames import Frame, StartFrame
from spotcool_world.pipeline.pipeline import PipelineSink, PipelineSource, link_all
from spotcool_world.processors.frame_processor import (
    FrameDirection,
    FrameProcessor,
    FrameProcessorSetup,
)
from spotcool_world.utils.seen_ids import SeenIds


class _Collection:
    """Branch outputs for one downstream frame while its branches run."""

    def __init__(self, frame_id: int, branches: int) -> None:
        self.frame_id = frame_id
        self.outputs: list[list[Frame]] = [[] for _ in range(branches)]
        self.forwarded = False
        self.closed = False


# Open collections by ParallelPipeline id. A context variable (not an attribute) keeps
# concurrent or nested entries apart: each branch task inherits its own entry's collection.
_collections: ContextVar[dict[int, _Collection] | None] = ContextVar(
    "spotcool_parallel_collections", default=None
)


class ParallelPipeline(FrameProcessor):
    """Runs every branch on each frame concurrently, with deterministic output.

    Downstream: a frame entering is handed to every branch at once (``asyncio.gather``). What
    the branches push out at their bottom is buffered per branch and, after all branches have
    finished, pushed on downstream in branch order. The incoming frame itself (matched by id)
    leaves once, after the branch outputs, provided at least one branch passed it on; a
    ``StartFrame`` leaves before them instead, so everything downstream has started before the
    branches' first data (such as a plant's initial state) arrives.

    Upstream: a frame entering from below goes into every branch at its bottom. Frames leaving
    the branches at the top are passed on immediately, deduplicated by id.

    Frames a branch pushes from a background task, outside any entry, pass straight through.

    Args:
        *branches: Each branch is a sequence of processors, upstream first.
        name: Display name.
    """

    def __init__(self, *branches: Sequence[FrameProcessor], name: str | None = None) -> None:
        super().__init__(name=name)
        if not branches:
            raise ValueError("ParallelPipeline needs at least one branch")
        self._branches: list[list[FrameProcessor]] = []
        for index, processors in enumerate(branches):
            chain = [
                PipelineSource(self._leave_upstream, name=f"{self.name}::B{index}::Source"),
                *processors,
                PipelineSink(
                    functools.partial(self._leave_downstream, index),
                    name=f"{self.name}::B{index}::Sink",
                ),
            ]
            link_all(chain)
            self._branches.append(chain)
        self._seen_upstream = SeenIds()

    async def setup(self, setup: FrameProcessorSetup) -> None:
        """Set up the parallel pipeline and every processor in every branch."""
        await super().setup(setup)
        for chain in self._branches:
            for processor in chain:
                await processor.setup(setup)

    async def cleanup(self) -> None:
        """Clean up the parallel pipeline and every processor in every branch."""
        await super().cleanup()
        for chain in self._branches:
            for processor in chain:
                await processor.cleanup()

    async def process_frame(self, frame: Frame, direction: FrameDirection) -> None:
        """Fan the frame out to every branch."""
        await super().process_frame(frame, direction)
        if direction is FrameDirection.UPSTREAM:
            await self._run_branches(frame, direction)
            return
        collection = _Collection(frame.id, len(self._branches))
        token = _collections.set({**(_collections.get() or {}), self.id: collection})
        try:
            await self._run_branches(frame, direction)
        finally:
            collection.closed = True
            _collections.reset(token)
        start_first = collection.forwarded and isinstance(frame, StartFrame)
        if start_first:
            await self.push_frame(frame, direction)
        seen = {frame.id}
        for outputs in collection.outputs:
            for output in outputs:
                if output.id not in seen:
                    seen.add(output.id)
                    await self.push_frame(output, direction)
        if collection.forwarded and not start_first:
            await self.push_frame(frame, direction)

    async def _run_branches(self, frame: Frame, direction: FrameDirection) -> None:
        entry = 0 if direction is FrameDirection.DOWNSTREAM else -1
        results = await asyncio.gather(
            *(chain[entry].process_frame(frame, direction) for chain in self._branches),
            return_exceptions=True,  # let every branch finish its step before raising
        )
        for result in results:
            if isinstance(result, BaseException):
                raise result

    async def _leave_downstream(self, index: int, frame: Frame) -> None:
        collection = (_collections.get() or {}).get(self.id)
        if collection is None or collection.closed:
            await self.push_frame(frame, FrameDirection.DOWNSTREAM)
        elif frame.id == collection.frame_id:
            collection.forwarded = True
        else:
            collection.outputs[index].append(frame)

    async def _leave_upstream(self, frame: Frame) -> None:
        if self._seen_upstream.add(frame.id):
            await self.push_frame(frame, FrameDirection.UPSTREAM)
