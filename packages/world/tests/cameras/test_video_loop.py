from collections.abc import Callable
from pathlib import Path

import cv2
import numpy as np
import pytest

from spotcool_world.services.clips import VideoLoop


def level(image: np.ndarray) -> int:
    return round(float(image.mean()))


def test_a_real_video_plays_seeks_back_and_wraps(
    tmp_path: Path, write_video: Callable[..., Path]
) -> None:
    path = write_video(tmp_path / "ramp.mp4", frames=30, ramp=True)  # frame k is grey 8 * k
    loop = VideoLoop(path)
    try:
        assert loop.fps == 30.0
        assert abs(level(loop.frame(0)) - 0) <= 3
        assert abs(level(loop.frame(10)) - 80) <= 3
        assert abs(level(loop.frame(4)) - 32) <= 3  # behind: back to the start, then forward
        assert abs(level(loop.frame(35)) - 40) <= 3  # past the end: wraps to frame 5
    finally:
        loop.close()


def test_a_segment_starts_at_start_s_and_loops_before_end_s(
    tmp_path: Path, write_video: Callable[..., Path]
) -> None:
    loop = VideoLoop(write_video(tmp_path / "ramp.mp4", frames=30, ramp=True), 0.5, 0.8)
    try:
        assert abs(level(loop.frame(0)) - 8 * 15) <= 3  # 0.5 s at 30 fps
        assert abs(level(loop.frame(8)) - 8 * 23) <= 3
        assert abs(level(loop.frame(9)) - 8 * 15) <= 3  # 9 frames long: back to its start
    finally:
        loop.close()


def test_what_cannot_play_raises_oserror(tmp_path: Path, write_video: Callable[..., Path]) -> None:
    with pytest.raises(OSError, match="cannot open"):
        VideoLoop(tmp_path / "missing.mp4")
    garbage = tmp_path / "garbage.mp4"
    garbage.write_bytes(b"not a video" * 100)
    with pytest.raises(OSError, match="cannot open"):
        VideoLoop(garbage)
    with pytest.raises(OSError, match="no frames between"):
        VideoLoop(write_video(tmp_path / "short.mp4", frames=10), 2.0, 3.0)


class FakeCapture:
    """A capture whose header promises more frames than the file holds."""

    def __init__(self, frames: int, header_frames: int) -> None:
        self.frames, self.header_frames, self.position = frames, header_frames, 0

    def isOpened(self) -> bool:
        return True

    def get(self, prop: int) -> float:
        return 30.0 if prop == cv2.CAP_PROP_FPS else float(self.header_frames)

    def set(self, prop: int, value: float) -> bool:
        self.position = int(value)
        return True

    def grab(self) -> bool:
        if self.position >= self.frames:
            return False
        self.position += 1
        return True

    def read(self) -> tuple[bool, np.ndarray | None]:
        if not self.grab():
            return False, None
        return True, np.full((2, 2, 3), self.position - 1, np.uint8)

    def release(self) -> None:
        pass


def test_a_file_shorter_than_its_header_loops_at_its_real_end(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(cv2, "VideoCapture", lambda *_: FakeCapture(10, 40))
    loop = VideoLoop(Path("fake.mp4"))
    assert level(loop.frame(25)) == 5  # read to the end at 10 frames, so 25 wraps to 5
    assert level(loop.frame(12)) == 2


def test_a_file_without_frames_raises_oserror(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(cv2, "VideoCapture", lambda *_: FakeCapture(0, 0))
    loop = VideoLoop(Path("fake.mp4"))
    with pytest.raises(OSError, match="no frames"):
        loop.frame(3)
