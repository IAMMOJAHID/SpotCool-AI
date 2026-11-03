"""Camera test helpers: tiny generated videos, JPEG decoding and people counting.

Videos are written by the tests themselves, into their tmp dir; no real footage, network or phone
is needed.
"""

import asyncio
import os
import time
from collections.abc import Awaitable, Callable, Iterator
from pathlib import Path

import cv2
import numpy as np
import numpy.typing as npt
import pytest
from loguru import logger

Image = npt.NDArray[np.uint8]
BLUE = (255, 0, 0)  # BGR: our clips in the tests
GREEN = (0, 255, 0)  # BGR: dataset segments in the tests


@pytest.fixture(autouse=True)
def no_world_settings(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    # No SPOTCOOL_WORLD_* variables and no stray world.yaml: settings take their defaults.
    for key in list(os.environ):
        if key.startswith("SPOTCOOL_WORLD_"):
            monkeypatch.delenv(key)
    monkeypatch.chdir(tmp_path)


@pytest.fixture
def logs() -> Iterator[list[str]]:
    """Every loguru record while the test runs, in loguru's default format (tracebacks too)."""
    records: list[str] = []
    sink = logger.add(lambda message: records.append(str(message)), level=0)
    yield records
    logger.remove(sink)


def _write_video(
    path: Path,
    colour: tuple[int, int, int] = BLUE,
    *,
    frames: int = 30,
    fps: float = 30.0,
    size: tuple[int, int] = (1280, 720),
    ramp: bool = False,
) -> Path:
    """Solid ``colour`` frames; with ``ramp``, frame k is grey at level 8 * k instead."""
    path.parent.mkdir(parents=True, exist_ok=True)
    writer = cv2.VideoWriter(str(path), cv2.VideoWriter.fourcc(*"mp4v"), fps, size)
    assert writer.isOpened()
    for k in range(frames):
        image = np.empty((size[1], size[0], 3), np.uint8)
        image[:] = (min(255, 8 * k),) * 3 if ramp else colour
        writer.write(image)
    writer.release()
    return path


@pytest.fixture
def write_video() -> Callable[..., Path]:
    return _write_video


def _decode(jpeg: bytes) -> Image:
    image = cv2.imdecode(np.frombuffer(jpeg, np.uint8), cv2.IMREAD_COLOR)
    assert image is not None, "not a JPEG"
    return np.asarray(image, np.uint8)


@pytest.fixture
def decode() -> Callable[[bytes], Image]:
    return _decode


def _count_people(jpeg: bytes) -> int:
    """People in a synthetic frame: blobs of saturated colour on the grey room."""
    hsv = cv2.cvtColor(_decode(jpeg), cv2.COLOR_BGR2HSV)
    mask = (hsv[:, :, 1] > 120).astype(np.uint8)
    _, _, stats, _ = cv2.connectedComponentsWithStats(mask, connectivity=8)
    return int((stats[1:, cv2.CC_STAT_AREA] > 400).sum())


@pytest.fixture
def count_people() -> Callable[[bytes], int]:
    return _count_people


def _centre(jpeg: bytes) -> tuple[int, int, int]:
    """Mean BGR colour of the frame's centre, where the test videos' colour is."""
    image = _decode(jpeg)
    h, w = image.shape[:2]
    blue, green, red = image[h // 3 : 2 * h // 3, w // 3 : 2 * w // 3].reshape(-1, 3).mean(axis=0)
    return round(blue), round(green), round(red)


@pytest.fixture
def centre() -> Callable[[bytes], tuple[int, int, int]]:
    return _centre


def _encode(image: Image) -> bytes:
    ok, jpeg = cv2.imencode(".jpg", image)
    assert ok
    return jpeg.tobytes()


@pytest.fixture
def encode() -> Callable[[Image], bytes]:
    return _encode


async def _wait_for(condition: Callable[[], object], timeout_s: float = 10.0) -> None:
    deadline = time.perf_counter() + timeout_s
    while not condition():
        assert time.perf_counter() < deadline, "timed out waiting"
        await asyncio.sleep(0.02)


@pytest.fixture
def wait_for() -> Callable[..., Awaitable[None]]:
    return _wait_for
