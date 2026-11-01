"""HttpServerWorker: serves the world's HTTP app (cameras and calendar) with uvicorn."""

import asyncio
import contextlib
import socket
from collections.abc import Iterator

import uvicorn
from fastapi import FastAPI
from loguru import logger

from spotcool_world.services.settings import WorldSettings
from spotcool_world.workers.base_worker import BaseWorker, WorkerState

STOP_TIMEOUT_S = 2
"""Seconds ``stop()`` lets open requests finish before uvicorn cancels them (uvicorn takes an int)."""


class _Server(uvicorn.Server):
    """uvicorn without signal handlers of its own: the ``WorkerRunner`` owns Ctrl+C."""

    @contextlib.contextmanager
    def capture_signals(self) -> Iterator[None]:
        yield


class HttpServerWorker(BaseWorker):
    """Serves an app, usually ``create_http_app``'s, on ``http_host:http_port`` until ``stop()``.

    ``run()`` binds the port itself, so a taken port fails it at once with an ``OSError`` that
    names the address (uvicorn alone would exit the process). Requests are not logged one by
    one. ``stop()`` sets the app's ``state.closing``, so open MJPEG streams end, asks uvicorn to
    exit and waits until it has; a request still open after ``STOP_TIMEOUT_S`` is cancelled.

    Args:
        app: The app to serve.
        settings: ``http_host`` and ``http_port``.
        name: Unique worker name.
    """

    def __init__(self, app: FastAPI, settings: WorldSettings, *, name: str | None = None) -> None:
        super().__init__(name=name)
        self._app = app
        self._host = settings.http_host
        self._port = settings.http_port
        self._server: _Server | None = None
        self._stopping = False
        self._done = asyncio.Event()

    async def run(self) -> None:
        """Serve until ``stop()`` is called; the state is then ``finished``.

        Raises:
            OSError: If the address cannot be bound, such as a port another program holds.
            RuntimeError: If the worker has already run.
        """
        if self._state is not WorkerState.IDLE:
            raise RuntimeError(f"{self.name} has already run")
        self._state = WorkerState.RUNNING
        try:
            if not self._stopping:
                await self._serve()
            self._state = WorkerState.FINISHED
        except asyncio.CancelledError:
            self._state = WorkerState.CANCELLED
            raise
        except Exception:
            self._state = WorkerState.FAILED
            raise
        finally:
            self._done.set()
            await self.cleanup()

    async def stop(self, reason: str = "") -> None:
        """End open streams, ask uvicorn to exit, and wait until ``run()`` is done serving.

        Args:
            reason: Why it is being stopped (logged).
        """
        logger.info(f"{self.name}: stopping{f' ({reason})' if reason else ''}")
        self._stopping = True
        closing = getattr(self._app.state, "closing", None)
        if isinstance(closing, asyncio.Event):
            closing.set()
        if self._server is not None:
            self._server.should_exit = True
            await self._done.wait()

    async def _serve(self) -> None:
        family = socket.AF_INET6 if ":" in self._host else socket.AF_INET
        try:
            sock = socket.create_server((self._host, self._port), family=family)
        except OSError as exc:
            raise OSError(
                f"{self.name}: cannot serve HTTP on {self._host}:{self._port}: {exc}"
            ) from exc
        config = uvicorn.Config(
            self._app,
            lifespan="off",
            ws="none",
            log_config=None,
            access_log=False,
            timeout_graceful_shutdown=STOP_TIMEOUT_S,
        )
        self._server = _Server(config)
        logger.info(f"{self.name}: serving HTTP on {self._host}:{self._port}")
        with sock:
            await self._server.serve(sockets=[sock])
