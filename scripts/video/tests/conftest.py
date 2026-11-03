import shutil
import subprocess
import sys
from collections.abc import Callable, Sequence
from pathlib import Path
from textwrap import dedent

import pytest

# The video scripts import each other as top-level modules, as when run from scripts/video.
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import media  # noqa: E402
from storyboard import Storyboard, load_storyboard  # noqa: E402


class FakeRun:
    """Stands in for ``media.run``: records each command and answers from a script."""

    def __init__(self) -> None:
        self.calls: list[list[str]] = []
        self.answers: list[Callable[[list[str]], tuple[str, str]]] = []

    def __call__(
        self, args: Sequence[str], cwd: Path | None = None, env: dict[str, str] | None = None
    ) -> subprocess.CompletedProcess[str]:
        args = list(args)
        self.calls.append(args)
        stdout, stderr = ("", "")
        for answer in self.answers:
            stdout, stderr = answer(args) or ("", "")
            if stdout or stderr:
                break
        return subprocess.CompletedProcess(args, 0, stdout, stderr)


@pytest.fixture
def fake_run(monkeypatch: pytest.MonkeyPatch) -> FakeRun:
    """Replace every program the scripts run with a recorder."""
    fake = FakeRun()
    monkeypatch.setattr(media, "run", fake)
    return fake


@pytest.fixture
def ffmpeg() -> None:
    """Skip a test that needs the real ffmpeg when it is missing."""
    if not (shutil.which("ffmpeg") and shutil.which("ffprobe")):
        pytest.skip("ffmpeg/ffprobe not on PATH")


TINY_BOARD = """
video: {title: Tiny, fps: 30, width: 1920, height: 1080, max_s: 178}
beats:
  - id: intro
    start: 0
    end: 2
    type: screen
    source: {take: intro_take, in_s: 1, scenes: [{at: 0, scene: Dashboard}]}
    narration_id: intro
    on_screen_text: {label: "A label at priority 8"}
    sources: [bacnet]
  - id: outro
    start: 2
    end: 4
    type: screen
    source: {take: outro_take, scenes: [{at: 0, scene: YABE}, {at: 1, scene: Dashboard}]}
    narration_id: outro
    on_screen_text: {}
    sources: []
"""


@pytest.fixture
def tiny_board(tmp_path: Path) -> Callable[..., Storyboard]:
    """A two-beat storyboard of screen takes, with ``TINY_BOARD`` edited by replacements."""

    def make(**replace: str) -> Storyboard:
        text = dedent(TINY_BOARD)
        for old, new in replace.items():
            text = text.replace(old, new)
        path = tmp_path / "storyboard.yaml"
        path.write_text(text, "utf-8")
        return load_storyboard(path)

    return make
