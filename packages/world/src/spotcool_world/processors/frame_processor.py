"""FrameProcessor: one small step of the simulation loop.

A processor receives frames in ``process_frame(frame, direction)`` and passes frames on with
``push_frame(frame, direction)``, downstream to the next processor or upstream to the previous
one. Subclasses call ``await super().process_frame(frame, direction)`` first, then handle the
frame, then push it (or new frames) on.

Unlike pipecat, processors have no input queues: ``push_frame`` awaits the neighbour's
``process_frame`` directly. The world is a lockstep co-simulation, so a tick must flow through
the whole pipeline, both building plants included, before the next tick starts, and the order
of work must be the same on every run.
"""

import asyncio
import time
from collections.abc import Coroutine
from dataclasses import dataclass
from enum import Enum
from typing import Any

from loguru import logger

from spotcool_world.bus import Bus
from spotcool_world.clocks import SimClock
from spotcool_world.frames import CancelFrame, ErrorFrame, Frame, StartFrame
from spotcool_world.observers.base_observer import BaseObserver, FramePushed
from spotcool_world.utils.base_object import BaseObject
from spotcool_world.utils.task_manager import TaskManager


class FrameDirection(Enum):
    """Which way a frame travels."""

    DOWNSTREAM = 1
    """From the clock towards the dashboard."""
    UPSTREAM = 2
    """Back towards the clock and the worker (errors, requests)."""


@dataclass(kw_only=True)
class FrameProcessorSetup:
    """What a worker hands every processor before the run starts.

    Parameters:
        clock: The run's simulation clock.
        bus: The bus shared by all workers.
        observers: Observers notified of every push.
        task_manager: Registry for background tasks.
    """

    clock: SimClock
    bus: Bus
    observers: list[BaseObserver]
    task_manager: TaskManager


class FrameProcessor(BaseObject):
    """Base class of every processor.

    The base ``process_frame`` handles the lifecycle: it records the ``StartFrame`` and cancels
    the processor's background tasks on ``CancelFrame``. It never pushes; the subclass decides.
    A processor with no neighbour in a direction drops frames pushed that way.

    Args:
        name: Display name. Defaults to ``ClassName#id``.
    """

    def __init__(self, *, name: str | None = None) -> None:
        super().__init__(name=name)
        self._prev: FrameProcessor | None = None
        self._next: FrameProcessor | None = None
        self._setup: FrameProcessorSetup | None = None
        self._start_frame: StartFrame | None = None

    @property
    def next(self) -> "FrameProcessor | None":
        """The downstream neighbour."""
        return self._next

    @property
    def prev(self) -> "FrameProcessor | None":
        """The upstream neighbour."""
        return self._prev

    @property
    def start_frame(self) -> StartFrame | None:
        """The run's ``StartFrame`` once it has arrived."""
        return self._start_frame

    @property
    def clock(self) -> SimClock:
        """The run's simulation clock (available after ``setup``)."""
        return self._require_setup().clock

    @property
    def bus(self) -> Bus:
        """The shared bus (available after ``setup``)."""
        return self._require_setup().bus

    def link(self, processor: "FrameProcessor") -> None:
        """Make ``processor`` this processor's downstream neighbour, and this its upstream one.

        Args:
            processor: The next processor.
        """
        self._next = processor
        processor._prev = self

    async def setup(self, setup: FrameProcessorSetup) -> None:
        """Receive the run's shared clock, bus, observers and task manager.

        Args:
            setup: The shared objects.
        """
        self._setup = setup
        self._task_manager = setup.task_manager

    def create_task(
        self, coro: Coroutine[Any, Any, Any], name: str | None = None
    ) -> asyncio.Task[Any]:
        """Run ``coro`` in the background; an exception it raises becomes a fatal error.

        Args:
            coro: The coroutine to run.
            name: Short task name.

        Returns:
            The running ``asyncio.Task``.
        """
        task = super().create_task(self._report_crash(coro), name)
        # A task cancelled before its first step never awaits coro; close it so it is not leaked.
        task.add_done_callback(lambda _: coro.close())
        return task

    async def process_frame(self, frame: Frame, direction: FrameDirection) -> None:
        """Handle one frame. Subclasses call this first, then push frames on themselves.

        Args:
            frame: The frame.
            direction: Which way it is travelling.
        """
        if isinstance(frame, StartFrame):
            self._start_frame = frame
        elif isinstance(frame, CancelFrame):
            await self.cancel_tasks()

    async def push_frame(
        self, frame: Frame, direction: FrameDirection = FrameDirection.DOWNSTREAM
    ) -> None:
        """Hand ``frame`` to the neighbour and wait until it has been handled.

        Observers see the push first. Without a neighbour in that direction the frame is
        dropped.

        Args:
            frame: The frame.
            direction: Downstream to the next processor, upstream to the previous one.
        """
        destination = self._next if direction is FrameDirection.DOWNSTREAM else self._prev
        if destination is None:
            return
        if self._setup is not None and self._setup.observers:
            pushed = FramePushed(
                source=self,
                destination=destination,
                frame=frame,
                direction=direction,
                timestamp_ns=time.perf_counter_ns(),
            )
            for observer in self._setup.observers:
                await observer.on_push_frame(pushed)
        await destination.process_frame(frame, direction)

    async def push_error(self, message: str, *, fatal: bool = False) -> None:
        """Log an error and send an ``ErrorFrame`` upstream to the worker.

        Args:
            message: What went wrong.
            fatal: Whether the run cannot continue; a fatal error cancels the run.
        """
        (logger.error if fatal else logger.warning)(f"{self.name}: {message}")
        error = ErrorFrame(
            message=message,
            fatal=fatal,
            processor=self.name,
            sim_time_s=self._setup.clock.now_s if self._setup else None,
        )
        await self.push_frame(error, FrameDirection.UPSTREAM)

    def _require_setup(self) -> FrameProcessorSetup:
        if self._setup is None:
            raise RuntimeError(f"{self.name} has not been set up by a worker")
        return self._setup

    async def _report_crash(self, coro: Coroutine[Any, Any, Any]) -> Any:
        try:
            return await coro
        except Exception as exc:
            logger.exception(f"{self.name}: background task crashed")
            await self.push_error(f"unhandled {type(exc).__name__}: {exc}", fatal=True)
