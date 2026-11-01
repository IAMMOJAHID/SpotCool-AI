from datetime import datetime
from typing import Any

import pytest

from spotcool_contracts import Zone
from spotcool_world.scenarios import (
    SIM_EPOCH,
    CompiledScenario,
    Scenario,
    compile_scenario,
    load_scenario,
    sim_datetime,
)

STEP_S = 900.0


def at(clock: str, day: int = 199) -> float:
    hours, minutes = clock.split(":")
    return day * 86_400 + int(hours) * 3600 + int(minutes) * 60


def entry(clock: str, scene: str, people: int) -> dict[str, Any]:
    return {"at": clock, "scene": scene, "people": people}


def compiled(
    *, zones: dict[str, Any] | None = None, events: list[Any] | None = None
) -> CompiledScenario:
    empty = [entry("07:00", "empty", 0)]
    data = {
        "name": "test_day",
        "day": 199,
        "start": "07:00",
        "end": "19:00",
        "zones": {zone.value: empty for zone in Zone} | (zones or {}),
        "events": events or [],
    }
    return compile_scenario(Scenario.model_validate(data), step_s=STEP_S)


def tick_times(scenario: CompiledScenario) -> list[float]:
    count = int((scenario.end_s - scenario.start_s) // STEP_S)
    return [scenario.start_s + i * STEP_S for i in range(count)]


@pytest.fixture(scope="module")
def tuesday() -> CompiledScenario:
    return compile_scenario(load_scenario("hybrid_tuesday").scenario, step_s=STEP_S)


def test_day_199_starts_at_its_boptest_second(tuesday: CompiledScenario) -> None:
    assert tuesday.start_s == 199 * 86400 + 25200
    assert tuesday.end_s == 199 * 86400 + 19 * 3600


def test_an_off_grid_time_does_not_compile() -> None:
    with pytest.raises(ValueError, match="08:40 is not on the 15-minute step grid"):
        compiled(zones={"cor": [entry("07:00", "empty", 0), entry("08:40", "many", 4)]})


def test_every_error_is_listed_when_compiling_fails() -> None:
    door = {"type": "door_open", "zone": "cor", "from": "10:45", "to": "10:00"}
    with pytest.raises(ValueError) as failure:
        compiled(events=[door])
    message = str(failure.value)
    assert "events.0.to: from 10:45 is not before to 10:00" in message
    assert "events.0.zone: zone cor has no door contact" in message


def test_the_date_mapping() -> None:
    assert SIM_EPOCH == datetime(2026, 1, 1)
    assert sim_datetime(at("14:00")) == datetime(2026, 7, 19, 14, 0)
    assert sim_datetime(0) == SIM_EPOCH


def test_an_entry_applies_from_its_time_inclusive(tuesday: CompiledScenario) -> None:
    def core(t_s: float) -> tuple[str, int]:
        scene = tuesday.state_at(t_s).zones[0]
        return scene.scene, scene.people

    assert core(at("07:00")) == ("empty", 0)
    assert core(at("08:30") - 1) == ("empty", 0)
    assert core(at("08:30")) == ("many", 4)
    assert core(at("12:29")) == ("many", 4)
    assert core(at("12:30")) == ("few", 1)
    assert core(at("19:00")) == ("empty", 0)  # the last entries hold to the end and after


def test_every_state_lists_the_five_zones_in_zone_order(tuesday: CompiledScenario) -> None:
    for t_s in tick_times(tuesday):
        assert [scene.zone for scene in tuesday.state_at(t_s).zones] == list(Zone)


def test_a_time_before_the_start_has_no_state(tuesday: CompiledScenario) -> None:
    with pytest.raises(ValueError, match="before the scenario's start"):
        tuesday.state_at(tuesday.start_s - 1)


def test_the_door_is_open_only_in_its_window_and_none_without_a_contact(
    tuesday: CompiledScenario,
) -> None:
    def doors(t_s: float) -> dict[Zone, bool | None]:
        return {scene.zone: scene.door_open for scene in tuesday.state_at(t_s).zones}

    closed = {Zone.COR: None, Zone.NOR: None, Zone.SOU: None, Zone.EAS: None, Zone.WES: False}
    assert doors(at("09:45")) == closed
    assert doors(at("10:00")) == closed | {Zone.WES: True}
    assert doors(at("10:44")) == closed | {Zone.WES: True}
    assert doors(at("10:45")) == closed
    assert tuesday.state_at(at("10:15")).active_events == ["door_open:wes"]
    assert tuesday.state_at(at("10:45")).active_events == []


def test_clothing_holds_until_the_next_change_and_is_none_in_an_empty_zone() -> None:
    core = [
        entry("07:00", "empty", 0),
        entry("08:30", "many", 4),
        entry("12:00", "empty", 0),
        entry("13:00", "few", 2),
    ]
    events = [
        {"type": "clothing", "zone": "cor", "at": "07:00", "clothing": "light"},
        {"type": "clothing", "zone": "cor", "at": "12:30", "clothing": "heavy"},
    ]
    scenario = compiled(zones={"cor": core}, events=events)

    def clothing(clock: str) -> str | None:
        return scenario.state_at(at(clock)).zones[0].clothing

    assert clothing("07:00") is None  # nobody there to wear it
    assert clothing("08:30") == "light"
    assert clothing("12:00") is None
    assert clothing("12:45") is None  # heavy from 12:30, but the zone is empty
    assert clothing("13:00") == "heavy"
    assert scenario.state_at(at("13:00")).zones[1].clothing is None  # North had no change


def test_the_south_jacket_goes_on_at_14_30(tuesday: CompiledScenario) -> None:
    def south(clock: str) -> str | None:
        return tuesday.state_at(at(clock)).zones[2].clothing

    assert [south(c) for c in ("14:15", "14:30", "14:45", "15:00")] == [
        None,
        "heavy",
        "heavy",
        None,
    ]


def test_a_booking_appears_at_booked_at_and_is_active_while_booked(
    tuesday: CompiledScenario,
) -> None:
    assert tuesday.booking_change_times == (at("13:45"),)
    assert tuesday.bookings_at(at("13:30")) == {"meeting-south": []}
    [item] = tuesday.bookings_at(at("13:45"))["meeting-south"]
    assert item.model_dump() == {
        "status": "busy",
        "subject": "Quarterly review",
        "location": "meeting-south",
        "start": {"dateTime": "2026-07-19T14:00:00", "timeZone": "UTC"},
        "end": {"dateTime": "2026-07-19T15:00:00", "timeZone": "UTC"},
        "isPrivate": False,
    }
    assert tuesday.bookings_at(at("18:45"))["meeting-south"] == [item]  # past meetings stay
    assert tuesday.state_at(at("13:45")).active_events == []
    assert tuesday.state_at(at("14:00")).active_events == ["booking:meeting-south"]
    assert tuesday.state_at(at("15:00")).active_events == []


def test_a_booking_without_booked_at_is_visible_from_the_start() -> None:
    booking = {"type": "booking", "room": "meeting-south", "from": "09:00", "to": "10:00"}
    scenario = compiled(events=[booking])
    assert scenario.booking_change_times == (scenario.start_s,)
    assert len(scenario.bookings_at(scenario.start_s)["meeting-south"]) == 1


def test_bookings_come_in_start_order_and_show_once_in_active_events() -> None:
    late = {"type": "booking", "room": "meeting-south", "from": "11:00", "to": "12:00"}
    early = {"type": "booking", "room": "meeting-south", "from": "10:00", "to": "11:30"}
    scenario = compiled(events=[late, early])
    items = scenario.bookings_at(at("07:00"))["meeting-south"]
    assert [item.start.dateTime[11:16] for item in items] == ["10:00", "11:00"]
    assert scenario.state_at(at("11:15")).active_events == ["booking:meeting-south"]


def test_cues_fire_exactly_once_over_a_run_including_one_at_the_start() -> None:
    def cue(clock: str) -> dict[str, Any]:
        return {"type": "complaint", "zone": "sou", "at": clock, "channel": "chat", "text": clock}

    scenario = compiled(events=[cue("10:00"), cue("07:00"), cue("18:45"), cue("10:00")])
    fired: list[tuple[float, str]] = []
    previous: float | None = None
    for t_s in tick_times(scenario):
        fired += [(t_s, c.text) for c in scenario.cues_between(previous, t_s)]
        previous = t_s
    assert fired == [
        (at("07:00"), "07:00"),
        (at("10:00"), "10:00"),
        (at("10:00"), "10:00"),
        (at("18:45"), "18:45"),
    ]


def test_a_cue_window_is_open_below_and_closed_above(tuesday: CompiledScenario) -> None:
    def texts(t0_s: float | None, t1_s: float) -> list[str]:
        return [cue.text for cue in tuesday.cues_between(t0_s, t1_s)]

    line = "It's freezing in this meeting room."
    assert texts(at("14:00"), at("14:15")) == [line]
    assert texts(at("14:15"), at("14:30")) == []
    assert texts(at("13:30"), at("14:30")) == [line]  # a coarser step still fires it once
    assert texts(None, at("14:15")) == [line]  # a first tick at the cue's own time
    assert texts(None, at("14:30")) == []  # a first tick after it: [t, t] only
