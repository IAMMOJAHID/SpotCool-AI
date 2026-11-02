"""VRF test fixtures: a gateway and a BACpypes3 client on loopback, and the worker on fake time.

Ports start at 47960, clear of 47808-47810 where a real device or YABE may listen, and of the
BMS tests' ports from 47900; a test is skipped when its ports cannot be bound. No test waits in
real time beyond a loopback round trip: the worker runs on a fake clock and sleep.
"""

import asyncio
import itertools
import socket
from collections.abc import AsyncIterator, Awaitable, Callable
from dataclasses import dataclass, field
from typing import Any

import pytest
import pytest_asyncio
from bacpypes3.ipv4.app import NormalApplication
from bacpypes3.local.device import DeviceObject
from bacpypes3.pdu import IPv4Address

from spotcool_contracts import load_vrf_profiles
from spotcool_contracts.events import EventEnvelope, EventType, VrfStateData
from spotcool_world.bus import InProcessBus
from spotcool_world.clocks import SimClock
from spotcool_world.transports.bacnet.vrf import BacnetVrfGateway
from spotcool_world.workers.vrf_worker import VrfWorker

HOST = "127.0.0.1"
T0 = 198 * 86_400 + 10 * 3600  # 18 July, 10:00
_PORTS = itertools.count(47960, 2)


@dataclass
class WorkerRun:
    """What one run of the worker did, in fake wall seconds since it started.

    Parameters:
        ticks: When each tick moved the unit on.
        published: When each ``vrf_state`` event went out, and its data.
        events: Every event it published.
    """

    ticks: list[float] = field(default_factory=list)
    published: list[tuple[float, VrfStateData]] = field(default_factory=list)
    events: list[EventEnvelope] = field(default_factory=list)


class FakeTime:
    """A wall clock, and a sleep that moves it and yields once to the loop.

    Parameters:
        start: The clock when the worker starts.
        now: The clock now; move it on to make the worker late.
    """

    def __init__(self) -> None:
        self.start = self.now = 1000.0

    @property
    def elapsed(self) -> float:
        """Seconds since the worker started."""
        return self.now - self.start

    def clock(self) -> float:
        return self.now

    async def sleep(self, seconds: float) -> None:
        self.now += seconds
        await asyncio.sleep(0)


class _RecordingBus(InProcessBus):
    """A bus that notes when, in fake time, each event was published."""

    def __init__(self, time: FakeTime, run: WorkerRun) -> None:
        super().__init__()
        self._time, self._run = time, run

    async def publish(self, topic: str, message: Any) -> None:
        assert isinstance(message, EventEnvelope)
        self._run.events.append(message)
        if message.type is EventType.VRF_STATE:
            state = VrfStateData.model_validate(message.data)
            self._run.published.append((self._time.elapsed, state))
        await super().publish(topic, message)


RunWorker = Callable[..., Awaitable[WorkerRun]]


@pytest.fixture
def run_worker() -> RunWorker:
    """``await run_worker(gateway, seconds, during=None)``: run the gateway's worker for
    ``seconds`` of fake wall time. ``await during(time)`` runs after each sleep, with the
    ``FakeTime``, before the tick moves the unit on."""

    async def run(
        gateway: BacnetVrfGateway,
        seconds: float,
        during: Callable[[FakeTime], Awaitable[None]] | None = None,
    ) -> WorkerRun:
        time, result = FakeTime(), WorkerRun()

        async def sleep(delay: float) -> None:
            await time.sleep(delay)
            if during is not None:
                await during(time)
            result.ticks.append(time.elapsed)
            if time.elapsed >= seconds - 1e-9:
                await worker.stop()

        worker = VrfWorker(gateway, SimClock(T0, 900.0), clock=time.clock, sleep=sleep)
        worker.bus = _RecordingBus(time, result)
        await worker.run()
        return result

    return run


@pytest.fixture
def ports() -> tuple[int, int]:
    """A device port and a client port on 127.0.0.1, both free now."""
    device = next(_PORTS)
    for port in (device, device + 1):
        with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as probe:
            try:
                probe.bind((HOST, port))
            except OSError as exc:
                pytest.skip(f"cannot bind UDP {HOST}:{port}: {exc}")
    return device, device + 1


@pytest.fixture
def bus() -> InProcessBus:
    """The bus the gateway publishes its writes on."""
    return InProcessBus()


MakeGateway = Callable[[str], Awaitable[BacnetVrfGateway]]


@pytest_asyncio.fixture
async def make_gateway(ports: tuple[int, int], bus: InProcessBus) -> AsyncIterator[MakeGateway]:
    """``await make_gateway(vendor)``: a started gateway on 127.0.0.1, stopped after the test.

    Its writes are stamped with a simulation clock at ``T0``.
    """
    started: list[BacnetVrfGateway] = []

    async def make(vendor: str) -> BacnetVrfGateway:
        profile = load_vrf_profiles()[vendor]
        clock = SimClock(T0, 900.0)
        gateway = BacnetVrfGateway(profile, bus, clock, address=HOST, port=ports[0])
        await gateway.start()
        started.append(gateway)
        return gateway

    yield make
    for gateway in started:
        await gateway.stop()


@pytest_asyncio.fixture
async def client(ports: tuple[int, int]) -> AsyncIterator[NormalApplication]:
    """A BACnet client like the agent's, on the client port."""
    device = DeviceObject(
        objectIdentifier=("device", 1999), objectName="test-client", vendorIdentifier=999
    )
    app = NormalApplication(device, IPv4Address(f"{HOST}:{ports[1]}"))
    await asyncio.gather(*app.normal.server._transport_tasks)
    yield app
    app.close()
