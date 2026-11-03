"""The stub agent: its policy (pure), and its worker against a fake BACnet application.

The worker's real BACnet writes to the BMS, and its back-off from an operator, are checked end
to end in ``test_world.py``.
"""

import asyncio
import time
from collections.abc import Callable, Iterator
from typing import Any

import pytest
from bacpypes3.apdu import AbortPDU, AbortReason
from bacpypes3.basetypes import PriorityValue
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
from spotcool_world.stub_agent.policy import (
    EMPTY_SETPOINT_C,
    OCCUPIED,
    UNOCCUPIED,
    Command,
    StubPolicy,
)
from spotcool_world.stub_agent.worker import StubAgentWorker
from spotcool_world.workers import WorkerState

DAY_S = 199 * 86_400.0
DEVICE = "127.0.0.1:47808"
BMS = ("127.0.0.1", 47808)
SENSORS = {zone: f"binary-value,{451 + index}" for index, zone in enumerate(Zone)}
SETPOINTS = {zone: f"analog-value,{201 + index}" for index, zone in enumerate(Zone)}
HEARTBEAT = "binary-value,901"
EMPTY = (UNOCCUPIED, EMPTY_SETPOINT_C)  # what an empty zone holds
PEOPLE = (OCCUPIED, None)  # what a zone with people holds


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


def held(commands: dict[Zone, Command]) -> dict[str, tuple[str | None, float | None]]:
    return {zone.value: (command.sensor, command.setpoint_c) for zone, command in commands.items()}


def test_an_empty_zone_is_unoccupied_at_28_and_a_full_one_follows_the_schedule() -> None:
    commands = StubPolicy().commands(state(cor=4, wes=1), at("10:00"))
    assert held(commands) == {
        "cor": PEOPLE,
        "nor": EMPTY,
        "sou": EMPTY,
        "eas": EMPTY,
        "wes": PEOPLE,
    }
    assert EMPTY_SETPOINT_C == 28.0
    assert commands[Zone.COR].reason == "4 people in the zone"
    assert commands[Zone.NOR].reason == "the zone is empty"


def test_a_booked_room_is_held_like_a_full_one_from_30_minutes_before_to_its_end() -> None:
    policy = StubPolicy()
    policy.calendar(booking("14:00", "15:00"))
    south = {t: policy.commands(state(), at(t))[Zone.SOU] for t in ("13:15", "13:30", "14:45")}
    south["15:00"] = policy.commands(state(), at("15:00"))[Zone.SOU]
    assert [(c.sensor, c.setpoint_c) for c in south.values()] == [EMPTY, PEOPLE, PEOPLE, EMPTY]
    assert south["13:30"].reason == "pre-cooling South for 'Quarterly review' at 14:00"
    assert held(policy.commands(state(), at("13:30")))["cor"] == EMPTY  # its room's zone only


def test_a_new_calendar_replaces_the_rooms_bookings() -> None:
    policy = StubPolicy()
    policy.calendar(booking("14:00", "15:00"))
    policy.calendar(CalendarData(room="meeting-south", items=[]))  # cancelled
    assert held(policy.commands(state(), at("14:00")))["sou"] == EMPTY


def test_every_slot_is_released_from_19_00() -> None:
    policy = StubPolicy()
    policy.calendar(booking("18:30", "20:00"))
    assert held(policy.commands(state(cor=3), at("18:45")))["cor"] == PEOPLE
    for t in ("19:00", "21:15"):
        released = policy.commands(state(cor=3), at(t))
        assert held(released) == dict.fromkeys(("cor", "nor", "sou", "eas", "wes"), (None, None))
        assert released[Zone.SOU].reason == "19:00, the occupied day is over"


# ---- the worker -------------------------------------------------------------------------


class FakeBacnet:
    """BACpypes3's ``NormalApplication`` for the stub: records writes, loses some on demand.

    It keeps a priority array per object: the stub's writes land in slot 10, and a test puts an
    operator's or another client's value in any slot with ``hold``.
    """

    def __init__(self, device: Any, address: Any) -> None:
        self.address: tuple[str, int] = address.addrTuple
        self.writes: list[tuple[str, Any]] = []  # object id, present value (None: NULL)
        self.lose: set[str] = set()  # object ids whose next write gets no answer
        self.unread: set[str] = set()  # object ids whose next priority-array read gets none
        self.slots: dict[str, dict[int, Any]] = {}  # object id -> priority -> present value
        self.reads: list[str] = []  # object ids whose priority array was read
        self.closed = False

    def hold(self, objid: str, priority: int, value: Any) -> None:
        """Put a value in a slot, as another client would; ``None`` releases it."""
        slots = self.slots.setdefault(objid, {})
        if value is None:
            slots.pop(priority, None)
        else:
            slots[priority] = value

    async def read_property(self, address: Any, objid: str, prop: str) -> list[Any]:
        assert (address.addrTuple, prop) == (BMS, "priority-array")
        if objid in self.unread:
            self.unread.discard(objid)
            raise AbortPDU(reason=AbortReason.noResponse)
        self.reads.append(objid)
        slots = self.slots.get(objid, {})
        return [priority_value(slots.get(priority)) for priority in range(1, 17)]

    async def write_property(
        self, address: Any, objid: str, prop: str, value: Any, priority: int | None = None
    ) -> None:
        assert (address.addrTuple, prop, priority) == (BMS, "present-value", AGENT_PRIORITY)
        if objid in self.lose:
            self.lose.discard(objid)
            raise AbortPDU(reason=AbortReason.noResponse)
        written = None if isinstance(value, Null) else value
        self.writes.append((objid, written))
        self.hold(objid, AGENT_PRIORITY, written)

    def zones(self) -> list[tuple[str, Any]]:
        """The writes to zone points: every write but the heartbeat's."""
        return [write for write in self.writes if write[0] != HEARTBEAT]

    def close(self) -> None:
        self.closed = True


def priority_value(value: Any) -> Any:
    """A slot as the BMS answers it: NULL, a binary point's enumerated state, or a Real."""
    if value is None:
        return PriorityValue(null=())
    if isinstance(value, str):
        return PriorityValue(enumerated=int(value == "active"))
    return PriorityValue(real=value)


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

    async def step(self, people: ScenarioStateData, clock: str, *bookings: CalendarData) -> None:
        """One step as the world publishes it: its state, any bookings, then its tick (close)."""
        await self.world(EventType.SCENARIO_STATE, people, clock)
        for calendar in bookings:
            await self.world(EventType.CALENDAR, calendar, clock)
        tick = TickData(index=0, step_s=900.0, clock=clock, day_of_year=200)
        await self.world(EventType.TICK, tick, clock)

    def published(self, kind: EventType) -> list[EventEnvelope]:
        return [e for e in self.events if e.type is kind and e.source is EventSource.AGENT]


def empty(zone: Zone) -> list[tuple[str, Any]]:
    """The writes that empty a zone: Unoccupied, then the setpoint at 28 degC."""
    return [(SENSORS[zone], "inactive"), (SETPOINTS[zone], EMPTY_SETPOINT_C)]


@pytest.mark.asyncio
async def test_the_stub_writes_each_zone_on_a_change_only(bacnet: list[FakeBacnet]) -> None:
    async with Stub() as stub:
        await stub.step(state(cor=2), "10:00")
        await until(lambda: len(bacnet) == 1 and len(bacnet[0].zones()) == 9)
        await stub.step(state(cor=2), "10:15")  # nothing changed
        await stub.step(state(), "10:30")  # Core empties
        await until(lambda: len(bacnet[0].zones()) == 11)
    app = bacnet[0]
    assert app.address == ("127.0.0.1", 0)  # its own port, any free one, on the BMS's address
    assert app.zones()[:11] == [
        (SENSORS[Zone.COR], "active"),  # Occupied; its setpoint is not held, so not written
        *empty(Zone.NOR),
        *empty(Zone.SOU),
        *empty(Zone.EAS),
        *empty(Zone.WES),
        *empty(Zone.COR),
    ]
    released = app.writes[-11:]  # at stop(): every priority-10 slot it holds, NULL
    every = [*SENSORS.values(), *SETPOINTS.values(), HEARTBEAT]
    assert sorted(released) == sorted((oid, None) for oid in every)
    assert app.closed
    assert stub.worker.state is WorkerState.FINISHED


@pytest.mark.asyncio
async def test_the_stub_says_what_it_sees_and_decides(bacnet: list[FakeBacnet]) -> None:
    async with Stub() as stub:
        await stub.step(state(eas=3), "09:00")
        await until(lambda: len(bacnet) == 1 and len(bacnet[0].zones()) == 9)
    perception = stub.published(EventType.PERCEPTION)
    assert [e.data for e in perception] == [
        {"stub": True, "people": {"cor": 0, "nor": 0, "sou": 0, "eas": 3, "wes": 0}}
    ]
    published = stub.published(EventType.DECISION)
    decisions, at_stop = published[:9], published[9:]
    assert [(e.data["point"], e.data["value"], e.data["reason"]) for e in at_stop] == [
        (e.data["point"], None, "the stub is stopping") for e in decisions
    ]  # stop() releases every slot it holds, by the same rules
    assert [(e.data["point"], e.data["value"]) for e in decisions] == [
        ("ZN-COR-OCC-SENSOR", "Unoccupied"),
        ("ZN-COR-CLG-SP", 28.0),
        ("ZN-NOR-OCC-SENSOR", "Unoccupied"),
        ("ZN-NOR-CLG-SP", 28.0),
        ("ZN-SOU-OCC-SENSOR", "Unoccupied"),
        ("ZN-SOU-CLG-SP", 28.0),
        ("ZN-EAS-OCC-SENSOR", "Occupied"),
        ("ZN-WES-OCC-SENSOR", "Unoccupied"),
        ("ZN-WES-CLG-SP", 28.0),
    ]
    assert decisions[6].data == {
        "stub": True,
        "zone": "eas",
        "point": "ZN-EAS-OCC-SENSOR",
        "value": "Occupied",
        "priority": 10,
        "reason": "3 people in the zone",
    }
    explanations = stub.published(EventType.EXPLANATION)[:5]
    emptied = "the zone is empty -> ZN-{0}-OCC-SENSOR Unoccupied, ZN-{0}-CLG-SP 28.0 at priority 10"
    assert [e.data["text"] for e in explanations] == [
        "Core: " + emptied.format("COR"),
        "North: " + emptied.format("NOR"),
        "South: " + emptied.format("SOU"),
        "East: 3 people in the zone -> ZN-EAS-OCC-SENSOR Occupied at priority 10",
        "West: " + emptied.format("WES"),
    ]
    assert all(e.data["stub"] is True for e in explanations)
    assert all(e.sim_time_s == at("09:00") for e in perception + decisions + explanations)
    kinds = [e.type for e in stub.events]
    assert kinds.index(EventType.TICK) < kinds.index(EventType.DECISION)  # at the step's close


@pytest.mark.asyncio
async def test_the_stub_precools_a_booking_and_releases_at_19_00(bacnet: list[FakeBacnet]) -> None:
    async with Stub() as stub:
        await stub.step(state(cor=4), "13:30")  # South empty...
        await stub.step(state(cor=4), "13:45", booking("14:00", "15:00"))  # ...but booked
        await stub.step(state(cor=4), "15:00")  # the meeting is over
        await stub.step(state(cor=4), "19:00")
        await until(lambda: len(bacnet) == 1 and len(bacnet[0].zones()) == 22)
    writes = bacnet[0].zones()
    south = [
        (oid, value) for oid, value in writes if oid in (SENSORS[Zone.SOU], SETPOINTS[Zone.SOU])
    ]
    assert south == [
        *empty(Zone.SOU),  # 13:30: empty
        (SENSORS[Zone.SOU], "active"),  # 13:45, booked: pre-cooling from 13:30
        (SETPOINTS[Zone.SOU], None),  # the schedule's setpoint while it is held
        *empty(Zone.SOU),  # 15:00
        (SENSORS[Zone.SOU], None),  # 19:00: released
        (SETPOINTS[Zone.SOU], None),
    ]
    held_at_19 = [
        SENSORS[Zone.COR],
        *(oid for z in Zone if z is not Zone.COR for oid in (SENSORS[z], SETPOINTS[z])),
    ]
    assert writes[13:] == [(oid, None) for oid in held_at_19]  # 19:00: every slot released


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
        await stub.step(state(cor=1), "10:00")
        await until(lambda: len(bacnet[0].zones()) == 8)  # Core's write was lost
        await stub.step(state(cor=1), "10:15")
        await until(lambda: len(bacnet[0].zones()) == 9)
    assert bacnet[0].zones()[8] == (SENSORS[Zone.COR], "active")


@pytest.mark.asyncio
async def test_a_stub_stopped_before_it_runs_ends_at_once(bacnet: list[FakeBacnet]) -> None:
    stub = StubAgentWorker(DEVICE, name="stub")
    await stub.stop("early")
    await asyncio.wait_for(WorkerRunner(handle_sigint=False).run(stub), 5)
    assert stub.state is WorkerState.FINISHED
    assert bacnet[0].closed
    assert bacnet[0].writes == []


# ---- backing off from an operator ---------------------------------------------------------

SOUTH_SP = SETPOINTS[Zone.SOU]
CORE_SP = SETPOINTS[Zone.COR]


def backed_off(stub: Stub) -> list[str]:
    """What the stub's back-off decisions and explanations said, in the order published."""
    said = [e for e in stub.events if e.source is EventSource.AGENT and "backed off" in str(e.data)]
    return [e.data.get("reason") or e.data["text"] for e in said]


@pytest.mark.asyncio
async def test_the_stub_backs_off_from_an_operator_says_so_once_and_resumes(
    bacnet: list[FakeBacnet],
) -> None:
    async with Stub() as stub:
        await until(lambda: len(bacnet) == 1)
        app = bacnet[0]
        app.hold(SOUTH_SP, 8, 23.0)  # an operator holds South's setpoint
        await stub.step(state(), "10:00")  # South empty: 28 wanted
        await until(lambda: len(app.zones()) == 9)
        await stub.step(state(), "10:15")  # still held
        await stub.step(state(), "10:30")
        await until(lambda: app.reads.count(SOUTH_SP) == 3)
        assert SOUTH_SP not in dict(app.zones())  # never written while slot 8 holds it
        app.hold(SOUTH_SP, 8, None)  # the operator releases
        await stub.step(state(), "10:45")
        await until(lambda: len(app.zones()) == 10)
    assert app.zones()[9] == (SOUTH_SP, EMPTY_SETPOINT_C)  # resumed with the next state
    assert backed_off(stub) == [  # once, though it tried at three states
        "backed off: held at 23.0 by an operator at priority 8",
        "South: ZN-SOU-CLG-SP is held at 23.0 by an operator at priority 8 -> backed off from "
        "writing 28.0; trying again each step",
    ]
    decision = next(e for e in stub.published(EventType.DECISION) if "backed" in e.data["reason"])
    assert decision.data | {"reason": ""} == {
        "stub": True,
        "zone": "sou",
        "point": "ZN-SOU-CLG-SP",
        "value": 28.0,
        "priority": 10,
        "reason": "",
    }
    assert decision.sim_time_s == at("10:00")
    resumed = [e.data["text"] for e in stub.published(EventType.EXPLANATION)]
    assert "South: the zone is empty -> ZN-SOU-CLG-SP 28.0 at priority 10" in resumed


@pytest.mark.asyncio
async def test_a_hold_at_19_00_is_left_alone_and_said_once(bacnet: list[FakeBacnet]) -> None:
    async with Stub() as stub:
        await stub.step(state(), "18:00")
        await until(lambda: len(bacnet) == 1 and len(bacnet[0].zones()) == 10)
        app = bacnet[0]
        app.hold(SOUTH_SP, 8, 23.0)  # over the stub's 28.0 at 10
        await stub.step(state(), "19:00")  # every slot released...
        await until(lambda: (SETPOINTS[Zone.WES], None) in app.zones())
    released = [oid for oid, value in app.zones()[10:] if value is None]
    assert len(released) == 9 and SOUTH_SP not in released  # ...but the held one, even at stop()
    assert app.slots[SOUTH_SP] == {8: 23.0, 10: EMPTY_SETPOINT_C}
    assert backed_off(stub) == [  # at 19:00, and not again at stop()
        "backed off: held at 23.0 by an operator at priority 8",
        "South: ZN-SOU-CLG-SP is held at 23.0 by an operator at priority 8 -> backed off from "
        "releasing it; trying again each step",
    ]


@pytest.mark.asyncio
async def test_a_hold_at_stop_is_left_alone(bacnet: list[FakeBacnet]) -> None:
    async with Stub() as stub:
        await stub.step(state(), "10:00")
        await until(lambda: len(bacnet) == 1 and len(bacnet[0].zones()) == 10)
        app = bacnet[0]
        app.hold(CORE_SP, 9, 26.0)
    released = [oid for oid, value in app.zones()[10:] if value is None]
    assert len(released) == 9 and CORE_SP not in released
    assert app.slots[CORE_SP] == {9: 26.0, 10: EMPTY_SETPOINT_C}
    assert backed_off(stub) == [
        "backed off: held at 26.0 by priority 9",
        "Core: ZN-COR-CLG-SP is held at 26.0 by priority 9 -> backed off from releasing it",
    ]


@pytest.mark.asyncio
async def test_the_stub_never_releases_a_slot_it_did_not_write(bacnet: list[FakeBacnet]) -> None:
    north, east = SETPOINTS[Zone.NOR], SETPOINTS[Zone.EAS]
    async with Stub() as stub:
        await stub.step(state(), "10:00")
        await until(lambda: len(bacnet) == 1 and len(bacnet[0].zones()) == 10)
        app = bacnet[0]
        app.hold(north, 10, 26.5)  # another client wrote over the stub at 10
        app.hold(east, 10, None)  # the BMS released the stub's slot (its heartbeat watchdog)
        await stub.step(state(nor=2, eas=1), "10:15")
        await until(lambda: len(app.zones()) == 12)
    assert app.zones()[10:12] == [(SENSORS[Zone.NOR], "active"), (SENSORS[Zone.EAS], "active")]
    released = {oid for oid, value in app.zones()[12:] if value is None}  # at stop()
    assert len(released) == 8 and not released & {north, east}
    assert app.slots[north] == {10: 26.5}  # left to the client that wrote it
    texts = [e.data["text"] for e in stub.published(EventType.EXPLANATION)]
    assert "North: 2 people in the zone -> ZN-NOR-OCC-SENSOR Occupied at priority 10" in texts


@pytest.mark.asyncio
async def test_an_unanswered_priority_array_is_tried_again_with_the_next_state(
    bacnet: list[FakeBacnet],
) -> None:
    async with Stub() as stub:
        await until(lambda: len(bacnet) == 1)
        app = bacnet[0]
        app.unread.add(SENSORS[Zone.COR])
        await stub.step(state(cor=1), "10:00")
        await until(lambda: len(app.zones()) == 8)  # Core's sensor was not written
        await stub.step(state(cor=1), "10:15")
        await until(lambda: len(app.zones()) == 9)
    assert app.zones()[8] == (SENSORS[Zone.COR], "active")
