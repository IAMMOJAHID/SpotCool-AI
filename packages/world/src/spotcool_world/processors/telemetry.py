"""TelemetryProcessor: each plant run's state in BMS units, with its power and energy."""

from collections.abc import Mapping
from dataclasses import dataclass
from functools import cache

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
    """A run's cumulative energy: the trapezoid rule over its power samples.

    The first sample (the run's initial state) starts the meter at 0 kWh. A sample that is not
    later than the previous one adds nothing.
    """

    def __init__(self) -> None:
        self.kwh = 0.0
        """Energy since the first sample."""
        self._last: tuple[float, float] | None = None

    def add(self, t_s: float, power_kw: float) -> float:
        """Add the power at a simulation time; return the energy up to that time.

        Args:
            t_s: The sample's simulation time.
            power_kw: The power at that time.
        """
        if self._last is not None and t_s > self._last[0]:
            last_s, last_kw = self._last
            self.kwh += (last_kw + power_kw) / 2 * (t_s - last_s) / 3600
        self._last = (t_s, power_kw)
        return self.kwh


class TelemetryProcessor(FrameProcessor):
    """Fills every ``PlantMeasurementsFrame``'s ``summary``: the run's state in BMS units.

    The summary (``summarize``) carries the run's cumulative energy, integrated by an
    ``EnergyMeter`` over the frames' stamps from the run's initial state (its first frame after
    a ``StartFrame``) on. A frame lacking a value the summary needs keeps ``summary=None`` and
    is reported as a non-fatal error. A ``StartFrame`` resets every meter. Every frame passes on.

    Args:
        name: Display name.
    """

    def __init__(self, *, name: str | None = None) -> None:
        super().__init__(name=name)
        self._meters: dict[Run, EnergyMeter] = {}

    async def process_frame(self, frame: Frame, direction: FrameDirection) -> None:
        """Summarize plant measurements; pass every frame on."""
        await super().process_frame(frame, direction)
        if direction is FrameDirection.DOWNSTREAM:
            if isinstance(frame, StartFrame):
                self._meters = {}
            elif isinstance(frame, PlantMeasurementsFrame):
                await self._summarize(frame)
        await self.push_frame(frame, direction)

    async def _summarize(self, frame: PlantMeasurementsFrame) -> None:
        try:
            summary = summarize(frame.run, frame.raw)
        except KeyError as exc:
            await self.push_error(
                f"{frame.run} measurements at {frame.sim_time_s} s lack {exc}; no summary"
            )
            return
        meter = self._meters.setdefault(frame.run, EnergyMeter())
        t_s = frame.sim_time_s
        energy = meter.kwh if t_s is None else meter.add(t_s, summary.total_power_kw)
        frame.summary = summary.model_copy(update={"energy_kwh": energy})
