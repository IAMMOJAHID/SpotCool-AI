"""Agent test helpers: free loopback ports, and a world app served by the world's own worker."""

import asyncio
import socket
import time
from collections.abc import AsyncIterator, Awaitable, Callable
from contextlib import AbstractAsyncContextManager, asynccontextmanager

import httpx
import pytest
from fastapi import FastAPI

HOST = "127.0.0.1"
Serve = Callable[[FastAPI], AbstractAsyncContextManager[int]]


def free_port(kind: socket.SocketKind = socket.SOCK_STREAM) -> int:
    """A port free on 127.0.0.1 a moment ago."""
    with socket.socket(socket.AF_INET, kind) as probe:
        probe.bind((HOST, 0))
        return probe.getsockname()[1]


async def until(condition: Callable[[], Awaitable[bool]], timeout_s: float = 10.0) -> None:
    """Wait until ``await condition()`` is true, at most ``timeout_s``."""
    deadline = time.perf_counter() + timeout_s
    while not await condition():
        assert time.perf_counter() < deadline, "timed out"
        await asyncio.sleep(0.05)


@asynccontextmanager
async def _serving(app: FastAPI) -> AsyncIterator[int]:
    from spotcool_world.runner.runner import WorkerRunner
    from spotcool_world.workers.http_server import HttpServerWorker

    port = free_port()
    worker = HttpServerWorker(app, HOST, port)
    running = asyncio.ensure_future(WorkerRunner(handle_sigint=False).run(worker))

    async def up() -> bool:
        try:
            async with httpx.AsyncClient() as http:
                await http.get(f"http://{HOST}:{port}/healthz")
        except httpx.HTTPError:
            return False
        return True

    try:
        await until(up)
        yield port
    finally:
        await worker.stop("test")
        await asyncio.wait_for(running, 5)


@pytest.fixture
def serve() -> Serve:
    """``async with serve(app) as port``: a world app served on loopback, stopped after."""
    return _serving
