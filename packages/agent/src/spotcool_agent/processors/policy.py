"""The agent's policy: the stub agent's, fed by the cameras instead of the scenario (pure).

Constants and rules are the stub's (``spotcool_world/stub_agent/policy.py``), unchanged, so
perception is the only difference between the agent's run and the stub's:

- an empty zone: ``ZN-<Z>-OCC-SENSOR`` Unoccupied (the BMS then runs occupied-standby) and
  ``ZN-<Z>-CLG-SP`` at ``EMPTY_SETPOINT_C``;
- a zone with people: ``OCC-SENSOR`` Occupied and ``CLG-SP`` released, so the building schedule
  applies, as in the baseline run;
- a booked room's zone is held as a zone with people from ``PRECOOL_S`` before the booking to
  its end;
- from ``RELEASE_AT_S`` (19:00) every slot is released.

What the stub never meets, because its perception is perfect:

- a zone without a count (unknown) keeps its current command for one step, then counts as
  occupied, so a camera that stays down fails towards comfort;
- a step with no count at all (every zone unknown), or the run's model-call cap used up,
  releases everything: the building's own control takes over, and the saving honestly drops.
"""

from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import datetime

from spotcool_agent.processors.occupancy import Occupancy
from spotcool_contracts import ZONES, Zone
from spotcool_contracts.calendar import ROOMS, ScheduleItem

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
SECONDS_PER_DAY = 86_400.0
SIM_EPOCH = datetime(2026, 1, 1)
"""The calendar date-time of simulation time 0, the world's (``spotcool_contracts.calendar``)."""
UNKNOWN_HOLD_STEPS = 1
"""Steps a zone without a count keeps its current command before it counts as occupied."""


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


class AgentPolicy:
    """Decides each zone's ``OCC-SENSOR`` and ``CLG-SP`` (see the module docstring).

    It remembers each zone's last command and how many steps in a row it had no count.
    """

    def __init__(self) -> None:
        self._bookings: dict[str, list[tuple[float, float, str]]] = {}  # room -> start, end, why
        self._last: dict[Zone, Command] = {}
        self._unknown: dict[Zone, int] = dict.fromkeys(Zone, 0)

    def calendar(self, room: str, items: Sequence[ScheduleItem]) -> None:
        """Take a room's bookings, replacing the ones known before.

        Args:
            room: The room, a key of ``spotcool_contracts.calendar.ROOMS``.
            items: Its bookings, as the room calendar serves them.
        """
        self._bookings[room] = [
            (_sim_s(item.start.dateTime), _sim_s(item.end.dateTime), item.subject or "a booking")
            for item in items
        ]

    def commands(
        self,
        seen: Mapping[Zone, Occupancy],
        people: Mapping[Zone, int],
        t_s: float,
        *,
        cap_reached: bool = False,
    ) -> dict[Zone, Command]:
        """Every zone's command for the step that starts at ``t_s``.

        The occupancy is the step just closed, as the cameras showed it; the bookings and 19:00
        are judged at ``t_s``, the first step the commands take effect in. A zone left out of
        the result keeps whatever it holds (no count yet, and no command before).

        Args:
            seen: Each zone's occupancy at the step's close.
            people: The people counted in each counted zone, for the reasons.
            t_s: The start of the step the commands apply to.
            cap_reached: Whether the run's model-call cap is used up.
        """
        for zone in Zone:
            unknown = seen.get(zone, Occupancy.UNKNOWN) is Occupancy.UNKNOWN
            self._unknown[zone] = self._unknown[zone] + 1 if unknown else 0
        if t_s % SECONDS_PER_DAY >= RELEASE_AT_S:
            return self._every(Command(None, None, "19:00, the occupied day is over"))
        if cap_reached:
            reason = "the run's model-call cap is used up: back to the building's own control"
            return self._every(Command(None, None, reason))
        if all(self._unknown[zone] for zone in Zone):
            reason = "no camera count this step: back to the building's own control"
            return self._every(Command(None, None, reason))
        commands: dict[Zone, Command] = {}
        for zone in Zone:
            state = seen.get(zone, Occupancy.UNKNOWN)
            booking = self._booking(zone, t_s)
            if state is Occupancy.OCCUPIED:
                count = people.get(zone, 0)
                noun = "person" if count == 1 else "people"
                command = Command(OCCUPIED, None, f"{count} {noun} on camera")
            elif booking is not None:
                command = Command(OCCUPIED, None, booking)
            elif state is Occupancy.EMPTY:
                command = Command(UNOCCUPIED, EMPTY_SETPOINT_C, "the zone is empty")
            elif self._unknown[zone] <= UNKNOWN_HOLD_STEPS:
                if zone not in self._last:
                    continue  # nothing to keep yet
                command = self._last[zone]
            else:
                steps = self._unknown[zone]
                command = Command(
                    OCCUPIED, None, f"no count for {steps} steps: treated as occupied"
                )
            commands[zone] = self._last[zone] = command
        return commands

    def _every(self, command: Command) -> dict[Zone, Command]:
        """The same command for every zone, remembered as each zone's last."""
        for zone in Zone:
            self._last[zone] = command
        return dict.fromkeys(Zone, command)

    def _booking(self, zone: Zone, t_s: float) -> str | None:
        """Why the zone is held for a booking at ``t_s``, or ``None`` if it is not."""
        for room, bookings in self._bookings.items():
            if ROOMS.get(room) is not zone:
                continue
            for start_s, end_s, subject in bookings:
                if start_s - PRECOOL_S <= t_s < end_s:
                    at = clock_text(start_s)
                    return f"pre-cooling {ZONES[zone].display_name} for {subject!r} at {at}"
        return None


def _sim_s(date_time: str) -> float:
    """A calendar date-time (``SIM_EPOCH`` plus the simulation seconds) as simulation time."""
    return (datetime.fromisoformat(date_time) - SIM_EPOCH).total_seconds()


def clock_text(t_s: float) -> str:
    """The time of day of a simulation time, as ``HH:MM``."""
    minutes = int(t_s % SECONDS_PER_DAY) // 60
    return f"{minutes // 60:02d}:{minutes % 60:02d}"
