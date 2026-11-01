import shutil
import subprocess
import sys
from collections.abc import Callable
from pathlib import Path

import pytest

# The footage scripts import each other as top-level modules, as when run from scripts/footage.
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))


@pytest.fixture
def make_clip() -> Callable[..., Path]:
    """Return a function that writes a synthetic ffmpeg clip; skips the test without ffmpeg.

    Clips carry a fake GPS "location" tag, like phone videos, so tests can check it is stripped.
    """
    if not (shutil.which("ffmpeg") and shutil.which("ffprobe")):
        pytest.skip("ffmpeg/ffprobe not on PATH")

    def make(
        path: Path, seconds: float = 3, size: str = "320x240", rate: int = 25, audio: bool = True
    ) -> Path:
        path.parent.mkdir(parents=True, exist_ok=True)
        cmd = ["ffmpeg", "-hide_banner", "-loglevel", "error", "-y"]
        cmd += ["-f", "lavfi", "-i", f"testsrc=size={size}:rate={rate}:duration={seconds}"]
        if audio:  # 44.1 kHz stereo AAC, like a phone recording
            cmd += ["-f", "lavfi", "-i", f"sine=frequency=440:sample_rate=44100:duration={seconds}"]
            cmd += ["-ac", "2", "-c:a", "aac"]
        cmd += ["-c:v", "mpeg4" if path.suffix == ".avi" else "libx264"]
        cmd += ["-metadata", "location=+48.8584+002.2945/", str(path)]
        subprocess.run(cmd, check=True)
        return path

    return make
