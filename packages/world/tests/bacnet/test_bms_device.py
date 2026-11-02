"""The BMS device on loopback, driven by a BACpypes3 client acting like the agent."""

import asyncio
import socket
import statistics
from collections.abc import Awaitable, Callable
from typing import Any, TypeVar, cast

import pytest
from bacpypes3.apdu import (
    ErrorRejectAbortNack,
    WritePropertyMultipleRequest,
    WritePropertyRequest,
)
from bacpypes3.basetypes import PropertyIdentifier, PropertyValue, WriteAccessSpecification
from bacpypes3.constructeddata import Any as AnyValue
from bacpypes3.ipv4.app import NormalApplication
from bacpypes3.pdu import Address
from bacpypes3.primitivedata import Null, ObjectIdentifier, Real

from spotcool_contracts import load_point_map
from spotcool_contracts.events import Run, ScenarioStateData, ZoneScene
from spotcool_contracts.points import ObjectType
from spotcool_contracts.zones import Zone
from spotcool_world.clocks import SimClock, WallPacer
from spotcool_world.frames import (
    BmsWriteFrame,
    ErrorFrame,
    Frame,
    PlantMeasurementsFrame,
    ScenarioStateFrame,
    SetpointCommandFrame,
    TickFrame,
)
from spotcool_world.pipeline import ParallelPipeline, Pipeline, PipelineParams
from spotcool_world.processors import FrameDirection, FrameProcessor, PlantProcessor, TickSource
from spotcool_world.services import WorldSettings, create_plant
from spotcool_world.services.boptest.catalog import MANAGED_INPUTS, bms_signal, zone_damper
from spotcool_world.testing import run_test
from spotcool_world.transports.bacnet.bms import BacnetBmsTransport

pytestmark = pytest.mark.bacnet

HOST = "127.0.0.1"
MAP = load_point_map("office_bms")
T0 = 198 * 86_400 + 10 * 3600  # 18 July, 10:00
STEP_S = 900.0
CORE_SP = bms_signal("ZN-COR-CLG-SP", write=True)
NORTH_SP = bms_signal("ZN-NOR-CLG-SP", write=True)
SCHEDULE_24C = {bms_signal(f"ZN-{z.value.upper()}-CLG-SP"): 297.15 for z in Zone}

_F = TypeVar("_F", bound=Frame)


class FakeClock:
    """A wall clock the test moves by hand."""

    def __init__(self) -> None:
        self.now = 0.0

    def __call__(self) -> float:
        return self.now


class Actions(FrameProcessor):
    """Runs the test's client actions as a tick passes, before the BMS sees the tick."""

    def __init__(self, actions: dict[int, Callable[[], Awaitable[None]]]) -> None:
        super().__init__(name="test::Actions")
        self._actions = actions

    async def process_frame(self, frame: Frame, direction: FrameDirection) -> None:
        await super().process_frame(frame, direction)
        if isinstance(frame, TickFrame) and frame.index in self._actions:
            await self._actions[frame.index]()
        await self.push_frame(frame, direction)


def tick(index: int) -> TickFrame:
    return TickFrame(index=index, step_s=STEP_S, sim_time_s=T0 + index * STEP_S)


def measured(run: Run, raw: dict[str, float], at_s: float = T0) -> PlantMeasurementsFrame:
    return PlantMeasurementsFrame(run=run, raw=raw, sim_time_s=at_s)


async def run(
    bms: BacnetBmsTransport,
    frames: list[Frame],
    actions: dict[int, Callable[[], Awaitable[None]]] | None = None,
) -> list[Frame]:
    """Run the BMS's two processors over ``frames``; return what came out at the bottom."""
    down, up = await run_test(
        Pipeline([Actions(actions or {}), bms.input(), bms.output()]),
        frames_to_send=frames,
        params=PipelineParams(clock=SimClock(T0, STEP_S), run_id="t"),
    )
    assert up == []
    return down


def only(frames: list[Frame], kind: type[_F]) -> list[_F]:
    return [f for f in frames if isinstance(f, kind)]


def device(bms: BacnetBmsTransport) -> Address:
    assert bms.address is not None
    return Address(bms.address)


async def read(
    client: NormalApplication, bms: BacnetBmsTransport, object_id: str, prop: str = "present-value"
) -> Any:
    return await client.read_property(device(bms), object_id, prop)


async def write(
    client: NormalApplication, bms: BacnetBmsTransport, object_id: str, value: Any, priority: int
) -> None:
    await client.write_property(device(bms), object_id, "present-value", value, priority=priority)


async def present_value(cov: Any) -> Any:
    """The next present value a COV subscription hears (skipping its status flags)."""
    while True:
        prop, value = await asyncio.wait_for(cov.get_value(), 5)
        if prop == PropertyIdentifier.presentValue:
            return value


async def refused(
    client: NormalApplication,
    bms: BacnetBmsTransport,
    object_id: str,
    value: Any,
    priority: int | None,
    prop: str = "present-value",
) -> str:
    """Write and expect the BMS to refuse; return the BACnet error text."""
    with pytest.raises(ErrorRejectAbortNack) as refusal:
        await client.write_property(device(bms), object_id, prop, value, priority=priority)
    return str(refusal.value)


@pytest.mark.asyncio
async def test_the_device_is_its_point_map(
    bms: BacnetBmsTransport, client: NormalApplication
) -> None:
    i_ams = await client.who_is(MAP.device_instance, MAP.device_instance, device(bms))
    assert [i_am.iAmDeviceIdentifier[1] for i_am in i_ams] == [1001]
    objects = await read(client, bms, "device,1001", "object-list")
    assert len(objects) == 52  # the 51 points and the device itself
    assert {f"{kind},{instance}" for kind, instance in objects} == {
        "device,1001",
        *(p.object_id for p in MAP.points),
    }
    assert await read(client, bms, "device,1001", "object-name") == "SpotCool-Office-BMS"
    for spec in MAP.points:  # read everything, as YABE does when it browses an object
        # BACpypes3 wants a flat [object, [properties], ...] list, whatever its annotation says.
        query = cast(Any, [spec.object_id, ["all"]])
        found = await client.read_property_multiple(device(bms), query)
        props = {str(prop): value for _, prop, _, value in found}
        assert props["object-name"] == spec.name
        assert props["description"] == spec.description
        if spec.units:
            assert str(props["units"]) == spec.units
        if spec.object_type is ObjectType.MULTI_STATE_VALUE:
            assert list(props["state-text"]) == spec.states
        elif spec.states:
            assert [props["inactive-text"], props["active-text"]] == spec.states
        assert ("priority-array" in props) is spec.is_commandable
        assert ("relinquish-default" in props) is spec.is_commandable


@pytest.mark.asyncio
async def test_points_show_the_spotcool_run_in_bms_units(
    bms: BacnetBmsTransport, client: NormalApplication
) -> None:
    power = {"chi_reaPChi_y": 1000.0, "hvac_reaAhu_PFanSup_y": 2000.0}  # W: 3 kW in all
    first = {"hvac_reaZonCor_TZon_y": 296.65, "hvac_reaZonCor_V_flow_y": 0.879, **power}
    later = {**first, "weaSta_reaWeaRelHum_y": 0.55, "chi_reaPChi_y": 5000.0}
    other = {"hvac_reaZonCor_TZon_y": 310.0}  # the baseline's: never shown
    await run(
        bms,
        [
            measured("spotcool", first),
            measured("baseline", other),
            measured("spotcool", later, T0 + STEP_S),
        ],
    )
    assert await read(client, bms, "analog-input,101") == pytest.approx(23.5, abs=1e-4)
    assert await read(client, bms, "analog-input,351") == pytest.approx(879.0, rel=1e-6)
    assert await read(client, bms, "analog-input,521") == pytest.approx(55.0, rel=1e-6)
    assert await read(client, bms, "analog-input,504") == pytest.approx(5.0)
    assert await read(client, bms, "analog-input,510") == pytest.approx(7.0)  # 501-507
    # Energy: the step's mean power, 7 kW, for its 15 minutes; the first state's 3 kW is
    # instantaneous and never counted (a trapezoid would say 1.25 kWh).
    assert await read(client, bms, "analog-input,511") == pytest.approx(1.75, rel=1e-6)
    assert await read(client, bms, "analog-value,950") == T0 + STEP_S


@pytest.mark.asyncio
async def test_agent_and_operator_commands_reach_the_setpoint_command(
    bms: BacnetBmsTransport, client: NormalApplication
) -> None:
    core = MAP.by_name("ZN-COR-CLG-SP").object_id
    seen: list[tuple[Any, Any]] = []

    async def show() -> None:
        ccp = await read(client, bms, core, "current-command-priority")
        seen.append((await read(client, bms, core), ccp.unsigned))

    async def agent() -> None:
        await write(client, bms, core, 26.5, 10)
        await show()

    async def operator() -> None:
        await write(client, bms, core, 27.0, 8)
        await show()

    async def agent_releases() -> None:
        await write(client, bms, core, Null(()), 10)
        await show()

    async def operator_releases() -> None:
        await write(client, bms, core, Null(()), 8)
        await show()

    down = await run(
        bms,
        [measured("baseline", SCHEDULE_24C), *(tick(i) for i in range(5))],
        {0: agent, 1: operator, 2: agent_releases, 3: operator_releases},
    )
    commands = only(down, SetpointCommandFrame)
    assert all(c.run == "spotcool" and list(c.overrides) == list(MANAGED_INPUTS) for c in commands)
    assert [c.overrides[CORE_SP] for c in commands] == [
        pytest.approx(299.65),  # the agent's 26.5 degC
        pytest.approx(300.15),  # the operator's 27 degC wins
        pytest.approx(300.15),  # the agent let go; the operator still holds
        None,  # both released: BOPTEST's own schedule applies
        None,
    ]
    assert all(c.overrides[NORTH_SP] is None for c in commands)
    assert seen == [(26.5, 10), (27.0, 8), (27.0, 8), (24.0, None)]
    writes = [w.data for w in only(down, BmsWriteFrame)]
    assert [(w.priority, w.value, w.accepted) for w in writes] == [
        (10, 26.5, True),
        (8, 27.0, True),
        (10, None, True),
        (8, None, True),
    ]
    host, port = client.normal.server.local_address
    assert {w.client for w in writes} == {f"{host}:{port}"}
    assert writes[2].reason == "priority 8 is in control"
    assert [w.sim_time_s for w in only(down, BmsWriteFrame)] == [T0] * 4


@pytest.mark.asyncio
async def test_refused_writes_answer_with_an_error_and_a_rejected_write_frame(
    bms: BacnetBmsTransport, client: NormalApplication
) -> None:
    core = MAP.by_name("ZN-COR-CLG-SP").object_id
    errors: list[str] = []

    async def try_everything() -> None:
        errors.extend(
            [
                await refused(client, bms, core, 26.0, 5),  # life-safety slots
                await refused(client, bms, core, 26.3, 10),  # off the 0.5 grid
                await refused(client, bms, core, 35.0, 10),  # outside 16..30
                await refused(client, bms, "analog-input,101", 20.0, 10),  # read-only
                await refused(client, bms, core, 20.0, None, "relinquish-default"),
                await refused(client, bms, "device,1001", "hacked", None, "object-name"),
            ]
        )

    down = await run(bms, [measured("baseline", SCHEDULE_24C), tick(0)], {0: try_everything})
    assert ["write-access-denied" in e for e in errors] == [True, False, False, True, True, True]
    assert all("value-out-of-range" in e for e in errors[1:3])
    writes = [w.data for w in only(down, BmsWriteFrame)]
    assert [(w.object_name, w.priority, w.accepted) for w in writes] == [
        ("ZN-COR-CLG-SP", 5, False),
        ("ZN-COR-CLG-SP", 10, False),
        ("ZN-COR-CLG-SP", 10, False),
        ("ZN-COR-T", 10, False),
        ("ZN-COR-CLG-SP", 16, False),  # no priority given means 16
        ("SpotCool-Office-BMS", 16, False),
    ]
    assert [w.reason for w in writes][:4] == [
        "priority 5 is reserved for life safety and critical equipment; network commands use 8-16",
        "26.3 is not a multiple of 0.5",
        "35 is outside ZN-COR-CLG-SP's range 16.0..30.0",
        "ZN-COR-T is read-only",
    ]
    assert only(down, SetpointCommandFrame)[0].overrides[CORE_SP] is None  # nothing applied
    assert await read(client, bms, core) == 24.0
    assert await read(client, bms, "device,1001", "object-name") == "SpotCool-Office-BMS"


@pytest.mark.asyncio
async def test_write_property_multiple_follows_the_same_rules(
    bms: BacnetBmsTransport, client: NormalApplication
) -> None:
    core = MAP.by_name("ZN-COR-CLG-SP").object_id

    def present_value(value: float, priority: int) -> Any:
        return PropertyValue(
            propertyIdentifier=PropertyIdentifier.presentValue,
            value=AnyValue(Real(value)),
            priority=priority,
        )

    async def write_two() -> None:  # BACpypes3 has no client call for WritePropertyMultiple
        spec = WriteAccessSpecification(
            objectIdentifier=ObjectIdentifier(core),
            listOfProperties=[present_value(26.0, 10), present_value(25.0, 3)],
        )
        request = WritePropertyMultipleRequest(
            listOfWriteAccessSpecs=[spec], destination=device(bms)
        )
        with pytest.raises(ErrorRejectAbortNack):
            await client.request(cast(Any, request))

    down = await run(bms, [measured("baseline", SCHEDULE_24C), tick(0)], {0: write_two})
    writes = [w.data for w in only(down, BmsWriteFrame)]
    assert [(w.priority, w.value, w.accepted) for w in writes] == [
        (10, 26.0, True),
        (3, 25.0, False),
    ]
    assert only(down, SetpointCommandFrame)[0].overrides[CORE_SP] == pytest.approx(299.15)


@pytest.mark.asyncio
async def test_a_cov_subscriber_hears_a_measured_change(
    bms: BacnetBmsTransport, client: NormalApplication
) -> None:
    core_t = cast(Any, ObjectIdentifier(MAP.by_name("ZN-COR-T").object_id))
    async with client.change_of_value(device(bms), core_t, 7, False, 60) as cov:
        assert await present_value(cov) == 0.0  # the first notification: nothing measured yet
        await run(bms, [measured("spotcool", {"hvac_reaZonCor_TZon_y": 296.65})])
        assert await present_value(cov) == pytest.approx(23.5, abs=1e-4)


@pytest.mark.asyncio
async def test_the_door_contact_follows_the_scenario(
    bms: BacnetBmsTransport, client: NormalApplication
) -> None:
    def door(open_: bool | None) -> ScenarioStateFrame:
        scenes = [ZoneScene(zone=Zone.WES, scene="reception", people=2, door_open=open_)]
        return ScenarioStateFrame(state=ScenarioStateData(zones=scenes))

    door_id = MAP.by_name("WES-ENTRANCE-DOOR").object_id
    assert str(await read(client, bms, door_id, "reliability")) == "communication-failure"
    shown: list[str] = []

    async def look() -> None:
        shown.append(str(await read(client, bms, door_id)))

    frames = [door(True), tick(0), door(None), tick(1), door(False), tick(2)]
    await run(bms, frames, dict.fromkeys(range(3), look))
    assert shown == ["active", "active", "inactive"]  # Open, unknown leaves it, Closed
    assert str(await read(client, bms, door_id, "reliability")) == "no-fault-detected"


@pytest.mark.asyncio
async def test_a_stopped_heartbeat_releases_every_priority_10_command_and_only_those(
    ports: tuple[int, int], client: NormalApplication
) -> None:
    clock = FakeClock()
    bms = BacnetBmsTransport(address=HOST, port=ports[0], clock=clock)
    await bms.start()
    try:
        await _heartbeat_stops(bms, client, clock)
    finally:
        await bms.stop()


async def _heartbeat_stops(
    bms: BacnetBmsTransport, client: NormalApplication, clock: FakeClock
) -> None:
    async def agent_and_operator() -> None:
        await write(client, bms, "binary-value,901", "active", 10)  # heartbeat On
        await write(client, bms, "analog-value,201", 26.0, 10)  # core, by the agent
        await write(client, bms, "analog-value,202", 27.0, 10)  # north, by the agent ...
        await write(client, bms, "analog-value,202", 28.0, 8)  # ... and by an operator

    async def silence() -> None:
        clock.now += 120.0  # no heartbeat toggle for 120 wall seconds

    down = await run(
        bms,
        [measured("baseline", SCHEDULE_24C), tick(0), tick(1)],
        {0: agent_and_operator, 1: silence},
    )
    first, second = only(down, SetpointCommandFrame)
    assert (first.overrides[CORE_SP], first.overrides[NORTH_SP]) == (
        pytest.approx(299.15),
        pytest.approx(301.15),
    )
    assert (second.overrides[CORE_SP], second.overrides[NORTH_SP]) == (
        None,
        pytest.approx(301.15),  # the operator's 28 degC stays
    )
    releases = [w.data for w in only(down, BmsWriteFrame) if w.data.reason == "heartbeat timeout"]
    assert {(w.object_name, w.priority, w.value) for w in releases} == {
        ("SPOTCOOL-HEARTBEAT", 10, None),
        ("ZN-COR-CLG-SP", 10, None),
        ("ZN-NOR-CLG-SP", 10, None),
    }
    north = await read(client, bms, "analog-value,202", "priority-array")
    assert [(i + 1, p.real) for i, p in enumerate(north) if p.real is not None] == [(8, 28.0)]
    assert str(await read(client, bms, "binary-value,901")) == "inactive"  # back to Off


@pytest.mark.asyncio
async def test_start_fails_at_once_on_a_taken_port_and_stop_frees_it(
    ports: tuple[int, int],
) -> None:
    taken = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    taken.bind((HOST, ports[0]))
    try:
        with pytest.raises(OSError) as refusal:
            await BacnetBmsTransport(address=HOST, port=ports[0]).start()
        assert refusal.value.errno is not None  # the bind's own error, not the ready timeout
    finally:
        taken.close()
    bms = BacnetBmsTransport(address=f"{HOST}/32", port=ports[0])
    await bms.start()
    assert bms.address == f"{HOST}:{ports[0]}"
    await bms.stop()
    await bms.stop()  # safe twice
    assert bms.address is None
    with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as again:
        again.bind((HOST, ports[0]))  # the device's socket is closed


@pytest.mark.parametrize("address", ["not-an-address", "10.0.0.1:47808"])
@pytest.mark.asyncio
async def test_a_bad_address_is_refused(address: str) -> None:
    with pytest.raises(ValueError, match="invalid BMS address"):
        await BacnetBmsTransport(address=address).start()


@pytest.mark.asyncio
async def test_an_address_the_pc_lost_is_reported_once_and_the_run_goes_on(
    ports: tuple[int, int],
) -> None:
    checked: list[str] = []
    network = {"same": True}

    def has_address(ip: str) -> bool:
        checked.append(ip)
        return network["same"]

    async def change_network() -> None:
        network["same"] = False  # Windows joined another saved Wi-Fi network

    bms = BacnetBmsTransport(address=HOST, port=ports[0], has_address=has_address)
    await bms.start()
    try:
        down, up = await run_test(
            Pipeline([Actions({1: change_network}), bms.input(), bms.output()]),
            frames_to_send=[tick(index) for index in range(4)],
            params=PipelineParams(clock=SimClock(T0, STEP_S), run_id="t"),
        )
    finally:
        await bms.stop()
    assert [(f.fatal, f.message) for f in only(up, ErrorFrame)] == [
        (
            False,
            f"the PC's network changed: the BMS at {HOST}:{ports[0]} is unreachable; restart "
            "the run on one network",
        )
    ]
    assert len(only(down, TickFrame)) == 4  # every tick went on
    assert checked == [HOST, HOST]  # once a tick until it was lost


@pytest.mark.asyncio
async def test_the_bms_commands_the_spotcool_run_only_even_without_a_network() -> None:
    bms = BacnetBmsTransport()  # never started: no sockets
    down = await run(bms, [measured("baseline", SCHEDULE_24C), tick(0), tick(1)])
    commands = only(down, SetpointCommandFrame)
    assert [c.run for c in commands] == ["spotcool", "spotcool"]
    assert all(c.overrides == dict.fromkeys(MANAGED_INPUTS) for c in commands)
    assert zone_damper(Zone.SOU) in commands[0].overrides


@pytest.mark.asyncio
async def test_standby_duty_cycles_an_empty_zone_all_day_against_the_rc_plant(
    bms: BacnetBmsTransport, client: NormalApplication
) -> None:
    settings = WorldSettings(plant="rc")
    start_s = 198 * 86_400 + 7 * 3600  # 07:00, in scheduled occupancy
    clock = SimClock(start_s, STEP_S)
    end_s = start_s + 49 * STEP_S  # ticks from 07:00 to 19:00
    south_t, south_flow = bms_signal("ZN-SOU-T"), bms_signal("ZN-SOU-SA-FLOW")
    south_sp, south_damper = bms_signal("ZN-SOU-CLG-SP", write=True), zone_damper(Zone.SOU)
    relinquish: list[Any] = []

    async def south_empties() -> None:  # the agent, acting as South's occupancy sensor
        await write(client, bms, MAP.by_name("ZN-SOU-OCC-SENSOR").object_id, "inactive", 10)

    async def look() -> None:
        relinquish.append(await read(client, bms, "analog-value,203", "relinquish-default"))

    plants = ParallelPipeline(
        [PlantProcessor(create_plant(settings, "spotcool"), "spotcool")],
        [PlantProcessor(create_plant(settings, "baseline"), "baseline")],
    )
    # A multi-state value answers COV only thanks to the BMS's criteria fix.
    occupancy_mode = cast(Any, ObjectIdentifier(MAP.by_name("ZN-SOU-OCC-MODE").object_id))
    async with client.change_of_value(device(bms), occupancy_mode, 11, False, 300) as cov:
        down, up = await run_test(
            Pipeline(
                [
                    TickSource(clock, WallPacer(None), end_s),
                    Actions({0: south_empties, 4: look}),
                    bms.input(),
                    plants,
                    bms.output(),
                ]
            ),
            frames_to_send=[],
            send_end=False,  # the tick source ends the run
            params=PipelineParams(clock=clock, end_time_s=end_s, run_id="standby"),
        )
        modes = [await present_value(cov) for _ in range(3)]
    assert up == []
    assert modes == [1, 2, 3]  # Occupied, Standby from 07:15, Unoccupied from 19:00
    commands = only(down, SetpointCommandFrame)
    assert [c.run for c in commands] == ["spotcool"] * 49
    for command in commands:  # no other zone is touched
        assert {k for k, v in command.overrides.items() if v is not None} <= {
            south_sp,
            south_damper,
        }
    dampers = [c.overrides[south_damper] for c in commands]
    assert dampers[:3] == [None, 0.0, 0.0]  # shut within two ticks
    assert commands[1].overrides[south_sp] == pytest.approx(297.65)  # the schedule's 24 + 0.5
    assert relinquish == [24.5]
    # All day long the damper duty-cycles: shut on most ticks (26 of 47 on the RC plant) ...
    in_standby = dampers[1:48]  # 07:15 to 18:45
    assert set(in_standby) == {0.0, None}
    assert sum(d == 0.0 for d in in_standby) / len(in_standby) > 0.5
    # ... and South gets well below the baseline's air (343 against 487 L/s on the RC plant).
    states = {(f.run, f.sim_time_s): f.raw for f in only(down, PlantMeasurementsFrame)}
    stamps = [start_s + k * STEP_S for k in range(2, 49)]  # 07:30 to 19:00
    spotcool_lps = statistics.mean(states["spotcool", t][south_flow] for t in stamps)
    baseline_lps = statistics.mean(states["baseline", t][south_flow] for t in stamps)
    assert spotcool_lps < 0.8 * baseline_lps
    shut_s = start_s + 2 * STEP_S  # the end of the first step with South's airflow off
    assert states["spotcool", shut_s][south_t] > states["baseline", shut_s][south_t] + 0.1
    assert states["baseline", shut_s][south_damper] > 0.1  # the baseline keeps its own control
    # At 19:00 the occupied hours are over, though the baseline still reports 24 degC.
    assert states["baseline", start_s + 48 * STEP_S][south_sp] == pytest.approx(297.15)
    assert (commands[48].overrides[south_sp], commands[48].overrides[south_damper]) == (None, None)
    sensor_write = only(down, BmsWriteFrame)[0]
    assert (sensor_write.data.value, sensor_write.data.accepted) == ("Unoccupied", True)
    assert sensor_write.sim_time_s == start_s


@pytest.mark.asyncio
async def test_a_point_reads_as_faulted_until_its_first_value(
    bms: BacnetBmsTransport, client: NormalApplication
) -> None:
    async def health(object_id: str) -> tuple[str, int]:
        flags = await read(client, bms, object_id, "status-flags")
        return str(await read(client, bms, object_id, "reliability")), flags[1]  # fault

    core_t, north_t = "analog-input,101", "analog-input,102"
    mode, setpoint, sensor = "multi-state-value,401", "analog-value,201", "binary-value,451"
    for object_id in (core_t, north_t, mode, setpoint, "analog-input,511"):
        assert await health(object_id) == ("communication-failure", 1)
    assert await health(sensor) == ("no-fault-detected", 0)  # a relinquish default is a value
    await run(bms, [measured("spotcool", {"hvac_reaZonCor_TZon_y": 296.65})])
    assert await health(core_t) == ("no-fault-detected", 0)
    assert await health(north_t) == ("communication-failure", 1)  # still not measured
    assert await health(mode) == ("communication-failure", 1)  # no schedule yet
    await run(bms, [measured("baseline", SCHEDULE_24C)])
    assert await health(mode) == ("no-fault-detected", 0)
    assert await health(setpoint) == ("no-fault-detected", 0)


@pytest.mark.asyncio
async def test_a_new_run_starts_the_meters_and_the_measurements_afresh(
    bms: BacnetBmsTransport, client: NormalApplication
) -> None:
    power = {"chi_reaPChi_y": 4000.0}
    await run(bms, [measured("spotcool", power), measured("spotcool", power, T0 + STEP_S)])
    assert await read(client, bms, "analog-input,511") == pytest.approx(1.0)  # 4 kW, 15 min
    await run(bms, [])  # a new run: nothing measured yet
    for object_id in ("analog-input,504", "analog-input,511", "analog-value,950"):
        assert str(await read(client, bms, object_id, "reliability")) == "communication-failure"
    await run(bms, [measured("spotcool", power)])
    assert await read(client, bms, "analog-input,511") == 0.0  # the new run's meter


@pytest.mark.asyncio
async def test_a_write_without_a_priority_is_at_16_and_a_bare_null_releases_it(
    bms: BacnetBmsTransport, client: NormalApplication
) -> None:
    core = MAP.by_name("ZN-COR-CLG-SP").object_id
    held: list[tuple[Any, Any]] = []

    async def write_and_release() -> None:
        await client.write_property(device(bms), core, "present-value", 26.0)  # no priority
        ccp = await read(client, bms, core, "current-command-priority")
        held.append((await read(client, bms, core), ccp.unsigned))
        release = WritePropertyRequest(  # a NULL without a priority: built by hand
            objectIdentifier=ObjectIdentifier(core),
            propertyIdentifier=PropertyIdentifier.presentValue,
            propertyValue=AnyValue(Null(())),
            destination=device(bms),
        )
        await client.request(cast(Any, release))

    down = await run(bms, [measured("baseline", SCHEDULE_24C), tick(0)], {0: write_and_release})
    assert held == [(26.0, 16)]
    writes = [(w.data.priority, w.data.value, w.data.accepted) for w in only(down, BmsWriteFrame)]
    assert writes == [(16, 26.0, True), (16, None, True)]
    assert only(down, SetpointCommandFrame)[0].overrides[CORE_SP] is None  # back to the schedule


@pytest.mark.asyncio
async def test_a_present_value_write_with_an_array_index_is_refused(
    bms: BacnetBmsTransport, client: NormalApplication
) -> None:
    core = MAP.by_name("ZN-COR-CLG-SP").object_id
    errors: list[str] = []

    async def write_indexed() -> None:
        with pytest.raises(ErrorRejectAbortNack) as refusal:
            await client.write_property(
                device(bms), core, "present-value", 26.0, array_index=3, priority=10
            )
        errors.append(str(refusal.value))

    down = await run(bms, [measured("baseline", SCHEDULE_24C), tick(0)], {0: write_indexed})
    assert len(errors) == 1 and "property: property-is-not-an-array" in errors[0]
    [record] = [w.data for w in only(down, BmsWriteFrame)]
    assert (record.accepted, record.reason) == (False, "present-value is not an array")
    assert only(down, SetpointCommandFrame)[0].overrides[CORE_SP] is None
