from pathlib import Path

import pytest

from spotcool_contracts import Zone
from spotcool_contracts.events import EventEnvelope, EventSource, EventType, KpiData
from spotcool_world.serializers import EventRecord, RawRecord, RunWriter
from spotcool_world.services import PlantStartParams
from spotcool_world.services.boptest.catalog import zone_damper
from spotcool_world.services.rc import RcPlant
from spotcool_world.services.replay import ReplayPlant

START = PlantStartParams(start_time_s=198 * 86_400 + 10 * 3600, step_s=900.0, warmup_s=3600.0)
DAMPER = zone_damper(Zone.SOU)


def kpi_event(data: KpiData) -> EventRecord:
    return EventRecord(envelope=EventEnvelope.build(EventType.KPI, EventSource.WORLD, data))


async def record(path: Path, steps: int) -> tuple[list[dict[str, float]], dict[str, float | None]]:
    """Record two RC runs the way the recorder does: raw records per tick, then final KPIs."""
    spotcool, baseline = RcPlant(), RcPlant()
    await spotcool.start(START)
    await baseline.start(START)
    recorded: list[dict[str, float]] = []
    with RunWriter(path) as writer:
        for index in range(steps):
            end_s = START.start_time_s + (index + 1) * START.step_s  # the frame's stamp
            raw = await spotcool.step({DAMPER: 0.0})
            recorded.append(raw)
            writer.write(RawRecord(run="spotcool", index=index, sim_time_s=end_s, raw=raw))
            other = await baseline.step({})
            writer.write(RawRecord(run="baseline", index=index, sim_time_s=end_s, raw=other))
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
    assert await plant.start(START) == {}
    assert [await plant.step({}) for _ in range(4)] == recorded  # float for float
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
    await plant.start(START.model_copy(update={"start_time_s": later}))
    assert await plant.step({}) == recorded[1]
    with pytest.raises(ValueError, match="no spotcool step"):
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
        for index, end_s in enumerate([900.0, 1800.0, 3600.0]):  # the tick at 1800 s is missing
            writer.write(RawRecord(run="spotcool", index=index, sim_time_s=end_s, raw=raw))
    plant = ReplayPlant(path, "spotcool")
    with pytest.raises(ValueError, match="step 2 is not 900.0 s after step 1"):
        await plant.start(PlantStartParams(start_time_s=0.0, step_s=900.0))
    with pytest.raises(ValueError, match="no spotcool step"):  # recorded at 900 s, not 600 s
        await plant.start(PlantStartParams(start_time_s=0.0, step_s=600.0))


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
