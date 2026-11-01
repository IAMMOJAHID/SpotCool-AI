"""WorkerRunner: owns the bus and the worker registry, runs workers, handles Ctrl+C."""

import asyncio
import contextlib
import signal
from collections.abc import Callable, Mapping
from types import FrameType, MappingProxyType
from typing import Any

from loguru import logger

from spotcool_world.bus import Bus, InProcessBus
from spotcool_world.utils.base_object import BaseObject
from spotcool_world.workers.base_worker import BaseWorker, WorkerState

_SIGNALS = (signal.SIGINT, signal.SIGTERM)


class WorkerRunner(BaseObject):
    """Runs workers on a shared bus until all of them finish.

    The first Ctrl+C (SIGINT) or SIGTERM asks every worker to ``stop()``; a second one
    cancels them outright. If one worker fails, the others are stopped and the error is
    re-raised from ``run()``.

    Args:
        bus: The bus to share between workers. Defaults to a new ``InProcessBus``.
        handle_sigint: Install the signal handlers while running (off in tests).
    """

    def __init__(self, *, bus: Bus | None = None, handle_sigint: bool = True) -> None:
        super().__init__()
        self._bus = bus or InProcessBus()
        self._handle_sigint = handle_sigint
        self._workers: dict[str, BaseWorker] = {}
        self._worker_tasks: list[asyncio.Task[Any]] = []
        self._running = False
        self._signal_count = 0

    @property
    def bus(self) -> Bus:
        """The bus shared by every worker."""
        return self._bus

    @property
    def workers(self) -> Mapping[str, BaseWorker]:
        """Registered workers by name (read-only view)."""
        return MappingProxyType(self._workers)

    def add(self, worker: BaseWorker) -> None:
        """Register a worker and give it the shared bus.

        Args:
            worker: The worker; its name must be unique.

        Raises:
            ValueError: If another worker has the same name.
            RuntimeError: If the runner is already running.
        """
        if self._running:
            raise RuntimeError("add workers before run()")
        if worker.name in self._workers:
            raise ValueError(f"a worker named {worker.name!r} is already registered")
        worker.bus = self._bus
        self._workers[worker.name] = worker

    async def run(self, *workers: BaseWorker) -> None:
        """Register ``workers``, then run every registered worker until all have finished.

        Args:
            *workers: More workers to register first.
        """
        for worker in workers:
            self.add(worker)
        self._running = True
        restore_signals = self._install_signal_handlers() if self._handle_sigint else None
        try:
            self._worker_tasks = [self.create_task(w.run(), w.name) for w in self._workers.values()]
            if not self._worker_tasks:
                return
            _, pending = await asyncio.wait(self._worker_tasks, return_when=asyncio.FIRST_EXCEPTION)
            if pending:
                await self.stop("another worker failed")
                await asyncio.wait(pending)
            for task in self._worker_tasks:
                if not task.cancelled() and (exc := task.exception()) is not None:
                    raise exc
        finally:
            if restore_signals is not None:
                restore_signals()
            self._running = False
            await self.cleanup()

    async def stop(self, reason: str = "") -> None:
        """Ask every worker that has not finished to stop.

        Args:
            reason: Passed to each worker's ``stop()``.
        """
        for worker in self._workers.values():
            if worker.state in (WorkerState.IDLE, WorkerState.RUNNING):
                await worker.stop(reason)

    def _on_signal(self, sig: signal.Signals) -> None:
        self._signal_count += 1
        if self._signal_count == 1:
            logger.warning(f"{sig.name}: stopping workers (repeat to force)")
            self.create_task(self.stop(sig.name), "stop")
        else:
            logger.warning(f"{sig.name} again: cancelling workers")
            for task in self._worker_tasks:
                task.cancel()

    def _install_signal_handlers(self) -> Callable[[], None]:
        loop = asyncio.get_running_loop()
        try:
            for sig in _SIGNALS:
                loop.add_signal_handler(sig, self._on_signal, sig)
        except (NotImplementedError, RuntimeError):
            # Windows loops have no add_signal_handler (and POSIX refuses off the main
            # thread); remove any half-installed handler and fall back to signal.signal.
            for sig in _SIGNALS:
                with contextlib.suppress(NotImplementedError, RuntimeError):
                    loop.remove_signal_handler(sig)
        else:

            def remove() -> None:
                for sig in _SIGNALS:
                    loop.remove_signal_handler(sig)

            return remove

        def handler(signum: int, frame: FrameType | None) -> None:
            loop.call_soon_threadsafe(self._on_signal, signal.Signals(signum))

        previous: dict[signal.Signals, Any] = {}
        try:
            for sig in _SIGNALS:
                previous[sig] = signal.signal(sig, handler)
        except ValueError:  # not the main thread: signals cannot be handled here
            logger.warning("signal handlers need the main thread; Ctrl+C will not stop cleanly")

        def restore() -> None:
            for sig, old in previous.items():
                signal.signal(sig, old if old is not None else signal.SIG_DFL)

        return restore
