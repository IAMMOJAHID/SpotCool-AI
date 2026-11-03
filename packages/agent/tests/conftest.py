"""Agent test helpers: free loopback ports, and a world app served by the world's own worker."""

import asyncio
import os
import socket
import sys
import time
from collections.abc import AsyncIterator, Awaitable, Callable, Iterator
from contextlib import AbstractAsyncContextManager, asynccontextmanager
from pathlib import Path
from typing import Any

import httpx
import pytest
from fastapi import FastAPI

import spotcool_agent

HOST = "127.0.0.1"
Serve = Callable[[FastAPI], AbstractAsyncContextManager[int]]
AGENT_SOURCE = Path(spotcool_agent.__file__).parent
WRITE_FLAGS = os.O_WRONLY | os.O_RDWR | os.O_CREAT | os.O_APPEND
_writes: list[str] | None = None  # while watched: every file the agent's code opened to write


def _audit(event: str, args: tuple[Any, ...]) -> None:
    if _writes is None or event != "open":
        return
    path, mode, flags = args
    writing = (isinstance(mode, str) and any(c in mode for c in "wax+")) or (
        isinstance(flags, int) and flags & WRITE_FLAGS
    )
    if not writing or str(path) == os.devnull:  # subprocess opens it; it stores nothing
        return
    if isinstance(path, int):  # a descriptor: the open that made it was audited with its path
        return
    if "__pycache__" in Path(str(path)).parts:  # the interpreter's bytecode cache, not data
        return
    frame = sys._getframe(1)
    while frame is not None:
        if frame.f_code.co_filename.startswith(str(AGENT_SOURCE)):
            _writes.append(str(path))
            return
        frame = frame.f_back


sys.addaudithook(_audit)  # hooks stay for the process; this one acts only while watched


@pytest.fixture
def writes() -> Iterator[list[str]]:
    """Every file opened to write by code with an agent frame on its stack, during the test."""
    global _writes
    _writes = []
    try:
        yield _writes
    finally:
        _writes = None


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
