"""The clip camera serves the frame the wall clock is on, as a JPEG, and loops."""

import sys
import threading
import urllib.request
from pathlib import Path

import pytest

cv2 = pytest.importorskip("cv2")
np = pytest.importorskip("numpy")

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from clip_camera import ClipPlayer, serve  # noqa: E402


def make_clip(path: Path, frames: int = 10, fps: float = 10.0) -> Path:
    """A synthetic clip whose frame i is filled with grey level 20*i."""
    writer = cv2.VideoWriter(str(path), cv2.VideoWriter_fourcc(*"MJPG"), fps, (64, 48))
    for i in range(frames):
        writer.write(np.full((48, 64, 3), 20 * i, np.uint8))
    writer.release()
    return path


def level(jpeg: bytes) -> float:
    return float(cv2.imdecode(np.frombuffer(jpeg, np.uint8), cv2.IMREAD_GRAYSCALE).mean())


def test_follows_the_wall_clock_and_loops(tmp_path: Path) -> None:
    now = [0.0]
    player = ClipPlayer(make_clip(tmp_path / "c.avi"), clock=lambda: now[0])
    assert level(player.jpeg()) == pytest.approx(0, abs=6)
    now[0] = 0.5  # frame 5
    assert level(player.jpeg()) == pytest.approx(100, abs=6)
    now[0] = 1.25  # 12 frames in: looped to frame 2
    assert level(player.jpeg()) == pytest.approx(40, abs=6)


def test_serves_shot_jpg(tmp_path: Path) -> None:
    server = serve(ClipPlayer(make_clip(tmp_path / "c.avi")), 0)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    try:
        port = server.server_address[1]
        with urllib.request.urlopen(f"http://127.0.0.1:{port}/shot.jpg", timeout=5) as answer:
            body = answer.read()
            assert answer.headers["Content-Type"] == "image/jpeg"
        assert body[:3] == b"\xff\xd8\xff"
        with pytest.raises(urllib.error.HTTPError):
            urllib.request.urlopen(f"http://127.0.0.1:{port}/other", timeout=5)
    finally:
        server.shutdown()
        server.server_close()


def test_a_second_camera_cannot_share_the_port(tmp_path: Path) -> None:
    first = serve(ClipPlayer(make_clip(tmp_path / "c.avi")), 0)
    try:
        with pytest.raises(OSError):
            serve(ClipPlayer(make_clip(tmp_path / "d.avi")), first.server_address[1])
    finally:
        first.server_close()
