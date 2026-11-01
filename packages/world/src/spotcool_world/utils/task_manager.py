"""The one place background tasks are created.

Processors, workers and the runner never call ``asyncio.create_task`` themselves; they call
``self.create_task()``, which lands here. Keeping every task in one registry lets a worker
cancel whatever is left when a run ends and logs crashes nobody awaited.
"""

import asyncio
from collections.abc import Coroutine
from typing import Any

from loguru import logger


class TaskManager:
    """Creates background tasks, remembers them until they finish and logs crashes.

    A pipeline worker hands one manager to all of its processors through
    ``FrameProcessorSetup``; an object that has not been set up uses a private one.
    """

    def __init__(self) -> None:
        self._tasks: set[asyncio.Task[Any]] = set()

    @property
    def tasks(self) -> frozenset[asyncio.Task[Any]]:
        """Tasks that have not finished yet."""
        return frozenset(self._tasks)

    def create_task(self, coro: Coroutine[Any, Any, Any], name: str) -> asyncio.Task[Any]:
        """Schedule ``coro`` as a task and track it until it finishes.

        Args:
            coro: The coroutine to run.
            name: Task name, shown in logs and ``asyncio`` debugging output.

        Returns:
            The running task.
        """
        task = asyncio.create_task(coro, name=name)
        self._tasks.add(task)
        task.add_done_callback(self._on_done)
        return task

    async def cancel_all(self, timeout_s: float = 5.0) -> None:
        """Cancel every tracked task except the caller's own, and wait for them to stop.

        Args:
            timeout_s: Longest wait for the cancelled tasks to finish.
        """
        await cancel_and_wait(self._tasks, timeout_s)

    def _on_done(self, task: asyncio.Task[Any]) -> None:
        self._tasks.discard(task)
        if not task.cancelled() and (exc := task.exception()) is not None:
            logger.opt(exception=exc).error(f"task {task.get_name()} crashed")


async def cancel_and_wait(tasks: set[asyncio.Task[Any]], timeout_s: float = 5.0) -> None:
    """Cancel ``tasks`` (never the caller's own task) and wait up to ``timeout_s`` for them.

    Args:
        tasks: Tasks to cancel. The set is not modified.
        timeout_s: Longest wait for the tasks to finish.
    """
    targets = {t for t in tasks if t is not asyncio.current_task() and not t.done()}
    for task in targets:
        task.cancel()
    if targets:
        _, pending = await asyncio.wait(targets, timeout=timeout_s)
        for task in pending:
            logger.warning(f"task {task.get_name()} ignored cancellation for {timeout_s} s")
