import time
from collections.abc import Awaitable, Callable

import pytest

from spotcool_contracts.cameras import CameraSource
from spotcool_contracts.events import CameraSceneData, ScenarioStateData, ZoneScene
from spotcool_contracts.zones import ZONES, Zone
from spotcool_world.frames import CameraSceneFrame, Frame, ScenarioStateFrame, TickFrame
from spotcool_world.pipeline import PipelineParams
from spotcool_world.processors import CameraSceneProcessor, FrameDirection
from spotcool_world.services import WorldSettings
from spotcool_world.services.camera_service import (
    CameraService,
    CameraSpec,
    CameraSystem,
    Snapshot,
    create_cameras,
)
from spotcool_world.testing import CaptureProcessor, run_test

T = 199 * 86_400 + 8 * 3600 + 30 * 60  # 08:30 on the demo day


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


def zone_cameras() -> dict[str, SceneCamera]:
    return {info.camera_id: SceneCamera(info.camera_id) for info in ZONES.values()}


def state(t_s: float = T, **scenes: tuple[str, int]) -> ScenarioStateFrame:
    """Every zone empty, except the ones given as ``zone=(scene, people)``."""
    zones = [
        ZoneScene(
            zone=zone,
            scene=scenes.get(zone.value, ("empty", 0))[0],
            people=scenes.get(zone.value, ("empty", 0))[1],
        )
        for zone in Zone
    ]
    return ScenarioStateFrame(state=ScenarioStateData(zones=zones), sim_time_s=t_s)


def reported(frames: list[Frame]) -> list[CameraSceneData]:
    return [frame.data for frame in frames if isinstance(frame, CameraSceneFrame)]


@pytest.mark.asyncio
async def test_the_first_state_of_a_run_sets_and_reports_every_camera() -> None:
    cameras = zone_cameras()
    first = state(cor=("many", 4), wes=("few", 1))
    tick = TickFrame(index=0, step_s=900.0, sim_time_s=T)
    down, up = await run_test(
        CameraSceneProcessor(CameraSystem(list(cameras.values()))), frames_to_send=[first, tick]
    )
    assert up == []
    assert down[0] is first and down[-1] is tick  # the state first, the tick last
    assert reported(down) == [
        CameraSceneData(camera_id="cam-cor", zone=Zone.COR, scene="many"),
        CameraSceneData(camera_id="cam-nor", zone=Zone.NOR, scene="empty"),
        CameraSceneData(camera_id="cam-sou", zone=Zone.SOU, scene="empty"),
        CameraSceneData(camera_id="cam-eas", zone=Zone.EAS, scene="empty"),
        CameraSceneData(camera_id="cam-wes", zone=Zone.WES, scene="few"),
    ]
    assert all(frame.sim_time_s == T for frame in down if isinstance(frame, CameraSceneFrame))
    assert cameras["cam-cor"].scenes == [("many", 4)]
    assert cameras["cam-wes"].scenes == [("few", 1)]


@pytest.mark.asyncio
async def test_later_states_report_only_the_cameras_whose_scene_changed() -> None:
    cameras = zone_cameras()
    frames = [
        state(T, cor=("many", 4)),
        state(T + 900, cor=("many", 3), sou=("meeting", 5)),  # cor: a new count, same scene
    ]
    down, _ = await run_test(
        CameraSceneProcessor(CameraSystem(list(cameras.values()))), frames_to_send=frames
    )
    later = reported(down[down.index(frames[1]) :])
    assert later == [CameraSceneData(camera_id="cam-sou", zone=Zone.SOU, scene="meeting")]
    assert cameras["cam-cor"].scenes == [("many", 4), ("many", 3)]  # set on every state


@pytest.mark.asyncio
async def test_a_new_run_reports_every_camera_again() -> None:
    cameras = zone_cameras()
    frames = [state(), PipelineParams(run_id="second").start_frame(), state()]
    down, _ = await run_test(
        CameraSceneProcessor(CameraSystem(list(cameras.values()))), frames_to_send=frames
    )
    assert len(reported(down)) == 10


@pytest.mark.asyncio
async def test_upstream_frames_pass_without_setting_scenes() -> None:
    cameras = zone_cameras()
    processor = CameraSceneProcessor(CameraSystem(list(cameras.values())))
    above: list[Frame] = []
    CaptureProcessor(FrameDirection.UPSTREAM, above).link(processor)
    frame = state()
    await processor.process_frame(frame, FrameDirection.UPSTREAM)
    assert above == [frame]
    assert all(camera.scenes == [] for camera in cameras.values())


def test_every_zone_camera_must_be_in_the_system() -> None:
    cameras = zone_cameras()
    del cameras["cam-sou"]
    with pytest.raises(ValueError, match="no cam-sou"):
        CameraSceneProcessor(CameraSystem(list(cameras.values())))


@pytest.mark.asyncio
async def test_a_scenario_state_switches_what_the_next_snapshot_shows(
    wait_for: Callable[..., Awaitable[None]], count_people: Callable[[bytes], int]
) -> None:
    cameras = create_cameras(WorldSettings())  # no footage: synthetic frames
    processor = CameraSceneProcessor(cameras)
    await cameras.start()
    try:
        for scene, people in (("many", 4), ("few", 1)):
            switched = time.perf_counter()
            await run_test(processor, frames_to_send=[state(cor=(scene, people))])

            def fresh(after_s: float = switched) -> bool:
                frame = cameras.snapshot("cam-cor")
                return frame is not None and frame.taken_s > after_s

            await wait_for(fresh)
            frame = cameras.snapshot("cam-cor")
            assert frame is not None and count_people(frame.jpeg) == people
    finally:
        await cameras.stop()
