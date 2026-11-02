"""Assembly test helpers: loopback settings on free ports, a short scenario, a fake BOPTEST.

Every world binds 127.0.0.1 only, on ports the operating system has just handed out, and records
into the test's tmp dir. No network beyond loopback, no BOPTEST, no footage.
"""

import asyncio
import os
import socket
from collections.abc import Callable, Mapping
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, cast

import pytest

from spotcool_world.services import PlantStartParams, WorldSettings
from spotcool_world.services.boptest.client import BoptestClient
from spotcool_world.services.boptest.plant import BoptestPlant
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


def free_ports(*kinds: socket.SocketKind) -> list[int]:
    """One port per kind (TCP or UDP) on 127.0.0.1, all free a moment ago and all different.

    The probes stay bound until every port is chosen, so no two are the same.
    """
    probes = [socket.socket(socket.AF_INET, kind) for kind in kinds]
    try:
        for probe in probes:
            probe.bind((HOST, 0))
        return [probe.getsockname()[1] for probe in probes]
    finally:
        for probe in probes:
            probe.close()


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
        http, dashboard, bms = free_ports(socket.SOCK_STREAM, socket.SOCK_STREAM, socket.SOCK_DGRAM)
        values: dict[str, Any] = {
            "speed": None,
            "run_dir": tmp_path / "runs",
            "http_host": HOST,
            "http_port": http,
            "dashboard_port": dashboard,
            "bms_address": HOST,
            "bms_port": bms,
        }
        return WorldSettings(**(values | changes))

    return make


class FakeBoptestClient:
    """``BoptestClient`` for one test, with an RC plant's physics; ``stop`` takes ``stop_s``.

    ``events`` records ``select``, ``stop sent``, ``stop done`` and ``closed``, in order.

    Args:
        base_url: The service URL, as the plant's provider passes it.
        stop_s: How long a stop request takes.
    """

    def __init__(self, base_url: str, stop_s: float) -> None:
        self.base_url = base_url
        self.stop_s = stop_s
        self.events: list[str] = []
        self.stop_sent = asyncio.Event()
        self.idle_s = 0.0
        self._rc = RcPlant()

    async def select(self, testcase: str) -> str:
        self.events.append("select")
        return f"test-{id(self)}"

    async def initialize(
        self, testid: str, start_time_s: float, warmup_s: float
    ) -> dict[str, float]:
        params = PlantStartParams(start_time_s=start_time_s, step_s=900.0, warmup_s=warmup_s)
        return await self._rc.start(params)

    async def set_step(self, testid: str, step_s: float) -> None:
        assert step_s == 900.0

    async def advance(self, testid: str, inputs: Mapping[str, float]) -> dict[str, float]:
        return await self._rc.step({})  # BOPTEST's own control: the RC plant's schedule

    async def kpis(self, testid: str) -> dict[str, float | None]:
        return await self._rc.kpis()

    async def keep_alive(self, testid: str) -> None:
        pass

    async def stop(self, testid: str) -> None:
        self.events.append("stop sent")
        self.stop_sent.set()
        await asyncio.sleep(self.stop_s)
        self.events.append("stop done")

    async def aclose(self) -> None:
        self.events.append("closed")


@dataclass
class FakeBoptest:
    """The fake BOPTEST clients the world's plants were given, and how long a stop takes.

    Parameters:
        clients: The clients, in the order the plants were made (spotcool, then baseline).
        stop_s: How long the next clients' stop requests take.
    """

    clients: list[FakeBoptestClient] = field(default_factory=list)
    stop_s: float = 0.0


@pytest.fixture
def fake_boptest(monkeypatch: pytest.MonkeyPatch) -> FakeBoptest:
    """Both BOPTEST providers make real ``BoptestPlant``s on ``FakeBoptestClient``s."""
    from spotcool_world.services.boptest import hosted, local

    fake = FakeBoptest()

    def make(base_url: str, testcase: str) -> BoptestPlant:
        fake.clients.append(FakeBoptestClient(base_url, fake.stop_s))
        return BoptestPlant(cast(BoptestClient, fake.clients[-1]), testcase)

    monkeypatch.setattr(local, "LocalBoptestPlant", make)
    monkeypatch.setattr(hosted, "HostedBoptestPlant", make)
    return fake
