"""TelemetryProcessor: each plant run's state in BMS units, with its power and energy."""

from collections.abc import Mapping
from dataclasses import dataclass
from functools import cache

from loguru import logger

from spotcool_contracts import Zone, load_point_map
from spotcool_contracts.events import PlantMeasurementsData, Run, ZoneMeasurement
from spotcool_contracts.points import Conversion, PointSpec
from spotcool_world.adapters.units import to_bms
from spotcool_world.frames import Frame, PlantMeasurementsFrame, StartFrame
from spotcool_world.processors.frame_processor import FrameDirection, FrameProcessor
from spotcool_world.services.boptest.catalog import load_catalog

_Read = tuple[str, Conversion]  # the BOPTEST key a point reads, and its conversion to BMS units


@dataclass(frozen=True)
class _Reads:
    zones: dict[Zone, tuple[_Read, _Read, _Read, _Read, _Read]]  # T, CLG-SP, HTG-SP, CO2, SA-FLOW
    meters: dict[str, _Read]  # BMS point name -> read
    outdoor: _Read


def _read(point: PointSpec) -> _Read:
    if point.boptest is None or point.boptest.read is None:
        raise KeyError(f"office_bms point {point.name} reads no BOPTEST signal")
    return point.boptest.read, point.boptest.conversion


@cache
def _reads() -> _Reads:
    """The point map's reads that a summary needs (cached, shared)."""
    points = load_point_map("office_bms")

    def zone(zone: Zone, field: str) -> _Read:
        return _read(points.by_name(f"ZN-{zone.value.upper()}-{field}"))

    meters = set(load_catalog().electric_power)
    return _Reads(
        zones={
            z: (
                zone(z, "T"),
                zone(z, "CLG-SP"),
                zone(z, "HTG-SP"),
                zone(z, "CO2"),
                zone(z, "SA-FLOW"),
            )
            for z in Zone
        },
        meters={
            p.name: _read(p)
            for p in points.points
            if p.boptest is not None and p.boptest.read in meters
        },
        outdoor=_read(points.by_name("OA-T")),
    )


def _value(raw: Mapping[str, float], read: _Read) -> float:
    key, conversion = read
    return to_bms(float(raw[key]), conversion)


def power_kw(raw: Mapping[str, float]) -> dict[str, float]:
    """The seven HVAC meters in kW, by BMS point name: BOPTEST's ElectricPower basis.

    After a step, plants report each meter's mean over the step; a run's initial state has no
    step, so its meters are instantaneous.

    Args:
        raw: A plant's outputs, in BOPTEST names and units.

    Raises:
        KeyError: If a meter is missing from ``raw``.
    """
    return {name: _value(raw, read) for name, read in _reads().meters.items()}


def summarize(run: Run, raw: Mapping[str, float], energy_kwh: float = 0.0) -> PlantMeasurementsData:
    """A plant run's outputs in BMS units, through the office BMS point map.

    Args:
        run: Which run produced ``raw``.
        raw: The run's outputs, in BOPTEST names and units.
        energy_kwh: The run's cumulative energy, which ``raw`` alone cannot tell.

    Returns:
        Every zone (temperature, setpoints, CO2, airflow), the meters and their total, the
        energy and the outdoor temperature (``None`` if ``raw`` lacks it).

    Raises:
        KeyError: If ``raw`` lacks a zone value or a meter.
    """
    reads = _reads()
    zones = [
        ZoneMeasurement(
            zone=zone,
            temp_c=_value(raw, temp),
            cooling_sp_c=_value(raw, cooling),
            heating_sp_c=_value(raw, heating),
            co2_ppm=_value(raw, co2),
            airflow_lps=_value(raw, airflow),
        )
        for zone, (temp, cooling, heating, co2, airflow) in reads.zones.items()
    ]
    power = power_kw(raw)
    outdoor_key, _ = reads.outdoor
    return PlantMeasurementsData(
        run=run,
        zones=zones,
        power_kw=power,
        total_power_kw=sum(power.values()),
        energy_kwh=energy_kwh,
        outdoor_temp_c=_value(raw, reads.outdoor) if outdoor_key in raw else None,
    )


class EnergyMeter:
    """A run's cumulative energy: each step's mean power times the step, from the first step.

    A sample stamped ``t`` is the mean power over the step that ends at ``t``, so it adds that
    power times the time since the previous sample: one rectangle per step. The first sample
    (the run's initial state, which has no step) starts the meter at 0 kWh, and its power is
    never counted. A sample that is not later than the previous one adds nothing.
    """

    def __init__(self) -> None:
        self.kwh = 0.0
        """Energy since the first sample."""
        self._last_s: float | None = None

    def add(self, t_s: float, power_kw: float) -> float:
        """Add the mean power over the step that ends at ``t_s``; return the energy up to then.

        Args:
            t_s: The sample's simulation time: the end of its step.
            power_kw: The mean power over that step.
        """
        if self._last_s is None:
            self._last_s = t_s
        elif t_s > self._last_s:
            self.kwh += power_kw * (t_s - self._last_s) / 3600
            self._last_s = t_s
        return self.kwh


def metered_summary(
    run: Run, raw: Mapping[str, float], t_s: float | None, meter: EnergyMeter
) -> PlantMeasurementsData:
    """``summarize`` with the run's energy, after ``meter`` has counted ``raw``'s power.

    The meter counts every stamped ``raw`` that has all seven meters, even one the summary
    cannot be built from, so a run's energy starts at its first frame with the meters.

    Args:
        run: Which run produced ``raw``.
        raw: The run's outputs, in BOPTEST names and units.
        t_s: The stamp of ``raw``; an unstamped one adds no energy.
        meter: The run's energy meter.

    Raises:
        KeyError: If ``raw`` lacks a value the summary needs (BOPTEST leaves out values it
            reports as null, such as setpoints in an initial state).
    """
    try:
        power = sum(power_kw(raw).values())
    except KeyError:
        energy = meter.kwh
    else:
        energy = meter.kwh if t_s is None else meter.add(t_s, power)
    return summarize(run, raw, energy)


class TelemetryProcessor(FrameProcessor):
    """Fills every ``PlantMeasurementsFrame``'s ``summary``: the run's state in BMS units.

    The summary (``metered_summary``) carries the run's cumulative energy: an ``EnergyMeter``
    adds each step's mean power times the step, from the run's first frame with the seven meters
    (normally its initial state, the first frame after a ``StartFrame``) on. A frame lacking a
    value the summary needs keeps ``summary=None``; that is logged once per run. A
    ``StartFrame`` starts afresh. Every frame passes on.

    Args:
        name: Display name.
    """

    def __init__(self, *, name: str | None = None) -> None:
        super().__init__(name=name)
        self._meters: dict[Run, EnergyMeter] = {}
        self._incomplete: set[Run] = set()  # runs whose missing values were logged

    async def process_frame(self, frame: Frame, direction: FrameDirection) -> None:
        """Summarize plant measurements; pass every frame on."""
        await super().process_frame(frame, direction)
        if direction is FrameDirection.DOWNSTREAM:
            if isinstance(frame, StartFrame):
                self._meters, self._incomplete = {}, set()
            elif isinstance(frame, PlantMeasurementsFrame):
                self._summarize(frame)
        await self.push_frame(frame, direction)

    def _summarize(self, frame: PlantMeasurementsFrame) -> None:
        meter = self._meters.setdefault(frame.run, EnergyMeter())
        try:
            frame.summary = metered_summary(frame.run, frame.raw, frame.sim_time_s, meter)
        except KeyError as exc:
            if frame.run not in self._incomplete:
                self._incomplete.add(frame.run)
                logger.warning(
                    f"{self.name}: {frame.run} measurements at {frame.sim_time_s} s lack {exc}; "
                    "frames without every value get no summary (logged once per run)"
                )
