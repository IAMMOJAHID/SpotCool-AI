import asyncio
import time
from collections.abc import Awaitable, Callable
from pathlib import Path

import httpx
import pytest

from spotcool_contracts.cameras import CameraSource
from spotcool_contracts.zones import Zone
from spotcool_world.services import WorldSettings
from spotcool_world.services.camera_service import (
    CameraService,
    CameraSpec,
    CameraSystem,
    Snapshot,
    create_cameras,
)
from spotcool_world.services.clips import ZoneCamera
from spotcool_world.services.live import PhoneCamera

WaitFor = Callable[..., Awaitable[None]]
BLUE, GREEN = (255, 0, 0), (0, 255, 0)


class BrokenCamera(CameraService):
    """A camera whose run() crashes at once."""

    @property
    def spec(self) -> CameraSpec:
        return CameraSpec("cam-broken", "Broken", None, 1280, 720, 5.0, CameraSource.SCENARIO)

    def latest(self) -> Snapshot | None:
        return None

    async def run(self) -> None:
        jpeg = b"\xff\xd8\xff" + b"frame" * 10  # a frame in scope must not reach the log
        raise RuntimeError(f"boom after {len(jpeg)} bytes")


def test_camera_ids_are_unique() -> None:
    with pytest.raises(ValueError, match="unique"):
        CameraSystem([BrokenCamera(), BrokenCamera()])


def test_unknown_cameras_raise_key_error() -> None:
    cameras = CameraSystem([BrokenCamera()])
    with pytest.raises(KeyError):
        cameras.snapshot("cam-zzz")
    with pytest.raises(KeyError):
        cameras.set_scene("cam-zzz", "empty", 0)
    with pytest.raises(TypeError, match="shows no scenario scenes"):
        cameras.set_scene("cam-broken", "empty", 0)


@pytest.mark.asyncio
async def test_a_crashing_camera_is_logged_without_a_traceback_and_the_others_run(
    wait_for: WaitFor, logs: list[str]
) -> None:
    system = CameraSystem([BrokenCamera(), ZoneCamera(Zone.COR)])
    await system.start()
    try:
        system.set_scene("cam-cor", "empty", 0)
        await wait_for(lambda: system.snapshot("cam-cor") is not None)
    finally:
        await system.stop()
    crashes = [line for line in logs if "camera cam-broken stopped" in line]
    assert len(crashes) == 1 and "RuntimeError: boom after 53 bytes" in crashes[0]
    assert not any("Traceback" in line for line in logs)


@pytest.mark.asyncio
async def test_create_cameras_plays_the_footage_dir(
    tmp_path: Path,
    write_video: Callable[..., Path],
    wait_for: WaitFor,
    centre: Callable[[bytes], tuple[int, int, int]],
) -> None:
    footage = tmp_path / "footage"
    write_video(footage / "clips" / "cor_many.mp4", BLUE)
    write_video(footage / "datasets" / "wisenet" / "video1_5.mp4", GREEN)
    (footage / "datasets" / "wisenet" / "index.yaml").write_text(
        "version: 1\ndataset: wisenet\ncameras:\n"
        '  cam-nor:\n    few: {file: "video1_5.mp4", start_s: 0.0, end_s: 1.0, people: 2}\n',
        encoding="utf-8",
    )
    cameras = create_cameras(WorldSettings(footage_dir=footage))
    await cameras.start()
    try:
        switched = time.perf_counter()
        cameras.set_scene("cam-cor", "many", 4)
        cameras.set_scene("cam-nor", "few", 2)

        def both() -> bool:
            frames = [cameras.snapshot(c) for c in ("cam-cor", "cam-nor")]
            return all(f is not None and f.taken_s > switched for f in frames)

        await wait_for(both)
        cor, nor = cameras.snapshot("cam-cor"), cameras.snapshot("cam-nor")
        assert cor is not None and nor is not None
        assert centre(cor.jpeg)[0] > 200 and centre(nor.jpeg)[1] > 200
        sources = {spec.id: spec.source for spec in cameras.cameras}
        assert sources["cam-cor"] is CameraSource.SCENARIO
        assert sources["cam-nor"] is CameraSource.DATASET
    finally:
        await cameras.stop()


def test_create_cameras_refuses_a_bad_live_url() -> None:
    with pytest.raises(ValueError, match="live_camera_url"):
        create_cameras(WorldSettings(live_camera_url="192.168.43.1:8080/shot.jpg"))


def test_the_http_host_and_live_camera_url_settings(monkeypatch: pytest.MonkeyPatch) -> None:
    settings = WorldSettings()
    assert (settings.http_host, settings.http_port, settings.live_camera_url) == (
        "0.0.0.0",
        8081,
        None,
    )
    monkeypatch.setenv("SPOTCOOL_WORLD_HTTP_HOST", "127.0.0.1")
    monkeypatch.setenv("SPOTCOOL_WORLD_LIVE_CAMERA_URL", "http://192.168.43.1:8080/shot.jpg")
    settings = WorldSettings()
    assert settings.http_host == "127.0.0.1"
    assert settings.live_camera_url == "http://192.168.43.1:8080/shot.jpg"
    monkeypatch.setenv("SPOTCOOL_WORLD_LIVE_CAMERA_URL", "none")
    assert WorldSettings().live_camera_url is None


@pytest.mark.asyncio
async def test_a_stopped_system_leaves_no_task() -> None:
    phone = PhoneCamera(
        "http://192.168.43.1:8080/shot.jpg",
        transport=httpx.MockTransport(lambda request: httpx.Response(503)),
    )
    cameras = CameraSystem([*(ZoneCamera(zone) for zone in Zone), phone])
    await cameras.start()
    cameras.set_scene("cam-sou", "meeting", 5)
    await asyncio.sleep(0.3)
    await cameras.stop()
    assert asyncio.all_tasks() == {asyncio.current_task()}
