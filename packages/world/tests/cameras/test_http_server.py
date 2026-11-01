import asyncio
import re
import socket
import time
from collections.abc import AsyncGenerator
from contextlib import asynccontextmanager

import httpx
import pytest
from fastapi import FastAPI, WebSocket
from websockets.asyncio.client import connect

from spotcool_contracts.zones import Zone
from spotcool_world.runner import WorkerRunner
from spotcool_world.services.camera_service import CameraSystem
from spotcool_world.services.clips import ZoneCamera
from spotcool_world.transports.http.cameras import cameras_router
from spotcool_world.transports.http.server import create_http_app
from spotcool_world.workers import WorkerState
from spotcool_world.workers.http_server import STOP_TIMEOUT_S, HttpServerWorker

HOST = "127.0.0.1"


def free_port() -> int:
    with socket.socket() as probe:
        probe.bind((HOST, 0))
        return probe.getsockname()[1]


async def until_up(port: int) -> None:
    for _ in range(400):
        try:
            _, writer = await asyncio.open_connection(HOST, port)
        except OSError:
            await asyncio.sleep(0.025)
        else:
            writer.close()
            await writer.wait_closed()
            return
    raise AssertionError("the server did not come up")


@asynccontextmanager
async def serving(app: FastAPI, port: int) -> AsyncGenerator[HttpServerWorker, None]:
    """The app served by a worker under a runner, stopped on the way out."""
    worker = HttpServerWorker(app, HOST, port)
    running = asyncio.ensure_future(WorkerRunner(handle_sigint=False).run(worker))
    try:
        await until_up(port)
        yield worker
    finally:
        await worker.stop("test")
        await asyncio.wait_for(running, 5)


@asynccontextmanager
async def zone_cameras() -> AsyncGenerator[CameraSystem, None]:
    cameras = CameraSystem([ZoneCamera(Zone.COR)])
    cameras.set_scene("cam-cor", "many", 4)
    await cameras.start()
    try:
        yield cameras
    finally:
        await cameras.stop()


async def first_mjpeg_part(port: int) -> bytes:
    async with (
        httpx.AsyncClient(base_url=f"http://{HOST}:{port}", timeout=5) as http,
        http.stream("GET", "/cameras/cam-cor/mjpeg") as response,
    ):
        return await anext(response.aiter_bytes())


@pytest.mark.asyncio
async def test_the_server_serves_until_stop_and_ends_open_streams() -> None:
    port = free_port()
    async with zone_cameras() as cameras:
        worker = HttpServerWorker(create_http_app([cameras_router(cameras)]), HOST, port)
        running = asyncio.ensure_future(WorkerRunner(handle_sigint=False).run(worker))
        await until_up(port)
        assert worker.state is WorkerState.RUNNING
        async with (
            httpx.AsyncClient(base_url=f"http://{HOST}:{port}", timeout=5) as http,
            http.stream("GET", "/cameras/cam-cor/mjpeg") as response,
        ):
            assert (await http.get("/healthz")).status_code == 200
            chunks = response.aiter_bytes()
            assert (await anext(chunks)).startswith(b"--frame\r\n")
            began = time.perf_counter()
            await worker.stop("test")
            took = time.perf_counter() - began
            async for _ in chunks:  # the server ends the body properly: no protocol error
                pass
        await asyncio.wait_for(running, 5)
    assert worker.state is WorkerState.FINISHED
    assert took < STOP_TIMEOUT_S  # the stream ended itself; uvicorn did not have to cancel it


@pytest.mark.parametrize(
    ("held", "wanted"),
    [(HOST, HOST), ("0.0.0.0", HOST), (HOST, "0.0.0.0")],
    ids=["same-address", "wildcard-then-specific", "specific-then-wildcard"],
)
@pytest.mark.asyncio
async def test_a_port_held_on_any_address_fails_run_at_once(held: str, wanted: str) -> None:
    with socket.create_server((held, 0)) as taker:
        port = taker.getsockname()[1]
        worker = HttpServerWorker(create_http_app([]), wanted, port)
        began = time.perf_counter()
        with pytest.raises(OSError, match=f"cannot serve HTTP on {re.escape(wanted)}:{port}"):
            await worker.run()
    assert time.perf_counter() - began < 1.0
    assert worker.state is WorkerState.FAILED


@pytest.mark.asyncio
async def test_a_worker_stopped_before_it_runs_ends_at_once() -> None:
    worker = HttpServerWorker(create_http_app([]), HOST, free_port())
    await worker.stop()
    await asyncio.wait_for(worker.run(), 1)
    assert worker.state is WorkerState.FINISHED
    with pytest.raises(RuntimeError, match="has already run"):
        await worker.run()


@pytest.mark.asyncio
async def test_an_app_streams_again_when_a_second_worker_serves_it() -> None:
    async with zone_cameras() as cameras:
        app = create_http_app([cameras_router(cameras)])
        for _ in range(2):  # the first worker's stop() set app.state.closing
            port = free_port()
            async with serving(app, port):
                assert (await first_mjpeg_part(port)).startswith(b"--frame\r\n")


def echo_app(events: list[str]) -> FastAPI:
    """An app with a WebSocket echo route and lifespan handlers, like WP6's dashboard."""

    @asynccontextmanager
    async def lifespan(app: FastAPI) -> AsyncGenerator[None, None]:
        events.append("started")
        yield
        events.append("stopped")

    app = FastAPI(lifespan=lifespan)

    @app.websocket("/echo")
    async def echo(websocket: WebSocket) -> None:
        await websocket.accept()
        await websocket.send_text(await websocket.receive_text())
        await websocket.close()

    return app


@pytest.mark.asyncio
async def test_the_worker_serves_websockets_and_runs_the_lifespan() -> None:
    events: list[str] = []
    port = free_port()
    async with serving(echo_app(events), port):
        assert events == ["started"]
        async with connect(f"ws://{HOST}:{port}/echo") as websocket:
            await websocket.send("hello")
            assert await websocket.recv() == "hello"
    assert events == ["started", "stopped"]


@pytest.mark.asyncio
async def test_a_failing_lifespan_startup_fails_run() -> None:
    @asynccontextmanager
    async def lifespan(app: FastAPI) -> AsyncGenerator[None, None]:
        raise RuntimeError("the dashboard cannot start")
        yield

    worker = HttpServerWorker(FastAPI(lifespan=lifespan), HOST, free_port())
    with pytest.raises(RuntimeError, match="lifespan startup failed"):
        await asyncio.wait_for(worker.run(), 5)
    assert worker.state is WorkerState.FAILED
