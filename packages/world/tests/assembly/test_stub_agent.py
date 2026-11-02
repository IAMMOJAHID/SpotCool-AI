"""The stub agent: its policy (pure), and its worker against a fake BACnet application.

The worker's real BACnet writes to the BMS are checked end to end in ``test_world.py``.
"""

import asyncio
import time
from collections.abc import Callable, Iterator
from typing import Any

import pytest
from bacpypes3.apdu import AbortPDU, AbortReason
from bacpypes3.primitivedata import Null
from pydantic import BaseModel

from spotcool_contracts import Zone
from spotcool_contracts.calendar import DateTimeTimeZone, ScheduleItem
from spotcool_contracts.events import (
    CalendarData,
    EventEnvelope,
    EventSource,
    EventType,
    ScenarioStateData,
    TickData,
    ZoneScene,
)
from spotcool_contracts.priorities import AGENT_PRIORITY
from spotcool_world.bus import InProcessBus
from spotcool_world.processors import EVENTS_TOPIC
from spotcool_world.runner import WorkerRunner
from spotcool_world.scenarios import sim_datetime
from spotcool_world.stub_agent import worker as worker_module
from spotcool_world.stub_agent.policy import OCCUPIED, UNOCCUPIED, Command, StubPolicy
from spotcool_world.stub_agent.worker import StubAgentWorker
from spotcool_world.workers import WorkerState

DAY_S = 199 * 86_400.0
DEVICE = "127.0.0.1:47808"
BMS = ("127.0.0.1", 47808)
SENSORS = {zone: f"binary-value,{451 + index}" for index, zone in enumerate(Zone)}
HEARTBEAT = "binary-value,901"


def at(clock: str) -> float:
    hours, minutes = clock.split(":")
    return DAY_S + int(hours) * 3600 + int(minutes) * 60


def state(**people: int) -> ScenarioStateData:
    """Every zone empty, except the ones given as ``zone=people``."""
    scenes = []
    for zone in Zone:
        count = people.get(zone.value, 0)
        scenes.append(ZoneScene(zone=zone, scene="few" if count else "empty", people=count))
    return ScenarioStateData(zones=scenes)


def booking(start: str, end: str, subject: str = "Quarterly review") -> CalendarData:
    item = ScheduleItem(
        status="busy",
        subject=subject,
        location="meeting-south",
        start=DateTimeTimeZone(dateTime=sim_datetime(at(start)).isoformat()),
        end=DateTimeTimeZone(dateTime=sim_datetime(at(end)).isoformat()),
    )
    return CalendarData(room="meeting-south", items=[item])


# ---- the policy -------------------------------------------------------------------------


def values(commands: dict[Zone, Command]) -> dict[str, str | None]:
    return {zone.value: command.value for zone, command in commands.items()}


def test_people_mean_occupied_and_nobody_unoccupied() -> None:
    commands = StubPolicy().commands(state(cor=4, wes=1), at("10:00"))
    assert values(commands) == {
        "cor": OCCUPIED,
        "nor": UNOCCUPIED,
        "sou": UNOCCUPIED,
        "eas": UNOCCUPIED,
        "wes": OCCUPIED,
    }
    assert commands[Zone.COR].reason == "4 people in the zone"
    assert commands[Zone.NOR].reason == "the zone is empty"


def test_a_booked_room_is_held_occupied_from_30_minutes_before_to_its_end() -> None:
    policy = StubPolicy()
    policy.calendar(booking("14:00", "15:00"))
    south = {t: policy.commands(state(), at(t))[Zone.SOU] for t in ("13:15", "13:30", "14:45")}
    south["15:00"] = policy.commands(state(), at("15:00"))[Zone.SOU]
    assert [command.value for command in south.values()] == [
        UNOCCUPIED,
        OCCUPIED,
        OCCUPIED,
        UNOCCUPIED,
    ]
    assert south["13:30"].reason == "pre-cooling South for 'Quarterly review' at 14:00"
    assert policy.commands(state(), at("13:30"))[Zone.COR].value == UNOCCUPIED  # its room only


def test_a_new_calendar_replaces_the_rooms_bookings() -> None:
    policy = StubPolicy()
    policy.calendar(booking("14:00", "15:00"))
    policy.calendar(CalendarData(room="meeting-south", items=[]))  # cancelled
    assert policy.commands(state(), at("14:00"))[Zone.SOU].value == UNOCCUPIED


def test_every_zone_is_released_from_19_00() -> None:
    policy = StubPolicy()
    policy.calendar(booking("18:30", "20:00"))
    assert policy.commands(state(cor=3), at("18:45"))[Zone.COR].value == OCCUPIED
    for t in ("19:00", "21:15"):
        released = policy.commands(state(cor=3), at(t))
        assert values(released) == dict.fromkeys(("cor", "nor", "sou", "eas", "wes"))
        assert released[Zone.SOU].reason == "19:00, the occupied day is over"


# ---- the worker -------------------------------------------------------------------------


class FakeBacnet:
    """BACpypes3's ``NormalApplication`` for the stub: records writes, loses some on demand."""

    def __init__(self, device: Any, address: Any) -> None:
        self.address: tuple[str, int] = address.addrTuple
        self.writes: list[tuple[str, str | None]] = []  # object id, present value (None: NULL)
        self.lose: set[str] = set()  # object ids whose next write gets no answer
        self.closed = False

    async def write_property(
        self, address: Any, objid: str, prop: str, value: Any, priority: int | None = None
    ) -> None:
        assert (address.addrTuple, prop, priority) == (BMS, "present-value", AGENT_PRIORITY)
        if objid in self.lose:
            self.lose.discard(objid)
            raise AbortPDU(reason=AbortReason.noResponse)
        self.writes.append((objid, None if isinstance(value, Null) else value))

    def sensors(self) -> list[tuple[str, str | None]]:
        return [write for write in self.writes if write[0] != HEARTBEAT]

    def close(self) -> None:
        self.closed = True


@pytest.fixture
def bacnet(monkeypatch: pytest.MonkeyPatch) -> Iterator[list[FakeBacnet]]:
    """The fake BACnet applications the stub makes, in place of BACpypes3's."""
    made: list[FakeBacnet] = []

    def make(device: Any, address: Any) -> FakeBacnet:
        made.append(FakeBacnet(device, address))
        return made[-1]

    monkeypatch.setattr(worker_module, "NormalApplication", make)
    yield made


async def until(condition: Callable[[], bool], timeout_s: float = 5.0) -> None:
    deadline = time.perf_counter() + timeout_s
    for _ in range(100_000):
        if condition():
            return
        if time.perf_counter() > deadline:
            break
        await asyncio.sleep(0.005)
    raise AssertionError("timed out")


class Stub:
    """A stub agent on a bus of its own, as a context manager; ``events`` once it has stopped."""

    def __init__(self, heartbeat_s: float = 3600.0) -> None:
        self.bus = InProcessBus()
        self.worker = StubAgentWorker(DEVICE, heartbeat_s=heartbeat_s, name="stub")
        self.events: list[EventEnvelope] = []
        self._seen = self.bus.subscribe(EVENTS_TOPIC, maxsize=10_000)
        self._running: asyncio.Future[None] | None = None

    async def __aenter__(self) -> "Stub":
        runner = WorkerRunner(bus=self.bus, handle_sigint=False)
        self._running = asyncio.ensure_future(runner.run(self.worker))
        await until(lambda: self.bus.subscriber_count(EVENTS_TOPIC) == 2)  # the stub listens
        return self

    async def __aexit__(self, *_: object) -> None:
        await self.worker.stop("test")
        assert self._running is not None
        await asyncio.wait_for(self._running, 5)
        while True:  # every event published, the stub's own included
            try:
                async with asyncio.timeout(0):
                    self.events.append(await anext(self._seen))
            except TimeoutError:
                break
        self._seen.close()

    async def world(self, kind: EventType, data: BaseModel, clock: str) -> None:
        envelope = EventEnvelope.build(kind, EventSource.WORLD, data, at(clock))
        await self.bus.publish(EVENTS_TOPIC, envelope)

    def published(self, kind: EventType) -> list[EventEnvelope]:
        return [e for e in self.events if e.type is kind and e.source is EventSource.AGENT]


@pytest.mark.asyncio
async def test_the_stub_writes_each_zone_on_a_change_only(bacnet: list[FakeBacnet]) -> None:
    async with Stub() as stub:
        await stub.world(EventType.SCENARIO_STATE, state(cor=2), "10:00")
        await until(lambda: len(bacnet) == 1 and len(bacnet[0].sensors()) == 5)
        await stub.world(EventType.SCENARIO_STATE, state(cor=2), "10:15")  # nothing changed
        tick = TickData(index=5, step_s=900.0, clock="10:15", day_of_year=200)
        await stub.world(EventType.TICK, tick, "10:15")  # not for the stub
        await stub.world(EventType.SCENARIO_STATE, state(), "10:30")  # Core empties
        await until(lambda: len(bacnet[0].sensors()) == 6)
    app = bacnet[0]
    assert app.address == ("127.0.0.1", 0)  # its own port, any free one, on the BMS's address
    assert app.sensors()[:6] == [
        (SENSORS[Zone.COR], "active"),  # Occupied
        (SENSORS[Zone.NOR], "inactive"),  # Unoccupied
        (SENSORS[Zone.SOU], "inactive"),
        (SENSORS[Zone.EAS], "inactive"),
        (SENSORS[Zone.WES], "inactive"),
        (SENSORS[Zone.COR], "inactive"),
    ]
    released = app.writes[-6:]  # at stop(): every priority-10 slot it holds, NULL
    held = [*SENSORS.values(), HEARTBEAT]
    assert sorted(released) == sorted((oid, None) for oid in held)
    assert app.closed
    assert stub.worker.state is WorkerState.FINISHED


@pytest.mark.asyncio
async def test_the_stub_says_what_it_sees_and_decides(bacnet: list[FakeBacnet]) -> None:
    async with Stub() as stub:
        await stub.world(EventType.SCENARIO_STATE, state(eas=3), "09:00")
        await until(lambda: len(bacnet) == 1 and len(bacnet[0].sensors()) == 5)
    perception = stub.published(EventType.PERCEPTION)
    assert [e.data for e in perception] == [
        {"stub": True, "people": {"cor": 0, "nor": 0, "sou": 0, "eas": 3, "wes": 0}}
    ]
    decisions = stub.published(EventType.DECISION)
    assert [(e.data["point"], e.data["value"]) for e in decisions] == [
        ("ZN-COR-OCC-SENSOR", "Unoccupied"),
        ("ZN-NOR-OCC-SENSOR", "Unoccupied"),
        ("ZN-SOU-OCC-SENSOR", "Unoccupied"),
        ("ZN-EAS-OCC-SENSOR", "Occupied"),
        ("ZN-WES-OCC-SENSOR", "Unoccupied"),
    ]
    assert decisions[3].data == {
        "stub": True,
        "zone": "eas",
        "point": "ZN-EAS-OCC-SENSOR",
        "value": "Occupied",
        "priority": 10,
        "reason": "3 people in the zone",
    }
    explanations = stub.published(EventType.EXPLANATION)
    assert explanations[3].data == {
        "stub": True,
        "text": "East: 3 people in the zone -> ZN-EAS-OCC-SENSOR Occupied at priority 10",
    }
    assert all(e.sim_time_s == at("09:00") for e in perception + decisions + explanations)


@pytest.mark.asyncio
async def test_the_stub_precools_a_booking_and_releases_at_19_00(bacnet: list[FakeBacnet]) -> None:
    async with Stub() as stub:
        await stub.world(EventType.SCENARIO_STATE, state(cor=4), "13:45")  # South empty...
        await stub.world(EventType.CALENDAR, booking("14:00", "15:00"), "13:45")  # ...but booked
        await stub.world(EventType.SCENARIO_STATE, state(cor=4), "15:00")  # the meeting is over
        await stub.world(EventType.SCENARIO_STATE, state(cor=4), "19:00")
        await until(lambda: len(bacnet) == 1 and len(bacnet[0].sensors()) == 12)
    sensors = bacnet[0].sensors()
    assert [value for oid, value in sensors if oid == SENSORS[Zone.SOU]] == [
        "inactive",
        "active",  # pre-cooling from 13:30, known at 13:45
        "inactive",
        None,  # 19:00
    ]
    assert sensors[7:] == [(oid, None) for oid in SENSORS.values()]  # 19:00: all released


@pytest.mark.asyncio
async def test_the_heartbeat_toggles_on_first(bacnet: list[FakeBacnet]) -> None:
    async with Stub(heartbeat_s=0.02):
        await until(lambda: len(bacnet) == 1 and len(bacnet[0].writes) >= 4)
    beats = [value for oid, value in bacnet[0].writes if oid == HEARTBEAT]
    assert beats[:4] == ["active", "inactive", "active", "inactive"]  # On, Off, On, Off
    assert beats[-1] is None  # released at stop()


@pytest.mark.asyncio
async def test_a_write_without_an_answer_is_tried_again_with_the_next_state(
    bacnet: list[FakeBacnet],
) -> None:
    async with Stub() as stub:
        await until(lambda: len(bacnet) == 1 and len(bacnet[0].writes) == 1)  # the heartbeat
        bacnet[0].lose.add(SENSORS[Zone.COR])
        await stub.world(EventType.SCENARIO_STATE, state(cor=1), "10:00")
        await until(lambda: len(bacnet[0].sensors()) == 4)  # Core's write was lost
        await stub.world(EventType.SCENARIO_STATE, state(cor=1), "10:15")
        await until(lambda: len(bacnet[0].sensors()) == 5)
    assert bacnet[0].sensors()[4] == (SENSORS[Zone.COR], "active")


@pytest.mark.asyncio
async def test_a_stub_stopped_before_it_runs_ends_at_once(bacnet: list[FakeBacnet]) -> None:
    stub = StubAgentWorker(DEVICE, name="stub")
    await stub.stop("early")
    await asyncio.wait_for(WorkerRunner(handle_sigint=False).run(stub), 5)
    assert stub.state is WorkerState.FINISHED
    assert bacnet[0].closed
    assert bacnet[0].writes == []
