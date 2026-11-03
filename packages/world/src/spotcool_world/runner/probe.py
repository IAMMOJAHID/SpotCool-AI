"""``spotcool-world probe``: the go/no-go check of a plant, BOPTEST above all.

It starts the plant on the settings' BOPTEST scenario (select and scenario on BOPTEST), prints
the catalogs' sizes, steps with the core zone's cooling setpoint overridden to 28 degC, prints
each call's latency and the KPIs, and always stops the plant.
"""

import time
from collections import defaultdict
from collections.abc import Sequence

from spotcool_contracts.points import Conversion
from spotcool_world.adapters.units import to_bms, to_boptest
from spotcool_world.clocks import SimClock
from spotcool_world.services.boptest.catalog import bms_signal, load_catalog
from spotcool_world.services.plant_factory import create_plant
from spotcool_world.services.plant_service import PlantStartParams
from spotcool_world.services.settings import WorldSettings

PROBE_SETPOINT_C = 28.0
"""Cooling setpoint the probe writes into the core zone."""

_K = Conversion.KELVIN_TO_CELSIUS


async def probe(settings: WorldSettings, steps: int) -> None:
    """Probe the plant ``settings.plant`` names, printing as it goes.

    Args:
        settings: Plant, BOPTEST URLs, test case, time period and step.
        steps: Steps to take with the core override.

    Raises:
        Exception: Whatever made the plant fail; it has been stopped by then.
    """
    catalog = load_catalog()
    core_temp, core_sp = bms_signal("ZN-COR-T"), bms_signal("ZN-COR-CLG-SP")
    override = {bms_signal("ZN-COR-CLG-SP", write=True): to_boptest(PROBE_SETPOINT_C, _K)}
    start_s = catalog.period_start_s(settings.time_period)
    plant = create_plant(settings, "spotcool")
    boptest = None
    if settings.plant.startswith("boptest"):
        from spotcool_world.services.boptest.plant import BoptestPlant  # needs the boptest extra

        assert isinstance(plant, BoptestPlant)
        boptest = plant
    print(
        f"probe: {settings.plant} plant, {settings.testcase}, {settings.time_period} from day "
        f"{SimClock.day_of_year(start_s)} {SimClock.clock_text(start_s)}, step {settings.step_s:.0f} s"
    )
    timings: list[tuple[str, float]] = []
    try:
        started = time.perf_counter()
        params = PlantStartParams(
            start_time_s=start_s, step_s=settings.step_s, time_period=settings.time_period
        )
        await plant.start(params)
        timings.append(("start", time.perf_counter() - started))
        print(f"catalog snapshot: {len(catalog.inputs)} inputs, {len(catalog.outputs)} outputs")
        if boptest is not None and boptest.testid is not None:
            inputs = await boptest.client.inputs(boptest.testid)
            measurements = await boptest.client.measurements(boptest.testid)
            print(
                f"BOPTEST test {boptest.testid}: {len(inputs)} inputs, {len(measurements)} outputs"
            )
        for index in range(1, steps + 1):
            started = time.perf_counter()
            raw = await plant.step(override)
            elapsed = time.perf_counter() - started
            timings.append(("step", elapsed))
            hvac_kw = sum(raw.get(name, 0.0) for name in catalog.electric_power) / 1000
            end_s = raw.get("time", start_s + index * settings.step_s)
            print(
                f"step {index}  {SimClock.clock_text(end_s)}  core {to_bms(raw[core_temp], _K):.2f} "
                f"degC  cooling sp {to_bms(raw[core_sp], _K):.2f} degC  HVAC {hvac_kw:.1f} kW  "
                f"{elapsed * 1000:.0f} ms"
            )
        started = time.perf_counter()
        kpis = await plant.kpis()
        timings.append(("kpis", time.perf_counter() - started))
        print(
            "KPIs: "
            + ", ".join(f"{k}={'n/a' if v is None else f'{v:.4g}'}" for k, v in kpis.items())
        )
    finally:
        started = time.perf_counter()
        await plant.stop()
        timings.append(("stop", time.perf_counter() - started))
        _print_latencies("plant calls", timings)
        if boptest is not None:
            _print_latencies("BOPTEST calls", boptest.client.calls)


def _print_latencies(title: str, timings: Sequence[tuple[str, float]]) -> None:
    by_call: dict[str, list[float]] = defaultdict(list)
    for call, seconds in timings:
        by_call[call].append(seconds)
    print(f"{title} (wall time):")
    for call, seconds in by_call.items():
        mean = sum(seconds) / len(seconds)
        print(f"  {call:<20} {len(seconds):>3} x  mean {mean:7.3f} s  max {max(seconds):7.3f} s")
