import asyncio
import json
import socket
import time
from collections.abc import AsyncGenerator, Awaitable, Callable
from contextlib import asynccontextmanager
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import httpx
import pytest
from fastapi import FastAPI
from loguru import logger
from websockets.asyncio.client import ClientConnection, connect
from websockets.exceptions import ConnectionClosed, InvalidStatus
from websockets.typing import Origin

from spotcool_contracts.events import (
    EventEnvelope,
    EventSource,
    EventType,
    MetricsData,
    RunStatusData,
    TickData,
    VrfStateData,
)
from spotcool_world.bus import InProcessBus, Subscription
from spotcool_world.frames import Frame, TickFrame
from spotcool_world.processors import FrameDirection, FrameProcessor
from spotcool_world.runner import WorkerRunner
from spotcool_world.serializers import EVENTS_FILE, EventRecord, read_records
from spotcool_world.transports.websocket import dashboard
from spotcool_world.transports.websocket.dashboard import (
    MAX_AGENT_MESSAGE_BYTES,
    MAX_AGENT_MESSAGES_PER_S,
    MAX_AGENT_STRING,
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
        assert bus.subscriber_count("events") == 1  # the feed's, made with the app
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
LONG = f"longer than {MAX_AGENT_STRING} characters"
REFUSED: list[tuple[str | bytes, str]] = [
    (json.dumps(agent_message(type="tick")), "not an agent type"),  # a world type
    (json.dumps(agent_message(type="scenario_state")), "not an agent type"),  # ground truth
    (json.dumps(agent_message(source="world")), "source must be 'agent'"),
    ("{not json", "not an EventEnvelope"),
    (json.dumps({"type": "decision", "source": "agent"}), "not an EventEnvelope"),
    (json.dumps(agent_message(extra=1)), "not an EventEnvelope"),
    (json.dumps(agent_message(data=BIG)), f"larger than {MAX_AGENT_MESSAGE_BYTES} bytes"),
    (json.dumps(agent_message()).encode(), "JSON text message"),  # binary
    (json.dumps(agent_message(data={"mask": "data:image/png;base64,iVBORw0KGgo="})), "data:"),
    (json.dumps(agent_message(data={"crops": [{"x": "/9j/" + "A" * MAX_AGENT_STRING}]})), LONG),
    (json.dumps(agent_message(data={" DATA:image/jpeg;base64,/9j/": 1})), "data:"),  # a key
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
        shapes = {"boxes": [[1, 2, 3, 4]], "polygon": [[0.1, 0.2], [0.3, 0.4]], "count": 5}
        valid = agent_message(data={**shapes, "note": "y" * MAX_AGENT_STRING})  # at the limit
        await agent.send(json.dumps(valid))
        assert await asyncio.wait_for(anext(watch), 5) == EventEnvelope.model_validate(valid)
    assert await taken(watch) == []  # only the valid message was published


@pytest.mark.asyncio
async def test_the_agent_socket_takes_at_most_twenty_messages_a_second(
    tmp_path: Path, taken: Taken, monkeypatch: pytest.MonkeyPatch
) -> None:
    now = [100.0]  # the server's clock, frozen: the test sets when messages arrive
    monkeypatch.setattr(dashboard, "time", SimpleNamespace(perf_counter=lambda: now[0]))
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
        now[0] += 0.999  # still within a second of the first twenty
        await agent.send(json.dumps(agent_message()))
        assert "messages a second" in json.loads(await asyncio.wait_for(agent.recv(), 5))["error"]
        now[0] += 0.001  # a second after them: room again
        await agent.send(json.dumps(agent_message()))
        await asyncio.wait_for(anext(watch), 5)
    assert await taken(watch) == []


@pytest.mark.asyncio
async def test_the_agent_socket_refuses_browser_pages_from_elsewhere(
    tmp_path: Path, taken: Taken
) -> None:
    bus = InProcessBus()
    watch = bus.subscribe("events")
    async with serving(create_dashboard_app(bus, tmp_path)) as (_, base):
        for origin in ("http://evil.example", "http://localhost.evil.example:5173"):
            with pytest.raises(InvalidStatus) as refused:
                await connect(f"{base}/ws/agent", origin=Origin(origin))
            assert refused.value.response.status_code == 403
        for origin in ("http://localhost:5173", "http://127.0.0.1:8090"):
            async with connect(f"{base}/ws/agent", origin=Origin(origin)) as page:
                await page.send(json.dumps(agent_message()))
                await asyncio.wait_for(anext(watch), 5)
    assert await taken(watch) == []


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


def run_status(run_id: str) -> EventEnvelope:
    status = RunStatusData(run_id=run_id, status="running", plant="rc", scenario="hybrid_tuesday")
    return EventEnvelope.build(EventType.RUN_STATUS, EventSource.WORLD, status, 17_218_800.0)


def vrf_state(angle_deg: float) -> EventEnvelope:
    state = VrfStateData(
        profile="mitsubishi",
        power_on=True,
        mode="Cool",
        setpoint_c=24.0,
        fan="Auto",
        vane_v_pos="Pos3",
        vane_h_pos="Pos3",
        vane_v_angle_deg=angle_deg,
        vane_h_angle_deg=0.0,
        room_temp_c=25.0,
        power_kw=1.0,
    )
    return EventEnvelope.build(EventType.VRF_STATE, EventSource.WORLD, state, None)


def metric(run: str, value: float) -> EventEnvelope:
    sample = MetricsData(name="plant_step_latency", value=value, unit="s", labels={"run": run})
    return EventEnvelope.build(EventType.METRICS, EventSource.WORLD, sample, 17_218_800.0)


async def kept(app: FastAPI, count: int) -> list[EventEnvelope]:
    """The feed's history once it holds ``count`` events (the feed takes them in a task)."""
    for _ in range(500):
        history = app.state.feed.history()
        if len(history) == count:
            return [EventEnvelope.model_validate_json(text) for text in history]
        await asyncio.sleep(0.01)
    raise AssertionError(f"the history holds {len(app.state.feed.history())}, not {count}")


@pytest.mark.asyncio
async def test_a_viewer_who_joins_mid_run_gets_the_run_so_far_then_the_live_events(
    tmp_path: Path,
) -> None:
    bus = InProcessBus()
    app = create_dashboard_app(bus, tmp_path)
    before = [run_status("run-1"), *(world_event(i) for i in range(3))]
    for event in before[:2]:  # published before the server is up: the feed has subscribed
        await bus.publish("events", event)
    async with serving(app) as (_, base):
        for event in before[2:]:
            await bus.publish("events", event)
        await kept(app, len(before))
        async with connect(f"{base}/ws/events") as late:
            after = [world_event(i) for i in range(3, 6)]
            for event in after:
                await bus.publish("events", event)
            assert await received(late, len(before) + len(after)) == before + after
            assert await silent(late)


@pytest.mark.asyncio
async def test_viewers_who_join_while_events_are_published_get_each_event_once_in_order(
    tmp_path: Path,
) -> None:
    bus = InProcessBus()
    app = create_dashboard_app(bus, tmp_path)
    events = [world_event(i) for i in range(300)]
    async with serving(app) as (_, base):

        async def publish() -> None:
            for i, event in enumerate(events):
                await bus.publish("events", event)
                if i % 10 == 0:
                    await asyncio.sleep(0.002)  # joins land between the feed's takes

        publishing = asyncio.ensure_future(publish())
        viewers: list[ClientConnection] = []
        joined_at: list[int] = []
        while not publishing.done():
            joined_at.append(len(app.state.feed.history()))
            viewers.append(await connect(f"{base}/ws/events"))
        await publishing
        assert any(0 < n < len(events) for n in joined_at)  # some joined mid-stream
        for viewer in viewers:
            assert await received(viewer, len(events)) == events  # no gap, no duplicate
            assert await silent(viewer, 0.05)
            await viewer.close()


@pytest.mark.asyncio
async def test_a_new_run_clears_the_history(tmp_path: Path) -> None:
    bus = InProcessBus()
    app = create_dashboard_app(bus, tmp_path)
    async with serving(app) as (_, base):
        for event in [run_status("run-1"), world_event(0), world_event(1)]:
            await bus.publish("events", event)
        await kept(app, 3)
        await bus.publish("events", run_status("run-1"))  # the same run: kept, nothing cleared
        await kept(app, 4)
        second = [run_status("run-2"), world_event(0)]
        for event in second:
            await bus.publish("events", event)
        assert await kept(app, 2) == second
        async with connect(f"{base}/ws/events") as viewer:
            assert await received(viewer, 2) == second
            assert await silent(viewer)


@pytest.mark.asyncio
async def test_the_history_holds_at_most_its_limit_and_says_so_once(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(dashboard, "HISTORY_LIMIT", 5)
    warnings: list[str] = []
    sink = logger.add(lambda m: warnings.append(m.record["message"]), level="WARNING")
    try:
        bus = InProcessBus()
        app = create_dashboard_app(bus, tmp_path)
        events = [world_event(i) for i in range(9)]
        async with serving(app) as (_, base):
            for event in events:
                await bus.publish("events", event)
            assert await kept(app, 5) == events[-5:]  # the oldest went first
            async with connect(f"{base}/ws/events") as viewer:
                assert await received(viewer, 5) == events[-5:]
    finally:
        logger.remove(sink)
    assert len([w for w in warnings if "history is full" in w]) == 1


@pytest.mark.asyncio
async def test_the_history_keeps_only_the_latest_vrf_state_and_the_latest_of_each_metric(
    tmp_path: Path,
) -> None:
    bus = InProcessBus()
    app = create_dashboard_app(bus, tmp_path)
    stream = [
        vrf_state(0.0),
        metric("spotcool", 0.1),
        world_event(0),
        vrf_state(-15.0),
        metric("baseline", 0.2),
        metric("spotcool", 0.3),
        world_event(1),
        vrf_state(-30.0),
    ]
    async with serving(app) as (_, base):
        for event in stream:
            await bus.publish("events", event)
        expected = [stream[2], stream[4], stream[5], stream[6], stream[7]]  # in arrival order
        assert await kept(app, len(expected)) == expected
        async with connect(f"{base}/ws/events") as viewer:
            assert await received(viewer, len(expected)) == expected
