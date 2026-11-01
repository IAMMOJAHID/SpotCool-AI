import asyncio
import time
from collections.abc import AsyncGenerator, Awaitable, Callable
from contextlib import asynccontextmanager
from pathlib import Path

import numpy as np
import pytest

from spotcool_contracts.cameras import CameraSource
from spotcool_contracts.zones import Zone
from spotcool_world.services.camera_service import CameraService, CameraSystem
from spotcool_world.services.clips import VideoLoop, ZoneCamera
from spotcool_world.services.clips import camera as camera_module
from spotcool_world.services.datasets import DatasetIndex, Segment

BLUE, GREEN = (255, 0, 0), (0, 255, 0)
Colour = tuple[int, int, int]
WaitFor = Callable[..., Awaitable[None]]


@asynccontextmanager
async def running(*cameras: CameraService) -> AsyncGenerator[CameraSystem, None]:
    system = CameraSystem(cameras)
    await system.start()
    try:
        yield system
    finally:
        await system.stop()


async def frame_after(camera: CameraService, after_s: float, wait_for: WaitFor) -> bytes:
    """The first frame the camera takes after ``after_s`` (``time.perf_counter()``)."""

    def taken() -> bool:
        frame = camera.latest()
        return frame is not None and frame.taken_s > after_s

    await wait_for(taken)
    frame = camera.latest()
    assert frame is not None
    return frame.jpeg


async def show(camera: ZoneCamera, scene: str, people: int, wait_for: WaitFor) -> bytes:
    switched = time.perf_counter()
    camera.set_scene(scene, people)
    return await frame_after(camera, switched, wait_for)


def near(colour: Colour, wanted: Colour) -> bool:
    return all(abs(a - b) <= 40 for a, b in zip(colour, wanted, strict=True))


@pytest.fixture
def synthetic(decode: Callable[[bytes], np.ndarray]) -> Callable[[bytes], bool]:
    def is_synthetic(jpeg: bytes) -> bool:
        bar = decode(jpeg)[10:45, 1000:1200].reshape(-1, 3).mean(axis=0)  # the caption bar
        return all(abs(channel - 40) <= 15 for channel in bar)

    return is_synthetic


@pytest.fixture
def wisenet(tmp_path: Path, write_video: Callable[..., Path]) -> DatasetIndex:
    path = write_video(tmp_path / "datasets" / "wisenet" / "video5_4.mp4", GREEN, frames=60)
    return DatasetIndex("wisenet", {("cam-cor", "many"): Segment(path, 0.5, 1.5)})


@pytest.mark.asyncio
async def test_there_is_no_frame_until_the_first_scene() -> None:
    camera = ZoneCamera(Zone.COR)
    async with running(camera):
        await asyncio.sleep(0.3)
        assert camera.latest() is None
    spec = camera.spec
    assert (spec.id, spec.zone, spec.width, spec.height) == ("cam-cor", Zone.COR, 1280, 720)
    assert (spec.fps_hint, spec.source, spec.name) == (
        5.0,
        CameraSource.SCENARIO,
        "Core: Open-plan workspace",
    )


@pytest.mark.asyncio
async def test_synthetic_frames_draw_the_scenarios_people_count(
    wait_for: WaitFor, count_people: Callable[[bytes], int], decode: Callable[[bytes], np.ndarray]
) -> None:
    camera = ZoneCamera(Zone.SOU)
    async with running(camera):
        jpeg = await show(camera, "meeting", 5, wait_for)
        assert decode(jpeg).shape == (720, 1280, 3)
        assert count_people(jpeg) == 5
        assert count_people(await show(camera, "meeting", 3, wait_for)) == 3  # same scene


@pytest.mark.asyncio
async def test_our_clip_comes_before_a_dataset_segment(
    tmp_path: Path,
    write_video: Callable[..., Path],
    wisenet: DatasetIndex,
    wait_for: WaitFor,
    centre: Callable[[bytes], Colour],
) -> None:
    write_video(tmp_path / "clips" / "cor_many.mp4", BLUE)
    camera = ZoneCamera(Zone.COR, clips_dir=tmp_path / "clips", datasets=[wisenet])
    async with running(camera):
        assert near(centre(await show(camera, "many", 4, wait_for)), BLUE)
        assert camera.spec.source is CameraSource.SCENARIO


@pytest.mark.asyncio
async def test_a_dataset_segment_comes_before_synthetic_frames(
    tmp_path: Path, wisenet: DatasetIndex, wait_for: WaitFor, centre: Callable[[bytes], Colour]
) -> None:
    camera = ZoneCamera(Zone.COR, clips_dir=tmp_path / "clips", datasets=[wisenet])
    async with running(camera):
        assert near(centre(await show(camera, "many", 4, wait_for)), GREEN)
        assert camera.spec.source is CameraSource.DATASET


@pytest.mark.asyncio
async def test_a_scene_switch_takes_the_next_frame_from_the_new_source(
    tmp_path: Path,
    write_video: Callable[..., Path],
    wisenet: DatasetIndex,
    wait_for: WaitFor,
    centre: Callable[[bytes], Colour],
    synthetic: Callable[[bytes], bool],
) -> None:
    write_video(tmp_path / "clips" / "cor_few.mp4", BLUE)
    camera = ZoneCamera(Zone.COR, clips_dir=tmp_path / "clips", datasets=[wisenet])
    async with running(camera):
        assert near(centre(await show(camera, "few", 1, wait_for)), BLUE)
        assert near(centre(await show(camera, "many", 4, wait_for)), GREEN)
        assert camera.spec.source is CameraSource.DATASET
        assert synthetic(await show(camera, "empty", 0, wait_for))
        assert camera.spec.source is CameraSource.SCENARIO
        assert near(centre(await show(camera, "few", 1, wait_for)), BLUE)


def level(jpeg: bytes, decode: Callable[[bytes], np.ndarray]) -> float:
    return float(decode(jpeg).mean())


@pytest.mark.asyncio
async def test_a_new_scene_starts_at_its_first_frame_and_plays_in_wall_time(
    tmp_path: Path,
    write_video: Callable[..., Path],
    wait_for: WaitFor,
    decode: Callable[[bytes], np.ndarray],
) -> None:
    for scene in ("few", "many"):  # frame k is grey 8 * k, white from frame 32
        write_video(tmp_path / "clips" / f"cor_{scene}.mp4", frames=90, ramp=True)
    camera = ZoneCamera(Zone.COR, clips_dir=tmp_path / "clips")
    async with running(camera):
        assert level(await show(camera, "few", 1, wait_for), decode) < 8 * 12
        await asyncio.sleep(1.2)  # past frame 32: the clip has played on
        assert level(await frame_after(camera, time.perf_counter(), wait_for), decode) > 200
        assert level(await show(camera, "many", 4, wait_for), decode) < 8 * 12


@pytest.mark.asyncio
async def test_a_clip_that_cannot_play_falls_back_and_says_so(
    tmp_path: Path,
    wisenet: DatasetIndex,
    wait_for: WaitFor,
    centre: Callable[[bytes], Colour],
    synthetic: Callable[[bytes], bool],
    logs: list[str],
) -> None:
    (tmp_path / "clips").mkdir()
    (tmp_path / "clips" / "cor_many.mp4").write_bytes(b"not a video" * 100)
    (tmp_path / "clips" / "cor_few.mp4").write_bytes(b"not a video" * 100)
    camera = ZoneCamera(Zone.COR, clips_dir=tmp_path / "clips", datasets=[wisenet])
    async with running(camera):
        assert near(centre(await show(camera, "many", 4, wait_for)), GREEN)
        assert synthetic(await show(camera, "few", 1, wait_for))
    assert sum("cannot play clip" in line for line in logs) == 2
    assert any("scene few from synthetic frames" in line for line in logs)


@pytest.mark.asyncio
async def test_the_source_is_logged_once_per_scene(
    tmp_path: Path, write_video: Callable[..., Path], wait_for: WaitFor, logs: list[str]
) -> None:
    write_video(tmp_path / "clips" / "cor_many.mp4", BLUE)
    camera = ZoneCamera(Zone.COR, clips_dir=tmp_path / "clips")
    async with running(camera):
        for scene in ("many", "empty", "many", "empty"):
            await show(camera, scene, 0, wait_for)
    assert sum("camera cam-cor: scene many from clip" in line for line in logs) == 1
    assert sum("camera cam-cor: scene empty from synthetic frames" in line for line in logs) == 1


@pytest.mark.asyncio
async def test_a_scene_name_cannot_reach_outside_the_clips_folder(
    tmp_path: Path,
    write_video: Callable[..., Path],
    wait_for: WaitFor,
    synthetic: Callable[[bytes], bool],
) -> None:
    write_video(tmp_path / "evil.mp4", BLUE)
    (tmp_path / "clips" / "cor_x").mkdir(parents=True)
    camera = ZoneCamera(Zone.COR, clips_dir=tmp_path / "clips")
    async with running(camera):
        assert synthetic(await show(camera, "x/../../evil", 1, wait_for))


@pytest.mark.asyncio
async def test_frames_are_1280x720_whatever_the_video_size(
    tmp_path: Path,
    write_video: Callable[..., Path],
    wait_for: WaitFor,
    decode: Callable[[bytes], np.ndarray],
) -> None:
    write_video(tmp_path / "clips" / "wes_few.mp4", BLUE, size=(640, 360))
    camera = ZoneCamera(Zone.WES, clips_dir=tmp_path / "clips")
    async with running(camera):
        assert decode(await show(camera, "few", 1, wait_for)).shape == (720, 1280, 3)


@pytest.mark.asyncio
async def test_stop_leaves_no_task_and_a_restart_plays_again(
    tmp_path: Path, write_video: Callable[..., Path], wait_for: WaitFor
) -> None:
    write_video(tmp_path / "clips" / "eas_few.mp4", BLUE)
    camera = ZoneCamera(Zone.EAS, clips_dir=tmp_path / "clips")
    system = CameraSystem([camera])
    await system.start()
    await system.start()  # a second start does nothing
    await show(camera, "few", 2, wait_for)
    await system.stop()
    assert asyncio.all_tasks() == {asyncio.current_task()}
    await system.start()
    await frame_after(camera, time.perf_counter(), wait_for)
    await system.stop()
    await system.stop()  # safe twice
    assert asyncio.all_tasks() == {asyncio.current_task()}


def test_snapshot_fps_must_be_positive() -> None:
    with pytest.raises(ValueError, match="snapshot_fps"):
        ZoneCamera(Zone.COR, snapshot_fps=0)


@pytest.mark.asyncio
async def test_a_render_that_ends_after_stop_opens_no_video(
    tmp_path: Path,
    write_video: Callable[..., Path],
    wait_for: WaitFor,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    write_video(tmp_path / "clips" / "cor_many.mp4", BLUE)
    opened: list[VideoLoop] = []
    closed: list[VideoLoop] = []

    class TrackedLoop(VideoLoop):
        def __init__(self, path: Path, start_s: float = 0.0, end_s: float | None = None) -> None:
            super().__init__(path, start_s, end_s)
            opened.append(self)

        def close(self) -> None:
            closed.append(self)
            super().close()

    class LateCamera(ZoneCamera):
        def _render(self, scene: str, people: int, began_s: float, now_s: float) -> bytes:
            time.sleep(0.3)  # the render thread reaches the lock after stop() closed the video
            return super()._render(scene, people, began_s, now_s)

    monkeypatch.setattr(camera_module, "VideoLoop", TrackedLoop)
    camera = LateCamera(Zone.COR, clips_dir=tmp_path / "clips")
    system = CameraSystem([camera])
    await system.start()
    camera.set_scene("many", 4)
    await wait_for(lambda: camera.latest() is not None)  # the next render is already sleeping
    await system.stop()
    await asyncio.sleep(0.6)  # that render ends now
    assert opened and all(loop in closed for loop in opened)
