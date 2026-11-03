from pathlib import Path

import pytest

from spotcool_contracts import Zone
from spotcool_contracts.events import EventEnvelope, EventSource, EventType, KpiData
from spotcool_world.clocks import SimClock
from spotcool_world.frames import PlantMeasurementsFrame, TickFrame
from spotcool_world.pipeline import PipelineParams
from spotcool_world.processors import PlantProcessor
from spotcool_world.serializers import EventRecord, RawRecord, RunWriter
from spotcool_world.services import PlantStartParams
from spotcool_world.services.boptest.catalog import zone_damper
from spotcool_world.services.rc import RcPlant
from spotcool_world.services.replay import ReplayPlant
from spotcool_world.testing import run_test

START = PlantStartParams(start_time_s=198 * 86_400 + 10 * 3600, step_s=900.0, warmup_s=3600.0)
DAMPER = zone_damper(Zone.SOU)


def kpi_event(data: KpiData) -> EventRecord:
    return EventRecord(envelope=EventEnvelope.build(EventType.KPI, EventSource.WORLD, data))


async def record(path: Path, steps: int) -> tuple[list[dict[str, float]], dict[str, float | None]]:
    """Record two RC runs the way the recorder does: each run's states, then its final KPIs.

    Record 0 is the state at the start time, record k the state after step k.
    """
    spotcool, baseline = RcPlant(), RcPlant()
    raw, other = await spotcool.start(START), await baseline.start(START)
    recorded = [raw]
    with RunWriter(path) as writer:
        for index in range(steps + 1):
            if index:
                raw, other = await spotcool.step({DAMPER: 0.0}), await baseline.step({})
                recorded.append(raw)
            t_s = START.start_time_s + index * START.step_s  # the frame's stamp
            writer.write(RawRecord(run="spotcool", index=index, sim_time_s=t_s, raw=raw))
            writer.write(RawRecord(run="baseline", index=index, sim_time_s=t_s, raw=other))
            writer.flush()
        kpis = await spotcool.kpis()
        writer.write(kpi_event(KpiData(run="spotcool", boptest={"ener_tot": 9.0})))  # provisional
        writer.write(kpi_event(KpiData(run="spotcool", boptest=kpis, provisional=False)))
        baseline_kpis = await baseline.kpis()
        writer.write(kpi_event(KpiData(run="baseline", boptest=baseline_kpis, provisional=False)))
    return recorded, kpis


@pytest.mark.asyncio
async def test_replay_reproduces_the_recorded_values_exactly(tmp_path: Path) -> None:
    path = tmp_path / "events.jsonl"
    recorded, kpis = await record(path, steps=4)
    plant = ReplayPlant(path, "spotcool")
    assert await plant.start(START) == recorded[0]  # record 0: the state at the start time
    assert [await plant.step({}) for _ in range(4)] == recorded[1:]  # float for float
    assert await plant.kpis() == kpis  # the final, not the provisional, KPIs
    with pytest.raises(RuntimeError, match="no more spotcool steps"):
        await plant.step({})
    await plant.stop()
    await plant.stop()


@pytest.mark.asyncio
async def test_replay_can_start_at_any_recorded_tick(tmp_path: Path) -> None:
    path = tmp_path / "events.jsonl"
    recorded, _ = await record(path, steps=3)
    plant = ReplayPlant(path, "spotcool")
    later = START.start_time_s + START.step_s
    assert await plant.start(START.model_copy(update={"start_time_s": later})) == recorded[1]
    assert await plant.step({}) == recorded[2]
    with pytest.raises(ValueError, match="no spotcool state at"):
        await plant.start(START.model_copy(update={"start_time_s": later + 1}))


@pytest.mark.asyncio
async def test_replay_refuses_two_runs_in_one_file(tmp_path: Path) -> None:
    path = tmp_path / "events.jsonl"
    await record(path, steps=2)
    await record(path, steps=2)  # RunWriter appends: a reused run directory
    with pytest.raises(ValueError, match="repeats spotcool step indices"):
        await ReplayPlant(path, "spotcool").start(START)


@pytest.mark.asyncio
async def test_replay_refuses_a_gap_or_another_step_length(tmp_path: Path) -> None:
    path = tmp_path / "events.jsonl"
    raw = {"time": 0.0}
    with RunWriter(path) as writer:
        for index, t_s in enumerate([0.0, 900.0, 1800.0, 3600.0]):  # the state at 2700 s is missing
            writer.write(RawRecord(run="spotcool", index=index, sim_time_s=t_s, raw=raw))
    plant = ReplayPlant(path, "spotcool")
    with pytest.raises(ValueError, match="step 3 is not 900.0 s after step 2"):
        await plant.start(PlantStartParams(start_time_s=0.0, step_s=900.0))
    with pytest.raises(ValueError, match="step 1 is not 600.0 s after step 0"):  # 900 s steps
        await plant.start(PlantStartParams(start_time_s=0.0, step_s=600.0))
    with pytest.raises(ValueError, match="no spotcool state at 300.0 s"):
        await plant.start(PlantStartParams(start_time_s=300.0, step_s=900.0))


@pytest.mark.asyncio
async def test_replay_checks_its_inputs_like_every_plant(tmp_path: Path) -> None:
    path = tmp_path / "events.jsonl"
    await record(path, steps=1)
    plant = ReplayPlant(path, "baseline")
    with pytest.raises(RuntimeError, match="not been started"):
        await plant.step({})
    await plant.start(START)
    with pytest.raises(ValueError, match="unknown BOPTEST inputs"):
        await plant.step({"nope_u": 1.0})


@pytest.mark.asyncio
async def test_a_replayed_run_round_trips_through_the_plant_processor(tmp_path: Path) -> None:
    path = tmp_path / "events.jsonl"
    recorded, _ = await record(path, steps=2)
    start_s, step_s = START.start_time_s, START.step_s
    ticks = [TickFrame(index=i, step_s=step_s, sim_time_s=start_s + i * step_s) for i in range(2)]
    down, _ = await run_test(
        PlantProcessor(ReplayPlant(path, "spotcool"), "spotcool", warmup_s=0.0),
        frames_to_send=ticks,
        params=PipelineParams(clock=SimClock(start_s, step_s), run_id="t"),
    )
    measured = [f for f in down if isinstance(f, PlantMeasurementsFrame)]
    assert [f.raw for f in measured] == recorded  # record 0 first, from the StartFrame
    assert [f.sim_time_s for f in measured] == [start_s + k * step_s for k in range(3)]
