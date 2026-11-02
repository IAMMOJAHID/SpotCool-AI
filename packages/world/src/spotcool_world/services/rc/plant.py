"""RcPlant: a numpy resistance-capacitance model of the five-zone office.

The RC plant is the network-free stand-in for BOPTEST ``multizone_office_simple_air``, so
tests and demos never depend on a server. It answers under BOPTEST's REST keys and units for
every point the office BMS map reads: zone temperatures in K, CO2 in ppm, VAV airflow in
m3/s, the seven power meters in W, outdoor air temperature (K) and humidity (fraction) and
AHU supply-air temperature (K). The zone setpoints (K), zone dampers and AHU fan speed
(fractions) come under their inputs' ``_u`` keys, as BOPTEST reports overwritten signals.
Nothing downstream can tell it from BOPTEST by the names. After a step, each power meter is its
mean over the step (its energy over the 60-second sub-steps divided by the step), as on the
BOPTEST plant; every other value is the one at the end of the step.

Model, one 1R1C node per zone (zones do not exchange heat):

- Zones: the DOE medium-office floor (core 983 m2, north and south 207, east and west 131),
  scaled to BOPTEST's 1,662.66 m2. Capacitance 80 kJ/(m2 K); envelope conductance per facade.
- Gains: about 120 W sensible per person from ``occupancy(sim_time_s)`` (the scenario's people
  counts), lighting and plug loads (12 W/m2 while the building is scheduled occupied, 3 W/m2
  otherwise), and a noon-peaked solar gain on the perimeter zones.
- Outdoor air: a peak-cooling-day sine for Chicago, 22 degC at 03:00 to 33 degC at 15:00, on
  every day of the year.
- VAV boxes, calibrated to BOPTEST: supply air at 12 degC. Each zone's airflow modulates
  between BOPTEST's minimum (879, 185, 185, 154 and 120 L/s) and its maximum to hold the
  cooling setpoint; at minimum airflow the zone floats. Flow is proportional to the damper
  position, whose minimum is 0.174 (cor, nor, sou) or 0.15 (eas, wes) and fully open is 1. A
  damper override (``hvac_oveZonAct<Z>_yDam_u``) sets the position; closed, the box leaks 14 %
  of its minimum flow. Reheat holds the heating setpoint. The AHU runs while the building is
  scheduled occupied, and otherwise only when a zone leaves its setpoints.
- Plant: the coil cools the supply air and 20 % outdoor air; chiller electric power is the coil
  load over COP 3.5; fan power is cubic in airflow (9 kW with every box fully open); pumps take
  a fixed share of the coil and heating loads.
- CO2: 400 ppm outdoors and 5.2e-6 m3/s per person, diluted by outdoor air (20 % of the supply
  while occupied) and 0.2 air changes per hour of infiltration.

Baseline schedule, the RC stand-in for BOPTEST's own controller, reported under the setpoints'
``_u`` keys: cooling 24 degC while occupied (06:00-19:00, as measured on BOPTEST) and 30 degC
otherwise; heating 20 degC and 12 degC (BOPTEST's values; 20 degC read live while occupied).
These are the Modelica Buildings G36 defaults. Overrides of the zone cooling and heating setpoints
and of the zone dampers (clipped to the catalog range, as BOPTEST does) apply for the step;
every other catalog input is accepted and ignored.

Calibration note: the RC plant is deliberately simple. It exists for speed and for the honesty
bracket, where its "people heat" matches the cameras. It does not replace BOPTEST.
"""

import math
from collections import defaultdict
from collections.abc import Callable, Mapping

import numpy as np
import numpy.typing as npt

from spotcool_contracts import Zone
from spotcool_world.adapters.units import KELVIN_OFFSET
from spotcool_world.services.boptest.catalog import bms_signal, load_catalog, zone_damper
from spotcool_world.services.plant_service import PlantService, PlantStartParams

Occupancy = Callable[[float], Mapping[Zone, int]]
"""People in each zone at a simulation time (warm-up times included); missing zones are empty."""

_Array = npt.NDArray[np.float64]

_ZONES = list(Zone)
_AREA_SHARES = np.array([983.0, 207.0, 207.0, 131.0, 131.0])  # in Zone order
_AREAS: _Array = _AREA_SHARES * (load_catalog().area_m2 / _AREA_SHARES.sum())
_CAPACITANCE = 80e3 * _AREAS  # J/K: air, furnishings and light structure
_UA = np.array([50.0, 257.0, 257.0, 171.0, 171.0])  # W/K: 33 % glazed facades; core via roof
_SOLAR_PEAK_W = np.array([0.0, 1500.0, 3000.0, 3000.0, 3000.0])
_V_MIN = np.array([0.879, 0.185, 0.185, 0.154, 0.120])  # m3/s, BOPTEST's minimum flows
_DAMPER_MIN = np.array([0.174, 0.174, 0.174, 0.15, 0.15])  # BOPTEST's minimum positions
_V_MAX = _V_MIN / _DAMPER_MIN  # flow is proportional to the damper position
_V_DESIGN = float(_V_MAX.sum())
_LEAKAGE = 0.14  # flow through a closed damper, as a fraction of the minimum flow
_VOLUME = _AREAS * 2.74  # m3, floor to ceiling
_RHO_CP = 1.2 * 1005.0  # J/(m3 K), air
_SUPPLY_K = 12.0 + KELVIN_OFFSET
_Q_HEAT_MAX = 50.0 * _AREAS
_OA_FRACTION = 0.2
_INFILTRATION = 0.2 * _VOLUME / 3600  # m3/s
_PEOPLE_W = 120.0
_EQUIPMENT_W_PER_M2 = (12.0, 3.0)  # occupied, unoccupied
_FAN_DESIGN_W = 9000.0
_CHILLER_COP = 3.5
_HEAT_PUMP_COP = 3.0
_CO2_OUT_PPM = 400.0
_CO2_PER_PERSON = 5.2e-6  # m3/s
_PEOPLE_PER_M2 = 0.05  # BOPTEST's design occupancy density
_OCCUPIED_H = (6.0, 19.0)
_COOLING_SP = (24.0 + KELVIN_OFFSET, 30.0 + KELVIN_OFFSET)  # occupied, unoccupied
_HEATING_SP = (20.0 + KELVIN_OFFSET, 12.0 + KELVIN_OFFSET)  # occupied, unoccupied
_SUBSTEP_S = 60.0
_WEEKDAY = (
    (7.0, 0.3),
    (8.0, 0.95),
    (12.0, 0.5),
    (13.0, 0.95),
    (17.0, 0.5),
    (18.0, 0.1),
    (19.0, 0.0),
)


def _zone_reads(suffix: str) -> list[str]:
    return [bms_signal(f"ZN-{zone.value.upper()}-{suffix}") for zone in _ZONES]


_TEMP_Y = _zone_reads("T")
# BOPTEST's REST API reports an overwritten signal under its input's _u key, so these keys
# are both what the point map reads and the inputs that override the setpoints.
_COOL_SP = _zone_reads("CLG-SP")
_HEAT_SP = _zone_reads("HTG-SP")
_DAMPERS = [zone_damper(zone) for zone in _ZONES]
_CO2_Y = _zone_reads("CO2")
_FLOW_Y = _zone_reads("SA-FLOW")
_FAN_W_Y = bms_signal("AHU-SF-PWR")
_AHU_COOL_PUMP_W_Y = bms_signal("AHU-CLG-PUMP-PWR")
_AHU_HEAT_PUMP_W_Y = bms_signal("AHU-HTG-PUMP-PWR")
_CHILLER_W_Y = bms_signal("CH-PWR")
_CHW_PUMP_W_Y = bms_signal("CHW-PUMP-PWR")
_HEAT_PUMP_W_Y = bms_signal("HP-PWR")
_HW_PUMP_W_Y = bms_signal("HW-PUMP-PWR")
_OUTDOOR_T_Y = bms_signal("OA-T")
_OUTDOOR_RH_Y = bms_signal("OA-RH")
_SUPPLY_T_Y = bms_signal("AHU-SAT")
_FAN_SPEED = bms_signal("AHU-SF-SPD")


def weekday_occupancy(sim_time_s: float) -> dict[Zone, int]:
    """A weekday office day at BOPTEST's design density (0.05 people/m2), the same every day.

    Args:
        sim_time_s: Simulation time.

    Returns:
        People per zone: none before 07:00 or from 19:00, 95 % of design mid-morning and
        mid-afternoon, half at lunch.
    """
    hour = sim_time_s % 86_400 / 3600
    fraction = 0.0
    for start_h, value in _WEEKDAY:
        if hour >= start_h:
            fraction = value
    return {
        zone: round(_PEOPLE_PER_M2 * area * fraction)
        for zone, area in zip(_ZONES, _AREAS.tolist(), strict=True)
    }


class RcPlant(PlantService):
    """The five-zone office as a numpy RC model (see the module docstring).

    Args:
        occupancy: People in each zone at a simulation time. Defaults to ``weekday_occupancy``;
            the world passes the scenario's people counts.
    """

    def __init__(self, occupancy: Occupancy = weekday_occupancy) -> None:
        self._occupancy = occupancy
        self._catalog = load_catalog()
        self._step_s: float | None = None
        self._time_s = 0.0
        self._temp_k: _Array = np.full(len(_ZONES), _COOLING_SP[0])
        self._co2_ppm: _Array = np.full(len(_ZONES), _CO2_OUT_PPM)
        self._overrides: dict[str, float] = {}
        self._outputs: dict[str, float] = {}
        self._energy_j = 0.0
        self._discomfort_kh = 0.0
        self._peak_w = 0.0

    @property
    def step_s(self) -> float:
        """Simulation seconds each ``step()`` advances (set by ``start``)."""
        if self._step_s is None:
            raise RuntimeError("the plant has not been started, or has been stopped")
        return self._step_s

    async def start(self, params: PlantStartParams) -> dict[str, float]:
        """Reset the building to 24 degC and 400 ppm, then run the warm-up on its own schedule.

        Args:
            params: Start time, step and warm-up; ``time_period`` is ignored (the weather is
                always the peak cooling day).

        Returns:
            The measurements at ``params.start_time_s``. Without a warm-up, no cooling or
            heating has been delivered yet.
        """
        self._step_s = params.step_s
        self._time_s = max(0.0, params.start_time_s - params.warmup_s)
        self._temp_k = np.full(len(_ZONES), _COOLING_SP[0])
        self._co2_ppm = np.full(len(_ZONES), _CO2_OUT_PPM)
        self._overrides = {}
        if self._time_s < params.start_time_s:
            self._advance(params.start_time_s - self._time_s)
        else:
            self._substep(0.0)
        self._energy_j = self._discomfort_kh = self._peak_w = 0.0
        return dict(self._outputs)

    async def step(self, overrides: Mapping[str, float | None]) -> dict[str, float]:
        """Advance one step with the given overrides.

        Args:
            overrides: Input name (``..._u``) to value; ``None`` or leaving it out releases it.

        Returns:
            The measurements at the end of the step, the power meters' means over the step.

        Raises:
            ValueError: If an override is not an input of the test case.
        """
        step_s = self.step_s
        self._catalog.check_overrides(overrides)
        self._overrides = {
            name: self._clip(name, value) for name, value in overrides.items() if value is not None
        }
        means = {name: joules / step_s for name, joules in self._advance(step_s).items()}
        self._outputs.update(means)
        self._peak_w = max(self._peak_w, sum(means.values()))
        return dict(self._outputs)

    async def kpis(self) -> dict[str, float | None]:
        """BOPTEST's core KPIs where the model has them (energy, comfort, peak); others ``None``.

        ``ener_tot`` is kWh/m2, ``tdis_tot`` K h per zone outside the schedule's band, and
        ``pele_tot`` the highest step-mean electric demand in kW/m2, as BOPTEST defines them.
        """
        area = self._catalog.area_m2
        return {
            "tdis_tot": self._discomfort_kh / len(_ZONES),
            "idis_tot": None,
            "ener_tot": self._energy_j / 3.6e6 / area,
            "cost_tot": None,
            "emis_tot": None,
            "pele_tot": self._peak_w / 1000 / area,
            "pgas_tot": None,
            "pdih_tot": None,
            "time_rat": None,
        }

    async def stop(self) -> None:
        """Stop stepping; ``start`` begins again. Safe to call twice."""
        self._step_s = None

    def _clip(self, name: str, value: float) -> float:
        point = self._catalog.inputs[name]
        low = -math.inf if point.min is None else point.min
        high = math.inf if point.max is None else point.max
        return min(max(value, low), high)

    def _inputs(self, names: list[str], default: float) -> _Array:
        return np.array([self._overrides.get(name, default) for name in names])

    def _advance(self, duration_s: float) -> dict[str, float]:
        """Run ``duration_s`` in sub-steps; return each power meter's energy over them (J)."""
        end_s = self._time_s + duration_s
        count = max(1, math.ceil(duration_s / _SUBSTEP_S - 1e-9))
        dt = duration_s / count
        energy_j: defaultdict[str, float] = defaultdict(float)
        for _ in range(count):
            for name, watts in self._substep(dt).items():
                energy_j[name] += watts * dt
        self._time_s = self._outputs["time"] = end_s  # exact, whatever the sub-steps summed to
        return energy_j

    def _substep(self, dt: float) -> dict[str, float]:
        """Advance ``dt`` seconds; 0 only reports the state, with nothing delivered yet.

        Returns:
            Each power meter's power over the sub-step (W).
        """
        mid_s = self._time_s + dt / 2
        hour = mid_s % 86_400 / 3600
        occupied = _OCCUPIED_H[0] <= hour < _OCCUPIED_H[1]
        schedule = 0 if occupied else 1
        wave = math.sin(2 * math.pi * (hour - 9.0) / 24.0)  # +1 at 15:00, -1 at 03:00
        outdoor_k = 27.5 + 5.5 * wave + KELVIN_OFFSET
        cool_sp = self._inputs(_COOL_SP, _COOLING_SP[schedule])
        heat_sp = self._inputs(_HEAT_SP, _HEATING_SP[schedule])
        command = self._inputs(_DAMPERS, math.nan)
        counts = self._occupancy(mid_s)
        people = np.array([float(counts.get(zone, 0)) for zone in _ZONES])
        sun = max(0.0, math.sin(math.pi * (hour - 6.0) / 12.0))  # 06:00 to 18:00
        gains = _PEOPLE_W * people + _EQUIPMENT_W_PER_M2[schedule] * _AREAS + _SOLAR_PEAK_W * sun
        temp = self._temp_k
        fan_on = occupied or bool(np.any(temp > cool_sp) or np.any(temp < heat_sp))
        flow = _V_MIN * fan_on
        if fan_on and dt > 0:
            # The flow that lands each zone on its cooling setpoint after dt, within the box's
            # range; held at the minimum, a zone with little load floats below the setpoint.
            needed = (_UA * (outdoor_k - temp) + gains + _CAPACITANCE * (temp - cool_sp) / dt) / (
                _RHO_CP * np.maximum(temp - _SUPPLY_K, 1.0)
            )
            flow = np.clip(needed, _V_MIN, _V_MAX)
        commanded = ~np.isnan(command)
        damper = np.where(commanded, command, _DAMPER_MIN * np.maximum(flow / _V_MIN, 1.0))
        flow = np.where(
            commanded, _V_MIN * np.maximum(damper / _DAMPER_MIN, _LEAKAGE) * fan_on, flow
        )
        heating = np.zeros(len(_ZONES))
        new_temp = temp
        if dt > 0:
            # Exact 1R1C step with the flow and the reheat held over dt.
            conductance = _UA + _RHO_CP * flow
            settle = -np.expm1(-dt * conductance / _CAPACITANCE)  # 1 - exp(-dt / tau)
            target = (_UA * outdoor_k + _RHO_CP * flow * _SUPPLY_K + gains) / conductance
            free = target + (temp - target) * (1.0 - settle)
            per_kelvin = conductance / settle  # W that move the end temperature by 1 K
            low = np.minimum(heat_sp, cool_sp)
            heating = np.clip((low - free) * per_kelvin, 0.0, _Q_HEAT_MAX) * fan_on
            new_temp = free + heating / per_kelvin
        cooling = np.maximum(0.0, _RHO_CP * flow * ((temp + new_temp) / 2 - _SUPPLY_K))
        self._temp_k = new_temp
        fresh = _OA_FRACTION * flow * occupied + _INFILTRATION
        co2_target = _CO2_OUT_PPM + people * _CO2_PER_PERSON * 1e6 / fresh
        self._co2_ppm = co2_target + (self._co2_ppm - co2_target) * np.exp(-fresh * dt / _VOLUME)
        total_flow = float(flow.sum())
        return_k = (
            float((new_temp * flow).sum()) / total_flow
            if total_flow > 0
            else float(new_temp.mean())
        )
        outdoor_air_w = _RHO_CP * _OA_FRACTION * total_flow * (outdoor_k - return_k) * occupied
        coil_w = max(0.0, float(cooling.sum()) + outdoor_air_w)
        heat_w = float(heating.sum())
        fan_speed = total_flow / _V_DESIGN
        power = {
            _FAN_W_Y: _FAN_DESIGN_W * fan_speed**3,
            _AHU_COOL_PUMP_W_Y: 0.01 * coil_w,
            _AHU_HEAT_PUMP_W_Y: 0.01 * heat_w,
            _CHILLER_W_Y: coil_w / _CHILLER_COP,
            _CHW_PUMP_W_Y: 0.02 * coil_w,
            _HEAT_PUMP_W_Y: heat_w / _HEAT_PUMP_COP,
            _HW_PUMP_W_Y: 0.02 * heat_w,
        }
        self._energy_j += sum(power.values()) * dt
        band_low, band_high = _HEATING_SP[schedule], _COOLING_SP[schedule]
        outside_band = np.maximum(0.0, new_temp - band_high) + np.maximum(0.0, band_low - new_temp)
        self._discomfort_kh += float(outside_band.sum()) * dt / 3600
        self._time_s += dt
        outputs = {"time": self._time_s}
        for names, values in (
            (_TEMP_Y, new_temp),
            (_COOL_SP, cool_sp),
            (_HEAT_SP, heat_sp),
            (_DAMPERS, damper),
            (_CO2_Y, self._co2_ppm),
            (_FLOW_Y, flow),
        ):
            outputs.update(zip(names, values.tolist(), strict=True))
        outputs.update(power)
        outputs[_OUTDOOR_T_Y] = outdoor_k
        outputs[_OUTDOOR_RH_Y] = 0.6 - 0.2 * wave
        outputs[_SUPPLY_T_Y] = _SUPPLY_K if total_flow > 0 else return_k
        outputs[_FAN_SPEED] = fan_speed
        self._outputs = outputs
        return power
