"""The dashboard server (the ``dashboard`` extra): the event stream, the agent's events, the runs.

``create_dashboard_app(bus, run_dir, dist_dir)`` builds the FastAPI app. WP11 serves it with
``HttpServerWorker`` on ``WorldSettings.http_host:dashboard_port`` (8090).

- ``/ws/events``: every envelope on the events topic, one JSON text message each, for the
  dashboards. A client that joins mid-run first gets the run so far (``EventFeed``), then the
  live events, each once and in order. Each client has its own queue, which drops its oldest
  messages when full, so a slow browser never holds up the world. What a client sends is
  ignored. ``app.state.viewers`` counts the clients connected (a replay can wait for the first
  one); ``app.state.feed`` is the ``EventFeed``.
- ``/ws/agent``: publish-only, for SpotCool's agent. Each message must be one ``EventEnvelope``
  as JSON text, with ``source: "agent"`` and an agent type (``AGENT_TYPES``), at most
  ``MAX_AGENT_MESSAGE_BYTES`` long and at most ``MAX_AGENT_MESSAGES_PER_S`` a second on the
  connection, and carry no image: no string starting ``data:`` and none longer than
  ``MAX_AGENT_STRING`` (perception sends counts, boxes and polygons; the recorder keeps what is
  sent). A valid one goes on the events topic; an invalid one is dropped and answered with
  ``{"error": "..."}``. A browser page from anywhere but this machine is refused (its
  ``Origin``); programs send no ``Origin``. This socket never sends world events, so ground
  truth cannot reach the agent through it.
- ``GET /runs``: the recorded runs; ``GET /runs/{run_id}/events.jsonl``: one run's record.
  A run id must match ``RUN_ID_PATTERN`` (422 otherwise), so it never leaves ``run_dir``.
- ``GET /healthz``; ``/`` serves the built dashboard (``apps/dashboard/dist``) if it exists.

Pages on ``http://127.0.0.1:<port>`` and ``http://localhost:<port>`` (the Vite dev server) may
read the GET routes. A failing socket ends only its own connection, never the world's run.
"""

import asyncio
import re
import time
from collections import OrderedDict, deque
from collections.abc import AsyncIterator, Hashable
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Annotated, Any

from fastapi import FastAPI, HTTPException, Response, WebSocket
from fastapi import Path as PathParam
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles
from loguru import logger
from pydantic import ValidationError
from starlette.websockets import WebSocketDisconnect, WebSocketDisconnected

from spotcool_contracts.events import EventEnvelope, EventSource, EventType
from spotcool_world import __version__
from spotcool_world.bus import Bus
from spotcool_world.processors.event_emitter import EVENTS_TOPIC
from spotcool_world.serializers.jsonl import EVENTS_FILE, RUN_ID_PATTERN
from spotcool_world.transports.http.server import LOCAL_ORIGINS

CLIENT_QUEUE_SIZE = 1000
"""Events a ``/ws/events`` client may fall behind by before its oldest are dropped."""
HISTORY_LIMIT = 50_000
"""Events of the current run kept for late viewers (a paced BOPTEST day is about 2,000)."""
FEED_QUEUE_SIZE = 100_000
"""Events published before the feed has taken them: large, so a burst loses none."""
MAX_AGENT_MESSAGE_BYTES = 64 * 1024
"""The largest message ``/ws/agent`` accepts, in UTF-8 bytes."""
MAX_AGENT_MESSAGES_PER_S = 20
"""The most messages ``/ws/agent`` takes from one connection in any second."""
MAX_AGENT_STRING = 8192
"""The longest string an agent event may hold, in characters: longer ones are media."""
AGENT_TYPES = frozenset(
    {EventType.PERCEPTION, EventType.DECISION, EventType.EXPLANATION, EventType.CHAT}
)
"""The event types the agent may publish."""
DASHBOARD_DIST = Path("apps/dashboard/dist")
"""The built dashboard, relative to the repo root the world runs from."""

RunId = Annotated[str, PathParam(pattern=RUN_ID_PATTERN)]


def create_dashboard_app(bus: Bus, run_dir: Path, dist_dir: Path | None = None) -> FastAPI:
    """The dashboard server's app; see the module docstring for its routes.

    Args:
        bus: The world's bus, the one ``BusPublisher`` and the recorder use.
        run_dir: Where the recorded runs are (``WorldSettings.run_dir``).
        dist_dir: The built dashboard to serve at ``/``; ``None`` means ``DASHBOARD_DIST``.
            Nothing is served there if the directory does not exist when the app is made.
    """
    feed = EventFeed(bus)  # subscribes now: a run's first events come before the server is up

    @asynccontextmanager
    async def lifespan(_: FastAPI) -> AsyncIterator[None]:
        feed.start()
        try:
            yield
        finally:
            await feed.stop()

    app = FastAPI(title="SpotCool World dashboard", version=__version__, lifespan=lifespan)
    app.add_middleware(CORSMiddleware, allow_origin_regex=LOCAL_ORIGINS, allow_methods=["GET"])
    app.state.viewers = 0  # /ws/events clients connected now
    app.state.feed = feed

    @app.get("/healthz")
    async def healthz() -> dict[str, str]:
        """Answers 200 while the server is up."""
        return {"status": "ok"}

    @app.get("/runs")
    async def runs() -> dict[str, list[str]]:
        """The ids of the recorded runs, sorted."""
        return {"runs": await asyncio.to_thread(_recorded_runs, run_dir)}

    @app.get("/runs/{run_id}/events.jsonl", response_class=Response)
    async def run_events(run_id: RunId) -> Response:
        """A run's record as JSON lines; a run still recording gives what is written so far."""
        try:
            body = await asyncio.to_thread((run_dir / run_id / EVENTS_FILE).read_bytes)
        except OSError:
            raise HTTPException(404, f"no recorded run {run_id!r}") from None
        return Response(body, media_type="application/x-ndjson")

    @app.websocket("/ws/events")
    async def events(websocket: WebSocket) -> None:
        """The run so far, then every event on the bus, as JSON text; what is sent is ignored."""
        # Join first: the client gets the run's events up to its handshake and all after it.
        history, viewer = feed.join()
        viewing = False
        try:
            await websocket.accept()
            app.state.viewers += 1
            viewing = True
            async with asyncio.TaskGroup() as group:
                group.create_task(_send_events(websocket, history, viewer))
                group.create_task(_ignore_inbound(websocket, viewer))
        except* (WebSocketDisconnect, WebSocketDisconnected):
            pass  # the client went away mid-send
        finally:
            feed.leave(viewer)
            if viewing:
                app.state.viewers -= 1

    @app.websocket("/ws/agent")
    async def agent(websocket: WebSocket) -> None:
        """Publishes each valid agent event on the bus; answers an invalid one with an error."""
        origin = websocket.headers.get("origin")
        if origin is not None and not re.fullmatch(LOCAL_ORIGINS, origin):
            await websocket.close(code=1008)  # before the handshake: the page gets a 403
            return
        await websocket.accept()
        admitted: deque[float] = deque()  # when the messages of the last second arrived
        try:
            while (message := await websocket.receive())["type"] != "websocket.disconnect":
                checked = _agent_event(message.get("text"), admitted)
                if isinstance(checked, EventEnvelope):
                    await bus.publish(EVENTS_TOPIC, checked)
                else:
                    await websocket.send_json({"error": checked})
        except (WebSocketDisconnect, WebSocketDisconnected):
            pass  # the agent went away mid-reply

    dist = DASHBOARD_DIST if dist_dir is None else dist_dir
    if dist.is_dir():
        app.mount("/", StaticFiles(directory=dist, html=True), name="dashboard")
    return app


def _recorded_runs(run_dir: Path) -> list[str]:
    """The run directories in ``run_dir`` that hold a record."""
    if not run_dir.is_dir():
        return []
    return sorted(
        path.name
        for path in run_dir.iterdir()
        if re.fullmatch(RUN_ID_PATTERN, path.name) and (path / EVENTS_FILE).is_file()
    )


class Viewer:
    """One ``/ws/events`` client's queue of events as JSON text; when full, it drops its oldest.

    Args:
        maxsize: How many events the client may fall behind by.
    """

    def __init__(self, maxsize: int) -> None:
        self._queue: asyncio.Queue[str | None] = asyncio.Queue(maxsize=maxsize)
        self._closed = False

    def offer(self, text: str) -> None:
        """Queue an event; the oldest goes when the queue is full."""
        if self._closed:
            return
        if self._queue.full():
            self._queue.get_nowait()
        self._queue.put_nowait(text)

    async def next(self) -> str | None:
        """The next event, waiting for one; ``None`` once the viewer is closed."""
        return None if self._closed else await self._queue.get()

    def close(self) -> None:
        """End a waiting or later ``next()``."""
        if self._closed:
            return
        self._closed = True
        if self._queue.full():
            self._queue.get_nowait()  # room for the wake-up; the client is leaving
        self._queue.put_nowait(None)


class EventFeed:
    """The events topic for the dashboards: the current run's history, then the live events.

    It subscribes when it is made, before the run starts, so the history has the run's first
    events even when the server comes up after them. ``start()`` (the app's lifespan) runs a
    task that takes each event in order: it keeps it in the history and hands it to every
    viewer, with no wait in between. ``join()`` gives a new viewer the history and adds it to
    the viewers in one step, so a viewer gets every event of the run once and in order,
    whatever is published meanwhile.

    The history keeps every event except ``vrf_state``, of which it keeps the latest, and
    ``metrics``, the latest of each metric (name and labels): both arrive all run long. A
    ``run_status`` of another run clears it first. It holds at most ``HISTORY_LIMIT`` events:
    the oldest go first, with one warning per run.

    Args:
        bus: The world's bus.
    """

    def __init__(self, bus: Bus) -> None:
        self._subscription = bus.subscribe(EVENTS_TOPIC, maxsize=FEED_QUEUE_SIZE)
        self._history: OrderedDict[Hashable, str] = OrderedDict()
        self._viewers: set[Viewer] = set()
        self._run_id: str | None = None
        self._taken = 0  # events kept in full so far: each one's history key
        self._warned = False
        self._task: asyncio.Task[None] | None = None

    def history(self) -> list[str]:
        """The current run's events kept for late viewers, oldest first, as JSON text."""
        return list(self._history.values())

    def join(self) -> tuple[list[str], Viewer]:
        """A new viewer: the history so far, and its queue of every event after that."""
        viewer = Viewer(CLIENT_QUEUE_SIZE)
        self._viewers.add(viewer)
        return self.history(), viewer

    def leave(self, viewer: Viewer) -> None:
        """Stop handing events to ``viewer`` and end its ``next()``."""
        self._viewers.discard(viewer)
        viewer.close()

    def start(self) -> None:
        """Start taking events from the bus; a second call does nothing."""
        if self._task is None:
            self._task = asyncio.create_task(self._take_all(), name="dashboard-feed")

    async def stop(self) -> None:
        """Unsubscribe, wait for the task to take what was left, and end every viewer."""
        self._subscription.close()
        if self._task is not None:
            await self._task
        for viewer in tuple(self._viewers):
            self.leave(viewer)

    async def _take_all(self) -> None:
        async for message in self._subscription:
            if isinstance(message, EventEnvelope):
                self._take(message)

    def _take(self, envelope: EventEnvelope) -> None:
        """Keep an event in the history and hand it to every viewer."""
        if envelope.type is EventType.RUN_STATUS:
            run_id = envelope.data.get("run_id")
            if self._run_id is not None and run_id != self._run_id:
                self._history.clear()
                self._warned = False
            if isinstance(run_id, str):
                self._run_id = run_id
        text = envelope.model_dump_json()
        key = self._key(envelope)
        self._history[key] = text
        self._history.move_to_end(key)  # a latest-only event takes the newest place
        if len(self._history) > HISTORY_LIMIT:
            self._history.popitem(last=False)
            if not self._warned:
                self._warned = True
                logger.warning(
                    f"dashboard: the run's history is full ({HISTORY_LIMIT} events); "
                    "late viewers miss its oldest events from now on"
                )
        for viewer in tuple(self._viewers):
            viewer.offer(text)

    def _key(self, envelope: EventEnvelope) -> Hashable:
        """The event's place in the history: its own, or the one its latest-only kind shares."""
        if envelope.type is EventType.VRF_STATE:
            return EventType.VRF_STATE
        if envelope.type is EventType.METRICS:
            labels = envelope.data.get("labels") or {}
            return EventType.METRICS, envelope.data.get("name"), tuple(sorted(labels.items()))
        self._taken += 1
        return self._taken


async def _send_events(websocket: WebSocket, history: list[str], viewer: Viewer) -> None:
    """Send the history, then every event until the viewer closes."""
    for text in history:
        await websocket.send_text(text)
    while (text := await viewer.next()) is not None:
        await websocket.send_text(text)


async def _ignore_inbound(websocket: WebSocket, viewer: Viewer) -> None:
    """Read and drop what the client sends; at its disconnect, end ``_send_events``."""
    try:
        while (await websocket.receive())["type"] != "websocket.disconnect":
            pass
    finally:
        viewer.close()


def _agent_event(text: str | None, admitted: deque[float]) -> EventEnvelope | str:
    """An agent message as the envelope to publish, or why it is refused.

    Args:
        text: The message's text; ``None`` for a binary message.
        admitted: The arrival times of the messages taken in the last second; updated.
    """
    now = time.perf_counter()
    while admitted and now - admitted[0] >= 1.0:
        admitted.popleft()
    if len(admitted) >= MAX_AGENT_MESSAGES_PER_S:
        return f"more than {MAX_AGENT_MESSAGES_PER_S} messages a second; message dropped"
    admitted.append(now)
    if text is None:
        return "send each event as one JSON text message"
    if len(text.encode("utf-8")) > MAX_AGENT_MESSAGE_BYTES:
        return f"message larger than {MAX_AGENT_MESSAGE_BYTES} bytes"
    try:
        envelope = EventEnvelope.model_validate_json(text)
    except ValidationError as exc:
        problems = exc.errors(include_url=False, include_input=False)[:3]
        listed = "; ".join(
            f"{'.'.join(map(str, p['loc'])) or 'message'}: {p['msg']}" for p in problems
        )
        return f"not an EventEnvelope: {listed}"
    if envelope.source is not EventSource.AGENT:
        return f"source must be 'agent', not {envelope.source.value!r}"
    if envelope.type not in AGENT_TYPES:
        allowed = ", ".join(sorted(kind.value for kind in AGENT_TYPES))
        return f"type {envelope.type.value!r} is not an agent type; use one of {allowed}"
    if (media := _media(envelope.data)) is not None:
        return f"data holds {media}; send counts, boxes and polygons, never images (Law 6)"
    return envelope


def _media(data: Any) -> str | None:
    """What in ``data`` looks like an image or a recording, or ``None``.

    A string (key or value, at any depth) that starts with ``data:`` (a data URI) or is longer
    than ``MAX_AGENT_STRING`` characters.
    """
    pending = [data]
    while pending:  # a stack, not recursion: the nesting depth is the sender's choice
        value = pending.pop()
        if isinstance(value, str):
            if value.lstrip()[:5].lower() == "data:":
                return "a data: URI"
            if len(value) > MAX_AGENT_STRING:
                return f"a string longer than {MAX_AGENT_STRING} characters"
        elif isinstance(value, dict):
            pending += [*value.keys(), *value.values()]
        elif isinstance(value, list):
            pending += value
    return None
