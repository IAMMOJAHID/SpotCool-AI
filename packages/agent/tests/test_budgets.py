"""Three Meta budgets: the building's SAM calls, the VRF loop's, and the language services'.

None can starve another: each has its own limiter and call cap. An exhausted VRF cap stops the
aiming and releases the vanes only; an exhausted building cap leaves the VRF loop aiming.
"""

import asyncio
import io
import socket
from types import SimpleNamespace
from typing import Any, cast

import pytest
from openai import AsyncOpenAI
from PIL import Image
from pydantic import BaseModel, SecretStr

from spotcool_agent.domain.events import Value
from spotcool_agent.loops.vrf import VrfLoop
from spotcool_agent.runner.agent import Agent
from spotcool_agent.services.meta.client import MetaClient
from spotcool_agent.services.meta.vision import SamVision
from spotcool_agent.services.vision import CallCapReached
from spotcool_agent.settings import AgentSettings
from spotcool_agent.transports.world_http import WorldHttp
from spotcool_contracts import load_vrf_profiles
from spotcool_contracts.cameras import CameraCatalog, CameraInfo, CameraSource
from spotcool_contracts.events import EventType
from spotcool_contracts.points import PointSpec

HOST = "127.0.0.1"
ONE_PERSON = "<0f>0<|box;x1=600;y1=200;x2=680;y2=560;w=1280;h=720;c=0.9|>"
"""SAM's answer: one standing person in front of the camera."""


def free_port(kind: socket.SocketKind) -> int:
    with socket.socket(socket.AF_INET, kind) as probe:
        probe.bind((HOST, 0))
        return probe.getsockname()[1]


def sam(cap: int) -> MetaClient:
    async def create(**kwargs: Any) -> Any:  # with_raw_response's answer
        answer = SimpleNamespace(output_text=ONE_PERSON)
        return SimpleNamespace(headers={}, status_code=200, parse=lambda: answer)

    async def close() -> None:
        return None

    fake = SimpleNamespace(
        responses=SimpleNamespace(with_raw_response=SimpleNamespace(create=create)), close=close
    )
    return MetaClient(
        SecretStr("sk-x"), base_url="x", call_cap=cap, make_openai=lambda: cast(AsyncOpenAI, fake)
    )


class Room:
    """The live camera's catalog and frames, and a Mitsubishi gateway, all in memory."""

    def __init__(self) -> None:
        buffer = io.BytesIO()
        Image.new("RGB", (1280, 720), (90, 90, 90)).save(buffer, format="JPEG")
        self.frame = buffer.getvalue()
        self.arrays: dict[str, list[Value]] = {}

    async def cameras(self) -> CameraCatalog:
        live = CameraInfo(
            id="cam-meeting-live",
            name="Meeting room: live phone",
            snapshot_url="http://phone/shot.jpg",
            width=1280,
            height=720,
            fps_hint=2.0,
            source=CameraSource.LIVE,
        )
        return CameraCatalog(cameras=[live])

    async def snapshot(self, url: str, timeout_s: float = 5.0) -> bytes:
        return self.frame

    async def slots(self, point: PointSpec) -> list[Value] | None:
        return list(self.arrays.setdefault(point.name, [None] * 16))

    async def write(self, point: PointSpec, value: Value) -> bool:
        self.arrays.setdefault(point.name, [None] * 16)[9] = value
        return True

    async def present(self, point: PointSpec) -> Value:
        return 24.0 if point.name in ("IDU-SP", "IDU-ROOM-T") else "Pos3"

    async def model_name(self, instance: int) -> str | None:
        return f"SpotCool simulated VRF gateway ({load_vrf_profiles()['mitsubishi'].label})"

    def vanes(self) -> tuple[Value, Value]:
        return tuple((self.arrays.get(n) or [None] * 16)[9] for n in ("IDU-VANE-V", "IDU-VANE-H"))  # type: ignore[return-value]


def vrf_loop(room: Room, client: MetaClient, events: list[tuple[EventType, BaseModel]]) -> VrfLoop:
    return VrfLoop(
        gateway=room,
        world=cast(WorldHttp, room),
        vision=SamVision(client),
        publish=lambda kind, data, t_s: events.append((kind, data)),
    )


async def run_until(loop: VrfLoop, condition: Any, timeout_s: float = 5.0) -> None:
    deadline = asyncio.get_running_loop().time() + timeout_s
    while not condition():
        assert asyncio.get_running_loop().time() < deadline, "timed out"
        await loop.tick()
        await asyncio.sleep(0.05)


def test_the_building_the_vrf_loop_and_the_language_services_each_have_a_budget() -> None:
    settings = AgentSettings(
        world_host=HOST,
        meta_api_key=SecretStr("sk-x"),
        vision="meta",
        vrf=True,
        bacnet_address=HOST,
        listen=f"{HOST}:{free_port(socket.SOCK_STREAM)}",
        sam_call_cap=7,
        vrf_call_cap=11,
        llm_call_cap=13,
    )
    agent = Agent(settings)
    budgets = [agent._meta, agent._vrf_meta, agent._language]  # noqa: SLF001 (the budgets)
    assert all(budgets) and len({id(b) for b in budgets}) == 3
    shape = [(b._max_concurrency, b._call_cap) for b in budgets if b is not None]  # noqa: SLF001
    assert shape == [(5, 7), (2, 11), (2, 13)]
    assert agent.vrf_vision is not agent.vision
    assert agent.vrf_bacnet is not None and agent.vrf_bacnet is not agent.bacnet
    assert agent.vrf_bacnet.device == f"{HOST}:47810" and agent.bacnet.device == f"{HOST}:47808"


@pytest.mark.asyncio
async def test_an_exhausted_vrf_cap_releases_the_vanes_and_leaves_the_building_counting() -> None:
    room, events = Room(), []
    building, vrf_budget = SamVision(sam(cap=100)), sam(cap=1)
    loop = vrf_loop(room, vrf_budget, events)
    await run_until(loop, lambda: room.vanes() == ("Pos3", "Pos3"))  # aimed at the person
    await run_until(loop, lambda: loop.capped and room.vanes() == (None, None))
    decisions = [d for kind, d in events if kind is EventType.DECISION]
    assert decisions[-1].model_dump()["reason"] == "the VRF loop's call cap is reached"
    found = await building.detect(room.frame, "humans", 5.0, camera_id="cam-sou")
    assert len(found.boxes) == 1  # the building's own budget still counts people
    await loop.stop()


@pytest.mark.asyncio
async def test_an_exhausted_building_cap_leaves_the_vrf_loop_aiming() -> None:
    room, events = Room(), []
    building = SamVision(sam(cap=0))
    with pytest.raises(CallCapReached):
        await building.detect(room.frame, "humans", 5.0, camera_id="cam-sou")
    loop = vrf_loop(room, sam(cap=100), events)
    await run_until(loop, lambda: room.vanes() == ("Pos3", "Pos3"))
    assert not loop.capped
    await loop.stop()
    assert room.vanes() == (None, None)


@pytest.mark.asyncio
async def test_trace_every_logs_the_first_call_then_every_nth() -> None:
    from loguru import logger
    from pydantic import SecretStr

    from spotcool_agent.services.meta.client import MetaClient

    lines: list[str] = []
    sink = logger.add(lines.append, format="{message}")
    try:
        client = MetaClient(SecretStr("k"), base_url="http://x", trace_every=3)
        for _ in range(7):
            async with client.call("sam-3.1", "count humans on cam-meeting-live"):
                pass
    finally:
        logger.remove(sink)
    sent = [line for line in lines if line.startswith("META ->")]
    assert len(sent) == 3 and all("(1 in 3 shown)" in line for line in sent)  # calls 1, 4, 7
    assert len(client.ledger) == 7  # the ledger keeps every call
