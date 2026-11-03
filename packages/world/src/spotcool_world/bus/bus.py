"""Publish/subscribe bus that workers use to talk to each other.

``Bus`` is the protocol; ``InProcessBus`` is the in-process implementation. A worker that moves
into its own process later only needs a networked ``Bus`` with the same two methods.
"""

import asyncio
from collections import defaultdict
from typing import Any, Protocol

from loguru import logger

_CLOSED = object()


class Subscription(Protocol):
    """Messages published on one topic, as an async iterator. Iteration ends after ``close()``."""

    @property
    def topic(self) -> str:
        """The subscribed topic."""
        ...

    def __aiter__(self) -> "Subscription": ...

    async def __anext__(self) -> Any: ...

    def close(self) -> None:
        """Stop receiving; a pending or later ``__anext__`` ends the iteration."""
        ...


class Bus(Protocol):
    """Topic-based publish/subscribe."""

    async def publish(self, topic: str, message: Any) -> None:
        """Deliver ``message`` to every current subscriber of ``topic``."""
        ...

    def subscribe(self, topic: str, *, maxsize: int = 1000) -> Subscription:
        """Start receiving messages published on ``topic`` from now on."""
        ...


class InProcessSubscription:
    """A subscriber's bounded queue on an ``InProcessBus``.

    When the queue is full the oldest message is dropped to make room, so a slow subscriber
    sees the latest state and never blocks publishers. ``dropped`` counts the losses.

    Args:
        bus: The bus that delivers to this subscription.
        topic: The subscribed topic.
        maxsize: Queue capacity.
    """

    def __init__(self, bus: "InProcessBus", topic: str, maxsize: int) -> None:
        if maxsize < 1:
            raise ValueError(f"maxsize must be at least 1, got {maxsize}")
        self._bus = bus
        self._topic = topic
        self._queue: asyncio.Queue[Any] = asyncio.Queue(maxsize=maxsize)
        self._closed = False
        self.dropped = 0
        """Messages discarded because the queue was full."""

    @property
    def topic(self) -> str:
        """The subscribed topic."""
        return self._topic

    @property
    def closed(self) -> bool:
        """Whether ``close()`` was called."""
        return self._closed

    def __aiter__(self) -> "InProcessSubscription":
        return self

    async def __anext__(self) -> Any:
        if self._closed:
            raise StopAsyncIteration
        message = await self._queue.get()
        if message is _CLOSED:
            raise StopAsyncIteration
        return message

    def close(self) -> None:
        """Unsubscribe; a pending or later ``__anext__`` ends the iteration."""
        if self._closed:
            return
        self._closed = True
        self._bus._unsubscribe(self)
        if self._queue.full():
            self._queue.get_nowait()  # room for the wake-up marker; the subscriber is leaving
        self._queue.put_nowait(_CLOSED)

    def _offer(self, message: Any) -> None:
        if self._queue.full():
            self._queue.get_nowait()
            self.dropped += 1
            if self.dropped == 1:
                logger.warning(f"bus subscriber on {self._topic!r} is full; dropping oldest")
        self._queue.put_nowait(message)


class InProcessBus:
    """Delivers each published message to every subscriber's own bounded queue.

    Messages are shared, not copied: publish immutable values (frames, Pydantic models).
    """

    def __init__(self) -> None:
        self._subscribers: dict[str, list[InProcessSubscription]] = defaultdict(list)

    async def publish(self, topic: str, message: Any) -> None:
        """Deliver ``message`` to every current subscriber of ``topic``; never blocks.

        Args:
            topic: Topic name.
            message: The message.
        """
        for subscription in tuple(self._subscribers.get(topic, ())):
            subscription._offer(message)

    def subscribe(self, topic: str, *, maxsize: int = 1000) -> InProcessSubscription:
        """Start receiving messages published on ``topic`` from now on.

        Args:
            topic: Topic name.
            maxsize: Queue capacity before the oldest messages are dropped.

        Returns:
            The subscription; close it when done.
        """
        subscription = InProcessSubscription(self, topic, maxsize)
        self._subscribers[topic].append(subscription)
        return subscription

    def subscriber_count(self, topic: str) -> int:
        """Number of open subscriptions on ``topic``."""
        return len(self._subscribers.get(topic, ()))

    def _unsubscribe(self, subscription: InProcessSubscription) -> None:
        subscribers = self._subscribers.get(subscription.topic, [])
        if subscription in subscribers:
            subscribers.remove(subscription)
