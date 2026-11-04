import asyncio
from typing import Any, cast

import pytest
from loguru import logger
from pydantic import BaseModel

from spotcool_agent.actuator import Actuator
from spotcool_agent.domain.events import Value
from spotcool_agent.loops.building import BuildingLoop, deadline_after
from spotcool_agent.observers.metrics import StageMetrics
from spotcool_agent.processors.policy import AgentPolicy
from spotcool_agent.services.fake.scripted import ScriptedVision
from spotcool_agent.transports.bacnet import ClockReading, StepClock
from spotcool_agent.transports.world_http import WorldHttp
from spotcool_contracts import ZONES, Zone
from spotcool_contracts.calendar import ScheduleInformation
from spotcool_contracts.cameras import CameraCatalog, CameraInfo, CameraSource
from spotcool_contracts.events import EventType
from spotcool_contracts.points import PointSpec

T0 = 199 * 86_400.0 + 9 * 3600
STEP_S = 900.0


def test_the_margin_is_2_5_s_or_40_percent_of_the_step() -> None:
    assert deadline_after(100.0, 15.0) == pytest.approx(109.0)  # speed 60: the change + 9 s
    assert deadline_after(100.0, 3.0) == pytest.approx(100.5)  # speed 300: 2.5 s left
    assert deadline_after(100.0, 30.0) == pytest.approx(118.0)  # speed 30


class FakeWorld:
    """Five cameras and an empty calendar; ``fail`` makes the next catalog request raise."""

    def __init__(self) -> None:
        self.fail: Exception | None = None

    async def cameras(self) -> CameraCatalog:
        if self.fail is not None:
            failure, self.fail = self.fail, None
            raise failure
        cameras = [
            CameraInfo(
                id=ZONES[zone].camera_id,
                name=zone.value,
                zone=zone,
                snapshot_url=f"http://world/{zone.value}",
                width=1280,
                height=720,
                fps_hint=5,
                source=CameraSource.SCENARIO,
            )
            for zone in Zone
        ]
        return CameraCatalog(cameras=cameras)

    async def snapshot(self, url: str, timeout_s: float = 5.0) -> bytes:
        return b"jpeg"

    async def schedule(self, room: str) -> ScheduleInformation:
        return ScheduleInformation(scheduleId=room, scheduleItems=[])


class FakeBms:
    def __init__(self) -> None:
        self.arrays: dict[str, list[Value]] = {}
        self.writes: list[tuple[str, Value]] = []

    async def slots(self, point: PointSpec) -> list[Value] | None:
        return list(self.arrays.setdefault(point.name, [None] * 16))

    async def write(self, point: PointSpec, value: Value) -> bool:
        self.arrays.setdefault(point.name, [None] * 16)[9] = value
        self.writes.append((point.name, value))
        return True


class Rig:
    """A building loop on fakes, stepped by hand through its clock."""

    def __init__(self, vision: Any = None, **features: Any) -> None:
        self.world = FakeWorld()
        self.bms = FakeBms()
        self.events: list[tuple[EventType, dict[str, Any]]] = []
        self.new_runs = 0
        self.clock = StepClock(lambda: asyncio.sleep(0, None))
        self.actuator = Actuator(self.bms, self.publish)

        def new_run() -> None:
            self.new_runs += 1

        self.loop = BuildingLoop(
            clock=self.clock,
            world=cast(WorldHttp, self.world),
            vision=vision or ScriptedVision(),
            policy=AgentPolicy(),
            actuator=self.actuator,
            publish=self.publish,
            metrics=StageMetrics(),
            settle_s=0.0,
            on_new_run=new_run,
            **features,
        )

    def publish(self, kind: EventType, data: BaseModel, t_s: float | None) -> None:
        self.events.append((kind, data.model_dump()))

    async def step(self, reading: ClockReading, *, steps: int) -> None:
        """Show a SIM-CLOCK value and wait until the loop has handled ``steps`` in all."""
        self.clock.observe(reading)
        for _ in range(500):
            if self.loop.steps >= steps:
                return
            await asyncio.sleep(0.002)
        raise AssertionError(f"the loop handled {self.loop.steps} steps, not {steps}")


@pytest.mark.asyncio
async def test_a_step_that_raises_is_logged_once_and_the_loop_goes_on() -> None:
    rig = Rig()
    said: list[str] = []
    sink = logger.add(lambda message: said.append(str(message)), level="ERROR")
    running = asyncio.create_task(rig.loop.run())
    try:
        for k in (1, 2):
            rig.world.fail = RuntimeError("the catalog broke")
            rig.clock.observe(ClockReading(T0 + k * STEP_S))
            await asyncio.sleep(0.05)
        assert rig.loop.steps == 0  # both steps failed ...
        await rig.step(ClockReading(T0 + 3 * STEP_S), steps=1)  # ... and the next one runs
    finally:
        running.cancel()
        await asyncio.gather(running, return_exceptions=True)
        logger.remove(sink)
    failures = [line for line in said if "failed, the loop goes on" in line]
    assert len(failures) == 1 and "RuntimeError: the catalog broke" in failures[0]
    assert rig.bms.arrays["ZN-SOU-OCC-SENSOR"][9] == "Unoccupied"


@pytest.mark.asyncio
async def test_a_provider_bug_makes_its_zone_unknown_not_the_step_fail() -> None:
    class Buggy(ScriptedVision):
        async def detect(
            self, jpeg: bytes, concept: str, timeout_s: float, *, camera_id: str | None = None
        ) -> Any:
            if camera_id == "cam-sou":
                raise KeyError("oops")
            return await super().detect(jpeg, concept, timeout_s, camera_id=camera_id)

    rig = Rig(Buggy())
    running = asyncio.create_task(rig.loop.run())
    try:
        await rig.step(ClockReading(T0 + STEP_S), steps=1)
    finally:
        running.cancel()
        await asyncio.gather(running, return_exceptions=True)
    (perception,) = [data for kind, data in rig.events if kind is EventType.PERCEPTION]
    assert set(perception["people"]) == set(Zone) - {Zone.SOU}


@pytest.mark.asyncio
async def test_a_new_run_releases_forgets_and_starts_the_cap_afresh() -> None:
    rig = Rig()
    running = asyncio.create_task(rig.loop.run())
    try:
        await rig.step(ClockReading(T0 + STEP_S), steps=1)
        assert rig.bms.arrays["ZN-SOU-CLG-SP"][9] == 28.0
        rig.loop._cap_reached = True
        rig.clock.observe(ClockReading(T0, initial=True))  # the world starts its run again
        await rig.step(ClockReading(T0 + STEP_S), steps=2)
    finally:
        running.cancel()
        await asyncio.gather(running, return_exceptions=True)
    assert rig.new_runs == 1
    assert not rig.loop._cap_reached
    reasons = [data["reason"] for kind, data in rig.events if kind is EventType.DECISION]
    assert "a new run started" in reasons  # the old run's slots were released ...
    assert rig.bms.arrays["ZN-SOU-CLG-SP"][9] == 28.0  # ... and the new run commands afresh


async def a_day(**features: Any) -> tuple[list[tuple[str, Value]], list[Any], list[str]]:
    """Eight steps of changing counts: the BMS writes, the decisions, the explanations."""
    vision = ScriptedVision()
    rig = Rig(vision, **features)
    days = [
        {"cam-sou": 2},
        {"cam-sou": 2, "cam-wes": 1},
        {"cam-wes": 1},
        {},
        {"cam-eas": 3, "cam-nor": 1},
        {"cam-eas": 3, "cam-cor": 4},
        {"cam-sou": 1},
        {},
    ]
    for k, counts in enumerate(days, start=1):
        vision.counts = dict(counts)
        rig.clock.observe(ClockReading(T0 + k * STEP_S))
        assert rig.clock.latest is not None
        await rig.loop.step(rig.clock.latest)
        await asyncio.sleep(0.01)  # the background readings and narration finish
    await rig.loop.stop_background()
    decisions = [data for kind, data in rig.events if kind is EventType.DECISION]
    texts = [data["text"] for kind, data in rig.events if kind is EventType.EXPLANATION]
    return rig.bms.writes, decisions, texts


@pytest.mark.asyncio
async def test_the_scene_notes_and_narration_never_change_a_bms_write() -> None:
    from spotcool_agent.loops.narration import Narrator
    from spotcool_agent.loops.scene import SceneNotes
    from spotcool_agent.services.fake.llm import FakeLLM
    from spotcool_agent.services.fake.scene import FakeScene, office

    plain_writes, plain_decisions, plain_texts = await a_day()

    events: list[tuple[EventType, dict[str, Any]]] = []

    def publish(kind: EventType, data: BaseModel, t_s: float | None) -> None:
        events.append((kind, data.model_dump()))

    scene = FakeScene(office(9, doors_open=1))  # it disagrees with SAM 3.1 everywhere
    llm = FakeLLM({"narrate": "The agent eased off the empty zones."})
    notes = SceneNotes(scene, publish, every_steps=1)
    writes, decisions, texts = await a_day(notes=notes, narrator=Narrator(llm, publish))

    assert plain_writes and writes == plain_writes  # the same writes, in the same order
    assert decisions == plain_decisions and texts == plain_texts
    said = [data["text"] for _, data in events]
    assert any(t.startswith("Muse Spark, South: 9 people") for t in said)  # they did run
    assert any(t.endswith("the policy keeps SAM's") for t in said)
    assert any(t == "Muse Wonder: The agent eased off the empty zones." for t in said)
    assert scene.asked and all(purpose == "narrate" for purpose, _ in llm.prompts)
