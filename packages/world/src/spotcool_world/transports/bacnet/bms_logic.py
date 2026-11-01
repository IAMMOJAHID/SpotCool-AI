"""What a real BMS does on its own: occupied-standby per zone and the agent heartbeat watchdog.

Pure logic, no sockets or frames; ``BacnetBmsTransport`` feeds it. Temperatures are degC.
"""

import time
from collections.abc import Callable
from enum import StrEnum

from spotcool_contracts.priorities import HEARTBEAT_TIMEOUT_S
from spotcool_world.transports.bacnet.priority import PointValue

STANDBY_DELAY_S = 300.0
"""Sim seconds a zone's occupancy sensor must read Unoccupied before the zone enters standby."""

STANDBY_OFFSET_K = 0.5
"""Standby raises the cooling setpoint and lowers the heating setpoint by this much."""

SHUTOFF_MARGIN_K = 0.5
"""The airflow shuts off once the zone temperature is this far inside both active setpoints."""

SCHEDULE_UNOCCUPIED_C = 26.0
"""A schedule cooling setpoint at or above this means the zone is scheduled unoccupied."""

_TOLERANCE_K = 1e-6  # float rounding only: a zone held at its setpoint must count as at it


class OccupancyMode(StrEnum):
    """A zone's occupancy mode, as the BMS shows it on ``ZN-<Z>-OCC-MODE``."""

    OCCUPIED = "Occupied"
    STANDBY = "Standby"
    UNOCCUPIED = "Unoccupied"


class ZoneStandby:
    """Occupied-standby for one zone (ASHRAE 90.1-2019 section 6.5.3.8; Guideline 36).

    During scheduled occupancy, a zone whose occupancy sensor has read Unoccupied for
    ``STANDBY_DELAY_S`` or more enters standby at the next tick. In standby the cooling
    setpoint's relinquish default (the schedule) rises by ``STANDBY_OFFSET_K``, and the zone's
    airflow shuts off while its temperature sits well inside the active setpoints. Any Occupied
    reading ends standby at once and resets the timer; so does the end of scheduled occupancy.
    """

    def __init__(self) -> None:
        self._scheduled = False
        self._occupied = True  # the sensor's relinquish default
        self._vacant_since_s: float | None = None
        self._standby = False
        self._shut_off = False

    @property
    def mode(self) -> OccupancyMode:
        """The zone's occupancy mode."""
        if not self._scheduled:
            return OccupancyMode.UNOCCUPIED
        return OccupancyMode.STANDBY if self._standby else OccupancyMode.OCCUPIED

    @property
    def standby(self) -> bool:
        """Whether the zone is in occupied-standby."""
        return self._standby

    def schedule(self, cooling_sp_c: float) -> None:
        """Take the building schedule's current cooling setpoint (the baseline run's).

        Args:
            cooling_sp_c: The schedule's cooling setpoint.
        """
        self._scheduled = cooling_sp_c < SCHEDULE_UNOCCUPIED_C
        if not self._scheduled:
            self._leave()

    def sensor(self, occupied: bool, now_s: float | None) -> None:
        """Take the occupancy sensor's effective value.

        Args:
            occupied: Whether the sensor reads Occupied.
            now_s: Simulation time of the reading, or ``None`` before the run's clock is known
                (the standby timer then starts at the next tick).
        """
        if occupied:
            self._occupied = True
            self._vacant_since_s = None
            self._leave()
        elif self._occupied:
            self._occupied = False
            self._vacant_since_s = now_s

    def tick(self, now_s: float) -> None:
        """Enter standby once the zone has been vacant long enough. Call it on every tick.

        Args:
            now_s: Simulation time of the tick.
        """
        if self._occupied or not self._scheduled:
            return
        if self._vacant_since_s is None:
            self._vacant_since_s = now_s
        if now_s - self._vacant_since_s >= STANDBY_DELAY_S:
            self._standby = True

    def cooling_setpoint(self, schedule_c: float) -> float:
        """The cooling setpoint's relinquish default: the schedule, raised in standby.

        Args:
            schedule_c: The schedule's cooling setpoint.
        """
        return schedule_c + STANDBY_OFFSET_K if self._standby else schedule_c

    def damper(self, temp_c: float, heating_sp_c: float, cooling_sp_c: float) -> float | None:
        """The zone damper override for the next step.

        In standby the damper closes once the zone is ``SHUTOFF_MARGIN_K`` inside both active
        setpoints and reopens (is released) when the temperature reaches either one; in between
        it keeps its last state. Out of standby it is always released.

        Args:
            temp_c: Zone temperature.
            heating_sp_c: The zone's heating setpoint, before the standby setback.
            cooling_sp_c: The zone's active cooling setpoint (commanded or relinquish default).

        Returns:
            ``0.0`` (airflow shut off) or ``None`` (released: the VAV box controls the zone).
        """
        if not self._standby:
            return None
        # shortcut: the standby heating setback only narrows the shut-off band; BOPTEST's
        # heating setpoint is not written, the scenarios being cooling days. Write
        # hvac_oveZonSup<Z>_TZonHeaSet_u (a managed input then) once a scenario needs heating.
        heating_sp_c -= STANDBY_OFFSET_K
        if temp_c >= cooling_sp_c - _TOLERANCE_K or temp_c <= heating_sp_c + _TOLERANCE_K:
            self._shut_off = False
        elif (
            heating_sp_c + SHUTOFF_MARGIN_K - _TOLERANCE_K
            <= temp_c
            <= cooling_sp_c - SHUTOFF_MARGIN_K + _TOLERANCE_K
        ):
            self._shut_off = True
        return 0.0 if self._shut_off else None

    def _leave(self) -> None:
        self._standby = False
        self._shut_off = False


class HeartbeatWatchdog:
    """Notices when the agent's heartbeat stops, on wall time.

    The first write to the heartbeat arms it. It expires once the heartbeat's effective value
    has not changed for ``timeout_s`` wall seconds, then stays disarmed until the next write.

    Args:
        timeout_s: Wall seconds without a change before it expires.
        clock: Wall clock in seconds; inject a fake one so tests never wait.
    """

    def __init__(
        self,
        timeout_s: float = HEARTBEAT_TIMEOUT_S,
        clock: Callable[[], float] = time.perf_counter,
    ) -> None:
        self._timeout_s = timeout_s
        self._clock = clock
        self._armed = False
        self._value: PointValue | None = None
        self._changed_at = 0.0

    @property
    def armed(self) -> bool:
        """Whether a heartbeat has been written since the watchdog last expired."""
        return self._armed

    def beat(self, value: PointValue | None) -> None:
        """Take the heartbeat's effective value after a write to it.

        Args:
            value: The heartbeat's effective value now.
        """
        if not self._armed or value != self._value:
            self._changed_at = self._clock()
        self._armed = True
        self._value = value

    def expired(self) -> bool:
        """Whether the heartbeat has just timed out. The watchdog then disarms itself."""
        if self._armed and self._clock() - self._changed_at >= self._timeout_s:
            self._armed = False
            return True
        return False
