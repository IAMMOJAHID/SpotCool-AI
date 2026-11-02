"""Telemetry test helpers: raw dicts in BOPTEST names, summaries, and a short recorded world run.

The world runs on the RC plant only: no network, no BOPTEST, no footage.
"""

import asyncio
import os
from collections.abc import Awaitable, Callable, Mapping, Sequence
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import pytest

from spotcool_contracts import Zone
from spotcool_contracts.events import (
    EventEnvelope,
    PlantMeasurementsData,
    Run,
    ScenarioStateData,
    ZoneMeasurement,
    ZoneScene,
)
from spotcool_world.adapters.units import KELVIN_OFFSET
from spotcool_world.bus import InProcessBus, Subscription
from spotcool_world.clocks import SimClock, WallPacer
from spotcool_world.frames import ErrorFrame, Frame
from spotcool_world.pipeline import ParallelPipeline, Pipeline, PipelineParams
from spotcool_world.processors import (
    BusPublisher,
    EventEmitterProcessor,
    FrameDirection,
    FrameProcessor,
    KpiProcessor,
    PlantProcessor,
    RecorderProcessor,
    ScenarioProcessor,
    TelemetryProcessor,
    TickSource,
)
from spotcool_world.runner import WorkerRunner
from spotcool_world.scenarios import CompiledScenario, compile_scenario, load_scenario
from spotcool_world.services import WorldSettings
from spotcool_world.services.boptest.catalog import bms_signal, load_catalog
from spotcool_world.services.rc import RcPlant
from spotcool_world.testing import CaptureProcessor
from spotcool_world.workers import PipelineWorker

STEP_S = 900.0
DAY_S = 199 * 86_400.0  # hybrid_tuesday's day
START_S = DAY_S + 7 * 3600  # 07:00, the scenario's start


@pytest.fixture(autouse=True)
def no_world_settings(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    # No SPOTCOOL_WORLD_* variables and no stray world.yaml: settings take their defaults.
    for key in list(os.environ):
        if key.startswith("SPOTCOOL_WORLD_"):
            monkeypatch.delenv(key)
    monkeypatch.chdir(tmp_path)


def _raw(
    temps_c: Mapping[Zone, float] | None = None,
    *,
    cooling_c: float = 24.0,
    heating_c: float = 20.0,
    meter_w: float = 1000.0,
    outdoor_c: float | None = 30.0,
) -> dict[str, float]:
    """A plant's outputs in BOPTEST names and units, as the point map reads them."""
    temps = temps_c or {}
    raw: dict[str, float] = {"time": 0.0}
    for zone in Zone:
        prefix = f"ZN-{zone.value.upper()}"
        raw[bms_signal(f"{prefix}-T")] = temps.get(zone, 24.0) + KELVIN_OFFSET
        raw[bms_signal(f"{prefix}-CLG-SP")] = cooling_c + KELVIN_OFFSET
        raw[bms_signal(f"{prefix}-HTG-SP")] = heating_c + KELVIN_OFFSET
        raw[bms_signal(f"{prefix}-CO2")] = 600.0
        raw[bms_signal(f"{prefix}-SA-FLOW")] = 0.2
    for meter in load_catalog().electric_power:
        raw[meter] = meter_w
    if outdoor_c is not None:
        raw[bms_signal("OA-T")] = outdoor_c + KELVIN_OFFSET
    return raw


def _summary(
    run: Run,
    temps_c: Mapping[Zone, float] | None = None,
    *,
    cooling_c: float = 24.0,
    heating_c: float = 20.0,
    energy_kwh: float = 0.0,
) -> PlantMeasurementsData:
    """A plant run's state in BMS units."""
    temps = temps_c or {}
    zones = [
        ZoneMeasurement(
            zone=zone,
            temp_c=temps.get(zone, 24.0),
            cooling_sp_c=cooling_c,
            heating_sp_c=heating_c,
            co2_ppm=600.0,
            airflow_lps=200.0,
        )
        for zone in Zone
    ]
    return PlantMeasurementsData(
        run=run, zones=zones, power_kw={}, total_power_kw=0.0, energy_kwh=energy_kwh
    )


def _state(people: Mapping[Zone, int] | None = None) -> ScenarioStateData:
    """A scenario state with the given people; every other zone empty."""
    counts = people or {}
    zones = [
        ZoneScene(
            zone=zone, scene="few" if counts.get(zone) else "empty", people=counts.get(zone, 0)
        )
        for zone in Zone
    ]
    return ScenarioStateData(zones=zones)


async def _taken(subscription: Subscription) -> list[Any]:
    """The messages waiting on a subscription, without waiting for more."""
    messages: list[Any] = []
    while True:
        try:
            async with asyncio.timeout(0):
                messages.append(await anext(subscription))
        except (TimeoutError, StopAsyncIteration):
            return messages


@pytest.fixture
def make_raw() -> Callable[..., dict[str, float]]:
    """``make_raw(temps_c, *, cooling_c, heating_c, meter_w, outdoor_c)``: a raw dict."""
    return _raw


@pytest.fixture
def make_summary() -> Callable[..., PlantMeasurementsData]:
    """``make_summary(run, temps_c, *, cooling_c, heating_c, energy_kwh)``: a summary."""
    return _summary


@pytest.fixture
def make_state() -> Callable[..., ScenarioStateData]:
    """``make_state(people)``: a scenario state."""
    return _state


@pytest.fixture
def taken() -> Callable[[Subscription], Awaitable[list[Any]]]:
    """``await taken(subscription)``: the messages waiting on it."""
    return _taken


def scenario_occupancy(scenario: CompiledScenario) -> Callable[[float], dict[Zone, int]]:
    def occupancy(t_s: float) -> dict[Zone, int]:
        if t_s < scenario.start_s:
            return {}
        return {zone.zone: zone.people for zone in scenario.state_at(t_s).zones}

    return occupancy


@dataclass
class Recorded:
    """A short world run on the RC plants, recorded.

    Parameters:
        folder: The run's directory.
        worker: The pipeline worker that ran it.
        published: Every envelope the bus carried on the events topic, in order.
        frames: The frames that left the bottom of the pipeline.
        errors: The error frames that reached the top.
    """

    folder: Path
    worker: PipelineWorker
    published: list[EventEnvelope] = field(default_factory=list)
    frames: list[Frame] = field(default_factory=list)
    errors: list[ErrorFrame] = field(default_factory=list)


RecordWorld = Callable[..., Awaitable[Recorded]]


@pytest.fixture
def record_world(tmp_path: Path) -> RecordWorld:
    """Runs hybrid_tuesday on two RC plants with telemetry, KPIs, emitter, recorder and publisher.

    Arguments: ``steps`` (default 4), ``start_s`` (default 07:00), ``run_id`` (default
    ``run-1``), ``settings`` (default: ``run_dir`` in the test's tmp dir), ``extra`` processors
    (placed after the scenario, before the plants), ``before_recorder`` and ``after_recorder``
    processors (right above and right below the recorder), the ``recorder`` itself, ``bus``,
    and ``on_worker``, called with the worker before it runs.
    """

    async def record(
        *,
        steps: int = 4,
        start_s: float = START_S,
        run_id: str = "run-1",
        settings: WorldSettings | None = None,
        extra: Sequence[FrameProcessor] = (),
        before_recorder: Sequence[FrameProcessor] = (),
        after_recorder: Sequence[FrameProcessor] = (),
        recorder: RecorderProcessor | None = None,
        bus: InProcessBus | None = None,
        on_worker: Callable[[PipelineWorker], None] | None = None,
    ) -> Recorded:
        settings = settings or WorldSettings(run_dir=tmp_path / "runs")
        bus = bus or InProcessBus()
        scenario = compile_scenario(load_scenario("hybrid_tuesday").scenario, step_s=STEP_S)
        clock = SimClock(start_s, STEP_S)
        end_s = start_s + steps * STEP_S
        occupancy = scenario_occupancy(scenario)
        plants = ParallelPipeline(
            [PlantProcessor(RcPlant(occupancy), "spotcool", warmup_s=3600.0)],
            [PlantProcessor(RcPlant(occupancy), "baseline", warmup_s=3600.0)],
        )
        frames: list[Frame] = []
        upstream: list[Frame] = []
        pipeline = Pipeline(
            [
                CaptureProcessor(FrameDirection.UPSTREAM, upstream),
                TickSource(clock, WallPacer(None), end_s),
                ScenarioProcessor(scenario),
                *extra,
                plants,
                TelemetryProcessor(),
                KpiProcessor(),
                EventEmitterProcessor(),
                *before_recorder,
                recorder or RecorderProcessor(settings),
                *after_recorder,
                BusPublisher(bus),
                CaptureProcessor(FrameDirection.DOWNSTREAM, frames),
            ]
        )
        params = PipelineParams(
            clock=clock, run_id=run_id, scenario="hybrid_tuesday", plant="rc", end_time_s=end_s
        )
        worker = PipelineWorker(pipeline, params=params)
        if on_worker is not None:
            on_worker(worker)
        events = bus.subscribe("events", maxsize=100_000)
        try:
            await WorkerRunner(bus=bus, handle_sigint=False).run(worker)
            published = await _taken(events)
        finally:
            events.close()
        errors = [f for f in upstream if isinstance(f, ErrorFrame)]
        return Recorded(settings.run_dir / run_id, worker, published, frames, errors)

    return record
