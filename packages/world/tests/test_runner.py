import asyncio
import signal

import pytest

from spotcool_world.bus import InProcessBus
from spotcool_world.frames import EndFrame
from spotcool_world.pipeline import Pipeline
from spotcool_world.runner import WorkerRunner
from spotcool_world.workers import BaseWorker, PipelineWorker, WorkerState


class WaitingWorker(BaseWorker):
    """Runs until stopped."""

    def __init__(self, name: str) -> None:
        super().__init__(name=name)
        self.stopped = asyncio.Event()
        self.reason = ""

    async def run(self) -> None:
        self._state = WorkerState.RUNNING
        await self.stopped.wait()
        self._state = WorkerState.CANCELLED

    async def stop(self, reason: str = "") -> None:
        self.reason = reason
        self.stopped.set()


class FailingWorker(BaseWorker):
    async def run(self) -> None:
        self._state = WorkerState.RUNNING
        await asyncio.sleep(0.01)
        self._state = WorkerState.FAILED
        raise RuntimeError("worker broke")

    async def stop(self, reason: str = "") -> None:
        pass


def test_registry_and_shared_bus() -> None:
    bus = InProcessBus()
    runner = WorkerRunner(bus=bus, handle_sigint=False)
    worker = WaitingWorker("vrf")
    runner.add(worker)
    assert runner.workers["vrf"] is worker
    assert worker.bus is bus
    with pytest.raises(ValueError, match="already registered"):
        runner.add(WaitingWorker("vrf"))
    with pytest.raises(TypeError):
        runner.workers["other"] = worker  # pyright: ignore[reportIndexIssue]


@pytest.mark.asyncio
async def test_runs_until_every_worker_finishes() -> None:
    first, second = PipelineWorker(Pipeline([])), PipelineWorker(Pipeline([]))
    await first.queue_frame(EndFrame())
    await second.queue_frame(EndFrame())
    await asyncio.wait_for(WorkerRunner(handle_sigint=False).run(first, second), timeout=2.0)
    assert first.state is WorkerState.FINISHED
    assert second.state is WorkerState.FINISHED


@pytest.mark.asyncio
async def test_a_failing_worker_stops_the_others() -> None:
    survivor = WaitingWorker("survivor")
    runner = WorkerRunner(handle_sigint=False)
    with pytest.raises(RuntimeError, match="worker broke"):
        await asyncio.wait_for(runner.run(survivor, FailingWorker()), timeout=2.0)
    assert survivor.reason == "another worker failed"


@pytest.mark.asyncio
async def test_sigint_stops_workers_gracefully() -> None:
    worker = WaitingWorker("loop")
    before = signal.getsignal(signal.SIGINT)

    async def press_ctrl_c() -> None:
        await asyncio.sleep(0.05)
        signal.raise_signal(signal.SIGINT)

    runner = WorkerRunner(handle_sigint=True)
    await asyncio.wait_for(asyncio.gather(runner.run(worker), press_ctrl_c()), timeout=2.0)
    assert worker.reason == "SIGINT"
    assert signal.getsignal(signal.SIGINT) == before  # handlers restored
