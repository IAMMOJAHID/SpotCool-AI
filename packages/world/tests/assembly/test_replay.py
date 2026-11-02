"""The replay: a recorded run's events back on the bus, in order and paced, with the cameras."""

import asyncio
import time
from collections.abc import Callable
from pathlib import Path
from typing import Any

import pytest

from spotcool_contracts import ZONES, Zone
from spotcool_contracts.cameras import CameraSource
from spotcool_contracts.events import (
    CameraSceneData,
    EventEnvelope,
    EventSource,
    EventType,
    KpiData,
    ScenarioStateData,
    TickData,
    ZoneScene,
)
from spotcool_world.bus import Subscription
from spotcool_world.clocks import SimClock
from spotcool_world.processors import EVENTS_TOPIC
from spotcool_world.runner import assembly
from spotcool_world.runner.assembly import Replay
from spotcool_world.serializers import EVENTS_FILE, EventRecord, RawRecord, RunWriter
from spotcool_world.services import WorldSettings
from spotcool_world.services.camera_service import CameraService, CameraSpec, CameraSystem, Snapshot
from spotcool_world.workers import WorkerState

START_S = 199 * 86_400.0 + 7 * 3600
STEP_S = 900.0
MakeSettings = Callable[..., WorldSettings]


class SceneCamera(CameraService):
    """Records the scenes it is asked to show."""

    def __init__(self, camera_id: str) -> None:
        self.camera_id = camera_id
        self.scenes: list[tuple[str, int]] = []

    @property
    def spec(self) -> CameraSpec:
        return CameraSpec(self.camera_id, "Scene", None, 1280, 720, 5.0, CameraSource.SCENARIO)

    def latest(self) -> Snapshot | None:
        return None

    async def run(self) -> None:
        return None

    def set_scene(self, scene: str, people: int) -> None:
        self.scenes.append((scene, people))


def write_record(folder: Path, steps: int) -> list[EventEnvelope]:
    """A record of ``steps`` steps: Core fills at step 2. Returns its events, in order."""
    events: list[EventEnvelope] = []

    def add(kind: EventType, data: Any, t_s: float) -> None:
        events.append(EventEnvelope.build(kind, EventSource.WORLD, data, t_s))

    for index in range(steps):
        t_s = START_S + index * STEP_S
        people = 4 if index >= 2 else 0
        scene = "many" if people else "empty"
        empty = [
            ZoneScene(zone=zone, scene="empty", people=0) for zone in Zone if zone is not Zone.COR
        ]
        zones = [ZoneScene(zone=Zone.COR, scene=scene, people=people), *empty]
        add(EventType.SCENARIO_STATE, ScenarioStateData(zones=zones), t_s)
        if index in (0, 2):
            add(
                EventType.CAMERA_SCENE,
                CameraSceneData(camera_id="cam-cor", zone=Zone.COR, scene=scene),
                t_s,
            )
        tick = TickData(index=index, step_s=STEP_S, clock=SimClock.clock_text(t_s), day_of_year=200)
        add(EventType.TICK, tick, t_s)
    add(
        EventType.KPI,
        KpiData(run="spotcool", boptest={}, provisional=False),
        START_S + steps * STEP_S,
    )
    with RunWriter(folder / EVENTS_FILE) as writer:
        writer.write(RawRecord(run="spotcool", index=0, sim_time_s=START_S, raw={"time": START_S}))
        for event in events:
            writer.write(EventRecord(envelope=event))
    return events


async def taken(subscription: Subscription) -> list[Any]:
    messages: list[Any] = []
    while True:
        try:
            async with asyncio.timeout(0):
                messages.append(await anext(subscription))
        except (TimeoutError, StopAsyncIteration):
            return messages


@pytest.mark.asyncio
async def test_a_record_is_replayed_in_order_at_its_speed(
    make_settings: MakeSettings, monkeypatch: pytest.MonkeyPatch
) -> None:
    settings = make_settings()
    recorded = write_record(settings.run_dir / "take-1", steps=5)
    cameras = {info.camera_id: SceneCamera(info.camera_id) for info in ZONES.values()}
    monkeypatch.setattr(assembly, "create_cameras", lambda _: CameraSystem(list(cameras.values())))
    replay = Replay(settings, "take-1", speed=STEP_S / 0.04)  # a step every 0.04 s
    seen = replay.bus.subscribe(EVENTS_TOPIC, maxsize=1000)
    started = time.perf_counter()
    await asyncio.wait_for(replay.run(handle_sigint=False), 30)
    elapsed = time.perf_counter() - started
    published = await taken(seen)
    seen.close()
    assert published == recorded  # every event, as recorded, in its order
    assert elapsed >= 5 * 0.04 - 0.05  # a step after each tick; Windows timers fire early
    assert cameras["cam-cor"].scenes == [("empty", 0)] * 2 + [("many", 4)] * 3
    assert cameras["cam-eas"].scenes == [("empty", 0)] * 5
    assert replay.pipeline.state is WorkerState.FINISHED
    assert all(worker.state is WorkerState.FINISHED for worker in replay.workers)
    assert {t for t in asyncio.all_tasks() if t is not asyncio.current_task()} == set()


def test_a_replay_needs_a_recorded_run(make_settings: MakeSettings) -> None:
    with pytest.raises(ValueError, match="no recorded run"):
        Replay(make_settings(), "nothing-here", speed=None)
    with pytest.raises(ValueError, match="not a run id"):
        Replay(make_settings(), "../escape", speed=None)
