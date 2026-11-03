import pytest

from spotcool_world.frames import DataFrame, Frame
from spotcool_world.pipeline import Pipeline
from spotcool_world.processors import FrameDirection, FrameProcessor
from spotcool_world.testing import run_test


class Tag(FrameProcessor):
    """Appends ``name:direction`` to ``frame.metadata["path"]`` and passes the frame on."""

    def __init__(self, label: str) -> None:
        super().__init__(name=label)
        self.cleaned_up = False

    async def process_frame(self, frame: Frame, direction: FrameDirection) -> None:
        await super().process_frame(frame, direction)
        arrow = "down" if direction is FrameDirection.DOWNSTREAM else "up"
        frame.metadata.setdefault("path", []).append(f"{self.name}:{arrow}")
        await self.push_frame(frame, direction)

    async def cleanup(self) -> None:
        await super().cleanup()
        self.cleaned_up = True


class Bouncer(FrameProcessor):
    """For every data frame going down, also sends a new data frame back up."""

    def __init__(self) -> None:
        super().__init__()
        self.sent: list[Frame] = []

    async def process_frame(self, frame: Frame, direction: FrameDirection) -> None:
        await super().process_frame(frame, direction)
        if isinstance(frame, DataFrame) and direction is FrameDirection.DOWNSTREAM:
            reply = DataFrame()
            self.sent.append(reply)
            await self.push_frame(reply, FrameDirection.UPSTREAM)
        await self.push_frame(frame, direction)


@pytest.mark.asyncio
async def test_downstream_through_nested_pipelines() -> None:
    frame = DataFrame()
    inner = Pipeline([Tag("b"), Tag("c")])
    down, _ = await run_test(Pipeline([Tag("a"), inner, Tag("d")]), frames_to_send=[frame])
    assert down == [frame]
    assert frame.metadata["path"] == ["a:down", "b:down", "c:down", "d:down"]


@pytest.mark.asyncio
async def test_upstream_frames_leave_at_the_top() -> None:
    frame = DataFrame()
    bouncer = Bouncer()
    pipeline = Pipeline([Tag("a"), Pipeline([Tag("b"), bouncer]), Tag("c")])
    down, up = await run_test(pipeline, frames_to_send=[frame])
    assert down == [frame]
    assert up == bouncer.sent
    assert frame.metadata["path"] == ["a:down", "b:down", "c:down"]
    assert bouncer.sent[0].metadata["path"] == ["b:up", "a:up"]


@pytest.mark.asyncio
async def test_setup_and_cleanup_reach_every_child() -> None:
    a, b = Tag("a"), Tag("b")
    await run_test(Pipeline([a, Pipeline([b])]), frames_to_send=[])
    assert a.clock is b.clock  # set up with the same shared objects
    assert a.cleaned_up and b.cleaned_up


def test_processors_lists_children_without_edges() -> None:
    a, b = Tag("a"), Tag("b")
    assert Pipeline([a, b]).processors == [a, b]
