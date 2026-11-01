from collections.abc import Callable

import pytest

from spotcool_contracts import Zone
from spotcool_contracts.events import KpiData, PlantMeasurementsData, Run, ScenarioStateData
from spotcool_world.frames import (
    Frame,
    KpiFrame,
    PlantMeasurementsFrame,
    ScenarioStateFrame,
    TickFrame,
)
from spotcool_world.processors import KpiProcessor
from spotcool_world.processors.kpi import comfort_kh, energy_saved_pct
from spotcool_world.testing import run_test

Summary = Callable[..., PlantMeasurementsData]
State = Callable[..., ScenarioStateData]
STEP_S = 900.0
T0 = 199 * 86_400.0 + 8 * 3600  # 08:00


def test_comfort_counts_zones_with_people_and_only_outside_the_band(
    make_summary: Summary, make_state: State
) -> None:
    state = make_state({Zone.COR: 4, Zone.NOR: 2, Zone.EAS: 1})
    run = make_summary("spotcool", {Zone.COR: 25.0, Zone.NOR: 23.0, Zone.SOU: 30.0, Zone.EAS: 18.5})
    baseline = make_summary("baseline", cooling_c=24.0, heating_c=20.0)
    scores = comfort_kh(state, run, baseline, STEP_S)
    assert scores == {
        Zone.COR: pytest.approx(0.25),  # 1 K above the cooling setpoint for a quarter hour
        Zone.NOR: 0.0,  # inside the band
        Zone.EAS: pytest.approx(0.375),  # 1.5 K below the heating setpoint
    }  # South is far outside the band, but nobody was there


def test_the_band_is_the_baseline_runs_setpoints(make_summary: Summary, make_state: State) -> None:
    state = make_state({Zone.COR: 1})
    run = make_summary("spotcool", {Zone.COR: 26.0}, cooling_c=28.0)  # its own setpoint is not it
    baseline = make_summary("baseline", cooling_c=25.0)
    assert comfort_kh(state, run, baseline, 3600.0) == {Zone.COR: pytest.approx(1.0)}


def test_energy_saved_is_relative_to_the_baseline() -> None:
    assert energy_saved_pct(4.0, 3.0) == pytest.approx(25.0)
    assert energy_saved_pct(4.0, 5.0) == pytest.approx(-25.0)
    assert energy_saved_pct(0.0, 0.0) is None


def tick(index: int) -> TickFrame:
    return TickFrame(index=index, step_s=STEP_S, sim_time_s=T0 + index * STEP_S)


def measured(summary: PlantMeasurementsData, t_s: float) -> PlantMeasurementsFrame:
    return PlantMeasurementsFrame(run=summary.run, raw={}, summary=summary, sim_time_s=t_s)


def final_from_plant(run: Run, boptest: dict[str, float | None]) -> KpiFrame:
    """A plant's own final KPI frame, as PlantProcessor pushes it at the end."""
    return KpiFrame(data=KpiData(run=run, boptest=boptest, provisional=False))


def kpis(frames: list[Frame]) -> list[KpiData]:
    return [f.data for f in frames if isinstance(f, KpiFrame)]


@pytest.mark.asyncio
async def test_comfort_uses_the_state_of_the_step_that_just_ended(
    make_summary: Summary, make_state: State
) -> None:
    hot = {Zone.COR: 25.0}  # 1 K above the band all along
    frames: list[Frame] = []
    for index, people in enumerate([{Zone.COR: 4}, {}]):  # occupied 08:00-08:15, then empty
        end_s = T0 + (index + 1) * STEP_S
        frames += [
            ScenarioStateFrame(state=make_state(people), sim_time_s=T0 + index * STEP_S),
            measured(make_summary("spotcool", hot), end_s),
            measured(make_summary("baseline", hot), end_s),
            tick(index),
        ]
    down, _ = await run_test(KpiProcessor(), frames_to_send=frames)
    finals = kpis(down)
    assert [k.run for k in finals] == ["spotcool", "baseline"]
    # Only the first step counts: its people were there. Looking the state up at the stamp
    # (08:15, empty) would have scored nothing.
    assert [k.comfort_where_people_kh for k in finals] == [pytest.approx(0.25)] * 2


@pytest.mark.asyncio
async def test_provisional_kpis_every_four_steps_and_final_ones_merge_the_plants_kpis(
    make_summary: Summary, make_state: State
) -> None:
    frames: list[Frame] = []
    for index in range(8):
        end_s = T0 + (index + 1) * STEP_S
        frames += [
            ScenarioStateFrame(state=make_state({Zone.COR: 2}), sim_time_s=T0 + index * STEP_S),
            measured(make_summary("spotcool", energy_kwh=3.0 * (index + 1)), end_s),
            measured(make_summary("baseline", energy_kwh=4.0 * (index + 1)), end_s),
            tick(index),
        ]
    frames += [
        final_from_plant("spotcool", {"ener_tot": 0.05, "tdis_tot": 1.5}),
        final_from_plant("baseline", {"ener_tot": 0.07, "tdis_tot": 0.0}),
    ]
    down, _ = await run_test(KpiProcessor(), frames_to_send=frames)
    out = [f for f in down if isinstance(f, KpiFrame)]
    provisional = [f for f in out if f.data.provisional]
    assert [(f.data.run, f.sim_time_s) for f in provisional] == [
        ("spotcool", T0 + 4 * STEP_S),
        ("baseline", T0 + 4 * STEP_S),
        ("spotcool", T0 + 8 * STEP_S),
        ("baseline", T0 + 8 * STEP_S),
    ]
    assert all(f.data.boptest == {} for f in provisional)
    assert provisional[0].data.energy_saved_pct == pytest.approx(25.0)
    assert provisional[1].data.energy_saved_pct is None  # the baseline saves nothing on itself
    finals = [f.data for f in out if not f.data.provisional]
    assert [(k.run, k.boptest, k.comfort_where_people_kh) for k in finals] == [
        ("spotcool", {"ener_tot": 0.05, "tdis_tot": 1.5}, 0.0),  # merged in, not passed on
        ("baseline", {"ener_tot": 0.07, "tdis_tot": 0.0}, 0.0),
    ]
    assert finals[0].energy_saved_pct == pytest.approx(25.0)
    assert finals[1].energy_saved_pct is None


@pytest.mark.asyncio
async def test_kpi_frames_go_ahead_of_their_tick(make_summary: Summary) -> None:
    frames: list[Frame] = []
    for index in range(4):
        frames += [measured(make_summary("spotcool"), T0 + (index + 1) * STEP_S), tick(index)]
    down, _ = await run_test(KpiProcessor(), frames_to_send=frames)
    before_last_tick = down[down.index(frames[-1]) - 1]
    assert isinstance(before_last_tick, KpiFrame) and before_last_tick.data.provisional


@pytest.mark.asyncio
async def test_without_a_scenario_or_a_baseline_nothing_is_scored(make_summary: Summary) -> None:
    frames: list[Frame] = [measured(make_summary("spotcool"), T0 + STEP_S), tick(0)]
    down, _ = await run_test(KpiProcessor(), frames_to_send=frames)
    assert kpis(down) == [
        KpiData(run="spotcool", boptest={}, comfort_where_people_kh=None, provisional=False)
    ]
