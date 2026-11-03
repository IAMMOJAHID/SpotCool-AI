import pytest

from spotcool_contracts import Zone
from spotcool_world.services import PlantStartParams
from spotcool_world.services.boptest.catalog import bms_signal, load_catalog, zone_damper
from spotcool_world.services.rc import RcPlant, weekday_occupancy
from spotcool_world.services.rc.plant import _CAPACITANCE

DAY_S = 198 * 86_400  # 18 July (day 199), 00:00
START = PlantStartParams(start_time_s=DAY_S + 10 * 3600, step_s=900.0, warmup_s=6 * 3600)
CORE_T = bms_signal("ZN-COR-T")
CORE_SP = bms_signal("ZN-COR-CLG-SP")
CORE_FLOW = bms_signal("ZN-COR-SA-FLOW")
CORE_DAMPER = zone_damper(Zone.COR)
WEST_DAMPER = zone_damper(Zone.WES)
FAN_W = bms_signal("AHU-SF-PWR")
METERS = load_catalog().electric_power
CORE_MIN_FLOW, CORE_MIN_DAMPER = 0.879, 0.174  # BOPTEST's core VAV box


@pytest.mark.asyncio
async def test_people_heat_warms_a_zone_as_the_cameras_would_count_it() -> None:
    # At night the AHU is off, so the core floats: 50 people add their 120 W each.
    night = PlantStartParams(start_time_s=DAY_S + 21 * 3600, step_s=900.0)
    crowded, empty = RcPlant(lambda _: {Zone.COR: 50}), RcPlant(lambda _: {})
    await crowded.start(night)
    await empty.start(night)
    for _ in range(3):
        await crowded.step({})
        await empty.step({})
    rise = (await crowded.step({}))[CORE_T] - (await empty.step({}))[CORE_T]
    assert rise == pytest.approx(50 * 120 * 3600 / _CAPACITANCE[0], rel=0.01)


@pytest.mark.asyncio
async def test_vav_box_modulates_floats_at_minimum_and_leaks_when_closed() -> None:
    plant = RcPlant()
    await plant.start(START)
    holding = await plant.step({})  # 10:00, occupied: the box holds 24 degC
    assert holding[CORE_T] == pytest.approx(297.15, abs=0.01)
    assert CORE_MIN_FLOW < holding[CORE_FLOW] < CORE_MIN_FLOW / CORE_MIN_DAMPER
    assert holding[CORE_DAMPER] == pytest.approx(CORE_MIN_DAMPER * holding[CORE_FLOW] / 0.879)

    floating = await plant.step({bms_signal("ZN-COR-CLG-SP", write=True): 301.15})
    assert floating[CORE_FLOW] == pytest.approx(CORE_MIN_FLOW)
    assert floating[CORE_DAMPER] == pytest.approx(CORE_MIN_DAMPER)

    closed = await plant.step({CORE_DAMPER: 0.0})
    assert closed[CORE_DAMPER] == 0.0
    assert closed[CORE_FLOW] == pytest.approx(0.14 * CORE_MIN_FLOW)  # leakage

    half = await plant.step({CORE_DAMPER: 0.5})
    assert half[CORE_FLOW] == pytest.approx(CORE_MIN_FLOW * 0.5 / CORE_MIN_DAMPER)


@pytest.mark.asyncio
async def test_the_ahu_is_off_at_night_on_its_own_schedule() -> None:
    plant = RcPlant()
    await plant.start(PlantStartParams(start_time_s=DAY_S + 2 * 3600, step_s=900.0, warmup_s=3600))
    raw = await plant.step({})
    assert (raw[CORE_FLOW], raw[FAN_W]) == (0.0, 0.0)
    assert raw[CORE_SP] == pytest.approx(303.15)  # 30 degC unoccupied
    assert raw[CORE_DAMPER] == pytest.approx(CORE_MIN_DAMPER)


@pytest.mark.asyncio
async def test_overrides_are_clipped_to_the_catalog_range_like_boptest() -> None:
    plant = RcPlant()
    await plant.start(START)
    raw = await plant.step({bms_signal("ZN-COR-CLG-SP", write=True): 250.0, CORE_DAMPER: 1.5})
    assert (raw[CORE_SP], raw[CORE_DAMPER]) == (285.15, 1.0)


@pytest.mark.asyncio
async def test_the_same_inputs_give_the_same_outputs() -> None:
    first, second = RcPlant(), RcPlant()
    await first.start(START)
    await second.start(START)
    for _ in range(3):
        assert await first.step({CORE_DAMPER: 0.0}) == await second.step({CORE_DAMPER: 0.0})


@pytest.mark.asyncio
async def test_kpis_count_the_steps_not_the_warm_up() -> None:
    plant = RcPlant()
    await plant.start(START)
    assert (await plant.kpis())["ener_tot"] == 0.0
    for _ in range(4):
        await plant.step({})
    kpis = await plant.kpis()
    ener_tot, pele_tot, tdis_tot = kpis["ener_tot"], kpis["pele_tot"], kpis["tdis_tot"]
    assert ener_tot is not None and ener_tot > 0
    assert pele_tot is not None and pele_tot > 0
    assert tdis_tot == pytest.approx(0.0, abs=1e-3)  # held inside the schedule's band


@pytest.mark.asyncio
async def test_each_meter_is_its_energy_over_the_sub_steps_divided_by_the_step() -> None:
    # One 15-minute step against fifteen 1-minute steps of one sub-step each, from the same
    # state: West's damper reopens after half an hour shut, so the power moves inside the step.
    whole, minutes = RcPlant(), RcPlant()
    await whole.start(START)
    await minutes.start(START.model_copy(update={"step_s": 60.0}))
    for _ in range(2):
        await whole.step({WEST_DAMPER: 0.0})
    for _ in range(30):
        await minutes.step({WEST_DAMPER: 0.0})
    step = await whole.step({})
    sub_steps = [await minutes.step({}) for _ in range(15)]
    for name in METERS:
        assert step[name] == pytest.approx(sum(raw[name] for raw in sub_steps) / 15)
    end_kw = sum(sub_steps[-1][name] for name in METERS)
    assert sum(step[name] for name in METERS) != pytest.approx(end_kw)  # not the end's power
    assert step[CORE_T] == pytest.approx(sub_steps[-1][CORE_T])  # the rest is the end's


def test_weekday_occupancy() -> None:
    assert set(weekday_occupancy(DAY_S + 3 * 3600).values()) == {0}
    morning = weekday_occupancy(DAY_S + 10 * 3600)
    assert morning[Zone.COR] == 47  # 95 % of 0.05 people/m2 on 985 m2
    assert set(morning) == set(Zone)
