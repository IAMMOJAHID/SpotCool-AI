import os
from collections.abc import Callable
from pathlib import Path

import pytest

BASE_YAML = """\
name: test_day
description: A small valid day.
day: 199
start: "07:00"
end: "19:00"
zones:
  cor:
    - {at: "07:00", scene: empty, people: 0}
    - {at: "08:30", scene: many, people: 4}
  nor:
    - {at: "07:00", scene: empty, people: 0}
  sou:
    - {at: "07:00", scene: empty, people: 0}
    - {at: "14:00", scene: meeting, people: 5}
  eas:
    - {at: "07:00", scene: empty, people: 0}
  wes:
    - {at: "07:00", scene: few, people: 1}
events:
  - {type: door_open, zone: wes, from: "10:00", to: "10:45"}
  - {type: booking, room: meeting-south, from: "14:00", to: "15:00", booked_at: "13:45"}
  - {type: complaint, zone: sou, at: "14:15", channel: chat, text: "Too cold in here."}
  - {type: clothing, zone: sou, at: "14:30", clothing: heavy}
"""


@pytest.fixture(autouse=True)
def no_world_settings(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    # No SPOTCOOL_WORLD_* variables and no stray world.yaml: settings take their defaults.
    for key in list(os.environ):
        if key.startswith("SPOTCOOL_WORLD_"):
            monkeypatch.delenv(key)
    monkeypatch.chdir(tmp_path)


@pytest.fixture
def base_yaml() -> str:
    return BASE_YAML


@pytest.fixture
def write_scenario(tmp_path: Path) -> Callable[..., Path]:
    def write(text: str, name: str = "test_day.yaml") -> Path:
        path = tmp_path / name
        path.write_text(text, encoding="utf-8")
        return path

    return write
