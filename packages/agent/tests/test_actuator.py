import asyncio
from typing import Any

import pytest
from pydantic import BaseModel

from spotcool_agent.actuator import STOPPING, Actuator
from spotcool_agent.domain.events import Value
from spotcool_agent.processors.policy import EMPTY_SETPOINT_C, OCCUPIED, UNOCCUPIED, Command
from spotcool_contracts import Zone
from spotcool_contracts.events import EventType
from spotcool_contracts.points import PointSpec

EMPTY = Command(UNOCCUPIED, EMPTY_SETPOINT_C, "the zone is empty")
PEOPLE = Command(OCCUPIED, None, "3 people on camera")
SENSOR, SETPOINT, HEARTBEAT = "ZN-SOU-OCC-SENSOR", "ZN-SOU-CLG-SP", "SPOTCOOL-HEARTBEAT"
T_S = 199 * 86_400.0 + 9 * 3600


class FakeBms:
    """Priority arrays by point name; records every write the agent makes, in order."""

    def __init__(self) -> None:
        self.arrays: dict[str, list[Value]] = {}
        self.writes: list[tuple[str, Value]] = []
        self.refuse: set[str] = set()

    def array(self, name: str) -> list[Value]:
        return self.arrays.setdefault(name, [None] * 16)

    async def slots(self, point: PointSpec) -> list[Value] | None:
        return list(self.array(point.name))

    async def write(self, point: PointSpec, value: Value) -> bool:
        self.writes.append((point.name, value))
        if point.name in self.refuse:
            return False
        self.array(point.name)[9] = value
        return True


@pytest.fixture
def bms() -> FakeBms:
    return FakeBms()


@pytest.fixture
def events() -> list[tuple[EventType, dict[str, Any], float | None]]:
    return []


@pytest.fixture
def actuator(bms: FakeBms, events: list[Any]) -> Actuator:
    def publish(kind: EventType, data: BaseModel, t_s: float | None) -> None:
        events.append((kind, data.model_dump(), t_s))

    return Actuator(bms, publish, heartbeat_s=0.05)


@pytest.mark.asyncio
async def test_points_are_written_only_when_their_value_changes(
    actuator: Actuator, bms: FakeBms, events: list[Any]
) -> None:
    await actuator.apply({Zone.SOU: EMPTY}, T_S)
    await actuator.apply({Zone.SOU: EMPTY}, T_S + 900)
    assert bms.writes == [(SENSOR, "Unoccupied"), (SETPOINT, 28.0)]
    assert [(kind, data.get("point"), t_s) for kind, data, t_s in events] == [
        (EventType.DECISION, SENSOR, T_S),
        (EventType.DECISION, SETPOINT, T_S),
        (EventType.EXPLANATION, None, T_S),
    ]
    await actuator.apply({Zone.SOU: PEOPLE}, T_S + 1800)
    assert bms.writes[2:] == [(SETPOINT, None), (SENSOR, "Occupied")]  # the setpoint back first
    assert actuator.held == {SENSOR: "Occupied"}


@pytest.mark.asyncio
async def test_it_backs_off_from_an_operator_at_8_says_so_once_and_resumes(
    actuator: Actuator, bms: FakeBms, events: list[Any]
) -> None:
    bms.array(SETPOINT)[7] = 23.0  # an operator at priority 8
    await actuator.apply({Zone.SOU: EMPTY}, T_S)
    await actuator.apply({Zone.SOU: EMPTY}, T_S + 900)
    assert bms.writes == [(SENSOR, "Unoccupied")]  # the setpoint left alone, both steps
    said = [data for kind, data, _ in events if "backed off" in str(data)]
    assert said == [
        {
            "zone": Zone.SOU,
            "point": SETPOINT,
            "value": 28.0,
            "priority": 10,
            "reason": "backed off: held at 23.0 by an operator at priority 8",
        },
        {
            "text": "South: ZN-SOU-CLG-SP is held at 23.0 by an operator at priority 8 -> backed "
            "off from writing 28.0; trying again each step"
        },
    ]
    bms.array(SETPOINT)[7] = None  # the operator lets go
    await actuator.apply({Zone.SOU: EMPTY}, T_S + 1800)
    assert bms.writes[-1] == (SETPOINT, 28.0)


@pytest.mark.asyncio
async def test_it_never_releases_a_slot_it_did_not_write(actuator: Actuator, bms: FakeBms) -> None:
    await actuator.apply({Zone.SOU: EMPTY}, T_S)
    bms.array(SETPOINT)[9] = 26.0  # another client took slot 10
    bms.array(SENSOR)[9] = None  # the BMS's watchdog released this one
    await actuator.apply({Zone.SOU: PEOPLE}, T_S + 900)
    assert bms.writes[2:] == [(SENSOR, "Occupied")]  # no release of the setpoint
    assert bms.array(SETPOINT)[9] == 26.0
    assert actuator.held == {SENSOR: "Occupied"}  # the setpoint is forgotten


@pytest.mark.asyncio
async def test_a_refused_write_is_not_held_and_is_tried_again(
    actuator: Actuator, bms: FakeBms
) -> None:
    bms.refuse.add(SETPOINT)
    await actuator.apply({Zone.SOU: EMPTY}, T_S)
    bms.refuse.clear()
    await actuator.apply({Zone.SOU: EMPTY}, T_S + 900)
    assert bms.writes == [(SENSOR, "Unoccupied"), (SETPOINT, 28.0), (SETPOINT, 28.0)]


@pytest.mark.asyncio
async def test_stop_ends_the_heartbeat_then_releases_the_zones_then_the_heartbeat(
    actuator: Actuator, bms: FakeBms, events: list[Any]
) -> None:
    actuator.start()
    await asyncio.sleep(0.12)  # On, Off, On
    await actuator.apply({Zone.SOU: EMPTY, Zone.COR: EMPTY}, T_S)
    beats = [value for name, value in bms.writes if name == HEARTBEAT]
    assert beats[:2] == ["On", "Off"]
    await actuator.stop()
    released = bms.writes[-5:]
    assert released == [
        ("ZN-COR-OCC-SENSOR", None),
        ("ZN-COR-CLG-SP", None),
        (SENSOR, None),
        (SETPOINT, None),
        (HEARTBEAT, None),
    ]
    count = len(bms.writes)
    await asyncio.sleep(0.12)
    assert len(bms.writes) == count  # no heartbeat after its release
    assert actuator.held == {}
    reasons = {data["reason"] for kind, data, _ in events[-5:] if kind is EventType.DECISION}
    assert reasons == {STOPPING}
    await actuator.stop()  # twice is safe
    assert len(bms.writes) == count


@pytest.mark.asyncio
@pytest.mark.parametrize("how", ["a zone release", "19:00", "stop"])
async def test_its_own_slot_is_released_under_an_operators_hold(
    how: str, actuator: Actuator, bms: FakeBms, events: list[Any]
) -> None:
    await actuator.apply({Zone.SOU: EMPTY}, T_S)
    bms.array(SETPOINT)[7] = 23.0  # an operator at priority 8
    if how == "stop":
        await actuator.stop()
    else:
        release = PEOPLE if how == "a zone release" else Command(None, None, "19:00")
        await actuator.apply({Zone.SOU: release}, T_S + 900)
    assert (bms.array(SETPOINT)[7], bms.array(SETPOINT)[9]) == (23.0, None)
    assert not [data for _, data, _ in events if "backed off" in str(data)]


@pytest.mark.asyncio
async def test_another_clients_value_at_10_is_never_released_even_under_a_hold(
    actuator: Actuator, bms: FakeBms
) -> None:
    await actuator.apply({Zone.SOU: EMPTY}, T_S)
    bms.array(SETPOINT)[7], bms.array(SETPOINT)[9] = 23.0, 26.0
    await actuator.stop()
    assert bms.array(SETPOINT)[9] == 26.0


@pytest.mark.asyncio
async def test_a_write_cut_off_in_flight_is_still_released_at_stop(bms: FakeBms) -> None:
    landed = asyncio.Event()

    class Hanging(FakeBms):
        async def write(self, point: PointSpec, value: Value) -> bool:
            if point.name == SETPOINT and value == 28.0:
                self.array(point.name)[9] = value  # the BMS took it ...
                landed.set()
                await asyncio.sleep(10)  # ... but its answer never came back in time
            return await super().write(point, value)

    hanging = Hanging()
    actuator = Actuator(hanging, lambda kind, data, t_s: None)
    applying = asyncio.create_task(actuator.apply({Zone.SOU: EMPTY}, T_S))
    await asyncio.wait_for(landed.wait(), 1)
    applying.cancel()  # Ctrl+C mid-write
    await asyncio.gather(applying, return_exceptions=True)
    assert SETPOINT not in actuator.held
    await actuator.stop()
    assert hanging.array(SETPOINT)[9] is None
    assert hanging.writes[-1] == (SETPOINT, None)


@pytest.mark.asyncio
async def test_reset_releases_and_forgets_every_zone_point_but_keeps_the_heartbeat(
    actuator: Actuator, bms: FakeBms
) -> None:
    actuator.start()
    await asyncio.sleep(0.01)
    await actuator.apply({Zone.SOU: EMPTY}, T_S)
    bms.array(SENSOR)[9] = None  # gone already: forgotten without a write
    await actuator.reset()
    assert bms.array(SETPOINT)[9] is None
    assert set(actuator.held) == {HEARTBEAT}
    await actuator.stop()
