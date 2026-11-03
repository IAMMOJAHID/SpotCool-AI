import asyncio
import contextlib
import time
from collections.abc import Awaitable, Callable, MutableMapping
from typing import Any

import cv2
import httpx
import numpy as np
import pytest
from fastapi import FastAPI

from spotcool_contracts.cameras import CameraCatalog, CameraSource
from spotcool_contracts.zones import Zone
from spotcool_world.services import WorldSettings
from spotcool_world.services.camera_service import (
    CameraService,
    CameraSpec,
    CameraSystem,
    Snapshot,
    create_cameras,
)
from spotcool_world.services.clips import synthetic_frame
from spotcool_world.transports.http.calendar import CalendarStore, calendar_router
from spotcool_world.transports.http.cameras import MJPEG_BOUNDARY, cameras_router
from spotcool_world.transports.http.server import create_http_app, is_dashboard_origin

BASE = "http://10.20.22.190:8081"
WaitFor = Callable[..., Awaitable[None]]


class StillCamera(CameraService):
    """A camera whose frame the test sets."""

    def __init__(self, camera_id: str = "cam-cor") -> None:
        self.camera_id = camera_id
        self.frame: Snapshot | None = None

    @property
    def spec(self) -> CameraSpec:
        return CameraSpec(self.camera_id, "Still", Zone.COR, 1280, 720, 5.0, CameraSource.SCENARIO)

    def latest(self) -> Snapshot | None:
        return self.frame

    async def run(self) -> None:
        await asyncio.Event().wait()


def snapshot(people: int = 2, age_s: float = 0.0) -> Snapshot:
    ok, data = cv2.imencode(".jpg", synthetic_frame("cam-cor", "many", people, 0))
    assert ok
    return Snapshot(data.tobytes(), time.perf_counter() - age_s)


def app_with(camera: StillCamera) -> FastAPI:
    return create_http_app([cameras_router(CameraSystem([camera]))])


async def get(app: FastAPI, url: str, **request: Any) -> httpx.Response:
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url=BASE) as http:
        return await http.get(url, **request)


@pytest.mark.asyncio
async def test_the_catalog_is_the_contract_with_urls_for_the_asker() -> None:
    app = create_http_app([cameras_router(create_cameras(WorldSettings()))])
    response = await get(app, "/cameras")
    assert response.status_code == 200
    catalog = CameraCatalog.model_validate(response.json())
    assert [c.id for c in catalog.cameras] == [
        "cam-cor",
        "cam-nor",
        "cam-sou",
        "cam-eas",
        "cam-wes",
    ]
    core = catalog.cameras[0]
    assert core.snapshot_url == f"{BASE}/cameras/cam-cor/snapshot.jpg"
    assert (core.zone, core.width, core.height, core.fps_hint) == (Zone.COR, 1280, 720, 5.0)
    assert core.source is CameraSource.SCENARIO and core.stream_url is None
    assert "never stored" in catalog.privacy_notice
    for_south = catalog.for_zone(Zone.SOU)
    assert [c.id for c in for_south] == ["cam-sou"]


@pytest.mark.asyncio
async def test_the_live_phone_is_in_the_catalog_only_when_its_url_is_set() -> None:
    settings = WorldSettings(live_camera_url="http://192.168.43.1:8080/shot.jpg")
    app = create_http_app([cameras_router(create_cameras(settings))])
    catalog = CameraCatalog.model_validate((await get(app, "/cameras")).json())
    live = catalog.cameras[-1]
    assert len(catalog.cameras) == 6
    assert (live.id, live.zone, live.source) == ("cam-meeting-live", None, CameraSource.LIVE)
    assert live.snapshot_url == f"{BASE}/cameras/cam-meeting-live/snapshot.jpg"
    assert live.fps_hint == 2.0


@pytest.mark.asyncio
async def test_a_snapshot_is_the_latest_jpeg_and_never_cached(
    decode: Callable[[bytes], np.ndarray],
) -> None:
    camera = StillCamera()
    camera.frame = snapshot()
    response = await get(app_with(camera), "/cameras/cam-cor/snapshot.jpg")
    assert response.status_code == 200
    assert response.headers["content-type"] == "image/jpeg"
    assert response.headers["cache-control"] == "no-store"
    assert "x-camera-stale" not in response.headers
    assert response.content == camera.frame.jpeg
    assert decode(response.content).shape == (720, 1280, 3)


@pytest.mark.asyncio
async def test_a_snapshot_from_a_running_zone_camera_decodes_as_1280x720(
    wait_for: WaitFor, decode: Callable[[bytes], np.ndarray]
) -> None:
    cameras = create_cameras(WorldSettings())
    app = create_http_app([cameras_router(cameras)])
    await cameras.start()
    try:
        cameras.set_scene("cam-nor", "few", 2)
        await wait_for(lambda: cameras.snapshot("cam-nor") is not None)
        response = await get(app, "/cameras/cam-nor/snapshot.jpg")
    finally:
        await cameras.stop()
    assert response.status_code == 200
    assert decode(response.content).shape == (720, 1280, 3)


@pytest.mark.asyncio
async def test_a_frame_5_s_old_is_marked_stale() -> None:
    camera = StillCamera()
    camera.frame = snapshot(age_s=5.5)
    response = await get(app_with(camera), "/cameras/cam-cor/snapshot.jpg")
    assert response.status_code == 200
    assert response.headers["x-camera-stale"] == "1"


@pytest.mark.parametrize(
    ("url", "status"),
    [
        ("/cameras/cam-cor/snapshot.jpg", 503),  # no frame yet
        ("/cameras/cam-zzz/snapshot.jpg", 404),
        ("/cameras/cam-zzz/mjpeg", 404),
        ("/cameras/CAM-COR/snapshot.jpg", 422),
        ("/cameras/cam.cor/snapshot.jpg", 422),
        (f"/cameras/{'c' * 65}/snapshot.jpg", 422),
    ],
)
@pytest.mark.asyncio
async def test_unknown_cameras_and_cameras_without_a_frame_are_refused(
    url: str, status: int
) -> None:
    response = await get(app_with(StillCamera()), url)
    assert response.status_code == status
    if status == 404:
        assert "cameras: cam-cor" in response.json()["detail"]


async def stream(app: FastAPI, path: str, parts: int) -> tuple[dict[str, Any], list[bytes], bool]:
    """GET ``path`` straight through ASGI; disconnect once ``parts`` body chunks arrived.

    Returns the response start message, the non-empty body chunks, and whether the app ended
    the body itself (``more_body`` false).
    """
    start: dict[str, Any] = {}
    chunks: list[bytes] = []
    ended = False
    gone = asyncio.Event()

    async def receive() -> dict[str, Any]:
        await gone.wait()
        return {"type": "http.disconnect"}

    async def send(message: MutableMapping[str, Any]) -> None:
        nonlocal ended
        if message["type"] == "http.response.start":
            start.update(message)
        elif message["type"] == "http.response.body":
            if message["body"]:
                chunks.append(message["body"])
            ended = ended or not message.get("more_body", False)
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
    return start, chunks, ended


def jpeg_of(part: bytes) -> bytes:
    """The JPEG in one multipart part, after checking the part's framing."""
    head, _, rest = part.partition(b"\r\n\r\n")
    lines = head.split(b"\r\n")
    assert lines[0] == f"--{MJPEG_BOUNDARY}".encode()
    assert lines[1] == b"Content-Type: image/jpeg"
    assert lines[2].startswith(b"Content-Length: ")
    length = int(lines[2].removeprefix(b"Content-Length: "))
    assert rest.endswith(b"\r\n") and len(rest) == length + 2
    return rest[:length]


@pytest.mark.asyncio
async def test_mjpeg_parts_are_well_formed_new_frames_at_most_5_a_second(
    count_people: Callable[[bytes], int],
) -> None:
    camera = StillCamera()
    camera.frame = snapshot(people=0)

    async def film() -> None:  # a new frame every 50 ms, one more person each time
        for people in range(1, 100):
            await asyncio.sleep(0.05)
            camera.frame = snapshot(people=people % 12)

    filming = asyncio.ensure_future(film())
    began = time.perf_counter()
    try:
        start, chunks, ended = await stream(app_with(camera), "/cameras/cam-cor/mjpeg", parts=4)
    finally:
        filming.cancel()
        with contextlib.suppress(asyncio.CancelledError):
            await filming
    took = time.perf_counter() - began
    headers = dict(start["headers"])
    assert start["status"] == 200
    assert (
        headers[b"content-type"] == f"multipart/x-mixed-replace; boundary={MJPEG_BOUNDARY}".encode()
    )
    assert headers[b"cache-control"] == b"no-store"
    counts = [count_people(jpeg_of(chunk)) for chunk in chunks]
    assert len(counts) == 4 and len(set(counts)) == 4  # four different frames
    assert took >= 3 * 0.2 - 0.05  # 5 a second at most (Windows timers may fire a bit early)
    assert not ended  # the client left; the app did not end the body


@pytest.mark.asyncio
async def test_mjpeg_sends_a_frame_once_and_ends_when_the_server_closes() -> None:
    camera = StillCamera()
    camera.frame = snapshot()
    app = app_with(camera)

    async def close_soon() -> None:
        await asyncio.sleep(0.7)
        app.state.closing.set()

    closing = asyncio.ensure_future(close_soon())
    start, chunks, ended = await stream(app, "/cameras/cam-cor/mjpeg", parts=99)
    await closing
    assert start["status"] == 200
    assert len(chunks) == 1  # the frame did not change, so it went once
    assert ended  # the stream ended itself, without a disconnect


@pytest.mark.asyncio
async def test_mjpeg_waits_for_the_first_frame() -> None:
    camera = StillCamera()

    async def first_frame() -> None:
        await asyncio.sleep(0.5)
        camera.frame = snapshot()

    arriving = asyncio.ensure_future(first_frame())
    _, chunks, _ = await stream(app_with(camera), "/cameras/cam-cor/mjpeg", parts=1)
    await arriving
    assert camera.frame is not None
    assert jpeg_of(chunks[0]) == camera.frame.jpeg


@pytest.mark.asyncio
async def test_healthz_answers_200() -> None:
    response = await get(create_http_app([]), "/healthz")
    assert (response.status_code, response.json()) == (200, {"status": "ok"})


@pytest.mark.parametrize(
    ("origin", "allowed"),
    [
        ("http://localhost:5173", True),
        ("http://127.0.0.1:8090", True),
        ("http://localhost", True),
        ("http://example.com", False),
        ("http://localhost.evil.com:5173", False),
        ("https://localhost:5173", False),
    ],
)
@pytest.mark.asyncio
async def test_cors_lets_local_pages_read_the_api(origin: str, allowed: bool) -> None:
    camera = StillCamera()
    camera.frame = snapshot(age_s=9.0)
    response = await get(
        app_with(camera), "/cameras/cam-cor/snapshot.jpg", headers={"Origin": origin}
    )
    assert response.headers.get("access-control-allow-origin") == (origin if allowed else None)
    if allowed:
        assert response.headers["access-control-expose-headers"] == "X-Camera-Stale"


@pytest.mark.asyncio
async def test_the_app_serves_the_calendar_too() -> None:
    cameras = CameraSystem([StillCamera()])
    app = create_http_app([cameras_router(cameras), calendar_router(CalendarStore())])
    schedule = await get(app, "/calendar/rooms/meeting-south/schedule")
    assert schedule.status_code == 200
    assert schedule.json() == {"scheduleId": "meeting-south", "scheduleItems": []}
    assert (await get(app, "/calendar/rooms/meeting-south.ics")).status_code == 200
    assert (await get(app, "/cameras")).status_code == 200


@pytest.mark.parametrize(
    ("origin", "allowed"),
    [
        ("http://192.168.1.12:8090", True),  # the dashboard opened from another PC on the LAN
        ("http://10.0.0.7:8090", True),
        ("http://172.16.4.2:8090", True),
        ("http://169.254.10.20:8090", True),
        ("http://[fe80::1]:8090", True),
        ("http://[::1]:8090", True),
        ("http://127.0.0.2:8090", True),
        ("http://localhost:5173", True),  # the development origins stay
        ("http://evil.example:8090", False),
        ("http://8.8.8.8:8090", False),
        ("http://172.32.0.1:8090", False),
        ("http://192.168.1.12:9999", False),  # not the dashboard's port
        ("http://192.168.1.12", False),
        ("https://192.168.1.12:8090", False),
        ("http://user@192.168.1.12:8090", False),
        ("http://192.168.1.12:8090/x", False),
    ],
)
@pytest.mark.asyncio
async def test_cors_lets_the_dashboard_page_on_a_private_address_read_the_api(
    origin: str, allowed: bool
) -> None:
    app = create_http_app([], dashboard_port=8090)
    response = await get(app, "/healthz", headers={"Origin": origin})
    assert response.headers.get("access-control-allow-origin") == (origin if allowed else None)
    assert is_dashboard_origin("http://192.168.1.12:8090", None) is False  # no dashboard: none
