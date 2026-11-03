"""HttpServerWorker: serves an HTTP app with uvicorn (the cameras and calendar, the dashboard)."""

import asyncio
import contextlib
import socket
import sys
from collections.abc import Iterator

import uvicorn
from fastapi import FastAPI
from loguru import logger

from spotcool_world.workers.base_worker import BaseWorker, WorkerState

STOP_TIMEOUT_S = 2
"""Seconds ``stop()`` lets open requests finish before uvicorn cancels them (uvicorn takes an int)."""


class _Server(uvicorn.Server):
    """uvicorn without signal handlers of its own: the ``WorkerRunner`` owns Ctrl+C."""

    @contextlib.contextmanager
    def capture_signals(self) -> Iterator[None]:
        yield


class HttpServerWorker(BaseWorker):
    """Serves an app on ``host:port`` with uvicorn until ``stop()``.

    WP11 runs one for ``create_http_app``'s app on ``http_host:http_port`` and one for WP6's
    dashboard on ``http_host:dashboard_port``. The app may have WebSocket routes and lifespan
    handlers. Requests are not logged one by one.

    ``run()`` binds the port itself, so a port another program holds, on any address, fails it
    at once with an ``OSError`` that names the address; a failing lifespan startup fails it with
    a ``RuntimeError`` (uvicorn alone would exit the process in both cases). ``run()`` clears the
    app's ``state.closing``, and ``stop()`` sets it, so open MJPEG streams end; ``stop()`` then
    asks uvicorn to exit and waits until it has. A request still open after ``STOP_TIMEOUT_S`` is
    cancelled.

    Args:
        app: The app to serve, such as ``create_http_app``'s.
        host: The address to bind, such as ``WorldSettings.http_host``.
        port: The TCP port.
        name: Unique worker name.
    """

    def __init__(self, app: FastAPI, host: str, port: int, *, name: str | None = None) -> None:
        super().__init__(name=name)
        self._app = app
        self._host = host
        self._port = port
        self._server: _Server | None = None
        self._stopping = False
        self._done = asyncio.Event()

    async def run(self) -> None:
        """Serve until ``stop()`` is called; the state is then ``finished``.

        Raises:
            OSError: If the address cannot be bound, such as a port another program holds.
            RuntimeError: If the worker has already run, or the app's lifespan startup failed.
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
        if (closing := self._closing()) is not None:
            closing.set()
        if self._server is not None:
            self._server.should_exit = True
            await self._done.wait()

    async def _serve(self) -> None:
        if (closing := self._closing()) is not None:
            closing.clear()  # set by an earlier server's stop()
        try:
            sock = _listen(self._host, self._port)
        except OSError as exc:
            raise OSError(
                f"{self.name}: cannot serve HTTP on {self._host}:{self._port}: {exc}"
            ) from exc
        config = uvicorn.Config(
            self._app,
            lifespan="auto",
            ws="auto",
            log_config=None,
            access_log=False,
            timeout_graceful_shutdown=STOP_TIMEOUT_S,
        )
        self._server = _Server(config)
        logger.info(f"{self.name}: serving HTTP on {self._host}:{self._port}")
        with sock:
            try:
                await self._server.serve(sockets=[sock])
            except SystemExit:  # uvicorn exits the process when the app's startup fails
                raise RuntimeError(f"{self.name}: the app's lifespan startup failed") from None

    def _closing(self) -> asyncio.Event | None:
        closing = getattr(self._app.state, "closing", None)
        return closing if isinstance(closing, asyncio.Event) else None


def _listen(host: str, port: int) -> socket.socket:
    """A listening TCP socket on ``host:port`` that no other socket shares.

    Raises:
        OSError: If the port is taken on that address, or (on Windows) on any address.
    """
    family = socket.AF_INET6 if ":" in host else socket.AF_INET
    if sys.platform != "win32":
        return socket.create_server((host, port), family=family)
    # Windows lets a socket take a port another one holds on a more or less specific address,
    # unless both opt out. The wildcard probe also refuses a port held on another address.
    wildcard = "::" if family == socket.AF_INET6 else "0.0.0.0"
    if host != wildcard:
        _exclusive(family, wildcard, port).close()
    sock = _exclusive(family, host, port)
    sock.listen()
    return sock


def _exclusive(family: socket.AddressFamily, host: str, port: int) -> socket.socket:
    """A TCP socket bound to ``host:port`` with ``SO_EXCLUSIVEADDRUSE`` (Windows only)."""
    sock = socket.socket(family, socket.SOCK_STREAM)
    try:
        if sys.platform == "win32":  # the option exists on Windows only (type checkers on POSIX)
            sock.setsockopt(socket.SOL_SOCKET, socket.SO_EXCLUSIVEADDRUSE, 1)
        sock.bind((host, port))
    except BaseException:
        sock.close()
        raise
    return sock
