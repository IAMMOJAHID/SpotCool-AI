"""Simulation time and best-effort wall-clock pacing.

Simulation time is authoritative: it is BOPTEST's clock, seconds since 1 January 00:00 of a
non-leap year. Wall time only decides how fast the ticks come; a slow step makes the run late,
never wrong.
"""

import asyncio
import time

SECONDS_PER_DAY = 86_400
DAYS_PER_YEAR = 365


class SimClock:
    """Counts simulation steps from a start time.

    ``now_s`` is computed as ``start + index * step`` rather than accumulated, so long runs
    carry no floating-point drift.

    Args:
        start_time_s: Simulation time of step 0.
        step_s: Simulation seconds per step.
    """

    def __init__(self, start_time_s: float, step_s: float) -> None:
        if step_s <= 0:
            raise ValueError(f"step_s must be positive, got {step_s}")
        if start_time_s < 0:
            raise ValueError(f"start_time_s must not be negative, got {start_time_s}")
        self._start_time_s = start_time_s
        self._step_s = step_s
        self._index = 0

    @property
    def start_time_s(self) -> float:
        """Simulation time of step 0."""
        return self._start_time_s

    @property
    def step_s(self) -> float:
        """Simulation seconds per step."""
        return self._step_s

    @property
    def index(self) -> int:
        """Steps taken since the start."""
        return self._index

    @property
    def now_s(self) -> float:
        """Current simulation time."""
        return self._start_time_s + self._index * self._step_s

    def advance(self) -> float:
        """Move one step forward.

        Returns:
            The new simulation time.
        """
        self._index += 1
        return self.now_s

    @staticmethod
    def day_of_year(t_s: float) -> int:
        """Day of the BOPTEST year for a simulation time.

        Args:
            t_s: Seconds since 1 January 00:00 (non-leap year).

        Returns:
            The 1-based day number (1 January is day 1).
        """
        return int(t_s // SECONDS_PER_DAY) % DAYS_PER_YEAR + 1

    @staticmethod
    def clock_text(t_s: float) -> str:
        """Time of day for a simulation time, as ``HH:MM``.

        Args:
            t_s: Seconds since 1 January 00:00.

        Returns:
            The local time of day, e.g. ``"08:30"``.
        """
        seconds = round(t_s) % SECONDS_PER_DAY
        return f"{seconds // 3600:02d}:{seconds % 3600 // 60:02d}"


class WallPacer:
    """Makes each simulation step last about ``step_s / speed`` wall seconds.

    Pacing is fixed-rate: the time a step spends working counts towards its slot, so the
    sleep only fills the rest. A step that overruns its slot is not made up later (no bursts).

    Args:
        speed: Simulation seconds per wall second (300 means 15 sim-minutes take 3 s), or
            ``None`` to never sleep.
    """

    def __init__(self, speed: float | None) -> None:
        if speed is not None and speed <= 0:
            raise ValueError(f"speed must be positive or None, got {speed}")
        self._speed = speed
        self._deadline: float | None = None

    @property
    def speed(self) -> float | None:
        """Simulation seconds per wall second, or ``None`` for no pacing."""
        return self._speed

    async def pace(self, step_s: float) -> None:
        """Sleep until the current step's wall-clock slot is over.

        Args:
            step_s: Simulation seconds the step covered.
        """
        if self._speed is None:
            return
        now = time.perf_counter()
        self._deadline = (self._deadline or now) + step_s / self._speed
        delay = self._deadline - now
        if delay > 0:
            await asyncio.sleep(delay)
        else:
            self._deadline = now  # overran the slot: restart the schedule instead of bursting
