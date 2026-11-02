"""Assembly test helpers: loopback settings on free ports, a short scenario, a fake BOPTEST.

Every world binds 127.0.0.1 only, on ports the operating system has just handed out, and records
into the test's tmp dir. No network beyond loopback, no BOPTEST, no footage.
"""

import asyncio
import os
import socket
import time
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, cast

import pytest

from spotcool_world.services import PlantStartParams, WorldSettings
from spotcool_world.services.boptest.client import BoptestClient, BoptestError
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
        tcp, udp = socket.SOCK_STREAM, socket.SOCK_DGRAM
        http, dashboard, bms, vrf = free_ports(tcp, tcp, udp, udp)
        values: dict[str, Any] = {
            "speed": None,
            "run_dir": tmp_path / "runs",
            "http_host": HOST,
            "http_port": http,
            "dashboard_port": dashboard,
            "bms_address": HOST,
            "bms_port": bms,
            "vrf_port": vrf,
        }
        return WorldSettings(**(values | changes))

    return make


class FakeBoptestClient:
    """``BoptestClient`` for one test, with an RC plant's physics; ``stop`` takes ``stop_s``.

    ``events`` records ``select``, ``stop sent``, ``stop done`` and ``closed``, in order, and
    ``stop_sent_s`` when the stop was sent (``time.perf_counter``). The first
    ``results_failures`` results calls fail, and the next ``lost_answers`` advances step the
    plant but lose their answer.

    Args:
        base_url: The service URL, as the plant's provider passes it.
        stop_s: How long a stop request takes.
        kpis_s: How long the KPIs take.
    """

    def __init__(self, base_url: str, stop_s: float, kpis_s: float = 0.0) -> None:
        self.base_url = base_url
        self.stop_s = stop_s
        self.kpis_s = kpis_s
        self.events: list[str] = []
        self.stop_sent = asyncio.Event()
        self.stop_sent_s: float | None = None
        self.kpis_asked = asyncio.Event()
        self.idle_s = 0.0
        self.results_failures = 0
        self.lost_answers = 0
        self._rc = RcPlant()
        self._state: dict[str, float] = {}

    async def select(self, testcase: str) -> str:
        self.events.append("select")
        return f"test-{id(self)}"

    async def initialize(
        self, testid: str, start_time_s: float, warmup_s: float
    ) -> dict[str, float]:
        params = PlantStartParams(start_time_s=start_time_s, step_s=900.0, warmup_s=warmup_s)
        self._state = await self._rc.start(params)
        return dict(self._state)

    async def set_step(self, testid: str, step_s: float) -> None:
        assert step_s == 900.0

    async def advance(
        self, testid: str, inputs: Mapping[str, float], *, timeout_s: float = 120.0
    ) -> dict[str, float]:
        self._state = await self._rc.step({})  # BOPTEST's own control: the RC plant's schedule
        if self.lost_answers:
            self.lost_answers -= 1
            raise BoptestError(f"POST /advance/{testid}: ReadTimeout after 120 s")
        return dict(self._state)

    async def results(
        self,
        testid: str,
        point_names: Sequence[str],
        start_time_s: float,
        final_time_s: float,
        *,
        timeout_s: float | None = None,
    ) -> dict[str, list[float]]:
        if self.results_failures:
            self.results_failures -= 1
            raise BoptestError("PUT /results: HTTP 500: results failed", status=500)
        # Each point holds the RC plant's value over the step: its meters' means.
        samples = {name: [self._state[name]] * 2 for name in point_names}
        return samples | {"time": [start_time_s, min(final_time_s, self._state["time"])]}

    async def state_at(
        self,
        testid: str,
        point_names: Sequence[str],
        time_s: float,
        *,
        timeout_s: float | None = None,
    ) -> dict[str, float]:
        assert time_s == self._state["time"]  # the latest stored time
        return {name: self._state[name] for name in point_names if name in self._state} | {
            "time": time_s
        }

    async def kpis(self, testid: str) -> dict[str, float | None]:
        self.kpis_asked.set()
        await asyncio.sleep(self.kpis_s)
        return await self._rc.kpis()

    async def keep_alive(self, testid: str) -> None:
        pass

    async def stop(self, testid: str) -> None:
        self.events.append("stop sent")
        self.stop_sent_s = time.perf_counter()
        self.stop_sent.set()
        await asyncio.sleep(self.stop_s)
        self.events.append("stop done")

    async def aclose(self) -> None:
        self.events.append("closed")


@dataclass
class FakeBoptest:
    """The fake BOPTEST clients the world's plants were given, and how long their calls take.

    Parameters:
        clients: The clients, in the order the plants were made (spotcool, then baseline).
        stop_s: How long the next clients' stop requests take.
        kpis_s: How long the next clients' KPIs take.
    """

    clients: list[FakeBoptestClient] = field(default_factory=list)
    stop_s: float = 0.0
    kpis_s: float = 0.0


@pytest.fixture
def fake_boptest(monkeypatch: pytest.MonkeyPatch) -> FakeBoptest:
    """Both BOPTEST providers make real ``BoptestPlant``s on ``FakeBoptestClient``s."""
    from spotcool_world.services.boptest import hosted, local

    fake = FakeBoptest()

    def make(base_url: str, testcase: str) -> BoptestPlant:
        fake.clients.append(FakeBoptestClient(base_url, fake.stop_s, fake.kpis_s))
        return BoptestPlant(cast(BoptestClient, fake.clients[-1]), testcase)

    monkeypatch.setattr(local, "LocalBoptestPlant", make)
    monkeypatch.setattr(hosted, "HostedBoptestPlant", make)
    return fake
