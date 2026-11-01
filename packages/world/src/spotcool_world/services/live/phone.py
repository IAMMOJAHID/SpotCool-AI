"""PhoneCamera: a real Android phone as the live camera ``cam-meeting-live``."""

import asyncio
import time

import cv2
import httpx
import numpy as np
from loguru import logger

from spotcool_contracts.cameras import CameraSource
from spotcool_world.services.camera_service import CameraService, CameraSpec, Snapshot

LIVE_CAMERA_ID = "cam-meeting-live"
"""The live camera's id in the catalog."""
MAX_POLL_FPS = 2.0
"""The most polls of the phone per second."""
TIMEOUT_S = 2.0
"""The longest one poll may take."""
MAX_JPEG_BYTES = 5_000_000
"""A snapshot must be smaller than this."""
DEFAULT_SIZE = (1280, 720)
"""Frame size in the catalog until the first frame: the resolution the phone steps set."""
_JPEG_MAGIC = b"\xff\xd8\xff"


class PhoneCamera(CameraService):
    """The live camera: polls a phone's snapshot URL and serves the last good JPEG.

    It sends a GET at most ``poll_fps`` times a second; each poll gives up after 2 s. An answer
    counts only if it is HTTP 200, starts with the JPEG magic bytes, is smaller than 5 MB (the
    body is read in chunks and dropped once it grows too large) and decodes. The JPEG is
    served as it came, and the catalog reports its size.

    The last good frame stays until a newer one arrives; the router marks it stale once it is
    5 s old. There is no snapshot until the first good frame. A failing phone is logged when it
    starts and stops failing, and is never fatal.

    Args:
        url: The phone's snapshot URL, such as ``http://192.168.43.1:8080/shot.jpg`` (IP Webcam).
        poll_fps: Polls per second, at most 2.
        transport: httpx transport; tests pass an ``httpx.MockTransport``.

    Raises:
        ValueError: If ``url`` is not an http or https URL with a host, or ``poll_fps`` is not
            in (0, 2].
    """

    def __init__(
        self,
        url: str,
        *,
        poll_fps: float = MAX_POLL_FPS,
        transport: httpx.AsyncBaseTransport | None = None,
    ) -> None:
        if not 0 < poll_fps <= MAX_POLL_FPS:
            raise ValueError(f"poll_fps must be in (0, {MAX_POLL_FPS:g}], got {poll_fps}")
        self._url = _phone_url(url)
        self._shown_url = self._url.copy_with(userinfo=b"")  # for logs: never the credentials
        self._fps = poll_fps
        self._transport = transport
        self._latest: Snapshot | None = None
        self._size = DEFAULT_SIZE

    @property
    def spec(self) -> CameraSpec:
        """The live camera's catalog entry; the size is that of the latest frame."""
        width, height = self._size
        name = "Meeting room: live phone"
        return CameraSpec(LIVE_CAMERA_ID, name, None, width, height, self._fps, CameraSource.LIVE)

    def latest(self) -> Snapshot | None:
        """The last good frame, or ``None`` before the first one."""
        return self._latest

    async def run(self) -> None:
        """Poll the phone until cancelled."""
        period = 1.0 / self._fps
        failing = False
        async with httpx.AsyncClient(timeout=TIMEOUT_S, transport=self._transport) as http:
            while True:
                started = time.perf_counter()
                try:
                    jpeg, size = await self._fetch(http)
                except Exception as exc:  # any failure of the phone; no traceback (Law 6)
                    if not failing:
                        logger.warning(
                            f"camera {LIVE_CAMERA_ID}: no frame from {self._shown_url}: "
                            f"{type(exc).__name__}: {exc}"
                        )
                    failing = True
                else:
                    if failing:
                        logger.info(f"camera {LIVE_CAMERA_ID}: frames from {self._shown_url} again")
                    failing = False
                    self._latest, self._size = Snapshot(jpeg, time.perf_counter()), size
                await asyncio.sleep(max(0.0, period - (time.perf_counter() - started)))

    async def _fetch(self, http: httpx.AsyncClient) -> tuple[bytes, tuple[int, int]]:
        """One snapshot from the phone and its size.

        Raises:
            ValueError: If the answer is not a usable JPEG.
            TimeoutError: If the poll took longer than ``TIMEOUT_S``.
            httpx.HTTPError: If the request failed.
        """
        body = bytearray()
        async with asyncio.timeout(TIMEOUT_S), http.stream("GET", self._url) as response:
            if response.status_code != 200:
                raise ValueError(f"HTTP {response.status_code}")
            async for chunk in response.aiter_bytes():
                body += chunk
                if len(body) >= MAX_JPEG_BYTES:
                    raise ValueError(f"the answer is not smaller than {MAX_JPEG_BYTES} bytes")
        if not body.startswith(_JPEG_MAGIC):
            raise ValueError("the answer is not a JPEG")
        jpeg = bytes(body)
        size = await asyncio.to_thread(_decoded_size, jpeg)
        if size is None:
            raise ValueError("the JPEG does not decode")
        return jpeg, size


def _decoded_size(jpeg: bytes) -> tuple[int, int] | None:
    """Width and height of a JPEG that decodes, else ``None``. Blocks (OpenCV)."""
    image = cv2.imdecode(np.frombuffer(jpeg, np.uint8), cv2.IMREAD_COLOR)
    return None if image is None else (int(image.shape[1]), int(image.shape[0]))


def _phone_url(text: str) -> httpx.URL:
    """The phone's snapshot URL, checked: an http or https scheme and a host.

    The messages never repeat the URL's user name and password.

    Raises:
        ValueError: If it is not such a URL.
    """
    try:
        url = httpx.URL(text)
    except httpx.InvalidURL as exc:
        raise ValueError(f"live_camera_url is not a URL: {exc}") from None
    if url.scheme not in ("http", "https") or not url.host:
        shown = str(url.copy_with(userinfo=b""))
        raise ValueError(
            f"live_camera_url {shown!r} needs an http:// or https:// scheme and a host"
        )
    return url
