"""Base class for named objects that own background tasks and fire pipecat-style events."""

import asyncio
import inspect
import itertools
from collections.abc import Callable, Coroutine
from typing import Any, TypeVar

from loguru import logger

from spotcool_world.utils.task_manager import TaskManager, cancel_and_wait

EventHandler = Callable[..., Any]
"""An event handler: ``handler(obj, *args)``, a plain function or a coroutine function."""

_H = TypeVar("_H", bound=EventHandler)

_ids = itertools.count(1)

EVENT_DRAIN_TIMEOUT_S = 5.0
"""How long ``cleanup()`` waits for background event handlers before cancelling them."""


class BaseObject:
    """A named object with background tasks and event handlers.

    Subclasses declare their events with ``register_event_handler`` in ``__init__``; users
    attach handlers with the ``@obj.event_handler("on_x")`` decorator; the object fires them
    with ``_call_event_handler``. Handlers receive the object first, then the event arguments.

    Args:
        name: Display name. Defaults to ``ClassName#id``.
    """

    def __init__(self, *, name: str | None = None) -> None:
        self._id = next(_ids)
        self._name = name or f"{type(self).__name__}#{self._id}"
        self._event_handlers: dict[str, list[EventHandler]] = {}
        self._task_manager = TaskManager()
        self._tasks: set[asyncio.Task[Any]] = set()
        self._event_tasks: set[asyncio.Task[Any]] = set()

    @property
    def id(self) -> int:
        """Process-wide unique object id."""
        return self._id

    @property
    def name(self) -> str:
        """Display name used in logs and task names."""
        return self._name

    def __str__(self) -> str:
        return self._name

    def create_task(
        self, coro: Coroutine[Any, Any, Any], name: str | None = None
    ) -> asyncio.Task[Any]:
        """Run ``coro`` in the background as a task owned by this object.

        ``cleanup()`` cancels tasks that are still running.

        Args:
            coro: The coroutine to run.
            name: Short task name; the object's name is prefixed.

        Returns:
            The running task.
        """
        task = self._task_manager.create_task(coro, f"{self._name}::{name or 'task'}")
        self._tasks.add(task)
        task.add_done_callback(self._tasks.discard)
        return task

    async def cancel_tasks(self) -> None:
        """Cancel this object's background tasks, except event handlers and the caller's own."""
        await cancel_and_wait(self._tasks - self._event_tasks)

    async def cleanup(self) -> None:
        """Cancel background tasks, then let running event handlers finish (bounded wait)."""
        await self.cancel_tasks()
        pending = {t for t in self._event_tasks if t is not asyncio.current_task()}
        if pending:
            _, late = await asyncio.wait(pending, timeout=EVENT_DRAIN_TIMEOUT_S)
            await cancel_and_wait(late)

    def register_event_handler(self, event_name: str) -> None:
        """Declare an event this object fires.

        Args:
            event_name: Event name, by convention ``on_something``.

        Raises:
            ValueError: If the event is already registered.
        """
        if event_name in self._event_handlers:
            raise ValueError(f"{self._name}: event {event_name!r} is already registered")
        self._event_handlers[event_name] = []

    def event_handler(self, event_name: str) -> Callable[[_H], _H]:
        """Decorator form of ``add_event_handler``.

        Args:
            event_name: A registered event name.

        Returns:
            A decorator that attaches the handler and returns it unchanged.
        """

        def decorator(handler: _H) -> _H:
            self.add_event_handler(event_name, handler)
            return handler

        return decorator

    def add_event_handler(self, event_name: str, handler: EventHandler) -> None:
        """Attach a handler to an event.

        Args:
            event_name: A registered event name.
            handler: Called as ``handler(obj, *args)``; may be a coroutine function.

        Raises:
            ValueError: If the event is not registered (usually a typo).
        """
        if event_name not in self._event_handlers:
            raise ValueError(f"{self._name}: unknown event {event_name!r}")
        self._event_handlers[event_name].append(handler)

    async def _call_event_handler(self, event_name: str, *args: Any, sync: bool = False) -> None:
        """Fire an event.

        Each handler runs as its own background task, so a slow handler never stalls the
        caller, unless ``sync`` is True, in which case handlers run one after another before
        this returns. A handler that raises is logged and never breaks the caller.

        Args:
            event_name: A registered event name.
            *args: Event arguments, passed after the object itself.
            sync: Run handlers inline instead of as background tasks.
        """
        if event_name not in self._event_handlers:
            raise ValueError(f"{self._name}: unknown event {event_name!r}")
        for handler in self._event_handlers[event_name]:
            if sync:
                await self._run_handler(event_name, handler, *args)
            else:
                task = self.create_task(self._run_handler(event_name, handler, *args), event_name)
                self._event_tasks.add(task)
                task.add_done_callback(self._event_tasks.discard)

    async def _run_handler(self, event_name: str, handler: EventHandler, *args: Any) -> None:
        try:
            result = handler(self, *args)
            if inspect.isawaitable(result):
                await result
        except Exception:
            logger.exception(f"{self._name}: handler for {event_name} failed")
