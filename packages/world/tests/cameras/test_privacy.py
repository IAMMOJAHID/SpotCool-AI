"""AGENTS.md Law 6: camera frames live in memory only, and no log line carries them."""

import asyncio
import builtins
import inspect
import io
import logging
import os
import time
from collections.abc import Awaitable, Callable, MutableMapping
from pathlib import Path
from types import ModuleType
from typing import Any

import cv2
import httpx
import numpy as np
import pytest

from spotcool_contracts.zones import Zone
from spotcool_world.processors import camera_scene
from spotcool_world.services import camera_service
from spotcool_world.services.camera_service import CameraService, CameraSystem
from spotcool_world.services.clips import ZoneCamera, camera, synthetic, video
from spotcool_world.services.datasets import index
from spotcool_world.services.live import PhoneCamera, phone
from spotcool_world.transports.http import cameras, server
from spotcool_world.transports.http.cameras import cameras_router
from spotcool_world.transports.http.server import create_http_app
from spotcool_world.workers import http_server

PHONE_URL = "http://192.168.43.1:8080/shot.jpg"
JPEG_MARKERS = ("\xff\xd8\xff", "\\xff\\xd8\\xff", "ffd8ff", "/9j/")  # raw, repr, hex, base64
WRITE_FLAGS = os.O_WRONLY | os.O_RDWR | os.O_CREAT
CAMERA_MODULES: tuple[ModuleType, ...] = (
    camera_service,
    camera,
    synthetic,
    video,
    index,
    phone,
    cameras,
    server,
    http_server,
    camera_scene,
)


def phone_answer(request: httpx.Request) -> httpx.Response:
    ok, data = cv2.imencode(".jpg", np.full((480, 640, 3), 120, np.uint8))
    assert ok
    return httpx.Response(200, content=data.tobytes())


async def mjpeg_parts(app: Any, path: str, parts: int) -> list[bytes]:
    """Read ``parts`` MJPEG parts straight through ASGI, then disconnect."""
    chunks: list[bytes] = []
    gone = asyncio.Event()

    async def receive() -> dict[str, Any]:
        await gone.wait()
        return {"type": "http.disconnect"}

    async def send(message: MutableMapping[str, Any]) -> None:
        if message["type"] == "http.response.body" and message["body"]:
            chunks.append(message["body"])
            if len(chunks) >= parts:
                gone.set()

    scope = {
        "type": "http",
        "asgi": {"version": "3.0", "spec_version": "2.3"},
        "http_version": "1.1",
        "method": "GET",
        "scheme": "http",
        "path": path,
        "raw_path": path.encode(),
        "query_string": b"",
        "root_path": "",
        "headers": [(b"host", b"world")],
        "client": ("127.0.0.1", 50000),
        "server": ("127.0.0.1", 8081),
    }
    await asyncio.wait_for(app(scope, receive, send), timeout=10)
    return chunks


@pytest.mark.asyncio
async def test_serving_frames_writes_nothing_and_logs_no_frame_bytes(
    tmp_path: Path,
    write_video: Callable[..., Path],
    wait_for: Callable[..., Awaitable[None]],
    logs: list[str],
    caplog: pytest.LogCaptureFixture,
    capfdbinary: pytest.CaptureFixture[bytes],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    clips = tmp_path / "clips"
    write_video(clips / "cor_many.mp4")  # the test's own video, written before the guards
    (clips / "nor_few.mp4").write_bytes(b"broken" * 100)  # a failing source logs warnings
    writes: list[str] = []
    real_open, real_os_open = builtins.open, os.open

    def guarded_open(file: Any, mode: str = "r", *args: Any, **kwargs: Any) -> Any:
        if any(flag in mode for flag in "wax+"):
            writes.append(f"open({file!r}, {mode!r})")
            raise PermissionError("camera frames are never written")
        return real_open(file, mode, *args, **kwargs)

    def guarded_os_open(path: Any, flags: int, *args: Any, **kwargs: Any) -> int:
        if flags & WRITE_FLAGS:
            writes.append(f"os.open({path!r}, {flags:#x})")
            raise PermissionError("camera frames are never written")
        return real_os_open(path, flags, *args, **kwargs)

    def refused(name: str) -> Callable[..., Any]:
        def refuse(*args: Any, **kwargs: Any) -> Any:
            writes.append(name)
            raise PermissionError("camera frames are never written")

        return refuse

    caplog.set_level(logging.DEBUG)
    feeds: list[CameraService] = [ZoneCamera(zone, clips_dir=clips) for zone in Zone]
    feeds.append(PhoneCamera(PHONE_URL, transport=httpx.MockTransport(phone_answer)))
    system = CameraSystem(feeds)
    app = create_http_app([cameras_router(system)])
    ids = [spec.id for spec in system.cameras]
    # The guards hold while frames are served; pytest's own teardown writes temporary files.
    with monkeypatch.context() as guards:
        guards.setattr(builtins, "open", guarded_open)
        guards.setattr(io, "open", guarded_open)
        guards.setattr(os, "open", guarded_os_open)
        guards.setattr(cv2, "imwrite", refused("cv2.imwrite"))
        guards.setattr(cv2, "VideoWriter", refused("cv2.VideoWriter"))
        await system.start()
        try:
            scenes = ("many", "few", "meeting", "empty", "door_open")
            for zone, scene in zip(Zone, scenes, strict=True):
                system.set_scene(f"cam-{zone.value}", scene, 3)
            await wait_for(lambda: all(system.snapshot(camera_id) is not None for camera_id in ids))
            transport = httpx.ASGITransport(app=app)
            async with httpx.AsyncClient(transport=transport, base_url="http://world") as http:
                for n in range(50):
                    response = await http.get(f"/cameras/{ids[n % len(ids)]}/snapshot.jpg")
                    assert response.status_code == 200
                    assert response.content.startswith(b"\xff\xd8\xff")
            began = time.perf_counter()
            parts = await mjpeg_parts(app, "/cameras/cam-cor/mjpeg", parts=3)
            assert len(parts) == 3 and time.perf_counter() - began < 5
        finally:
            await system.stop()

    assert writes == []
    out, err = capfdbinary.readouterr()  # the process's stdout and stderr, native writes too
    text = "\n".join([*logs, caplog.text, out.decode("latin-1"), err.decode("latin-1")])
    assert "cannot play clip" in text  # the failing source was logged...
    for marker in JPEG_MARKERS:  # ...and no log line or output carries frame bytes
        assert marker not in text and marker not in text.lower()


@pytest.mark.parametrize("module", CAMERA_MODULES, ids=lambda module: module.__name__)
def test_no_camera_module_logs_a_traceback(module: ModuleType) -> None:
    source = inspect.getsource(module)
    assert "logger.exception" not in source
    assert "opt(exception" not in source
