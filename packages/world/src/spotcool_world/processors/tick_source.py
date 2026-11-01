"""TickSource: the world's heartbeat and the pipeline's only producer after start."""

import asyncio

from spotcool_world.clocks import SimClock, WallPacer
from spotcool_world.frames import EndFrame, Frame, StartFrame, TickFrame
from spotcool_world.processors.frame_processor import FrameDirection, FrameProcessor


class TickSource(FrameProcessor):
    """Pushes one ``TickFrame`` per simulation step, then an ``EndFrame``.

    On ``StartFrame`` it starts a background task that, until ``clock.now_s`` reaches
    ``end_time_s``, pushes a tick downstream and waits for the whole pipeline to handle it
    (lockstep), paces to wall time, and advances the clock. Then it pushes an ``EndFrame``.
    An ``EndFrame`` arriving from upstream stops the ticks early and is passed on.

    Args:
        clock: The run's clock; share it with ``PipelineParams.clock``.
        pacer: Wall-clock pacing between ticks.
        end_time_s: Simulation time at which the run ends.
        name: Display name.
    """

    def __init__(
        self,
        clock: SimClock,
        pacer: WallPacer,
        end_time_s: float,
        *,
        name: str | None = None,
    ) -> None:
        super().__init__(name=name)
        self._clock = clock
        self._pacer = pacer
        self._end_time_s = end_time_s
        self._ticks: asyncio.Task[None] | None = None

    async def process_frame(self, frame: Frame, direction: FrameDirection) -> None:
        """Start ticking on ``StartFrame``; stop on an ``EndFrame`` from upstream."""
        await super().process_frame(frame, direction)
        if isinstance(frame, EndFrame) and self._ticks is not None:
            self._ticks.cancel()
        await self.push_frame(frame, direction)
        if isinstance(frame, StartFrame) and self._ticks is None:
            self._ticks = self.create_task(self._tick_loop(), "ticks")

    async def _tick_loop(self) -> None:
        clock = self._clock
        while clock.now_s < self._end_time_s:
            tick = TickFrame(index=clock.index, step_s=clock.step_s, sim_time_s=clock.now_s)
            await self.push_frame(tick)
            await self._pacer.pace(clock.step_s)
            clock.advance()
            await asyncio.sleep(0)  # yield every tick, so stop() and cancels get a turn
        await self.push_frame(EndFrame(reason="end time reached", sim_time_s=clock.now_s))
