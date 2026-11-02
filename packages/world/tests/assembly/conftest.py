"""Assembly test helpers: loopback settings on free ports, short scenarios, a fake BOPTEST plant.

Every world binds 127.0.0.1 only, on ports the operating system has just handed out, and records
into the test's tmp dir. No network beyond loopback, no BOPTEST, no footage.
"""

import os
import socket
from collections.abc import Callable
from pathlib import Path
from typing import Any

import pytest

from spotcool_world.services import PlantStartParams, WorldSettings
from spotcool_world.services.rc import RcPlant

HOST = "127.0.0.1"

TWO_HOURS = """\
name: two_hours
description: Two hours of a hybrid morning; South is booked from 08:30.
day: 199
start: "07:00"
end: "09:00"
zones:
  cor:
    - {at: "07:00", scene: empty, people: 0}
    - {at: "08:00", scene: many, people: 4}
  nor:
    - {at: "07:00", scene: empty, people: 0}
  sou:
    - {at: "07:00", scene: empty, people: 0}
  eas:
    - {at: "07:00", scene: empty, people: 0}
  wes:
    - {at: "07:00", scene: few, people: 1}
events:
  - {type: booking, room: meeting-south, from: "08:30", to: "09:00", subject: Stand-up,
     booked_at: "07:00"}
"""


@pytest.fixture(autouse=True)
def no_world_settings(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    # No SPOTCOOL_WORLD_* variables and no stray world.yaml: settings take their defaults.
    for key in list(os.environ):
        if key.startswith("SPOTCOOL_WORLD_"):
            monkeypatch.delenv(key)
    monkeypatch.chdir(tmp_path)


def free_port(kind: socket.SocketKind) -> int:
    """A port on 127.0.0.1 that was free a moment ago (TCP or UDP)."""
    with socket.socket(socket.AF_INET, kind) as probe:
        probe.bind((HOST, 0))
        return probe.getsockname()[1]


@pytest.fixture
def two_hours(tmp_path: Path) -> str:
    """A scenario file: 07:00-09:00 on the demo day, Core filling at 08:00, South booked 08:30."""
    path = tmp_path / "two_hours.yaml"
    path.write_text(TWO_HOURS, encoding="utf-8")
    return str(path)


@pytest.fixture
def make_settings(tmp_path: Path) -> Callable[..., WorldSettings]:
    """``make_settings(**changes)``: loopback settings on free ports, runs in the tmp dir."""

    def make(**changes: Any) -> WorldSettings:
        values: dict[str, Any] = {
            "speed": None,
            "run_dir": tmp_path / "runs",
            "http_host": HOST,
            "http_port": free_port(socket.SOCK_STREAM),
            "dashboard_port": free_port(socket.SOCK_STREAM),
            "bms_address": HOST,
            "bms_port": free_port(socket.SOCK_DGRAM),
        }
        return WorldSettings(**(values | changes))

    return make


class FakeBoptest(RcPlant):
    """An RC plant in a BOPTEST plant's place: it records its starts and stops.

    Args:
        base_url: The service URL, as ``create_plant`` passes it.
        testcase: The test case, as ``create_plant`` passes it.
    """

    made: list["FakeBoptest"] = []

    def __init__(self, base_url: str, testcase: str) -> None:
        super().__init__()
        self.base_url = base_url
        self.starts: list[PlantStartParams] = []
        self.stops = 0
        FakeBoptest.made.append(self)

    async def start(self, params: PlantStartParams) -> dict[str, float]:
        self.starts.append(params)
        return await super().start(params)

    async def stop(self) -> None:
        self.stops += 1
        await super().stop()


@pytest.fixture
def fake_boptest(monkeypatch: pytest.MonkeyPatch) -> list[FakeBoptest]:
    """Puts ``FakeBoptest`` in place of both BOPTEST plants; returns the plants made."""
    from spotcool_world.services.boptest import hosted, local

    made: list[FakeBoptest] = []
    monkeypatch.setattr(FakeBoptest, "made", made)
    monkeypatch.setattr(local, "LocalBoptestPlant", FakeBoptest)
    monkeypatch.setattr(hosted, "HostedBoptestPlant", FakeBoptest)
    return made
