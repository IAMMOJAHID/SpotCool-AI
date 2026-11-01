import asyncio
import socket
import time

import httpx
import pytest

from spotcool_world.runner import WorkerRunner
from spotcool_world.services import WorldSettings
from spotcool_world.services.camera_service import create_cameras
from spotcool_world.transports.http.cameras import cameras_router
from spotcool_world.transports.http.server import create_http_app
from spotcool_world.workers import WorkerState
from spotcool_world.workers.http_server import STOP_TIMEOUT_S, HttpServerWorker

HOST = "127.0.0.1"


def free_port() -> int:
    with socket.socket() as probe:
        probe.bind((HOST, 0))
        return probe.getsockname()[1]


async def until_up(base: str) -> None:
    async with httpx.AsyncClient(base_url=base) as http:
        for _ in range(400):
            try:
                if (await http.get("/healthz")).status_code == 200:
                    return
            except httpx.TransportError:
                await asyncio.sleep(0.025)
    raise AssertionError("the server did not come up")


@pytest.mark.asyncio
async def test_the_server_serves_until_stop_and_ends_open_streams() -> None:
    port = free_port()
    cameras = create_cameras(WorldSettings())
    cameras.set_scene("cam-cor", "many", 4)
    await cameras.start()
    app = create_http_app([cameras_router(cameras)])
    worker = HttpServerWorker(app, WorldSettings(http_host=HOST, http_port=port))
    running = asyncio.ensure_future(WorkerRunner(handle_sigint=False).run(worker))
    base = f"http://{HOST}:{port}"
    try:
        await until_up(base)
        assert worker.state is WorkerState.RUNNING
        async with (
            httpx.AsyncClient(base_url=base, timeout=5) as http,
            http.stream("GET", "/cameras/cam-cor/mjpeg") as response,
        ):
            chunks = response.aiter_bytes()
            assert (await anext(chunks)).startswith(b"--frame\r\n")
            began = time.perf_counter()
            await worker.stop("test")
            took = time.perf_counter() - began
            async for _ in chunks:  # the server ends the body properly: no protocol error
                pass
        await asyncio.wait_for(running, 5)
    finally:
        await cameras.stop()
    assert worker.state is WorkerState.FINISHED
    assert took < STOP_TIMEOUT_S  # the stream ended itself; uvicorn did not have to cancel it


@pytest.mark.asyncio
async def test_a_taken_port_fails_run_at_once() -> None:
    with socket.create_server((HOST, 0)) as taker:
        port = taker.getsockname()[1]
        worker = HttpServerWorker(
            create_http_app([]), WorldSettings(http_host=HOST, http_port=port)
        )
        began = time.perf_counter()
        with pytest.raises(OSError, match=f"cannot serve HTTP on {HOST}:{port}"):
            await worker.run()
    assert time.perf_counter() - began < 1.0
    assert worker.state is WorkerState.FAILED


@pytest.mark.asyncio
async def test_a_worker_stopped_before_it_runs_ends_at_once() -> None:
    worker = HttpServerWorker(
        create_http_app([]), WorldSettings(http_host=HOST, http_port=free_port())
    )
    await worker.stop()
    await asyncio.wait_for(worker.run(), 1)
    assert worker.state is WorkerState.FINISHED
    with pytest.raises(RuntimeError, match="has already run"):
        await worker.run()
