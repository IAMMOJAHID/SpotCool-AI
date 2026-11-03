"""05: One plant step: the building's physics behind a run.

A PlantService starts a plant, steps it and stops it, in BOPTEST's names and units (kelvin,
watts). Here the Core zone's cooling setpoint is raised to 26 degC for one 15-minute step on
the peak cooling day. The RC plant always runs; set SPOTCOOL_LIVE_BOPTEST_URL (for example
http://127.0.0.1:8000) to step a real BOPTEST test case the same way. Its select and its
one-day warm-up take about 20 s. A plant is always stopped, so no BOPTEST test is left
running.

Run: uv run python packages/world/examples/foundational/05_boptest_step.py
"""

import asyncio
import os

from spotcool_contracts.points import Conversion
from spotcool_world.adapters.units import to_bms, to_boptest
from spotcool_world.services import PlantService, PlantStartParams
from spotcool_world.services.boptest.catalog import bms_signal, load_catalog
from spotcool_world.services.rc import RcPlant

K = Conversion.KELVIN_TO_CELSIUS
START_S = 198 * 86_400 + 10 * 3600  # 18 July (day 199), 10:00
CORE_T = bms_signal("ZN-COR-T")
CORE_SP = bms_signal("ZN-COR-CLG-SP")
CORE_SP_WRITE = bms_signal("ZN-COR-CLG-SP", write=True)


async def one_step(label: str, plant: PlantService) -> None:
    try:
        await plant.start(PlantStartParams(start_time_s=START_S, step_s=900, warmup_s=86_400))
        raw = await plant.step({CORE_SP_WRITE: to_boptest(26.0, K)})
        hvac_kw = sum(raw[name] for name in load_catalog().electric_power) / 1000
        print(
            f"{label:<8} core {to_bms(raw[CORE_T], K):.2f} degC, cooling setpoint "
            f"{to_bms(raw[CORE_SP], K):.1f} degC, HVAC {hvac_kw:.1f} kW ({len(raw)} values)"
        )
    finally:
        await plant.stop()


async def main() -> None:
    await one_step("RC", RcPlant())
    url = os.environ.get("SPOTCOOL_LIVE_BOPTEST_URL")
    if url:
        from spotcool_world.services.boptest.client import BoptestClient
        from spotcool_world.services.boptest.plant import BoptestPlant

        await one_step("BOPTEST", BoptestPlant(BoptestClient(url)))
    else:
        print("BOPTEST  skipped: set SPOTCOOL_LIVE_BOPTEST_URL to step a real test case")


if __name__ == "__main__":
    asyncio.run(main())
