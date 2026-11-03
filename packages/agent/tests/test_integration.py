"""The agent's transports against the world's real servers, in process on loopback."""

import asyncio
import socket
import time
from pathlib import Path
from typing import Any

import pytest
from bacpypes3.ipv4.app import NormalApplication
from bacpypes3.local.device import DeviceObject
from bacpypes3.pdu import Address, IPv4Address
from bacpypes3.primitivedata import Null
from pydantic import BaseModel

from spotcool_agent.actuator import Actuator
from spotcool_agent.domain.events import Perception
from spotcool_agent.processors.policy import EMPTY_SETPOINT_C, OCCUPIED, UNOCCUPIED, Command
from spotcool_agent.services.fake.blob import BlobVision
from spotcool_agent.transports.bacnet import BacnetClient
from spotcool_agent.transports.dashboard import DashboardPublisher, agent_url
from spotcool_agent.transports.world_http import WorldHttp, WorldHttpError
from spotcool_contracts import ZONES, EventEnvelope, EventSource, EventType, Zone, load_point_map
from spotcool_contracts.calendar import DateTimeTimeZone, ScheduleItem
from spotcool_contracts.events import CalendarData
from spotcool_world.bus import InProcessBus
from spotcool_world.frames import CalendarFrame
from spotcool_world.processors.event_emitter import EVENTS_TOPIC
from spotcool_world.services.camera_service import CameraSystem
from spotcool_world.services.clips.camera import ZoneCamera
from spotcool_world.transports.bacnet.bms import BacnetBmsTransport
from spotcool_world.transports.http.calendar import CalendarStore, calendar_router
from spotcool_world.transports.http.cameras import cameras_router
from spotcool_world.transports.http.server import create_http_app
from spotcool_world.transports.websocket.dashboard import create_dashboard_app

HOST = "127.0.0.1"
POINTS = load_point_map("office_bms")
SENSOR, SETPOINT = POINTS.by_name("ZN-SOU-OCC-SENSOR"), POINTS.by_name("ZN-SOU-CLG-SP")
HEARTBEAT = POINTS.by_name("SPOTCOOL-HEARTBEAT")
EMPTY = Command(UNOCCUPIED, EMPTY_SETPOINT_C, "the zone is empty")
PEOPLE = Command(OCCUPIED, None, "5 people on camera")
T_S = 199 * 86_400.0 + 9 * 3600


def free_port(kind: socket.SocketKind = socket.SOCK_STREAM) -> int:
    with socket.socket(socket.AF_INET, kind) as probe:
        probe.bind((HOST, 0))
        return probe.getsockname()[1]


async def until(condition: Any, timeout_s: float = 10.0) -> None:
    deadline = time.perf_counter() + timeout_s
    while not await condition():
        assert time.perf_counter() < deadline, "timed out"
        await asyncio.sleep(0.05)


@pytest.mark.bacnet
@pytest.mark.asyncio
async def test_the_agent_commands_the_bms_backs_off_from_an_operator_and_releases() -> None:
    bms_port = free_port(socket.SOCK_DGRAM)
    bms = BacnetBmsTransport(address=HOST, port=bms_port)
    await bms.start()
    client = BacnetClient(f"{HOST}:{bms_port}", local=f"{HOST}:0")
    await client.open()
    device = DeviceObject(objectIdentifier=("device", 1999), objectName="op", vendorIdentifier=999)
    operator = NormalApplication(device, IPv4Address(f"{HOST}:0"))
    events: list[tuple[EventType, dict[str, Any]]] = []

    def publish(kind: EventType, data: BaseModel, t_s: float | None) -> None:
        events.append((kind, data.model_dump()))

    actuator = Actuator(client, publish, heartbeat_s=0.2)
    try:
        assert await client.clock() is None  # no measurement yet: SIM-CLOCK shows fault
        actuator.start()
        await actuator.apply({Zone.SOU: EMPTY}, T_S)
        assert (await client.slots(SENSOR) or [])[9] == "Unoccupied"
        assert (await client.slots(SETPOINT) or [])[9] == 28.0
        beats = [(await client.slots(HEARTBEAT) or [])[9]]
        await asyncio.sleep(0.3)
        beats.append((await client.slots(HEARTBEAT) or [])[9])
        assert set(beats) == {"On", "Off"}  # toggled at priority 10

        await operator.write_property(
            Address(f"{HOST}:{bms_port}"), SETPOINT.object_id, "present-value", 23.0, priority=8
        )
        await actuator.apply({Zone.SOU: PEOPLE}, T_S + 900)
        slots = await client.slots(SETPOINT) or []
        assert (slots[7], slots[9]) == (23.0, 28.0)  # backed off: the release waits
        assert (await client.slots(SENSOR) or [])[9] == "Occupied"
        reasons = [data["reason"] for kind, data in events if kind is EventType.DECISION]
        assert "backed off: held at 23.0 by an operator at priority 8" in reasons

        await operator.write_property(
            Address(f"{HOST}:{bms_port}"), SETPOINT.object_id, "present-value", Null(()), priority=8
        )
        await actuator.apply({Zone.SOU: PEOPLE}, T_S + 1800)
        assert (await client.slots(SETPOINT) or [])[9] is None  # released once the operator let go

        await actuator.stop()
        for point in (SENSOR, SETPOINT, HEARTBEAT):
            assert (await client.slots(point) or [None] * 16)[9] is None
    finally:
        await actuator.stop()
        operator.close()
        await client.close()
        await bms.stop()


@pytest.mark.asyncio
async def test_the_agent_reads_the_catalog_snapshots_and_the_calendar(serve: Any) -> None:
    cameras = CameraSystem([ZoneCamera(zone) for zone in Zone])
    for zone in Zone:
        people = 5 if zone is Zone.SOU else 0
        cameras.set_scene(ZONES[zone].camera_id, "meeting" if people else "empty", people)
    store = CalendarStore()
    booking = ScheduleItem(
        status="busy",
        subject="Quarterly review",
        start=DateTimeTimeZone(dateTime="2026-07-19T14:00:00"),
        end=DateTimeTimeZone(dateTime="2026-07-19T15:00:00"),
    )
    store.update(CalendarFrame(data=CalendarData(room="meeting-south", items=[booking])))
    await cameras.start()
    try:
        async with serve(
            create_http_app([cameras_router(cameras), calendar_router(store)])
        ) as port:
            world = WorldHttp(f"http://{HOST}:{port}")
            try:
                catalog = await world.cameras()
                ids = {camera.id for camera in catalog.cameras}
                assert {ZONES[zone].camera_id for zone in Zone} <= ids
                (south,) = catalog.for_zone(Zone.SOU)
                snapshots: list[bytes] = []

                async def snapshot() -> bool:
                    try:
                        snapshots.append(await world.snapshot(south.snapshot_url))
                    except WorldHttpError:  # 503 until the camera's first frame
                        return False
                    return True

                await until(snapshot)
                found = await BlobVision().detect(snapshots[-1], "humans", 5.0)
                assert len(found.boxes) == 5
                schedule = await world.schedule("meeting-south")
                assert [item.subject for item in schedule.scheduleItems] == ["Quarterly review"]
                with pytest.raises(WorldHttpError, match="HTTP 404"):
                    await world.schedule("nowhere")
            finally:
                await world.aclose()
    finally:
        await cameras.stop()


@pytest.mark.asyncio
async def test_the_agents_events_reach_the_worlds_bus_through_ws_agent(
    tmp_path: Path, serve: Any
) -> None:
    bus = InProcessBus()
    events = bus.subscribe(EVENTS_TOPIC)
    async with serve(create_dashboard_app(bus, tmp_path)) as port:
        publisher = DashboardPublisher(agent_url(HOST, port))
        publisher.start()
        masks = {"cam-sou": [[[10.0, 20.0], [30.0, 20.0], [30.0, 80.0], [10.0, 80.0]]]}
        seen = Perception(people={Zone.SOU: 1, Zone.COR: 0}, masks=masks)
        publisher.publish(EventType.PERCEPTION, seen, T_S)
        envelope = await asyncio.wait_for(anext(aiter(events)), 5.0)
        await publisher.stop()
    events.close()
    assert isinstance(envelope, EventEnvelope)
    assert (envelope.source, envelope.type, envelope.sim_time_s) == (
        EventSource.AGENT,
        EventType.PERCEPTION,
        T_S,
    )
    assert envelope.data == {"people": {"sou": 1, "cor": 0}, "masks": masks}
    assert publisher.dialled and all(url.endswith("/ws/agent") for url in publisher.dialled)
