from collections.abc import Mapping

import pytest

from spotcool_agent.processors import explain
from spotcool_agent.processors.occupancy import Occupancy, occupancy
from spotcool_agent.processors.policy import (
    EMPTY_SETPOINT_C,
    OCCUPIED,
    PRECOOL_S,
    SIM_EPOCH,
    UNOCCUPIED,
    AgentPolicy,
    Command,
)
from spotcool_contracts import Zone
from spotcool_contracts.calendar import DateTimeTimeZone, ScheduleItem

DAY_S = 199 * 86_400.0
SEEN, EMPTY, UNSEEN = Occupancy.OCCUPIED, Occupancy.EMPTY, Occupancy.UNKNOWN


def at(clock: str) -> float:
    hours, minutes = clock.split(":")
    return DAY_S + int(hours) * 3600 + int(minutes) * 60


def booked() -> AgentPolicy:
    policy = AgentPolicy()
    item = ScheduleItem(
        status="busy",
        subject="Quarterly review",
        start=DateTimeTimeZone(dateTime="2026-07-19T14:00:00"),
        end=DateTimeTimeZone(dateTime="2026-07-19T15:00:00"),
    )
    policy.calendar("meeting-south", [item])
    return policy


def every(state: Occupancy, **zones: Occupancy) -> dict[Zone, Occupancy]:
    return {zone: zones.get(zone.value, state) for zone in Zone}


def south(policy: AgentPolicy, seen: Mapping[Zone, Occupancy], clock: str) -> Command:
    return policy.commands(seen, {Zone.SOU: 5}, at(clock))[Zone.SOU]


def test_counts_become_occupancy_and_a_missing_count_is_unknown() -> None:
    assert occupancy({Zone.COR: 2, Zone.NOR: 0, Zone.SOU: None}) == {
        Zone.COR: SEEN,
        Zone.NOR: EMPTY,
        Zone.SOU: UNSEEN,
        Zone.EAS: UNSEEN,
        Zone.WES: UNSEEN,
    }


def test_the_stubs_rules_and_constants() -> None:
    commands = AgentPolicy().commands(every(EMPTY, cor=SEEN), {Zone.COR: 1}, at("09:00"))
    assert commands[Zone.COR] == Command(OCCUPIED, None, "1 person on camera")
    assert commands[Zone.NOR] == Command(UNOCCUPIED, EMPTY_SETPOINT_C, "the zone is empty")
    assert (PRECOOL_S, EMPTY_SETPOINT_C) == (1800.0, 28.0)


@pytest.mark.parametrize(
    ("clock", "sensor"),
    [
        ("13:15", UNOCCUPIED),  # 45 minutes before: not yet
        ("13:30", OCCUPIED),  # 30 minutes before: pre-cooling starts
        ("14:45", OCCUPIED),
        ("15:00", UNOCCUPIED),  # the booking's end
    ],
)
def test_a_booked_room_is_held_from_30_minutes_before_to_its_end(clock: str, sensor: str) -> None:
    command = south(booked(), every(EMPTY), clock)
    assert command.sensor == sensor
    if sensor == OCCUPIED:
        assert command == Command(
            OCCUPIED, None, "pre-cooling South for 'Quarterly review' at 14:00"
        )


@pytest.mark.parametrize("clock", ["19:00", "19:15", "23:45"])
def test_from_19_00_everything_is_released(clock: str) -> None:
    commands = booked().commands(every(SEEN), {zone: 3 for zone in Zone}, at(clock))
    assert set(commands.values()) == {Command(None, None, "19:00, the occupied day is over")}
    assert len(commands) == len(Zone)


def test_unknown_keeps_the_zones_command_for_one_step_then_counts_as_occupied() -> None:
    policy = AgentPolicy()
    empty = policy.commands(every(EMPTY), {}, at("09:00"))[Zone.EAS]
    assert policy.commands(every(EMPTY, eas=UNSEEN), {}, at("09:15"))[Zone.EAS] == empty  # kept
    second = policy.commands(every(EMPTY, eas=UNSEEN), {}, at("09:30"))[Zone.EAS]
    assert second == Command(OCCUPIED, None, "no count for 2 steps: treated as occupied")
    assert policy.commands(every(EMPTY), {}, at("09:45"))[Zone.EAS] == empty  # counted again


def test_an_unknown_zone_with_no_command_yet_is_left_alone() -> None:
    commands = AgentPolicy().commands(every(EMPTY, eas=UNSEEN), {}, at("07:00"))
    assert Zone.EAS not in commands and len(commands) == len(Zone) - 1


def test_a_booking_holds_a_zone_whose_count_failed() -> None:
    assert south(booked(), every(EMPTY, sou=UNSEEN), "13:45").sensor == OCCUPIED


def test_a_step_without_any_count_or_the_cap_releases_everything() -> None:
    policy = AgentPolicy()
    policy.commands(every(EMPTY), {}, at("09:00"))
    blind = policy.commands(every(UNSEEN), {}, at("09:15"))
    reason = "no camera count this step: back to the building's own control"
    assert set(blind.values()) == {Command(None, None, reason)}
    capped = policy.commands(every(EMPTY), {}, at("09:30"), cap_reached=True)
    assert {command.sensor for command in capped.values()} == {None}
    assert {command.setpoint_c for command in capped.values()} == {None}


def test_the_sim_epoch_is_the_worlds() -> None:
    from spotcool_world.scenarios import SIM_EPOCH as WORLD_EPOCH

    assert SIM_EPOCH == WORLD_EPOCH


def test_the_explanations_are_the_stubs_sentences() -> None:
    text = explain.commanded(
        Zone.SOU,
        "the zone is empty",
        [("ZN-SOU-OCC-SENSOR", "Unoccupied"), ("ZN-SOU-CLG-SP", 28.0)],
    )
    assert text == (
        "South: the zone is empty -> ZN-SOU-OCC-SENSOR Unoccupied, ZN-SOU-CLG-SP 28.0 at "
        "priority 10"
    )
    held = explain.backed_off(Zone.SOU, "ZN-SOU-CLG-SP", 23.0, 8, 28.0, retry=True)
    assert held == (
        "South: ZN-SOU-CLG-SP is held at 23.0 by an operator at priority 8 -> backed off from "
        "writing 28.0; trying again each step"
    )


@pytest.mark.parametrize("name", ["hybrid_tuesday", "all_hands_day", "empty_friday"])
def test_with_perfect_counts_it_decides_as_the_stub_on_every_step(name: str) -> None:
    from spotcool_contracts.events import CalendarData
    from spotcool_world.scenarios import compile_scenario, load_scenario
    from spotcool_world.stub_agent.policy import StubPolicy

    step_s = 900.0
    day = compile_scenario(load_scenario(name).scenario, step_s=step_s)
    stub, agent = StubPolicy(), AgentPolicy()
    t_s = day.start_s
    while t_s < day.end_s:
        state = day.state_at(t_s)
        for room, items in day.bookings_at(t_s).items():
            stub.calendar(CalendarData(room=room, items=items))
            agent.calendar(room, items)
        people = {scene.zone: scene.people for scene in state.zones}
        wanted = stub.commands(state, t_s + step_s)
        got = agent.commands(occupancy(people), people, t_s + step_s)
        assert {z: (c.sensor, c.setpoint_c) for z, c in got.items()} == {
            z: (c.sensor, c.setpoint_c) for z, c in wanted.items()
        }, t_s
        t_s += step_s
