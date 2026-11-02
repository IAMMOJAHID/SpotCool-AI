"""The VRF gateway on loopback, driven by a BACpypes3 client acting like the agent."""

import asyncio
import socket
from collections.abc import Awaitable, Callable
from typing import Any, cast

import pytest
from bacpypes3.apdu import ErrorRejectAbortNack
from bacpypes3.basetypes import PropertyIdentifier
from bacpypes3.ipv4.app import NormalApplication
from bacpypes3.pdu import Address
from bacpypes3.primitivedata import Null, Unsigned

from spotcool_contracts import load_point_map, load_vrf_profiles
from spotcool_contracts.events import BmsWriteData, EventEnvelope, EventSource, EventType
from spotcool_contracts.points import ObjectType
from spotcool_world.bus import InProcessBus, Subscription
from spotcool_world.clocks import SimClock
from spotcool_world.processors import EVENTS_TOPIC
from spotcool_world.transports.bacnet import vrf as vrf_module
from spotcool_world.transports.bacnet.server import open_application
from spotcool_world.transports.bacnet.vrf import BacnetVrfGateway

pytestmark = pytest.mark.bacnet

HOST = "127.0.0.1"
T0 = 198 * 86_400 + 10 * 3600  # the gateways' simulation time (conftest)
MakeGateway = Callable[[str], Awaitable[BacnetVrfGateway]]

MAP = load_point_map("vrf_gateway")
OID = {p.name: p.object_id for p in MAP.points}


def device(gateway: BacnetVrfGateway) -> Address:
    assert gateway.address is not None
    return Address(gateway.address)


async def read(
    client: NormalApplication, gateway: BacnetVrfGateway, point: str, prop: str = "present-value"
) -> Any:
    return await client.read_property(device(gateway), OID[point], prop)


async def write(
    client: NormalApplication, gateway: BacnetVrfGateway, point: str, value: Any, priority: int
) -> None:
    await client.write_property(
        device(gateway), OID[point], "present-value", value, priority=priority
    )


async def refused(
    client: NormalApplication, gateway: BacnetVrfGateway, point: str, value: Any, priority: int
) -> str:
    """Write and expect the gateway to refuse; return the BACnet error text."""
    with pytest.raises(ErrorRejectAbortNack) as refusal:
        await write(client, gateway, point, value, priority)
    return str(refusal.value)


async def taken(subscription: Subscription) -> list[EventEnvelope]:
    """The messages waiting on a subscription, without waiting for more."""
    messages: list[EventEnvelope] = []
    while True:
        try:
            async with asyncio.timeout(0):
                messages.append(await anext(subscription))
        except TimeoutError:
            return messages


@pytest.mark.asyncio
async def test_the_device_is_its_point_map(
    make_gateway: MakeGateway, client: NormalApplication
) -> None:
    gateway = await make_gateway("mitsubishi")
    i_ams = await client.who_is(2001, 2001, device(gateway))
    assert [i_am.iAmDeviceIdentifier[1] for i_am in i_ams] == [2001]
    objects = await client.read_property(device(gateway), "device,2001", "object-list")
    assert {f"{kind},{instance}" for kind, instance in objects} == {"device,2001", *OID.values()}
    name = await client.read_property(device(gateway), "device,2001", "object-name")
    assert name == "VRF-Gateway-MeetingRoom"
    for spec in MAP.points:
        query = cast(Any, [spec.object_id, ["all"]])  # BACpypes3 wants a flat list
        found = await client.read_property_multiple(device(gateway), query)
        props = {str(prop): value for _, prop, _, value in found}
        assert props["object-name"] == spec.name
        if spec.object_type is ObjectType.MULTI_STATE_VALUE:
            assert list(props["state-text"]) == spec.states
        assert ("priority-array" in props) is spec.is_commandable
    defaults = [await read(client, gateway, p.name) for p in MAP.commandable()]
    assert [str(d) for d in defaults] == ["active", "24.0", "1", "1", "3", "3"]  # On ... Pos3
    assert await read(client, gateway, "IDU-ROOM-T") == pytest.approx(30.0)  # ambient
    assert await read(client, gateway, "IDU-PWR") > 0


@pytest.mark.asyncio
async def test_a_louver_write_at_10_slews_to_its_angle(
    make_gateway: MakeGateway, client: NormalApplication, run_worker: Any
) -> None:
    gateway = await make_gateway("mitsubishi")
    await write(client, gateway, "IDU-VANE-V", 4, 10)  # Pos4: -45 deg
    assert await read(client, gateway, "IDU-VANE-V") == 4
    assert gateway.unit.vane_v == "Pos4"  # at once
    assert await read(client, gateway, "IDU-VANE-V-ANGLE") == 0.0  # not yet moved
    first = await run_worker(gateway, 2.0)
    assert await read(client, gateway, "IDU-VANE-V-ANGLE") == pytest.approx(-30.0)  # 15 deg/s
    second = await run_worker(gateway, 2.0)
    assert await read(client, gateway, "IDU-VANE-V-ANGLE") == pytest.approx(-45.0)
    angles = [state.vane_v_angle_deg for _, state in (*first.published, *second.published)]
    assert angles[0] == 0.0 and angles[-1] == -45.0
    assert all(b <= a for a, b in zip(angles, angles[1:], strict=False))  # it slewed down
    arrived = next(t for t, state in second.published if state.vane_v_angle_deg == -45.0)
    assert 2.0 + arrived >= 3.0  # 0 to -45 deg at 15 deg/s


@pytest.mark.asyncio
async def test_the_operator_at_8_beats_the_agent_at_10_and_null_releases(
    make_gateway: MakeGateway, client: NormalApplication, bus: InProcessBus
) -> None:
    gateway = await make_gateway("mitsubishi")
    events = bus.subscribe(EVENTS_TOPIC)
    seen: list[tuple[Any, str]] = []

    async def look() -> None:
        seen.append((await read(client, gateway, "IDU-VANE-H"), gateway.unit.vane_h))

    await write(client, gateway, "IDU-VANE-H", 2, 10)  # the agent: Pos2
    await look()
    await write(client, gateway, "IDU-VANE-H", 5, 8)  # the operator: Pos5
    await look()
    await write(client, gateway, "IDU-VANE-H", 1, 10)  # the agent again: Pos1
    await look()
    ccp = await read(client, gateway, "IDU-VANE-H", "current-command-priority")
    assert ccp.unsigned == 8
    await write(client, gateway, "IDU-VANE-H", Null(()), 8)  # the operator lets go
    await look()
    await write(client, gateway, "IDU-VANE-H", Null(()), 10)  # and the agent
    await look()
    assert seen == [(2, "Pos2"), (5, "Pos5"), (5, "Pos5"), (1, "Pos1"), (3, "Pos3")]
    writes = [BmsWriteData.model_validate(e.data) for e in await taken(events)]
    assert [(w.priority, w.value, w.accepted, w.reason) for w in writes] == [
        (10, "Pos2", True, None),
        (8, "Pos5", True, None),
        (10, "Pos1", True, "priority 8 is in control"),
        (8, None, True, None),
        (10, None, True, None),
    ]
    events.close()


@pytest.mark.asyncio
async def test_refused_writes_answer_with_the_brands_bacnet_error(
    make_gateway: MakeGateway, client: NormalApplication
) -> None:
    daikin = await make_gateway("daikin")
    errors = [
        await refused(client, daikin, "IDU-VANE-H", 1, 10),  # Daikin has no horizontal louver
        await refused(client, daikin, "IDU-SP", 24.3, 10),  # off the 0.5 grid
        await refused(client, daikin, "IDU-SP", 31.0, 10),  # outside 16..30
        await refused(client, daikin, "IDU-VANE-V", 7, 10),  # no seventh state
        await refused(client, daikin, "IDU-VANE-V", 1, 5),  # life-safety slots
        await refused(client, daikin, "IDU-ROOM-T", 20.0, 10),  # read-only
    ]
    assert ["write-access-denied" in e for e in errors] == [True, False, False, False, True, True]
    assert all("value-out-of-range" in e for e in errors[1:4])
    await write(client, daikin, "IDU-VANE-H", Null(()), 10)  # a release is always accepted
    await write(client, daikin, "IDU-VANE-V", 6, 10)  # Swing
    assert daikin.unit.vane_v == "Swing"
    await daikin.stop()

    lg = await make_gateway("lg")
    for position in range(1, 6):
        error = await refused(client, lg, "IDU-VANE-V", position, 10)
        assert "value-out-of-range" in error
    assert "write-access-denied" in await refused(client, lg, "IDU-VANE-H", 6, 10)
    await write(client, lg, "IDU-VANE-V", 6, 10)  # Swing: the one state LG has
    assert lg.unit.vane_v == "Swing"
    await write(client, lg, "IDU-VANE-V", Null(()), 10)  # back to Pos3: swing off, held
    assert lg.unit.vane_v == "Pos3"


@pytest.mark.asyncio
async def test_each_write_puts_one_bms_write_event_on_the_bus(
    make_gateway: MakeGateway, client: NormalApplication, bus: InProcessBus
) -> None:
    gateway = await make_gateway("daikin")
    events = bus.subscribe(EVENTS_TOPIC)
    await write(client, gateway, "IDU-SP", 22.5, 10)
    await refused(client, gateway, "IDU-VANE-H", 3, 10)
    await write(client, gateway, "IDU-FAN", 4, 10)  # High
    await refused(client, gateway, "IDU-MODE", 2, 3)
    published = await taken(events)
    events.close()
    assert [(e.type, e.source, e.sim_time_s) for e in published] == [
        (EventType.BMS_WRITE, EventSource.WORLD, T0)
    ] * 4
    writes = [BmsWriteData.model_validate(e.data) for e in published]
    host, port = client.normal.server.local_address
    assert {(w.device, w.client) for w in writes} == {("VRF-Gateway-MeetingRoom", f"{host}:{port}")}
    assert [(w.object_name, w.value, w.priority, w.accepted, w.reason) for w in writes] == [
        ("IDU-SP", 22.5, 10, True, None),
        ("IDU-VANE-H", "Pos3", 10, False, "Daikin's gateway exposes no horizontal louver"),
        ("IDU-FAN", "High", 10, True, None),
        (
            "IDU-MODE",
            "Dry",
            3,
            False,
            "priority 3 is reserved for life safety and critical equipment; network commands "
            "use 8-16",
        ),
    ]
    assert (gateway.unit.setpoint_c, gateway.unit.fan, gateway.unit.mode) == (22.5, "High", "Cool")


@pytest.mark.asyncio
async def test_the_angle_of_an_axis_the_brand_lacks_reads_no_sensor(
    make_gateway: MakeGateway, client: NormalApplication
) -> None:
    gateway = await make_gateway("daikin")

    async def health(point: str) -> tuple[str, int]:
        flags = await read(client, gateway, point, "status-flags")
        return str(await read(client, gateway, point, "reliability")), flags[1]  # fault

    assert await health("IDU-VANE-H-ANGLE") == ("no-sensor", 1)
    assert await health("IDU-VANE-V-ANGLE") == ("no-fault-detected", 0)
    gateway.advance(0.05)  # a refresh keeps it so
    assert await health("IDU-VANE-H-ANGLE") == ("no-sensor", 1)


@pytest.mark.asyncio
async def test_start_fails_at_once_on_a_taken_port_and_stop_frees_it(
    ports: tuple[int, int],
) -> None:
    profile = load_vrf_profiles()["mitsubishi"]
    clock = SimClock(T0, 900.0)
    gateway = BacnetVrfGateway(profile, InProcessBus(), clock, address=HOST, port=ports[0])
    with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as holder:
        holder.bind((HOST, ports[0]))
        with pytest.raises(OSError) as refusal:
            await gateway.start()
        assert refusal.value.errno is not None  # the bind's own error, not a timeout
    assert gateway.address is None
    await gateway.start()
    assert gateway.address == f"{HOST}:{ports[0]}"
    await gateway.stop()
    await gateway.stop()  # safe twice
    assert gateway.address is None
    with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as again:
        again.bind((HOST, ports[0]))  # the device's socket is closed


@pytest.mark.asyncio
async def test_a_write_served_while_the_socket_binds_shows_on_its_object(
    ports: tuple[int, int], client: NormalApplication, monkeypatch: pytest.MonkeyPatch
) -> None:
    async def write_during_the_bind(device: Any, objects: Any, local: Any) -> Any:
        objects = list(objects)
        app = await open_application(device, objects, local)
        # A client's write served before start() resumes: the bind's last moment.
        [vane] = [obj for obj in objects if obj.objectName == "IDU-VANE-V"]
        await vane.write_property(PropertyIdentifier.presentValue, Unsigned(5), None, 10)
        return app

    monkeypatch.setattr(vrf_module, "open_application", write_during_the_bind)
    profile = load_vrf_profiles()["mitsubishi"]
    clock = SimClock(T0, 900.0)
    gateway = BacnetVrfGateway(profile, InProcessBus(), clock, address=HOST, port=ports[0])
    await gateway.start()
    try:
        assert gateway.unit.vane_v == "Pos5"
        assert await read(client, gateway, "IDU-VANE-V") == 5  # the object shows it too
        slots = await read(client, gateway, "IDU-VANE-V", "priority-array")
        assert [(i + 1, p.unsigned) for i, p in enumerate(slots) if p.unsigned] == [(10, 5)]
    finally:
        await gateway.stop()


@pytest.mark.asyncio
async def test_a_failed_bind_leaves_no_objects(
    ports: tuple[int, int], monkeypatch: pytest.MonkeyPatch
) -> None:
    async def refuse(device: Any, objects: Any, local: Any) -> Any:
        raise OSError("taken")

    monkeypatch.setattr(vrf_module, "open_application", refuse)
    profile = load_vrf_profiles()["mitsubishi"]
    gateway = BacnetVrfGateway(profile, InProcessBus(), SimClock(T0, 900.0), port=ports[0])
    with pytest.raises(OSError, match="taken"):
        await gateway.start()
    assert gateway.address is None
    assert all(point.obj is None for point in gateway._points.values())
