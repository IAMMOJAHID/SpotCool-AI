import asyncio
from collections.abc import AsyncIterator, Awaitable, Callable

import cv2
import httpx
import numpy as np
import pytest

from spotcool_contracts.cameras import CameraSource
from spotcool_world.services.camera_service import CameraSystem
from spotcool_world.services.live import LIVE_CAMERA_ID, PhoneCamera
from spotcool_world.services.live import phone as phone_module

URL = "http://192.168.43.1:8080/shot.jpg"
WaitFor = Callable[..., Awaitable[None]]
Answer = httpx.Response | Exception | Callable[[], Awaitable[httpx.Response]]


def jpeg(width: int = 640, height: int = 480, level: int = 90) -> bytes:
    ok, data = cv2.imencode(".jpg", np.full((height, width, 3), level, np.uint8))
    assert ok
    return data.tobytes()


def ok(body: bytes) -> httpx.Response:
    return httpx.Response(200, content=body, headers={"Content-Type": "image/jpeg"})


class Phone:
    """Answers the n-th poll with the n-th answer (the last one repeats) and counts the polls."""

    def __init__(self, *answers: Answer) -> None:
        self.answers = answers
        self.polls = 0

    async def __call__(self, request: httpx.Request) -> httpx.Response:
        assert str(request.url) == URL
        self.polls += 1
        answer = self.answers[min(self.polls, len(self.answers)) - 1]
        if isinstance(answer, Exception):
            raise answer
        if isinstance(answer, httpx.Response):
            return answer
        return await answer()


@pytest.mark.asyncio
async def test_a_good_jpeg_is_served_as_it_came(wait_for: WaitFor) -> None:
    frame = jpeg(640, 480)
    camera = PhoneCamera(URL, transport=httpx.MockTransport(Phone(ok(frame))))
    assert camera.latest() is None
    assert (camera.spec.width, camera.spec.height) == (1280, 720)  # until the first frame
    system = CameraSystem([camera])
    await system.start()
    try:
        await wait_for(lambda: camera.latest() is not None)
    finally:
        await system.stop()
    snapshot = camera.latest()
    assert snapshot is not None and snapshot.jpeg == frame
    spec = camera.spec
    assert (spec.id, spec.zone, spec.source, spec.fps_hint) == (
        LIVE_CAMERA_ID,
        None,
        CameraSource.LIVE,
        2.0,
    )
    assert (spec.width, spec.height) == (640, 480)


async def endless_jpeg() -> AsyncIterator[bytes]:
    yield b"\xff\xd8\xff"
    while True:
        yield b"0" * 1_000_000


async def slow() -> httpx.Response:
    await asyncio.sleep(5)
    return ok(jpeg())


@pytest.mark.parametrize(
    ("answer", "problem"),
    [
        (httpx.Response(200, text="<html>IP Webcam</html>"), "the answer is not a JPEG"),
        (ok(b"\xff\xd8\xff" + b"garbage" * 100), "the JPEG does not decode"),
        (httpx.Response(200, content=endless_jpeg()), "not smaller than 5000000 bytes"),
        (httpx.Response(500, text="oops"), "HTTP 500"),
        (httpx.Response(302, headers={"Location": "http://elsewhere/"}), "HTTP 302"),
        (httpx.ReadTimeout("timed out"), "ReadTimeout"),
        (httpx.ConnectError("no route to host"), "ConnectError"),
        (slow, "TimeoutError"),
    ],
)
@pytest.mark.asyncio
async def test_a_bad_answer_is_never_served(
    answer: Answer,
    problem: str,
    wait_for: WaitFor,
    logs: list[str],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(phone_module, "TIMEOUT_S", 0.1)
    phone = Phone(answer)
    camera = PhoneCamera(URL, transport=httpx.MockTransport(phone))
    system = CameraSystem([camera])
    await system.start()
    try:
        await wait_for(lambda: any(problem in line for line in logs))
    finally:
        await system.stop()
    assert camera.latest() is None
    warnings = [line for line in logs if "WARNING" in line]
    assert len(warnings) == 1 and f"camera {LIVE_CAMERA_ID}: no frame from {URL}" in warnings[0]


@pytest.mark.asyncio
async def test_the_last_good_frame_stays_while_the_phone_fails(
    wait_for: WaitFor, logs: list[str]
) -> None:
    first, second = jpeg(level=60), jpeg(level=200)
    phone = Phone(ok(first), httpx.Response(503), httpx.ConnectError("gone"), ok(second))
    camera = PhoneCamera(URL, transport=httpx.MockTransport(phone))
    system = CameraSystem([camera])
    await system.start()
    try:
        await wait_for(lambda: camera.latest() is not None)
        await wait_for(lambda: phone.polls >= 3)
        snapshot = camera.latest()
        assert snapshot is not None and snapshot.jpeg == first
        await wait_for(lambda: (s := camera.latest()) is not None and s.jpeg == second)
    finally:
        await system.stop()
    assert sum("WARNING" in line for line in logs) == 1  # logged when the failing starts
    assert any(f"frames from {URL} again" in line for line in logs)


@pytest.mark.parametrize(
    "url", ["ftp://192.168.43.1/shot.jpg", "http:///shot.jpg", "192.168.43.1:8080/shot.jpg"]
)
def test_the_url_must_be_http_with_a_host(url: str) -> None:
    with pytest.raises(ValueError, match="live_camera_url"):
        PhoneCamera(url)


@pytest.mark.parametrize("poll_fps", [0.0, 2.5])
def test_polls_are_at_most_two_a_second(poll_fps: float) -> None:
    with pytest.raises(ValueError, match="poll_fps"):
        PhoneCamera(URL, poll_fps=poll_fps)


def test_the_phone_shows_no_scenes() -> None:
    with pytest.raises(TypeError, match="cam-meeting-live shows no scenario scenes"):
        PhoneCamera(URL).set_scene("empty", 0)
