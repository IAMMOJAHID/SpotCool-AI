import asyncio
import json
import socket
import time
from collections.abc import AsyncGenerator, Awaitable, Callable
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Any

import httpx
import pytest
from fastapi import FastAPI
from websockets.asyncio.client import ClientConnection, connect
from websockets.exceptions import ConnectionClosed

from spotcool_contracts.events import EventEnvelope, EventSource, EventType, TickData
from spotcool_world.bus import InProcessBus, Subscription
from spotcool_world.frames import Frame, TickFrame
from spotcool_world.processors import FrameDirection, FrameProcessor
from spotcool_world.runner import WorkerRunner
from spotcool_world.serializers import EVENTS_FILE, EventRecord, read_records
from spotcool_world.transports.websocket import dashboard
from spotcool_world.transports.websocket.dashboard import (
    MAX_AGENT_MESSAGE_BYTES,
    MAX_AGENT_MESSAGES_PER_S,
    create_dashboard_app,
)
from spotcool_world.workers.http_server import STOP_TIMEOUT_S, HttpServerWorker

HOST = "127.0.0.1"
Taken = Callable[[Subscription], Awaitable[list[Any]]]


def free_port() -> int:
    with socket.socket() as probe:
        probe.bind((HOST, 0))
        return probe.getsockname()[1]


async def until_up(port: int) -> None:
    for _ in range(400):
        try:
            _, writer = await asyncio.open_connection(HOST, port)
        except OSError:
            await asyncio.sleep(0.025)
        else:
            writer.close()
            await writer.wait_closed()
            return
    raise AssertionError("the server did not come up")


@asynccontextmanager
async def serving(app: FastAPI) -> AsyncGenerator[tuple[HttpServerWorker, str], None]:
    """The app served on a free port; yields the worker and the ws:// base URL."""
    port = free_port()
    worker = HttpServerWorker(app, HOST, port)
    running = asyncio.ensure_future(WorkerRunner(handle_sigint=False).run(worker))
    try:
        await until_up(port)
        yield worker, f"ws://{HOST}:{port}"
    finally:
        await worker.stop("test")
        await asyncio.wait_for(running, 5)


def world_event(index: int) -> EventEnvelope:
    tick = TickData(index=index, step_s=900.0, clock="07:00", day_of_year=200)
    return EventEnvelope.build(EventType.TICK, EventSource.WORLD, tick, 17_218_800.0 + index)


def agent_message(**changes: Any) -> dict[str, Any]:
    message = {
        "v": 1,
        "type": "perception",
        "source": "agent",
        "t_ms": 1_793_000_000_000,
        "sim_time_s": 17_218_800.0,
        "data": {"zone": "sou", "people": 5},
    }
    return {**message, **changes}


async def silent(websocket: ClientConnection, timeout_s: float = 0.3) -> bool:
    """Whether nothing arrives on the socket within the timeout."""
    try:
        await asyncio.wait_for(websocket.recv(), timeout_s)
    except TimeoutError:
        return True
    return False


async def received(websocket: ClientConnection, count: int) -> list[EventEnvelope]:
    return [
        EventEnvelope.model_validate_json(await asyncio.wait_for(websocket.recv(), 5))
        for _ in range(count)
    ]


@pytest.mark.asyncio
async def test_every_client_gets_every_event_and_what_it_sends_is_ignored(tmp_path: Path) -> None:
    bus = InProcessBus()
    async with (
        serving(create_dashboard_app(bus, tmp_path)) as (_, base),
        connect(f"{base}/ws/events") as first,
        connect(f"{base}/ws/events") as second,
    ):
        assert bus.subscriber_count("events") == 2  # subscribed before the handshake ended
        await first.send("hello")
        await first.send(b"\x00\x01")
        events = [world_event(i) for i in range(3)]
        for event in events:
            await bus.publish("events", event)
        assert await received(first, 3) == events
        assert await received(second, 3) == events
        assert await silent(first)  # no reply to what it sent
    for _ in range(100):  # each subscription closes with its connection
        if bus.subscriber_count("events") == 0:
            break
        await asyncio.sleep(0.01)
    assert bus.subscriber_count("events") == 0


@pytest.mark.asyncio
async def test_a_slow_client_drops_its_oldest_events_and_never_holds_up_the_world(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(dashboard, "CLIENT_QUEUE_SIZE", 5)
    bus = InProcessBus()
    async with (
        serving(create_dashboard_app(bus, tmp_path)) as (_, base),
        connect(f"{base}/ws/events") as client,
    ):
        events = [world_event(i) for i in range(50)]
        began = time.perf_counter()
        for event in events:  # faster than the server can send: nothing awaits in between
            await bus.publish("events", event)
        assert time.perf_counter() - began < 0.5  # publishing never waits for the browser
        assert await received(client, 5) == events[-5:]  # the newest five; the oldest dropped
        assert await silent(client)


@pytest.mark.asyncio
async def test_the_agent_socket_publishes_a_valid_agent_event(tmp_path: Path, taken: Taken) -> None:
    bus = InProcessBus()
    watch = bus.subscribe("events")
    async with (
        serving(create_dashboard_app(bus, tmp_path)) as (_, base),
        connect(f"{base}/ws/agent") as agent,
    ):
        for kind in ("perception", "decision", "explanation", "chat"):
            message = agent_message(type=kind)
            await agent.send(json.dumps(message))
            published = await asyncio.wait_for(anext(watch), 5)
            assert published == EventEnvelope.model_validate(message)
        assert await silent(agent)  # no reply to a valid message
    assert await taken(watch) == []


BIG = {"blob": "x" * MAX_AGENT_MESSAGE_BYTES}
REFUSED: list[tuple[str | bytes, str]] = [
    (json.dumps(agent_message(type="tick")), "not an agent type"),  # a world type
    (json.dumps(agent_message(type="scenario_state")), "not an agent type"),  # ground truth
    (json.dumps(agent_message(source="world")), "source must be 'agent'"),
    ("{not json", "not an EventEnvelope"),
    (json.dumps({"type": "decision", "source": "agent"}), "not an EventEnvelope"),
    (json.dumps(agent_message(extra=1)), "not an EventEnvelope"),
    (json.dumps(agent_message(data=BIG)), f"larger than {MAX_AGENT_MESSAGE_BYTES} bytes"),
    (json.dumps(agent_message()).encode(), "JSON text message"),  # binary
]


@pytest.mark.asyncio
async def test_the_agent_socket_refuses_invalid_messages_and_carries_on(
    tmp_path: Path, taken: Taken
) -> None:
    bus = InProcessBus()
    watch = bus.subscribe("events")
    async with (
        serving(create_dashboard_app(bus, tmp_path)) as (_, base),
        connect(f"{base}/ws/agent", max_size=None) as agent,
    ):
        for message, reason in REFUSED:
            await agent.send(message)
            reply = json.loads(await asyncio.wait_for(agent.recv(), 5))
            assert list(reply) == ["error"] and reason in reply["error"], message[:80]
        valid = agent_message()
        await agent.send(json.dumps(valid))
        assert await asyncio.wait_for(anext(watch), 5) == EventEnvelope.model_validate(valid)
    assert await taken(watch) == []  # only the valid message was published


@pytest.mark.asyncio
async def test_the_agent_socket_takes_at_most_twenty_messages_a_second(
    tmp_path: Path, taken: Taken
) -> None:
    bus = InProcessBus()
    watch = bus.subscribe("events")
    async with (
        serving(create_dashboard_app(bus, tmp_path)) as (_, base),
        connect(f"{base}/ws/agent") as agent,
    ):
        for _ in range(MAX_AGENT_MESSAGES_PER_S + 5):
            await agent.send(json.dumps(agent_message()))
        replies = [json.loads(await asyncio.wait_for(agent.recv(), 5)) for _ in range(5)]
        assert all("messages a second" in reply["error"] for reply in replies)
        assert len(await taken(watch)) == MAX_AGENT_MESSAGES_PER_S
        await asyncio.sleep(1.1)  # a second later the window has room again
        await agent.send(json.dumps(agent_message()))
        await asyncio.wait_for(anext(watch), 5)


@pytest.mark.asyncio
async def test_the_agent_socket_never_sends_world_events(tmp_path: Path) -> None:
    bus = InProcessBus()
    async with (
        serving(create_dashboard_app(bus, tmp_path)) as (_, base),
        connect(f"{base}/ws/agent") as agent,
    ):
        for index in range(3):
            await bus.publish("events", world_event(index))
        await agent.send(json.dumps(agent_message()))  # not even its own events come back
        assert await silent(agent, 0.5)


@pytest.mark.asyncio
async def test_the_server_stops_promptly_with_clients_connected(tmp_path: Path) -> None:
    bus = InProcessBus()
    app = create_dashboard_app(bus, tmp_path)
    async with serving(app) as (worker, base):
        events = await connect(f"{base}/ws/events")
        agent = await connect(f"{base}/ws/agent")
        began = time.perf_counter()
        await worker.stop("test")
        assert time.perf_counter() - began < STOP_TIMEOUT_S  # the sockets ended themselves
        for client in (events, agent):
            with pytest.raises(ConnectionClosed):
                await asyncio.wait_for(client.recv(), 5)
            await client.close()
    assert bus.subscriber_count("events") == 0


async def get(app: FastAPI, path: str, **headers: str) -> httpx.Response:
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://world") as http:
        return await http.get(path, headers=headers)


@pytest.mark.asyncio
async def test_runs_lists_the_recorded_runs_and_serves_their_records(tmp_path: Path) -> None:
    runs = tmp_path / "runs"
    (runs / "run-2").mkdir(parents=True)
    (runs / "run-2" / EVENTS_FILE).write_bytes(b'{"kind": "raw"}\n')
    (runs / "run-1").mkdir()
    (runs / "run-1" / EVENTS_FILE).write_bytes(b"")
    (runs / "no-record").mkdir()
    (runs / "not a run").mkdir()
    (runs / "not a run" / EVENTS_FILE).write_bytes(b"")
    app = create_dashboard_app(InProcessBus(), runs)
    assert (await get(app, "/runs")).json() == {"runs": ["run-1", "run-2"]}
    response = await get(app, "/runs/run-2/events.jsonl")
    assert response.status_code == 200 and response.content == b'{"kind": "raw"}\n'
    assert response.headers["content-type"] == "application/x-ndjson"
    assert (await get(app, "/runs/run-9/events.jsonl")).status_code == 404
    empty = create_dashboard_app(InProcessBus(), tmp_path / "missing")
    assert (await get(empty, "/runs")).json() == {"runs": []}


@pytest.mark.parametrize(
    "path",
    [
        "/runs/%2e%2e/events.jsonl",
        "/runs/..%2Fsecret/events.jsonl",
        "/runs/a%5C..%5C..%5Csecret/events.jsonl",
        "/runs/run.1/events.jsonl",
        "/runs/run%001/events.jsonl",
        "/runs/" + "a" * 65 + "/events.jsonl",
    ],
)
@pytest.mark.asyncio
async def test_a_run_path_never_leaves_the_run_dir(path: str, tmp_path: Path) -> None:
    (tmp_path / "secret").mkdir()
    (tmp_path / "secret" / EVENTS_FILE).write_text("SECRET", encoding="utf-8")
    (tmp_path / "runs").mkdir()
    response = await get(create_dashboard_app(InProcessBus(), tmp_path / "runs"), path)
    assert response.status_code in (404, 422) and b"SECRET" not in response.content


@pytest.mark.asyncio
async def test_the_built_dashboard_is_served_at_the_root_when_it_exists(tmp_path: Path) -> None:
    dist = tmp_path / "dist"
    dist.mkdir()
    (dist / "index.html").write_text("<!doctype html><title>dash</title>", encoding="utf-8")
    app = create_dashboard_app(InProcessBus(), tmp_path / "runs", dist)
    page = await get(app, "/")
    assert page.status_code == 200 and "<title>dash</title>" in page.text
    assert (await get(app, "/runs")).json() == {"runs": []}  # the routes come first
    assert (await get(app, "/healthz")).json() == {"status": "ok"}
    bare = create_dashboard_app(InProcessBus(), tmp_path / "runs", tmp_path / "nothing")
    assert (await get(bare, "/")).status_code == 404


@pytest.mark.asyncio
async def test_local_pages_may_read_the_routes(tmp_path: Path) -> None:
    app = create_dashboard_app(InProcessBus(), tmp_path)
    vite = await get(app, "/healthz", origin="http://localhost:5173")
    assert vite.headers["access-control-allow-origin"] == "http://localhost:5173"
    other = await get(app, "/healthz", origin="http://evil.example")
    assert "access-control-allow-origin" not in other.headers


class GateAtTick(FrameProcessor):
    """Holds one tick until the test opens the gate."""

    def __init__(self, index: int, reached: asyncio.Event, gate: asyncio.Event) -> None:
        super().__init__()
        self._index, self._reached, self._gate = index, reached, gate

    async def process_frame(self, frame: Frame, direction: FrameDirection) -> None:
        await super().process_frame(frame, direction)
        if isinstance(frame, TickFrame) and frame.index == self._index:
            self._reached.set()
            await self._gate.wait()
        await self.push_frame(frame, direction)


@pytest.mark.asyncio
async def test_an_agent_event_sent_during_a_run_lands_in_its_record(
    tmp_path: Path, record_world: Callable[..., Awaitable[Any]]
) -> None:
    bus = InProcessBus()
    reached, gate = asyncio.Event(), asyncio.Event()
    async with serving(create_dashboard_app(bus, tmp_path / "runs")) as (_, base):
        recording = asyncio.ensure_future(
            record_world(steps=3, bus=bus, extra=[GateAtTick(1, reached, gate)])
        )
        await asyncio.wait_for(reached.wait(), 10)
        watch = bus.subscribe("events")
        async with connect(f"{base}/ws/agent") as agent:
            await agent.send(json.dumps(agent_message(type="decision")))
            published = await asyncio.wait_for(anext(watch), 5)
        watch.close()
        gate.set()
        run = await asyncio.wait_for(recording, 30)
    records = read_records(run.folder / EVENTS_FILE)
    assert published in [r.envelope for r in records if isinstance(r, EventRecord)]
