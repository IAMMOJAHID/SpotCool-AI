from collections.abc import Mapping
from typing import Any

import pytest

from spotcool_contracts.events import Run
from spotcool_world.clocks import SimClock
from spotcool_world.frames import (
    CancelFrame,
    ErrorFrame,
    Frame,
    KpiFrame,
    MetricsFrame,
    PlantMeasurementsFrame,
    SetpointCommandFrame,
    TickFrame,
)
from spotcool_world.pipeline import Pipeline, PipelineParams
from spotcool_world.processors import FrameDirection, FrameProcessor, PlantProcessor
from spotcool_world.runner import WorkerRunner
from spotcool_world.services import PlantService, PlantStartParams
from spotcool_world.services.boptest.catalog import bms_signal
from spotcool_world.testing import run_test
from spotcool_world.workers import PipelineWorker

CORE = bms_signal("ZN-COR-CLG-SP", write=True)
START_S = 198 * 86_400 + 7 * 3600


class FakePlant(PlantService):
    """Records every call; its measurements are the number of calls so far."""

    def __init__(self, *, fail_start: bool = False, fail_step: bool = False) -> None:
        self.calls: list[tuple[str, Any]] = []
        self.fail_start = fail_start
        self.fail_step = fail_step

    @property
    def step_s(self) -> float:
        return 900.0

    async def start(self, params: PlantStartParams) -> dict[str, float]:
        self.calls.append(("start", params))
        if self.fail_start:
            raise RuntimeError("no plant today")
        return {"time": params.start_time_s}

    async def step(self, overrides: Mapping[str, float | None]) -> dict[str, float]:
        self.calls.append(("step", dict(overrides)))
        if self.fail_step:
            raise RuntimeError("boom")
        return {"time": float(len(self.calls))}

    async def kpis(self) -> dict[str, float | None]:
        self.calls.append(("kpis", None))
        return {"ener_tot": 1.0}

    async def stop(self) -> None:
        self.calls.append(("stop", None))


def tick(index: int) -> TickFrame:
    return TickFrame(index=index, step_s=900.0, sim_time_s=START_S + index * 900.0)


def command(run: Run, overrides: dict[str, float | None]) -> SetpointCommandFrame:
    return SetpointCommandFrame(run=run, overrides=overrides)


def names(frames: list[Frame]) -> list[str]:
    return [type(f).__name__ for f in frames]


def params() -> PipelineParams:
    return PipelineParams(clock=SimClock(START_S, 900.0), run_id="t")


@pytest.mark.asyncio
async def test_ticks_step_the_plant_and_the_end_brings_kpis_then_stop() -> None:
    plant = FakePlant()
    frames = [
        command("spotcool", {CORE: 301.15}),
        tick(0),
        command("spotcool", {CORE: None}),
        tick(1),
    ]
    down, up = await run_test(
        PlantProcessor(plant, "spotcool", time_period="peak_cool_day", warmup_s=3600.0),
        frames_to_send=frames,
        params=params(),
    )
    assert names(down) == [
        "PlantMeasurementsFrame",  # the initial state, right after the StartFrame
        "SetpointCommandFrame",
        "PlantMeasurementsFrame",
        "MetricsFrame",
        "TickFrame",
        "SetpointCommandFrame",
        "PlantMeasurementsFrame",
        "MetricsFrame",
        "TickFrame",
        "KpiFrame",
    ]
    assert up == []
    start = PlantStartParams(
        start_time_s=START_S, step_s=900.0, warmup_s=3600.0, time_period="peak_cool_day"
    )
    assert plant.calls == [
        ("start", start),
        ("step", {CORE: 301.15}),
        ("step", {CORE: None}),  # a release stays in the map, so it is sent explicitly
        ("kpis", None),
        ("stop", None),
    ]
    measured = down[2]
    assert isinstance(measured, PlantMeasurementsFrame)
    assert (measured.run, measured.raw) == ("spotcool", {"time": 2.0})
    assert measured.sim_time_s == START_S + 900.0  # the end of the step the values describe
    metrics = down[3]
    assert isinstance(metrics, MetricsFrame)
    assert [s.name for s in metrics.samples] == ["plant_step_latency", "plant_time_ratio"]
    assert metrics.sim_time_s == START_S  # the tick's time
    kpis = down[-1]
    assert isinstance(kpis, KpiFrame)
    assert (kpis.data.run, kpis.data.boptest, kpis.data.provisional) == (
        "spotcool",
        {"ener_tot": 1.0},
        False,
    )


@pytest.mark.asyncio
async def test_the_initial_state_follows_the_start_frame_stamped_with_the_start_time() -> None:
    start = params().start_frame()
    down, _ = await run_test(
        PlantProcessor(FakePlant(), "baseline"),
        frames_to_send=[start],  # our own StartFrame, so it comes back like any other frame
        send_start=False,
        params=params(),
    )
    assert names(down) == ["StartFrame", "PlantMeasurementsFrame", "KpiFrame"]
    initial = down[1]
    assert isinstance(initial, PlantMeasurementsFrame)
    assert (initial.run, initial.raw) == ("baseline", {"time": START_S})
    assert initial.sim_time_s == START_S  # the start time: no step has run yet


@pytest.mark.asyncio
async def test_commands_merge_until_released() -> None:
    plant = FakePlant()
    other = bms_signal("ZN-NOR-CLG-SP", write=True)
    frames = [command("spotcool", {CORE: 300.0}), command("spotcool", {other: 299.0}), tick(0)]
    await run_test(PlantProcessor(plant, "spotcool"), frames_to_send=frames, params=params())
    assert plant.calls[1] == ("step", {CORE: 300.0, other: 299.0})


@pytest.mark.asyncio
async def test_the_baseline_and_the_other_run_never_get_overrides() -> None:
    baseline, spotcool = FakePlant(), FakePlant()
    frames = [command("baseline", {CORE: 301.15}), tick(0)]
    await run_test(PlantProcessor(baseline, "baseline"), frames_to_send=frames, params=params())
    await run_test(PlantProcessor(spotcool, "spotcool"), frames_to_send=frames, params=params())
    assert baseline.calls[1] == ("step", {})
    assert spotcool.calls[1] == ("step", {})


@pytest.mark.asyncio
async def test_an_unknown_input_is_rejected_without_ending_the_run() -> None:
    plant = FakePlant()
    frames = [command("spotcool", {"nope_u": 1.0, CORE: 300.0}), tick(0)]
    down, up = await run_test(
        PlantProcessor(plant, "spotcool"), frames_to_send=frames, params=params()
    )
    assert [(type(f), f.fatal) for f in up if isinstance(f, ErrorFrame)] == [(ErrorFrame, False)]
    assert plant.calls[1] == ("step", {})  # the whole command was dropped
    assert names(down)[-1] == "KpiFrame"


@pytest.mark.asyncio
async def test_a_failed_step_is_fatal_and_the_plant_is_still_stopped() -> None:
    plant = FakePlant(fail_step=True)
    down, up = await run_test(
        PlantProcessor(plant, "spotcool"), frames_to_send=[tick(0), tick(1)], params=params()
    )
    errors = [f for f in up if isinstance(f, ErrorFrame)]
    assert len(errors) == 1 and errors[0].fatal and "boom" in errors[0].message
    assert any(isinstance(f, CancelFrame) for f in down)
    assert [call for call, _ in plant.calls] == ["start", "step", "stop"]  # no KPIs after a failure


@pytest.mark.asyncio
async def test_a_failed_start_is_fatal_and_nothing_steps() -> None:
    plant = FakePlant(fail_start=True)
    down, up = await run_test(
        PlantProcessor(plant, "spotcool"), frames_to_send=[tick(0)], params=params()
    )
    assert [f.fatal for f in up if isinstance(f, ErrorFrame)] == [True]
    assert [call for call, _ in plant.calls] == ["start", "stop"]
    assert not any(isinstance(f, PlantMeasurementsFrame) for f in down)  # no initial state


@pytest.mark.asyncio
async def test_a_cancel_on_a_healthy_plant_brings_kpis_then_stop() -> None:
    plant = FakePlant()
    down, _ = await run_test(  # the cancel jumps the queued tick, as a Ctrl+C does
        PlantProcessor(plant, "spotcool"),
        frames_to_send=[tick(0), CancelFrame(reason="ctrl-c")],
        send_end=False,
        params=params(),
    )
    assert [call for call, _ in plant.calls] == ["start", "kpis", "stop"]
    assert any(isinstance(f, KpiFrame) for f in down)


class Crash(FrameProcessor):
    """A later processor that raises on every tick."""

    async def process_frame(self, frame: Frame, direction: FrameDirection) -> None:
        await super().process_frame(frame, direction)
        if isinstance(frame, TickFrame):
            raise RuntimeError("a later processor crashed")
        await self.push_frame(frame, direction)


@pytest.mark.asyncio
async def test_a_crash_elsewhere_still_stops_the_plant_in_cleanup() -> None:
    plant = FakePlant()
    pipeline = Pipeline([PlantProcessor(plant, "spotcool"), Crash()])
    worker = PipelineWorker(pipeline, params=params())
    await worker.queue_frames([tick(0)])
    with pytest.raises(RuntimeError, match="crashed"):
        await WorkerRunner(handle_sigint=False).run(worker)
    assert [call for call, _ in plant.calls] == ["start", "step", "stop"]  # no End, no Cancel
