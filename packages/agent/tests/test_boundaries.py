"""The agent's boundaries: no world import, no file written, no frame logged, no ground truth."""

import asyncio
import base64
import io
import re
import subprocess
import sys
import wave
from pathlib import Path
from types import SimpleNamespace
from typing import Any, cast

import httpx
import pytest
from loguru import logger
from openai import AsyncOpenAI
from pydantic import SecretStr

import spotcool_agent
from spotcool_agent.actuator import Actuator
from spotcool_agent.domain.events import Value
from spotcool_agent.loops.building import BuildingLoop
from spotcool_agent.loops.scene import SceneNotes
from spotcool_agent.observers.metrics import StageMetrics
from spotcool_agent.processors.policy import AgentPolicy
from spotcool_agent.services.fake.blob import BlobVision
from spotcool_agent.services.fake.scene import FakeScene
from spotcool_agent.services.meta.client import MetaClient
from spotcool_agent.services.meta.scene import SparkScene
from spotcool_agent.services.meta.vision import SamVision
from spotcool_agent.services.scene import SceneError
from spotcool_agent.transports.bacnet import ClockReading, StepClock
from spotcool_agent.transports.dashboard import DashboardPublisher, agent_url
from spotcool_agent.transports.server import Complaint, create_app
from spotcool_agent.transports.world_http import WorldHttp
from spotcool_contracts import ZONES, Zone
from spotcool_contracts.points import PointSpec

AGENT_SOURCE = Path(spotcool_agent.__file__).parent
T0 = 199 * 86_400.0 + 9 * 3600


def test_no_agent_module_imports_the_world() -> None:
    code = (
        "import importlib, pkgutil, sys, spotcool_agent\n"
        "for m in pkgutil.walk_packages(spotcool_agent.__path__, 'spotcool_agent.'):\n"
        "    importlib.import_module(m.name)\n"
        "world = sorted(n for n in sys.modules if n.startswith('spotcool_world'))\n"
        "assert not world, world\n"
        "print(sum(n.startswith('spotcool_agent') for n in sys.modules))\n"
    )
    done = subprocess.run([sys.executable, "-c", code], capture_output=True, text=True, timeout=60)
    assert done.returncode == 0, done.stderr
    assert int(done.stdout) >= 20  # every module was imported


def test_the_agent_never_reads_the_worlds_ground_truth() -> None:
    for path in AGENT_SOURCE.rglob("*.py"):
        text = path.read_text(encoding="utf-8")
        assert not re.search(r"^\s*(from|import)\s+spotcool_world", text, re.M), path
        for truth in ("/ws/events", "scenario_state", "camera_scene", "ScenarioState"):
            assert truth not in text, (path, truth)


class FakeBms:
    def __init__(self) -> None:
        self.arrays: dict[str, list[Value]] = {}

    async def slots(self, point: PointSpec) -> list[Value] | None:
        return list(self.arrays.setdefault(point.name, [None] * 16))

    async def write(self, point: PointSpec, value: Value) -> bool:
        self.arrays.setdefault(point.name, [None] * 16)[9] = value
        return True


def wav_near_the_cap() -> bytes:
    buffer = io.BytesIO()
    with wave.open(buffer, "wb") as out:
        out.setnchannels(1)
        out.setsampwidth(2)
        out.setframerate(16_000)
        out.writeframes(b"\x01\x00" * 450_000)  # 900 kB: one upload close to the cap
    return buffer.getvalue()


@pytest.mark.asyncio
async def test_frames_and_audio_stay_in_memory_and_out_of_the_logs(
    writes: list[str], tmp_path: Path, capfd: pytest.CaptureFixture[str], serve: Any
) -> None:
    from spotcool_world.bus import InProcessBus
    from spotcool_world.services.camera_service import CameraSystem
    from spotcool_world.services.clips.camera import ZoneCamera
    from spotcool_world.transports.http.calendar import CalendarStore, calendar_router
    from spotcool_world.transports.http.cameras import cameras_router
    from spotcool_world.transports.http.server import create_http_app
    from spotcool_world.transports.websocket.dashboard import create_dashboard_app

    logged: list[str] = []
    sink = logger.add(lambda message: logged.append(str(message)), level=0)  # tracebacks too
    cameras = CameraSystem([ZoneCamera(zone) for zone in Zone])
    for zone in Zone:
        cameras.set_scene(ZONES[zone].camera_id, "few", 2)
    await cameras.start()
    http_app = create_http_app([cameras_router(cameras), calendar_router(CalendarStore())])
    try:
        async with (
            serve(http_app) as http_port,
            serve(create_dashboard_app(InProcessBus(), tmp_path)) as dashboard_port,
        ):
            world = WorldHttp(f"http://127.0.0.1:{http_port}")
            publisher = DashboardPublisher(agent_url("127.0.0.1", dashboard_port))
            publisher.start()
            clock = StepClock(lambda: asyncio.sleep(0, None))
            actuator = Actuator(FakeBms(), publisher.publish)
            loop = BuildingLoop(
                clock=clock,
                world=world,
                vision=BlobVision(),
                policy=AgentPolicy(),
                actuator=actuator,
                publish=publisher.publish,
                metrics=StageMetrics(),
                settle_s=0.0,
                notes=SceneNotes(FakeScene(), publisher.publish, every_steps=1),
            )
            await asyncio.sleep(1.0)  # every camera has a frame
            for k in range(1, 4):
                clock.observe(ClockReading(T0 + k * 900.0))
                assert clock.latest is not None
                await loop.step(clock.latest)
            snapshot = await world.snapshot(
                f"http://127.0.0.1:{http_port}/cameras/cam-sou/snapshot.jpg"
            )

            fake = SimpleNamespace(
                responses=SimpleNamespace(
                    with_raw_response=SimpleNamespace(create=lambda **kw: _answer())
                ),
                close=_none,
            )
            meta = MetaClient(
                SecretStr("sk-x"), base_url="x", make_openai=lambda: cast(AsyncOpenAI, fake)
            )
            await SamVision(meta).detect(snapshot, "humans", 5.0, camera_id="cam-sou")
            with pytest.raises(SceneError):  # the frame goes to Muse Spark from memory too
                await SparkScene(meta).assess(snapshot, 2, 5.0, camera_id="cam-sou")

            queue: asyncio.Queue[Complaint] = asyncio.Queue()
            app = httpx.ASGITransport(app=create_app(queue, origins=[]))
            async with httpx.AsyncClient(transport=app, base_url="http://agent") as agent:
                files = {"audio": ("voice.wav", wav_near_the_cap(), "audio/wav")}
                assert (await agent.post("/voice", files=files)).status_code == 202
            await loop.stop_background()
            await actuator.stop()
            await publisher.stop()
            await world.aclose()
    finally:
        await cameras.stop()
        logger.remove(sink)
    assert loop.steps == 3
    assert writes == []
    out, err = capfd.readouterr()
    seen = "\n".join([*logged, out, err])
    for jpeg in (
        "\xff\xd8\xff",
        "\\xff\\xd8\\xff",
        "ffd8ff",
        "/9j/",
        base64.b64encode(snapshot[:12]).decode(),
    ):
        assert jpeg not in seen


async def _answer() -> Any:  # with_raw_response's answer
    answer = SimpleNamespace(output_text="")
    return SimpleNamespace(headers={}, status_code=200, parse=lambda: answer)


async def _none() -> None:
    return None
