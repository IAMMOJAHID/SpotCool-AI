"""Scenario compiler: a checked scenario as functions of simulation time.

Simulation time is seconds since 1 January 00:00 of the BOPTEST year, so a scenario time is
``day * 86400`` plus its seconds after midnight. A tick at ``t`` starts the step
``[t, t + step)``; an entry applies from its time, inclusive. On the calendar, simulation time
``t`` is ``SIM_EPOCH`` plus ``t`` seconds (day 199 is 2026-07-19), as
``spotcool_contracts.calendar`` documents; the BMS's ``SIM-CLOCK`` counts the same seconds.
"""

import bisect
from datetime import datetime, timedelta

from spotcool_contracts import Zone
from spotcool_contracts.calendar import ROOMS, DateTimeTimeZone, ScheduleItem
from spotcool_contracts.events import ScenarioStateData, ZoneScene
from spotcool_world.clocks.sim_clock import SECONDS_PER_DAY
from spotcool_world.scenarios.linter import check
from spotcool_world.scenarios.schema import (
    BookingEvent,
    Clothing,
    ClothingEvent,
    ComplaintEvent,
    DoorOpenEvent,
    Scenario,
    clock_s,
    door_zones,
)

SIM_EPOCH = datetime(2026, 1, 1)
"""The calendar date-time of simulation time 0."""


def sim_datetime(t_s: float) -> datetime:
    """The calendar date-time of a simulation time: ``SIM_EPOCH`` plus ``t_s`` seconds.

    Args:
        t_s: Simulation time.
    """
    return SIM_EPOCH + timedelta(seconds=t_s)


def compile_scenario(scenario: Scenario, *, step_s: float) -> "CompiledScenario":
    """Compile a scenario that ``check`` finds no errors in.

    Args:
        scenario: The scenario.
        step_s: The world's step, for the step-grid rule.

    Raises:
        ValueError: If the scenario has errors; the message lists them.
    """
    errors = [issue for issue in check(scenario, step_s=step_s) if issue.level == "error"]
    if errors:
        listed = "; ".join(f"{'.'.join(map(str, e.loc))}: {e.message}" for e in errors)
        raise ValueError(f"scenario {scenario.name!r} has errors: {listed}")
    return CompiledScenario(scenario)


class CompiledScenario:
    """A scenario as functions of simulation time. Build it with ``compile_scenario``.

    Pure and deterministic: the same times always give the same answers.

    Args:
        scenario: A scenario without errors.

    Attributes:
        name: The scenario's name.
        start_s: Simulation time of the scenario's start.
        end_s: Simulation time of its end.
        booking_change_times: The times a room's visible bookings change (each booking's
            ``booked_at``), sorted.
    """

    def __init__(self, scenario: Scenario) -> None:
        day_s = scenario.day * SECONDS_PER_DAY

        def sim_s(text: str) -> float:
            return float(day_s + clock_s(text))

        self.name = scenario.name
        self.start_s = sim_s(scenario.start)
        self.end_s = sim_s(scenario.end)
        self._zones = {
            zone: ([sim_s(e.at) for e in entries], entries)
            for zone, entries in scenario.zones.items()
        }
        events = scenario.events
        self._doors = [
            (e.zone, sim_s(e.from_), sim_s(e.to)) for e in events if isinstance(e, DoorOpenEvent)
        ]
        self._clothing: dict[Zone, tuple[list[float], list[Clothing]]] = {}
        changes = sorted(
            ((sim_s(e.at), e) for e in events if isinstance(e, ClothingEvent)),
            key=lambda change: change[0],
        )
        for zone in Zone:
            in_zone = [(t, e) for t, e in changes if e.zone is zone]
            self._clothing[zone] = ([t for t, _ in in_zone], [e.clothing for _, e in in_zone])
        self._cues = sorted(
            ((sim_s(e.at), e) for e in events if isinstance(e, ComplaintEvent)),
            key=lambda cue: cue[0],
        )
        bookings = sorted(
            (e for e in events if isinstance(e, BookingEvent)), key=lambda e: sim_s(e.from_)
        )
        self._bookings = [
            (
                e.room,
                sim_s(e.from_),
                sim_s(e.to),
                sim_s(e.booked_at or scenario.start),
                ScheduleItem(
                    status="busy",
                    subject=e.subject,
                    location=e.room,
                    start=DateTimeTimeZone(dateTime=_calendar_text(sim_s(e.from_))),
                    end=DateTimeTimeZone(dateTime=_calendar_text(sim_s(e.to))),
                ),
            )
            for e in bookings
        ]
        self.booking_change_times = tuple(sorted({b[3] for b in self._bookings}))

    def state_at(self, t_s: float) -> ScenarioStateData:
        """The scenario state in force from ``t_s``: all five zones, in ``Zone`` order.

        ``door_open`` is ``True`` or ``False`` for a zone with a door contact and ``None`` for
        the others. ``clothing`` is the zone's latest clothing change, or ``None`` before the
        first one and whenever the zone is empty. ``active_events`` lists the door events
        (``door_open:<zone>``) and bookings (``booking:<room>``) in force.

        Args:
            t_s: Simulation time, at or after the start.

        Raises:
            ValueError: If ``t_s`` is before the scenario's start.
        """
        if t_s < self.start_s:
            raise ValueError(f"{t_s:g} s is before the scenario's start, {self.start_s:g} s")
        scenes: list[ZoneScene] = []
        for zone in Zone:
            times, entries = self._zones[zone]
            entry = entries[bisect.bisect_right(times, t_s) - 1]
            door = None
            if zone in door_zones():
                door = any(z is zone and opens <= t_s < closes for z, opens, closes in self._doors)
            clothing = self._clothing_at(zone, t_s) if entry.people else None
            scene = ZoneScene(
                zone=zone, scene=entry.scene, people=entry.people, door_open=door, clothing=clothing
            )
            scenes.append(scene)
        active = [
            f"door_open:{z.value}" for z, opens, closes in self._doors if opens <= t_s < closes
        ]
        active += [f"booking:{b[0]}" for b in self._bookings if b[1] <= t_s < b[2]]
        return ScenarioStateData(zones=scenes, active_events=list(dict.fromkeys(active)))

    def cues_between(self, t0_s: float | None, t1_s: float) -> list[ComplaintEvent]:
        """The complaints due in a tick's window, in time order.

        Args:
            t0_s: The previous tick's time, for the window ``(t0_s, t1_s]``; ``None`` on the
                first tick, whose window is ``[t1_s, t1_s]``.
            t1_s: This tick's time.
        """
        if t0_s is None:
            return [cue for at_s, cue in self._cues if at_s == t1_s]
        return [cue for at_s, cue in self._cues if t0_s < at_s <= t1_s]

    def bookings_at(self, t_s: float) -> dict[str, list[ScheduleItem]]:
        """Every room's bookings the calendar shows at ``t_s`` (booked by then), by start.

        Args:
            t_s: Simulation time.

        Returns:
            Room to its visible bookings, for every room in ``ROOMS``; each item is busy.
        """
        visible: dict[str, list[ScheduleItem]] = {room: [] for room in ROOMS}
        for room, _, _, booked_s, item in self._bookings:
            if booked_s <= t_s:
                visible[room].append(item)
        return visible

    def _clothing_at(self, zone: Zone, t_s: float) -> Clothing | None:
        times, clothing = self._clothing[zone]
        index = bisect.bisect_right(times, t_s)
        return clothing[index - 1] if index else None


def _calendar_text(t_s: float) -> str:
    """A simulation time as a Graph ``dateTime``, e.g. ``2026-07-19T14:00:00``."""
    return sim_datetime(t_s).isoformat(timespec="seconds")
