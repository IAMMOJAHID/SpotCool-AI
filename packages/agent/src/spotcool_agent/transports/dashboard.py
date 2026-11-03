"""DashboardPublisher: the agent's events onto the world's ``/ws/agent``, without ever blocking.

``publish()`` wraps the data in an ``EventEnvelope`` (``source: "agent"``) and queues its JSON;
a background task sends the queue on one WebSocket. The world takes at most 64 KB and 20
messages per second per connection, so:

- an envelope over ``MAX_MESSAGE_BYTES`` is dropped at once (counted, logged once);
- a token bucket of ``BURST`` tokens, refilled at ``RATE_PER_S``, spaces the sends, so at most
  ``BURST + RATE_PER_S`` (19) leave in any second;
- the queue holds ``QUEUE_SIZE`` messages and drops its oldest when full;
- a dropped connection is dialled again with backoff, from ``RECONNECT_MIN_S`` doubling to
  ``RECONNECT_MAX_S``; a message whose send failed is dropped, never sent twice.

It dials only ``/ws/agent``, which never sends world events, so ground truth cannot reach the
agent this way; the world's answers (``{"error": ...}`` for a refused message) are logged.
"""

import asyncio
import contextlib
import time
from collections import deque
from collections.abc import Awaitable, Callable
from typing import Any

from loguru import logger
from pydantic import BaseModel
from websockets.asyncio.client import connect as ws_connect
from websockets.exceptions import WebSocketException

from spotcool_contracts.events import EventEnvelope, EventSource, EventType

AGENT_PATH = "/ws/agent"
"""The world's publish-only socket for the agent."""
MAX_MESSAGE_BYTES = 64 * 1024
"""The largest message the world takes, in UTF-8 bytes."""
BURST = 10
"""Messages the token bucket lets out at once."""
RATE_PER_S = 9.0
"""Tokens added per second: with ``BURST``, at most 19 messages leave in any second."""
QUEUE_SIZE = 500
"""Messages waiting to be sent before the oldest are dropped."""
RECONNECT_MIN_S = 0.5
RECONNECT_MAX_S = 10.0
OPEN_TIMEOUT_S = 5.0

Connect = Callable[[str], Awaitable[Any]]
"""Opens a WebSocket to a URL; tests pass a fake."""


def agent_url(host: str, port: int) -> str:
    """The world's ``/ws/agent`` URL on ``host:port``."""
    return f"ws://{host}:{port}{AGENT_PATH}"


class DashboardPublisher:
    """Publishes the agent's events to the dashboard (see the module docstring).

    Args:
        url: The ``/ws/agent`` URL (``agent_url``).
        connect: Opens the WebSocket; websockets' client by default.
        queue_size: Messages kept while the socket is down or the bucket empty.
        burst: The token bucket's size.
        rate_per_s: Its refill rate.
    """

    def __init__(
        self,
        url: str,
        *,
        connect: Connect | None = None,
        queue_size: int = QUEUE_SIZE,
        burst: int = BURST,
        rate_per_s: float = RATE_PER_S,
    ) -> None:
        self.url = url
        self._connect = connect or _open
        self._queue: deque[str] = deque(maxlen=queue_size)
        self._wake = asyncio.Event()
        self._idle = asyncio.Event()  # set while the queue is empty
        self._idle.set()
        self._burst = burst
        self._rate = rate_per_s
        self._tokens = float(burst)
        self._filled_at = time.perf_counter()
        self._task: asyncio.Task[None] | None = None
        self.sent = 0
        """Messages sent."""
        self.dropped = 0
        """Messages dropped: the queue was full, a send failed, or one was too large."""
        self.dialled: list[str] = []
        """Every URL dialled, in order (for the ground-truth check)."""

    def publish(self, kind: EventType, data: BaseModel, sim_time_s: float | None) -> None:
        """Queue one agent event; never blocks.

        Args:
            kind: The event type (``perception``, ``decision``, ``explanation``, ``chat``).
            data: Its data.
            sim_time_s: The simulation time it refers to.
        """
        text = EventEnvelope.build(kind, EventSource.AGENT, data, sim_time_s).model_dump_json()
        if len(text.encode("utf-8")) > MAX_MESSAGE_BYTES:
            self.dropped += 1
            logger.warning(f"dashboard: a {kind} event over {MAX_MESSAGE_BYTES} bytes is dropped")
            return
        if len(self._queue) == self._queue.maxlen:
            self.dropped += 1  # the deque drops its oldest
        self._queue.append(text)
        self._idle.clear()
        self._wake.set()

    @property
    def pending(self) -> int:
        """Messages waiting to be sent."""
        return len(self._queue)

    def start(self) -> None:
        """Start sending in a background task. Does nothing if it runs."""
        if self._task is None:
            self._task = asyncio.create_task(self._run(), name="dashboard-publisher")

    async def stop(self, grace_s: float = 1.0) -> None:
        """Send what is queued for up to ``grace_s`` wall seconds, then stop. Safe to call twice.

        Args:
            grace_s: How long the queue may take to empty.
        """
        task, self._task = self._task, None
        if task is None:
            return
        with contextlib.suppress(TimeoutError):
            await asyncio.wait_for(self._idle.wait(), grace_s)
        task.cancel()
        await asyncio.gather(task, return_exceptions=True)
        if self._queue:
            logger.info(f"dashboard: {len(self._queue)} events not sent at stop")

    async def _run(self) -> None:
        delay = RECONNECT_MIN_S
        while True:
            try:
                self.dialled.append(self.url)
                socket = await self._connect(self.url)
            except (OSError, TimeoutError, WebSocketException) as exc:
                logger.debug(f"dashboard: cannot reach {self.url}: {type(exc).__name__}")
                await asyncio.sleep(delay)
                delay = min(delay * 2, RECONNECT_MAX_S)
                continue
            delay = RECONNECT_MIN_S
            logger.info(f"dashboard: publishing on {self.url}")
            tasks = (
                asyncio.create_task(self._send_all(socket), name="dashboard-sender"),
                asyncio.create_task(self._read(socket), name="dashboard-reader"),
            )
            try:  # either direction ending means the connection is gone
                await asyncio.wait(tasks, return_when=asyncio.FIRST_COMPLETED)
            finally:
                for task in tasks:
                    task.cancel()
                await asyncio.gather(*tasks, return_exceptions=True)
                await _close(socket)
            logger.info(f"dashboard: lost {self.url}; dialling again")

    async def _send_all(self, socket: Any) -> None:
        """Send the queue as it fills, paced by the token bucket, until the socket fails."""
        while True:
            while not self._queue:
                self._wake.clear()
                await self._wake.wait()
            await self._take_token()
            text = self._queue.popleft()
            try:
                await socket.send(text)
            except BaseException:
                self.dropped += 1  # never sent twice: it may have reached the world
                raise
            self.sent += 1
            if not self._queue:
                self._idle.set()

    async def _take_token(self) -> None:
        while True:
            now = time.perf_counter()
            self._tokens = min(self._burst, self._tokens + (now - self._filled_at) * self._rate)
            self._filled_at = now
            if self._tokens >= 1:
                self._tokens -= 1
                return
            await asyncio.sleep((1 - self._tokens) / self._rate)

    async def _read(self, socket: Any) -> None:
        """Log what the world answers: an ``{"error": ...}`` for each refused message."""
        async for message in socket:
            logger.warning(f"dashboard: the world refused an event: {str(message)[:300]}")


async def _open(url: str) -> Any:
    return await ws_connect(url, open_timeout=OPEN_TIMEOUT_S, max_size=MAX_MESSAGE_BYTES)


async def _close(socket: Any) -> None:
    try:
        await socket.close()
    except (OSError, WebSocketException):
        pass
