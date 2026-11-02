"""The stub agent's policy: what each zone's occupancy sensor and cooling setpoint hold, and why.

Pure: no sockets, no bus. ``StubAgentWorker`` feeds it the scenario's ground truth and the room
calendar, and writes what it decides to the BMS.
"""

from dataclasses import dataclass
from datetime import datetime

from spotcool_contracts import ZONES, Zone
from spotcool_contracts.calendar import ROOMS
from spotcool_contracts.events import CalendarData, ScenarioStateData
from spotcool_world.clocks import SimClock
from spotcool_world.clocks.sim_clock import SECONDS_PER_DAY
from spotcool_world.scenarios import SIM_EPOCH

OCCUPIED = "Occupied"
"""The ``ZN-<Z>-OCC-SENSOR`` state text for people in the zone."""
UNOCCUPIED = "Unoccupied"
"""The ``ZN-<Z>-OCC-SENSOR`` state text for an empty zone."""
PRECOOL_S = 1800.0
"""A booked room's zone is held Occupied from this long before the booking starts."""
RELEASE_AT_S = 19 * 3600.0
"""Time of day from which every zone is released: the end of the occupied day."""
EMPTY_SETPOINT_C = 28.0
"""The cooling setpoint (``ZN-<Z>-CLG-SP``, degC) an empty zone is held at."""


@dataclass(frozen=True)
class Command:
    """What a zone's points should hold at priority 10; ``None`` releases a slot (NULL).

    Parameters:
        sensor: ``ZN-<Z>-OCC-SENSOR``: ``OCCUPIED`` or ``UNOCCUPIED``.
        setpoint_c: ``ZN-<Z>-CLG-SP`` in degC: ``EMPTY_SETPOINT_C`` while the zone is empty.
        reason: Why, in a few words, for the dashboard.
    """

    sensor: str | None
    setpoint_c: float | None
    reason: str


class StubPolicy:
    """Decides each zone's ``OCC-SENSOR`` and ``CLG-SP`` from its people and the room bookings.

    - An empty zone is Unoccupied (the BMS then runs its own occupied-standby) with its cooling
      setpoint at ``EMPTY_SETPOINT_C``.
    - A zone with people is Occupied with its cooling setpoint released, so the building
      schedule applies, as in the baseline run.
    - A booked room's zone is held Occupied, its setpoint released, from ``PRECOOL_S`` before
      the booking to its end (pre-cooling), people or not.
    - From ``RELEASE_AT_S`` (19:00) every slot is released.
    """

    def __init__(self) -> None:
        # room -> each booking's start, end and subject
        self._bookings: dict[str, list[tuple[float, float, str]]] = {}

    def calendar(self, data: CalendarData) -> None:
        """Take a room's bookings, replacing the ones known before.

        Args:
            data: The room's bookings, as a ``calendar`` event carries them.
        """
        self._bookings[data.room] = [
            (_sim_s(item.start.dateTime), _sim_s(item.end.dateTime), item.subject or "a booking")
            for item in data.items
        ]

    def commands(self, state: ScenarioStateData, t_s: float) -> dict[Zone, Command]:
        """Every zone's command for the step that starts at ``t_s``.

        Args:
            state: Who is where in that step.
            t_s: The step's simulation time.
        """
        if t_s % SECONDS_PER_DAY >= RELEASE_AT_S:
            return {
                scene.zone: Command(None, None, "19:00, the occupied day is over")
                for scene in state.zones
            }
        commands: dict[Zone, Command] = {}
        for scene in state.zones:
            booking = self._booking(scene.zone, t_s)
            if scene.people:
                commands[scene.zone] = Command(OCCUPIED, None, f"{scene.people} people in the zone")
            elif booking is not None:
                commands[scene.zone] = Command(OCCUPIED, None, booking)
            else:
                commands[scene.zone] = Command(UNOCCUPIED, EMPTY_SETPOINT_C, "the zone is empty")
        return commands

    def _booking(self, zone: Zone, t_s: float) -> str | None:
        """Why the zone is held for a booking at ``t_s``, or ``None`` if it is not."""
        for room, bookings in self._bookings.items():
            if ROOMS.get(room) is not zone:
                continue
            for start_s, end_s, subject in bookings:
                if start_s - PRECOOL_S <= t_s < end_s:
                    at = SimClock.clock_text(start_s)
                    return f"pre-cooling {ZONES[zone].display_name} for {subject!r} at {at}"
        return None


def _sim_s(date_time: str) -> float:
    """A calendar date-time (``SIM_EPOCH`` plus the simulation seconds) as simulation time."""
    return (datetime.fromisoformat(date_time) - SIM_EPOCH).total_seconds()
