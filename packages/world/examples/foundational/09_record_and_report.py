"""09: Record a run, then report on it.

The hybrid Tuesday runs on two RC plants: SpotCool's and the baseline's. The telemetry turns
their measurements into BMS units, power and energy; the KPI processor scores both runs; the
emitter turns every frame into a dashboard event; the recorder keeps it all, raw measurements
included, in runs/<run_id>/; BusPublisher puts the events on the bus.

  TickSource -> ScenarioProcessor(hybrid_tuesday) -> EmptyZoneStandby -> ParallelPipeline[
  RC spotcool || RC baseline] -> Telemetry -> Kpi -> EventEmitter -> Recorder -> BusPublisher

EmptyZoneStandby stands in for SpotCool's agent so the two runs differ: it reads the
scenario's ground truth, which the real agent never sees, and puts each empty zone on standby
(cooling setpoint 28 degC, damper shut). Then the report turns the run into report.md and
charts (the report extra). The run takes a few seconds.

Runs go to the settings' run_dir (default ./runs). Keep them out of the repo:

  SPOTCOOL_WORLD_RUN_DIR=/tmp/spotcool-runs uv run python packages/world/examples/foundational/09_record_and_report.py
  SPOTCOOL_WORLD_RUN_DIR=/tmp/spotcool-runs uv run spotcool-world report <run_id>
"""

import asyncio
from datetime import datetime

from spotcool_contracts import Zone
from spotcool_contracts.points import Conversion
from spotcool_world.adapters.units import to_boptest
from spotcool_world.clocks import SimClock, WallPacer
from spotcool_world.frames import Frame, ScenarioStateFrame, SetpointCommandFrame
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
from spotcool_world.runner.report import write_report
from spotcool_world.scenarios import CompiledScenario, compile_scenario, load_scenario
from spotcool_world.serializers import MANIFEST_FILE, RunManifest
from spotcool_world.services import WorldSettings
from spotcool_world.services.boptest.catalog import bms_signal, zone_damper
from spotcool_world.services.rc import Occupancy, RcPlant
from spotcool_world.workers import PipelineWorker

SCENARIO = "hybrid_tuesday"
STANDBY_K = to_boptest(28.0, Conversion.KELVIN_TO_CELSIUS)


def scenario_people(scenario: CompiledScenario) -> Occupancy:
    """The RC plant's occupancy: the scenario's people, nobody before it starts (the warm-up)."""

    def occupancy(t_s: float) -> dict[Zone, int]:
        if t_s < scenario.start_s:
            return {}
        return {zone.zone: zone.people for zone in scenario.state_at(t_s).zones}

    return occupancy


class EmptyZoneStandby(FrameProcessor):
    """Commands the SpotCool run from the scenario state: empty zones on standby, others released."""

    async def process_frame(self, frame: Frame, direction: FrameDirection) -> None:
        await super().process_frame(frame, direction)
        await self.push_frame(frame, direction)
        if isinstance(frame, ScenarioStateFrame):
            overrides: dict[str, float | None] = {}
            for zone in frame.state.zones:
                empty = zone.people == 0
                setpoint = bms_signal(f"ZN-{zone.zone.value.upper()}-CLG-SP", write=True)
                overrides[setpoint] = STANDBY_K if empty else None
                overrides[zone_damper(zone.zone)] = 0.0 if empty else None
            command = SetpointCommandFrame(
                run="spotcool", overrides=overrides, sim_time_s=frame.sim_time_s
            )
            await self.push_frame(command)


async def main() -> None:
    settings = WorldSettings(scenario=SCENARIO, plant="rc", speed=None)
    scenario = compile_scenario(load_scenario(SCENARIO).scenario, step_s=settings.step_s)
    clock = SimClock(scenario.start_s, settings.step_s)
    run_id = f"{datetime.now():%Y%m%d-%H%M%S}-{SCENARIO}"
    plants = ParallelPipeline(
        [PlantProcessor(RcPlant(scenario_people(scenario)), "spotcool")],
        [PlantProcessor(RcPlant(scenario_people(scenario)), "baseline")],
    )
    runner = WorkerRunner()
    pipeline = Pipeline(
        [
            TickSource(clock, WallPacer(settings.speed), scenario.end_s),
            ScenarioProcessor(scenario),
            EmptyZoneStandby(),
            plants,
            TelemetryProcessor(),
            KpiProcessor(),
            EventEmitterProcessor(),
            RecorderProcessor(settings),
            BusPublisher(runner.bus),
        ]
    )
    params = PipelineParams(
        clock=clock, run_id=run_id, scenario=SCENARIO, plant="rc", end_time_s=scenario.end_s
    )
    await runner.run(PipelineWorker(pipeline, params=params))

    run_dir = settings.run_dir / run_id
    manifest = RunManifest.model_validate_json((run_dir / MANIFEST_FILE).read_text("utf-8"))
    print(f"run {run_id} recorded in {run_dir} ({manifest.ended})")
    for name, kpi in manifest.final_kpis.items():
        saved = "" if kpi.energy_saved_pct is None else f", saved {kpi.energy_saved_pct:.1f} %"
        print(f"  {name:<9} comfort where people were {kpi.comfort_where_people_kh:.2f} K h{saved}")
    print(f"report: {write_report(settings.run_dir, run_id)}")


if __name__ == "__main__":
    asyncio.run(main())
