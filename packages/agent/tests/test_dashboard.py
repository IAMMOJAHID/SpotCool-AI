import asyncio
import json
import time
from typing import Any

import pytest
from websockets.exceptions import ConnectionClosedError

from spotcool_agent.domain.events import Explanation
from spotcool_agent.transports.dashboard import (
    MAX_MESSAGE_BYTES,
    DashboardPublisher,
    agent_url,
)
from spotcool_contracts.events import EventType

URL = agent_url("127.0.0.1", 8090)


class FakeSocket:
    """Records what is sent and when; its incoming side stays silent until closed."""

    def __init__(self, fail_after: int | None = None) -> None:
        self.sent: list[tuple[float, dict[str, Any]]] = []
        self.fail_after = fail_after
        self.closed = asyncio.Event()

    async def send(self, text: str) -> None:
        if self.fail_after is not None and len(self.sent) >= self.fail_after:
            raise ConnectionClosedError(None, None)
        self.sent.append((time.perf_counter(), json.loads(text)))

    async def close(self) -> None:
        self.closed.set()

    def __aiter__(self) -> "FakeSocket":
        return self

    async def __anext__(self) -> str:
        await self.closed.wait()
        raise StopAsyncIteration


class Dialler:
    """A ``connect`` that refuses the first ``refusals`` dials, then hands out sockets."""

    def __init__(self, *sockets: FakeSocket, refusals: int = 0) -> None:
        self.sockets = list(sockets)
        self.refusals = refusals
        self.urls: list[str] = []

    async def __call__(self, url: str) -> FakeSocket:
        self.urls.append(url)
        if self.refusals:
            self.refusals -= 1
            raise ConnectionRefusedError("refused")
        return self.sockets.pop(0)


def say(publisher: DashboardPublisher, text: str) -> None:
    publisher.publish(EventType.EXPLANATION, Explanation(text=text), 17_218_800.0)


async def until(condition: Any, timeout_s: float = 5.0) -> None:
    deadline = time.perf_counter() + timeout_s
    while not condition():
        assert time.perf_counter() < deadline, "timed out"
        await asyncio.sleep(0.01)


@pytest.mark.asyncio
async def test_envelopes_are_the_agents_and_at_most_19_leave_in_any_second() -> None:
    socket = FakeSocket()
    publisher = DashboardPublisher(URL, connect=Dialler(socket))
    publisher.start()
    for k in range(25):
        say(publisher, f"step {k}")
    await until(lambda: len(socket.sent) == 25)
    await publisher.stop()
    times = [t for t, _ in socket.sent]
    for k, t in enumerate(times):  # every window of a second from a send
        assert sum(t <= u < t + 1.0 for u in times[k:]) <= 19
    first = socket.sent[0][1]
    assert (first["source"], first["type"], first["data"]) == (
        "agent",
        "explanation",
        {"text": "step 0"},
    )
    assert first["sim_time_s"] == 17_218_800.0


@pytest.mark.asyncio
async def test_an_envelope_over_64_kb_is_dropped_and_never_sent() -> None:
    socket = FakeSocket()
    publisher = DashboardPublisher(URL, connect=Dialler(socket))
    publisher.start()
    say(publisher, "x" * MAX_MESSAGE_BYTES)
    say(publisher, "small")
    await until(lambda: len(socket.sent) == 1)
    await publisher.stop()
    assert [data["data"]["text"] for _, data in socket.sent] == ["small"]
    assert publisher.dropped == 1


@pytest.mark.asyncio
async def test_while_the_world_is_away_the_oldest_are_dropped_and_publish_never_blocks() -> None:
    socket = FakeSocket()
    publisher = DashboardPublisher(URL, connect=Dialler(socket, refusals=2), queue_size=3)
    began = time.perf_counter()
    for k in range(5):
        say(publisher, f"step {k}")
    assert time.perf_counter() - began < 0.1
    assert (publisher.pending, publisher.dropped) == (3, 2)
    publisher.start()  # refused twice, then connected, with backoff
    await until(lambda: len(socket.sent) == 3)
    await publisher.stop()
    assert [data["data"]["text"] for _, data in socket.sent] == ["step 2", "step 3", "step 4"]
    assert publisher.dialled == [URL] * 3


@pytest.mark.asyncio
async def test_a_failed_send_is_dropped_never_resent_and_the_socket_dialled_again() -> None:
    broken, fresh = FakeSocket(fail_after=1), FakeSocket()
    publisher = DashboardPublisher(URL, connect=Dialler(broken, fresh))
    publisher.start()
    for k in range(3):
        say(publisher, f"step {k}")
    await until(lambda: len(fresh.sent) == 1)
    await publisher.stop()
    sent = [data["data"]["text"] for _, data in broken.sent + fresh.sent]
    assert sent == ["step 0", "step 2"]  # step 1 went down with the socket
    assert publisher.dropped == 1
    assert broken.closed.is_set() and fresh.closed.is_set()


def test_it_dials_only_the_worlds_publish_only_socket() -> None:
    assert URL == "ws://127.0.0.1:8090/ws/agent"
