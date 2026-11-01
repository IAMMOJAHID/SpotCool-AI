from collections.abc import Callable

import pytest

from spotcool_contracts import Zone
from spotcool_contracts.events import Run
from spotcool_world.frames import ErrorFrame, PlantMeasurementsFrame, StartFrame
from spotcool_world.processors import TelemetryProcessor
from spotcool_world.processors.telemetry import EnergyMeter, power_kw, summarize
from spotcool_world.services.boptest.catalog import bms_signal, load_catalog
from spotcool_world.testing import run_test

Raw = Callable[..., dict[str, float]]
METERS = {
    "AHU-SF-PWR",
    "AHU-CLG-PUMP-PWR",
    "AHU-HTG-PUMP-PWR",
    "CH-PWR",
    "CHW-PUMP-PWR",
    "HP-PWR",
    "HW-PUMP-PWR",
}


def test_a_summary_has_every_zone_and_meter_in_bms_units(make_raw: Raw) -> None:
    raw = make_raw({Zone.SOU: 26.5}, cooling_c=24.0, heating_c=20.0, outdoor_c=31.0)
    raw[bms_signal("ZN-SOU-CO2")] = 812.0
    raw[bms_signal("ZN-SOU-SA-FLOW")] = 0.185  # m3/s
    summary = summarize("spotcool", raw, energy_kwh=3.5)
    assert summary.run == "spotcool"
    assert [zone.zone for zone in summary.zones] == list(Zone)
    south = summary.zones[list(Zone).index(Zone.SOU)]
    assert south.temp_c == pytest.approx(26.5)  # K -> degC
    assert south.cooling_sp_c == pytest.approx(24.0)
    assert south.heating_sp_c == pytest.approx(20.0)
    assert south.co2_ppm == 812.0  # no conversion
    assert south.airflow_lps == pytest.approx(185.0)  # m3/s -> L/s
    assert summary.outdoor_temp_c == pytest.approx(31.0)
    assert summary.energy_kwh == 3.5
    assert set(summary.power_kw) == METERS


def test_the_total_is_the_seven_electric_power_meters_in_kw(make_raw: Raw) -> None:
    raw = make_raw(meter_w=0.0)
    meters = load_catalog().electric_power
    assert len(meters) == 7
    for watts, name in zip([100, 200, 300, 400, 500, 600, 700], meters, strict=True):
        raw[name] = float(watts)
    raw["res_PHvac_y"] = 99_000.0  # not one of BOPTEST's ElectricPower meters: never counted
    assert sum(power_kw(raw).values()) == pytest.approx(2.8)
    assert summarize("baseline", raw).total_power_kw == pytest.approx(2.8)
    assert power_kw(raw)["CH-PWR"] == pytest.approx(raw["chi_reaPChi_y"] / 1000)


def test_a_missing_value_is_a_key_error_and_the_outdoor_temperature_is_optional(
    make_raw: Raw,
) -> None:
    assert summarize("spotcool", make_raw(outdoor_c=None)).outdoor_temp_c is None
    for missing in ("chi_reaPChi_y", bms_signal("ZN-WES-T")):
        raw = make_raw()
        del raw[missing]
        with pytest.raises(KeyError, match=missing):
            summarize("spotcool", raw)


def test_energy_is_the_trapezoid_integral_from_the_first_sample() -> None:
    meter = EnergyMeter()
    profile = [(0.0, 0.0), (900.0, 4.0), (1800.0, 4.0), (2700.0, 0.0)]  # (s, kW)
    assert [meter.add(t_s, kw) for t_s, kw in profile] == pytest.approx([0.0, 0.5, 1.5, 2.0])
    assert meter.add(2700.0, 50.0) == pytest.approx(2.0)  # the same stamp again adds nothing
    assert meter.kwh == pytest.approx(2.0)


def measured(run: Run, t_s: float, raw: dict[str, float]) -> PlantMeasurementsFrame:
    return PlantMeasurementsFrame(run=run, raw=raw, sim_time_s=t_s)


@pytest.mark.asyncio
async def test_the_processor_meters_each_run_from_its_initial_state(make_raw: Raw) -> None:
    one_kw, two_kw = make_raw(meter_w=1000 / 7), make_raw(meter_w=2000 / 7)
    frames = [
        measured("spotcool", 0.0, one_kw),  # the initial states
        measured("baseline", 0.0, two_kw),
        measured("spotcool", 900.0, one_kw),
        measured("baseline", 900.0, two_kw),
        measured("spotcool", 1800.0, one_kw),
    ]
    down, up = await run_test(TelemetryProcessor(), frames_to_send=frames)
    assert down == frames and not up  # every frame passes on, the same object
    energies = [(f.run, f.summary.energy_kwh) for f in frames if f.summary is not None]
    assert energies == [
        ("spotcool", 0.0),
        ("baseline", 0.0),
        ("spotcool", pytest.approx(0.25)),
        ("baseline", pytest.approx(0.5)),
        ("spotcool", pytest.approx(0.5)),
    ]
    assert frames[0].summary is not None
    assert frames[0].summary.total_power_kw == pytest.approx(1.0)


@pytest.mark.asyncio
async def test_a_start_frame_resets_the_meters(make_raw: Raw) -> None:
    raw = make_raw(meter_w=1000 / 7)

    def start() -> StartFrame:
        return StartFrame(
            run_id="t",
            scenario="",
            plant="",
            step_s=900.0,
            speed=None,
            start_time_s=0.0,
            end_time_s=1800.0,
        )

    again = measured("spotcool", 0.0, raw)
    frames = [start(), measured("spotcool", 0.0, raw), measured("spotcool", 900.0, raw)]
    await run_test(TelemetryProcessor(), frames_to_send=[*frames, start(), again], send_start=False)
    assert again.summary is not None and again.summary.energy_kwh == 0.0


@pytest.mark.asyncio
async def test_measurements_without_a_needed_value_keep_no_summary(make_raw: Raw) -> None:
    raw = make_raw()
    del raw[bms_signal("ZN-COR-T")]
    frame = measured("spotcool", 900.0, raw)
    down, up = await run_test(TelemetryProcessor(), frames_to_send=[frame])
    assert down == [frame] and frame.summary is None
    assert len(up) == 1 and isinstance(up[0], ErrorFrame)
    assert not up[0].fatal and "hvac_reaZonCor_TZon_y" in up[0].message
