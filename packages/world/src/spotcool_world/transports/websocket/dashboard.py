"""The dashboard server (the ``dashboard`` extra): the event stream, the agent's events, the runs.

``create_dashboard_app(bus, run_dir, dist_dir)`` builds the FastAPI app. WP11 serves it with
``HttpServerWorker`` on ``WorldSettings.http_host:dashboard_port`` (8090).

- ``/ws/events``: every envelope on the events topic, one JSON text message each, for the
  dashboards. Each client gets its own subscription, whose queue drops its oldest messages
  when full, so a slow browser never holds up the world. What a client sends is ignored.
- ``/ws/agent``: publish-only, for Person A's agent. Each message must be one ``EventEnvelope``
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
from collections import deque
from pathlib import Path
from typing import Annotated, Any

from fastapi import FastAPI, HTTPException, Response, WebSocket
from fastapi import Path as PathParam
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles
from pydantic import ValidationError
from starlette.websockets import WebSocketDisconnect, WebSocketDisconnected

from spotcool_contracts.events import EventEnvelope, EventSource, EventType
from spotcool_world import __version__
from spotcool_world.bus import Bus, Subscription
from spotcool_world.processors.event_emitter import EVENTS_TOPIC
from spotcool_world.serializers.jsonl import EVENTS_FILE, RUN_ID_PATTERN
from spotcool_world.transports.http.server import LOCAL_ORIGINS

CLIENT_QUEUE_SIZE = 1000
"""Events a ``/ws/events`` client may fall behind by before its oldest are dropped."""
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
    app = FastAPI(title="SpotCool World dashboard", version=__version__)
    app.add_middleware(CORSMiddleware, allow_origin_regex=LOCAL_ORIGINS, allow_methods=["GET"])

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
        """Every event on the bus as JSON text; what the client sends is ignored."""
        # Subscribe first: the client gets everything published after its handshake.
        subscription = bus.subscribe(EVENTS_TOPIC, maxsize=CLIENT_QUEUE_SIZE)
        try:
            await websocket.accept()
            async with asyncio.TaskGroup() as group:
                group.create_task(_send_events(websocket, subscription))
                group.create_task(_ignore_inbound(websocket, subscription))
        except* (WebSocketDisconnect, WebSocketDisconnected):
            pass  # the client went away mid-send
        finally:
            subscription.close()

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


async def _send_events(websocket: WebSocket, subscription: Subscription) -> None:
    """Send every event until the subscription closes."""
    async for envelope in subscription:
        await websocket.send_text(envelope.model_dump_json())


async def _ignore_inbound(websocket: WebSocket, subscription: Subscription) -> None:
    """Read and drop what the client sends; at its disconnect, end ``_send_events``."""
    try:
        while (await websocket.receive())["type"] != "websocket.disconnect":
            pass
    finally:
        subscription.close()


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
