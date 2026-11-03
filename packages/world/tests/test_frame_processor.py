import asyncio
from typing import Any

import pytest

from spotcool_world.frames import CancelFrame, DataFrame, EndFrame, ErrorFrame, Frame, StartFrame
from spotcool_world.pipeline import Pipeline
from spotcool_world.processors import FrameDirection, FrameProcessor
from spotcool_world.testing import run_test


class Tag(FrameProcessor):
    """Appends its name to ``frame.metadata["path"]`` and passes the frame on."""

    def __init__(self, label: str) -> None:
        super().__init__(name=label)

    async def process_frame(self, frame: Frame, direction: FrameDirection) -> None:
        await super().process_frame(frame, direction)
        frame.metadata.setdefault("path", []).append(self.name)
        await self.push_frame(frame, direction)


class Complainer(FrameProcessor):
    """Reports an error for every data frame and passes it on."""

    def __init__(self, *, fatal: bool) -> None:
        super().__init__()
        self._fatal = fatal

    async def process_frame(self, frame: Frame, direction: FrameDirection) -> None:
        await super().process_frame(frame, direction)
        if isinstance(frame, DataFrame):
            await self.push_error("bad data", fatal=self._fatal)
        await self.push_frame(frame, direction)


class Sleeper(FrameProcessor):
    """Starts a background task that never ends."""

    def __init__(self) -> None:
        super().__init__()
        self.task: asyncio.Task[Any] | None = None

    async def process_frame(self, frame: Frame, direction: FrameDirection) -> None:
        await super().process_frame(frame, direction)
        if isinstance(frame, StartFrame):
            self.task = self.create_task(asyncio.sleep(3600), "forever")
        await self.push_frame(frame, direction)


class Crasher(FrameProcessor):
    """Starts a background task that raises."""

    async def process_frame(self, frame: Frame, direction: FrameDirection) -> None:
        await super().process_frame(frame, direction)
        if isinstance(frame, StartFrame):
            self.create_task(self._crash(), "crash")
        await self.push_frame(frame, direction)

    async def _crash(self) -> None:
        raise RuntimeError("sensor fell off")


def test_link_sets_both_neighbours() -> None:
    a, b = FrameProcessor(), FrameProcessor()
    a.link(b)
    assert a.next is b
    assert b.prev is a
    assert a.prev is None
    assert b.next is None


@pytest.mark.asyncio
async def test_push_without_neighbour_drops_silently() -> None:
    await FrameProcessor().push_frame(EndFrame())
    await FrameProcessor().push_frame(EndFrame(), FrameDirection.UPSTREAM)


@pytest.mark.asyncio
async def test_frames_visit_processors_in_order() -> None:
    frames = [DataFrame(), DataFrame()]
    down, up = await run_test(Pipeline([Tag("a"), Tag("b"), Tag("c")]), frames_to_send=frames)
    assert down == frames
    assert up == []
    assert all(f.metadata["path"] == ["a", "b", "c"] for f in frames)


@pytest.mark.asyncio
async def test_start_frame_is_recorded() -> None:
    tag = Tag("a")
    await run_test(tag, frames_to_send=[])
    assert isinstance(tag.start_frame, StartFrame)


@pytest.mark.asyncio
@pytest.mark.parametrize("fatal", [False, True])
async def test_push_error_goes_upstream(fatal: bool) -> None:
    complainer = Complainer(fatal=fatal)
    _, up = await run_test(complainer, frames_to_send=[DataFrame()])
    assert len(up) == 1
    error = up[0]
    assert isinstance(error, ErrorFrame)
    assert error.fatal is fatal
    assert error.processor == complainer.name
    assert error.message == "bad data"


@pytest.mark.asyncio
async def test_cancel_frame_cancels_background_tasks() -> None:
    sleeper = Sleeper()
    await run_test(sleeper, frames_to_send=[CancelFrame(reason="test")], send_end=False)
    assert sleeper.task is not None
    assert sleeper.task.cancelled()


@pytest.mark.asyncio
async def test_crash_in_background_task_is_a_fatal_error() -> None:
    _, up = await run_test(Crasher(), frames_to_send=[], send_end=False)
    errors = [f for f in up if isinstance(f, ErrorFrame)]
    assert len(errors) == 1
    assert errors[0].fatal
    assert "sensor fell off" in errors[0].message


def test_clock_needs_setup() -> None:
    with pytest.raises(RuntimeError, match="not been set up"):
        _ = FrameProcessor().clock


@pytest.mark.asyncio
async def test_event_handlers_background_and_sync() -> None:
    processor = FrameProcessor()
    processor.register_event_handler("on_reading")
    calls: list[tuple[Any, ...]] = []

    @processor.event_handler("on_reading")
    async def on_reading(obj: FrameProcessor, value: int) -> None:
        calls.append(("async", obj, value))

    def on_reading_plain(obj: FrameProcessor, value: int) -> None:
        calls.append(("plain", obj, value))

    processor.add_event_handler("on_reading", on_reading_plain)

    await processor._call_event_handler("on_reading", 1, sync=True)
    assert calls == [("async", processor, 1), ("plain", processor, 1)]

    calls.clear()
    await processor._call_event_handler("on_reading", 2)
    assert calls == []  # background handlers have not run yet
    await processor.cleanup()  # cleanup lets running event handlers finish
    assert calls == [("async", processor, 2), ("plain", processor, 2)]


@pytest.mark.asyncio
async def test_failing_handler_is_logged_not_raised() -> None:
    processor = FrameProcessor()
    processor.register_event_handler("on_x")

    @processor.event_handler("on_x")
    async def on_x(obj: FrameProcessor) -> None:
        raise RuntimeError("handler bug")

    await processor._call_event_handler("on_x", sync=True)


@pytest.mark.asyncio
async def test_unknown_events_are_rejected() -> None:
    processor = FrameProcessor()
    processor.register_event_handler("on_x")
    with pytest.raises(ValueError, match="already registered"):
        processor.register_event_handler("on_x")
    with pytest.raises(ValueError, match="unknown event"):
        processor.add_event_handler("on_y", lambda obj: None)
    with pytest.raises(ValueError, match="unknown event"):
        await processor._call_event_handler("on_y")
