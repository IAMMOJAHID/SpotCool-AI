"""StepTimingObserver: how long each tick takes to flow through the pipeline."""

import math

from loguru import logger

from spotcool_world.frames import CancelFrame, EndFrame, TickFrame
from spotcool_world.observers.base_observer import BaseObserver, FramePushed


class StepTimingObserver(BaseObserver):
    """Measures the wall time of each tick, from the first to the last push of its frame.

    A tick ends when the next tick, an ``EndFrame`` or a ``CancelFrame`` is first pushed.
    Pacing sleeps happen after the last push, so they are not counted.

    Args:
        warn_above_s: Log a warning for ticks slower than this.
        name: Display name.
    """

    def __init__(self, *, warn_above_s: float = 1.0, name: str | None = None) -> None:
        super().__init__(name=name)
        self._warn_above_s = warn_above_s
        self._tick: TickFrame | None = None
        self._first_ns = 0
        self._last_ns = 0
        self._count = 0
        self._total_s = 0.0
        self._min_s = math.inf
        self._max_s = 0.0

    @property
    def count(self) -> int:
        """Ticks measured so far."""
        return self._count

    @property
    def min_s(self) -> float | None:
        """Fastest tick, or ``None`` before the first one."""
        return self._min_s if self._count else None

    @property
    def mean_s(self) -> float | None:
        """Average tick, or ``None`` before the first one."""
        return self._total_s / self._count if self._count else None

    @property
    def max_s(self) -> float | None:
        """Slowest tick, or ``None`` before the first one."""
        return self._max_s if self._count else None

    async def on_push_frame(self, data: FramePushed) -> None:
        """Track the pushes of the current tick; close it when the next one starts."""
        frame = data.frame
        if isinstance(frame, TickFrame):
            if self._tick is not None and frame.id == self._tick.id:
                self._last_ns = data.timestamp_ns
                return
            self._finish_tick()
            self._tick = frame
            self._first_ns = self._last_ns = data.timestamp_ns
        elif isinstance(frame, EndFrame | CancelFrame):
            self._finish_tick()

    def _finish_tick(self) -> None:
        if self._tick is None:
            return
        duration_s = (self._last_ns - self._first_ns) / 1e9
        self._count += 1
        self._total_s += duration_s
        self._min_s = min(self._min_s, duration_s)
        self._max_s = max(self._max_s, duration_s)
        if duration_s > self._warn_above_s:
            logger.warning(
                f"tick {self._tick.index} took {duration_s:.3f} s (limit {self._warn_above_s} s)"
            )
        self._tick = None
