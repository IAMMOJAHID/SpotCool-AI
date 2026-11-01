"""The PlantService contract: one suite, run on RC always and on BOPTEST with -m live.

Live runs need SPOTCOOL_LIVE_BOPTEST_URL (e.g. http://127.0.0.1:8000); they skip when it is
unset or BOPTEST does not answer within 5 s. Every test stops the test it selected.
"""

import math
import os
from collections.abc import AsyncIterator

import pytest
import pytest_asyncio

from spotcool_contracts import Zone, load_point_map
from spotcool_world.services import PlantService, PlantStartParams
from spotcool_world.services.boptest.catalog import bms_signal, load_catalog, zone_damper
from spotcool_world.services.rc import RcPlant

LIVE_URL_ENV = "SPOTCOOL_LIVE_BOPTEST_URL"
START_S = 198 * 86_400 + 10 * 3600  # 18 July (day 199), 10:00: occupied, cooling at 24 degC
PARAMS = PlantStartParams(start_time_s=START_S, step_s=900.0, warmup_s=86_400.0)
CORE_T = bms_signal("ZN-COR-T")
CORE_SP = bms_signal("ZN-COR-CLG-SP")
CORE_WRITE = bms_signal("ZN-COR-CLG-SP", write=True)
SOUTH_T = bms_signal("ZN-SOU-T")
SOUTH_DAMPER = zone_damper(Zone.SOU)
# Measured on BOPTEST (Nov 1, this PARAMS): South +0.55 K above baseline after the hour; RC +1.6 K.
SOUTH_RISE_K = 0.25
OVERRIDE_K = 301.15  # 28 degC
OCCUPIED_COOLING_K = 297.15  # 24 degC, the building's own schedule
KPI_KEYS = {
    "cost_tot",
    "emis_tot",
    "ener_tot",
    "idis_tot",
    "pdih_tot",
    "pele_tot",
    "pgas_tot",
    "tdis_tot",
    "time_rat",
}


def point_map_reads() -> set[str]:
    bindings = [p.boptest for p in load_point_map("office_bms").points if p.boptest is not None]
    names = [b.read for b in bindings] + [b.relinquish_from_baseline for b in bindings]
    return {name for name in names if name is not None}


def rest_names() -> set[str]:
    catalog = load_catalog()
    activate = {name.removesuffix("_u") + "_activate" for name in catalog.inputs}
    return set(catalog.outputs) | set(catalog.inputs) | activate | {"time"}


async def live_plant() -> PlantService:
    from spotcool_world.services.boptest.client import BoptestClient, BoptestError
    from spotcool_world.services.boptest.plant import BoptestPlant

    url = os.environ.get(LIVE_URL_ENV)
    if not url:
        pytest.skip(f"set {LIVE_URL_ENV} to run live BOPTEST tests")
    probe = BoptestClient(url)
    try:
        await probe.version(timeout_s=5.0)
    except BoptestError as exc:
        pytest.skip(f"BOPTEST at {url} does not answer: {exc}")
    finally:
        await probe.aclose()
    return BoptestPlant(BoptestClient(url))


@pytest_asyncio.fixture(
    params=[pytest.param("rc"), pytest.param("boptest", marks=pytest.mark.live)]
)
async def plant(request: pytest.FixtureRequest) -> AsyncIterator[PlantService]:
    service = RcPlant() if request.param == "rc" else await live_plant()
    try:
        yield service
    finally:
        await service.stop()


@pytest.mark.asyncio
async def test_every_point_map_read_is_measured(plant: PlantService) -> None:
    first = await plant.start(PARAMS)
    raw = await plant.step({})
    reads = point_map_reads()
    assert reads <= first.keys()
    assert reads <= raw.keys()
    assert raw.keys() <= rest_names()  # BOPTEST's REST names only
    assert all(math.isfinite(value) for value in raw.values())
    assert plant.step_s == PARAMS.step_s


@pytest.mark.asyncio
async def test_overrides_hold_then_release(plant: PlantService) -> None:
    await plant.start(PARAMS)
    baseline = [await plant.step({}) for _ in range(4)]
    await plant.start(PARAMS)  # the same start again, now with the overrides
    held = {CORE_WRITE: OVERRIDE_K, SOUTH_DAMPER: 0.0}
    overridden = [await plant.step(held) for _ in range(4)]
    released = await plant.step(dict.fromkeys(held))

    assert [raw[CORE_SP] for raw in overridden] == pytest.approx([OVERRIDE_K] * 4)
    assert [raw[SOUTH_DAMPER] for raw in overridden] == pytest.approx([0.0] * 4)
    assert released[CORE_SP] == pytest.approx(OCCUPIED_COOLING_K)
    assert released[SOUTH_DAMPER] > 0  # the VAV box controls it again
    # BOPTEST's core sits near minimum airflow, so a setpoint alone may not warm it in an hour.
    assert overridden[-1][CORE_T] >= baseline[-1][CORE_T] - 0.05
    # A closed damper is the lever that works: the zone floats up.
    assert overridden[-1][SOUTH_T] > baseline[-1][SOUTH_T] + SOUTH_RISE_K


@pytest.mark.asyncio
async def test_kpis_have_the_boptest_keys(plant: PlantService) -> None:
    await plant.start(PARAMS)
    await plant.step({})
    kpis = await plant.kpis()
    assert kpis.keys() == KPI_KEYS
    ener_tot = kpis["ener_tot"]
    assert ener_tot is not None and ener_tot >= 0


@pytest.mark.asyncio
async def test_unknown_inputs_are_rejected(plant: PlantService) -> None:
    await plant.start(PARAMS)
    with pytest.raises(ValueError, match="unknown BOPTEST inputs"):
        await plant.step({"hvac_oveZonSupCor_TZonCooSet_y": OVERRIDE_K})  # an output


@pytest.mark.asyncio
async def test_stop_is_idempotent(plant: PlantService) -> None:
    await plant.start(PARAMS)
    await plant.stop()
    await plant.stop()
    with pytest.raises(RuntimeError):
        await plant.step({})


@pytest.mark.asyncio
async def test_rc_core_override_warms_the_core() -> None:
    plant = RcPlant()
    await plant.start(PARAMS)
    baseline = [await plant.step({}) for _ in range(4)]
    await plant.start(PARAMS)
    overridden = [await plant.step({CORE_WRITE: OVERRIDE_K}) for _ in range(4)]
    assert overridden[-1][CORE_T] > baseline[-1][CORE_T] + 0.1  # +0.22 K at minimum airflow
