"""The whole agent with ``--vrf`` against the world's real VRF gateway, camera and dashboard.

All in process on loopback: the gateway (Mitsubishi or Daikin) on its own UDP port, the camera
server with the live phone camera fed by a mock phone, and the dashboard's ``/ws/agent``, whose
bus also carries the gateway's ``bms_write`` events. No BMS answers: the building loop idles,
and the VRF loop must not wait for it. A scripted person walks across the room in front of the
camera; the gateway's louvers are moved on by hand (``advance``), so no test waits on them.
"""

import asyncio
import io
import socket
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import httpx
import pytest
from PIL import Image

from spotcool_agent.domain.geometry import Detection, Detections
from spotcool_agent.domain.louvers import CameraPose, project_floor_point
from spotcool_agent.runner.agent import Agent
from spotcool_agent.runner.cli import tone_wav
from spotcool_agent.services.vision import VisionService
from spotcool_agent.settings import AgentSettings
from spotcool_agent.transports.bacnet import BacnetPeer
from spotcool_contracts import (
    EventEnvelope,
    EventSource,
    EventType,
    load_point_map,
    load_vrf_profiles,
)
from spotcool_contracts.events import BmsWriteData
from spotcool_world.bus import InProcessBus
from spotcool_world.clocks import SimClock
from spotcool_world.processors.event_emitter import EVENTS_TOPIC
from spotcool_world.services.camera_service import CameraSystem
from spotcool_world.services.live import PhoneCamera
from spotcool_world.transports.bacnet.vrf import BacnetVrfGateway
from spotcool_world.transports.http.cameras import cameras_router
from spotcool_world.transports.http.server import create_http_app
from spotcool_world.transports.websocket.dashboard import create_dashboard_app

pytestmark = [pytest.mark.bacnet, pytest.mark.asyncio]

HOST = "127.0.0.1"
T0 = 199 * 86_400.0 + 10 * 3600
W, H = 1280, 720
GATEWAY = load_point_map("vrf_gateway")
COMMANDED = ("IDU-VANE-V", "IDU-VANE-H", "IDU-SP", "IDU-FAN")
ACROSS = [(-2.6, "Pos1"), (-1.2, "Pos2"), (0.0, "Pos3"), (1.2, "Pos4"), (2.6, "Pos5")]
"""Where the person stands, 3 m out, and the horizontal position that aims at them."""


def free_port(kind: socket.SocketKind) -> int:
    with socket.socket(socket.AF_INET, kind) as probe:
        probe.bind((HOST, 0))
        return probe.getsockname()[1]


def jpeg() -> bytes:
    buffer = io.BytesIO()
    Image.new("RGB", (W, H), (90, 90, 90)).save(buffer, format="JPEG")
    return buffer.getvalue()


class Walker(VisionService):
    """One standing person wherever ``lateral_m`` says (3 m out), or nobody for ``None``."""

    def __init__(self) -> None:
        super().__init__(name="Walker")
        self.lateral_m: float | None = None

    async def detect(
        self, jpeg: bytes, concept: str, timeout_s: float, *, camera_id: str | None = None
    ) -> Detections:
        if camera_id != "cam-meeting-live" or self.lateral_m is None:
            return Detections((), W, H)
        pose = CameraPose()
        feet = project_floor_point(self.lateral_m, 3.0, 0.0, W, H, pose)
        head = project_floor_point(self.lateral_m, 3.0, 1.7, W, H, pose)
        assert feet is not None and head is not None
        half = (feet[1] - head[1]) / 6
        box = Detection(0, (feet[0] - half) / W, head[1] / H, (feet[0] + half) / W, feet[1] / H)
        return Detections((box,), W, H)


@dataclass
class Room:
    gateway: BacnetVrfGateway
    agent: Agent
    walker: Walker
    events: list[EventEnvelope]
    listen: str
    running: "asyncio.Task[None]"

    async def stop_agent(self) -> None:
        """Stop the agent as Ctrl+C does, and let its last writes reach the bus."""
        self.running.cancel()
        await asyncio.gather(self.running, return_exceptions=True)
        await asyncio.sleep(0.2)

    def writes(self, point: str) -> list[BmsWriteData]:
        """The agent's writes the gateway took for a point, in order."""
        found = []
        for envelope in self.events:
            if envelope.type is EventType.BMS_WRITE:
                write = BmsWriteData.model_validate(envelope.data)
                if write.object_name == point and write.priority == 10 and write.accepted:
                    found.append(write)
        return found

    def agent_events(self, kind: EventType) -> list[EventEnvelope]:
        return [e for e in self.events if e.type is kind and e.source is EventSource.AGENT]

    async def until(self, condition: Any, timeout_s: float = 10.0) -> None:
        deadline = asyncio.get_running_loop().time() + timeout_s
        while not condition():
            assert asyncio.get_running_loop().time() < deadline, "timed out"
            await asyncio.sleep(0.05)


@asynccontextmanager
async def meeting_room(vendor: str, serve: Any, tmp_path: Path) -> AsyncIterator[Room]:
    """The world's VRF gateway, live camera and dashboard, and the agent with ``--vrf``."""
    bus = InProcessBus()
    subscription = bus.subscribe(EVENTS_TOPIC)
    events: list[EventEnvelope] = []

    async def collect() -> None:
        async for envelope in subscription:
            events.append(envelope)

    collecting = asyncio.create_task(collect())
    vrf_port = free_port(socket.SOCK_DGRAM)
    profile = load_vrf_profiles()[vendor]
    gateway = BacnetVrfGateway(profile, bus, SimClock(T0, 900.0), address=HOST, port=vrf_port)
    frame = jpeg()
    phone = PhoneCamera(
        "http://phone.test/shot.jpg",
        transport=httpx.MockTransport(lambda request: httpx.Response(200, content=frame)),
    )
    cameras = CameraSystem([phone])
    await gateway.start()
    await cameras.start()
    walker = Walker()
    try:
        async with (
            serve(create_http_app([cameras_router(cameras)])) as http_port,
            serve(create_dashboard_app(bus, tmp_path)) as dashboard_port,
        ):
            listen = f"{HOST}:{free_port(socket.SOCK_STREAM)}"
            settings = AgentSettings(
                world_host=HOST,
                bms_port=free_port(socket.SOCK_DGRAM),  # nothing answers there
                http_port=http_port,
                dashboard_port=dashboard_port,
                listen=listen,
                vision="scripted",
                stt="fake",
                llm="off",
                bacnet_address=HOST,
                bacnet_port=0,
                vrf=True,
                vrf_port=vrf_port,
            )
            agent = Agent(settings, vision=walker)
            room = Room(gateway, agent, walker, events, listen, asyncio.create_task(agent.run()))
            try:
                yield room
            finally:
                await room.stop_agent()
    finally:
        await cameras.stop()
        await gateway.stop()
        subscription.close()
        collecting.cancel()
        await asyncio.gather(collecting, return_exceptions=True)


async def slot_10(room: Room) -> dict[str, object]:
    """Each commanded point's slot 10 on the gateway, read over BACnet after the agent stopped."""
    gateway = room.gateway.address
    assert gateway is not None
    await room.agent.bacnet.open()
    try:
        peer = BacnetPeer(room.agent.bacnet, gateway, GATEWAY.device_instance)
        return {
            name: ((await peer.slots(GATEWAY.by_name(name))) or [0] * 16)[9] for name in COMMANDED
        }
    finally:
        await room.agent.bacnet.close()


async def test_on_mitsubishi_the_louvers_follow_a_person_across_and_a_voice_complaint_acts(
    serve: Any, tmp_path: Path, writes: list[str]
) -> None:
    async with meeting_room("mitsubishi", serve, tmp_path) as room:
        address = room.gateway.address
        assert address is not None
        peer = BacnetPeer(room.agent.bacnet, address, GATEWAY.device_instance)
        angle = GATEWAY.by_name("IDU-VANE-H-ANGLE")
        angles = {f"Pos{n}": 20.0 * (n - 3) for n in range(1, 6)}  # Mitsubishi: -40 to 40
        for lateral, position in ACROSS:
            room.walker.lateral_m = lateral
            await room.until(
                lambda p=position: [w.value for w in room.writes("IDU-VANE-H")][-1:] == [p]
            )
            for _ in range(40):  # 4 s of louver motion at 15 deg/s
                room.gateway.advance(0.1)
            assert await peer.present(angle) == pytest.approx(angles[position], abs=0.01)
        assert [w.value for w in room.writes("IDU-VANE-H")] == [p for _, p in ACROSS]
        # The tilt follows the distance too: 4.0 m away at the sides (21 deg, Pos2), 3.0-3.2 m
        # in the middle (25-27 deg, Pos3).
        assert [w.value for w in room.writes("IDU-VANE-V")] == ["Pos2", "Pos3", "Pos2"]
        decisions = [
            e for e in room.agent_events(EventType.DECISION) if e.data["point"] == "IDU-VANE-H"
        ]
        assert [e.data["value"] for e in decisions] == [p for _, p in ACROSS]
        assert all(e.data["zone"] == "sou" and e.sim_time_s is None for e in decisions)
        perceptions = room.agent_events(EventType.PERCEPTION)
        assert perceptions and all(e.data["people"] == {} for e in perceptions)

        async with httpx.AsyncClient() as http:
            files = {"audio": ("complaint.wav", tone_wav(1.5), "audio/wav")}
            response = await http.post(f"http://{room.listen}/voice", files=files)
        assert response.status_code == 202
        await room.until(
            lambda: (
                [w.value for w in room.writes("IDU-SP")] == [25.0]
                and [w.value for w in room.writes("IDU-FAN")] == ["Low"]
            )
        )
        await room.until(lambda: bool(room.agent_events(EventType.CHAT)))
        (reply,) = room.agent_events(EventType.CHAT)
        assert reply.data["text"] == (
            "Thanks, I've raised the meeting room's setpoint to 25.0 degC and turned its fan "
            "down to Low."
        )
        await room.stop_agent()
        assert await slot_10(room) == dict.fromkeys(COMMANDED)  # stop released every slot
    released = {
        e.data["object_name"]
        for e in room.events
        if e.type is EventType.BMS_WRITE and e.data["value"] is None and e.data["priority"] == 10
    }
    assert released == set(COMMANDED)
    assert writes == []


async def test_on_daikin_the_horizontal_vane_is_never_written(serve: Any, tmp_path: Path) -> None:
    async with meeting_room("daikin", serve, tmp_path) as room:
        room.walker.lateral_m = 0.0
        await room.until(lambda: [w.value for w in room.writes("IDU-VANE-V")] == ["Pos3"])
        for lateral, _ in ACROSS:
            room.walker.lateral_m = lateral
            await asyncio.sleep(0.6)
        assert room.agent.vrf is not None and room.agent.vrf.profile is not None
        assert room.agent.vrf.profile.vendor == "daikin"  # from the gateway's Model_Name
        await room.stop_agent()
        assert await slot_10(room) == dict.fromkeys(COMMANDED)
    tried = [
        e
        for e in room.events
        if e.type is EventType.BMS_WRITE and e.data["object_name"] == "IDU-VANE-H"
    ]
    assert tried == []  # not even refused: never sent
