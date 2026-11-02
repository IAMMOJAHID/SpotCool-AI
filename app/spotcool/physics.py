"""Deterministic building physics: ISO 7730 comfort, psychrometrics, dilution,
a grey-box zone model and the HVAC energy model.

Nothing in this module calls a language model; every number the controller
acts on is computed here.
"""

import math
from dataclasses import dataclass, field, replace
from functools import lru_cache
from typing import Any, Optional

RHO_AIR = 1.2  # kg/m3
CP_AIR = 1005.0  # J/(kg K)
H_FG = 2.45e6  # J/kg, latent heat of vaporisation
P_ATM = 101325.0  # Pa

# ASHRAE 62.1 ventilation rate procedure (Table 6.2.2.1): (Rp L/s/person, Ra L/s/m2).
VRP_RATES_LPS: dict[str, tuple[float, float]] = {
    "office": (2.5, 0.3),
    "meeting_room": (2.5, 0.3),
    "retail": (3.8, 0.6),
    "lobby": (2.5, 0.3),
    "classroom": (5.0, 0.6),
    "restaurant": (3.8, 0.9),
    "warehouse": (0.0, 0.3),
    "other": (2.5, 0.3),
}


# --------------------------------------------------------------------------
# Psychrometrics
# --------------------------------------------------------------------------

def saturation_pressure_pa(temperature_c: float) -> float:
    """Magnus form (Alduchov & Eskridge 1996)."""
    return 610.94 * math.exp(17.625 * temperature_c / (temperature_c + 243.04))


def humidity_ratio(temperature_c: float, rh_pct: float) -> float:
    vapour = max(0.0, min(rh_pct, 100.0)) / 100.0 * saturation_pressure_pa(temperature_c)
    return 0.622 * vapour / (P_ATM - vapour)


def relative_humidity(temperature_c: float, ratio: float) -> float:
    vapour = ratio * P_ATM / (0.622 + ratio)
    return max(0.0, min(100.0, 100.0 * vapour / saturation_pressure_pa(temperature_c)))


def dew_point_c(temperature_c: float, rh_pct: float) -> float:
    gamma = math.log(max(rh_pct, 0.1) / 100.0) + 17.625 * temperature_c / (243.04 + temperature_c)
    return 243.04 * gamma / (17.625 - gamma)


# --------------------------------------------------------------------------
# Thermal comfort (ISO 7730 / ASHRAE 55)
# --------------------------------------------------------------------------

@lru_cache(maxsize=65536)
def _pmv_ppd_cached(ta: float, tr: float, vel: float, rh: float, met: float, clo: float) -> tuple[float, float]:
    pa = rh * 10 * math.exp(16.6536 - 4030.183 / (ta + 235))
    icl = 0.155 * clo
    m = met * 58.15
    mw = m
    fcl = 1 + 1.29 * icl if icl <= 0.078 else 1.05 + 0.645 * icl
    hcf = 12.1 * math.sqrt(vel)
    taa = ta + 273
    tra = tr + 273
    tcla = taa + (35.5 - ta) / (3.5 * icl + 0.1)
    p1 = icl * fcl
    p2 = p1 * 3.96
    p3 = p1 * 100
    p4 = p1 * taa
    p5 = 308.7 - 0.028 * mw + p2 * (tra / 100) ** 4
    xn = tcla / 100
    xf = tcla / 50
    iterations = 0
    hc = hcf
    while abs(xn - xf) > 0.00015:
        xf = (xf + xn) / 2
        hc = max(hcf, 2.38 * abs(100 * xf - taa) ** 0.25)
        xn = (p5 + p4 * hc - p2 * xf**4) / (100 + p3 * hc)
        iterations += 1
        if iterations > 150:
            break
    tcl = 100 * xn - 273
    hl1 = 3.05e-3 * (5733 - 6.99 * mw - pa)
    hl2 = 0.42 * (mw - 58.15) if mw > 58.15 else 0.0
    hl3 = 1.7e-5 * m * (5867 - pa)
    hl4 = 0.0014 * m * (34 - ta)
    hl5 = 3.96 * fcl * (xn**4 - (tra / 100) ** 4)
    hl6 = fcl * hc * (tcl - ta)
    ts = 0.303 * math.exp(-0.036 * m) + 0.028
    pmv = ts * (mw - hl1 - hl2 - hl3 - hl4 - hl5 - hl6)
    ppd = 100.0 - 95.0 * math.exp(-0.03353 * pmv**4 - 0.2179 * pmv**2)
    return pmv, ppd


def pmv_ppd(ta: float, tr: float, vel: float, rh: float, met: float, clo: float) -> tuple[float, float]:
    """ISO 7730 PMV/PPD. `vel` is the relative air speed in m/s."""
    return _pmv_ppd_cached(
        round(ta, 2), round(tr, 2), round(max(vel, 0.0), 3),
        round(rh, 1), round(met, 2), round(clo, 2),
    )


def relative_air_speed(air_speed: float, met: float) -> float:
    """ASHRAE 55: body movement adds 0.3 (met - 1) m/s above 1 met."""
    return air_speed + 0.3 * (met - 1.0) if met > 1.0 else air_speed


def dynamic_clo(clo: float, met: float) -> float:
    """ASHRAE 55: clothing insulation drops with activity above 1.2 met."""
    return clo * (0.6 + 0.4 / met) if met > 1.2 else clo


def occupant_pmv(ta: float, rh: float, air_speed: float, met: float, clo: float, radiant_delta_c: float = 0.0) -> tuple[float, float]:
    return pmv_ppd(
        ta, ta + radiant_delta_c, relative_air_speed(air_speed, met), rh, met, dynamic_clo(clo, met)
    )


def draft_rate_pct(ta: float, air_speed: float, turbulence_pct: float) -> float:
    """ISO 7730 draft rate; only meaningful for cool or neutral occupants."""
    if ta >= 34.0 or air_speed <= 0.05:
        return 0.0
    rate = (34.0 - ta) * (air_speed - 0.05) ** 0.62 * (0.37 * air_speed * turbulence_pct + 3.14)
    return max(0.0, min(100.0, rate))


# --------------------------------------------------------------------------
# Occupants, environment and zone state
# --------------------------------------------------------------------------

@dataclass(frozen=True)
class Occupant:
    weight: float
    met: float
    clo: float
    radiant_delta_c: float = 0.0
    lateral_m: Optional[float] = None
    forward_m: Optional[float] = None


@dataclass
class Environment:
    zone: dict[str, Any]
    hvac: dict[str, Any]
    camera: dict[str, Any]
    control: dict[str, Any]
    outdoor_temperature_c: float
    outdoor_humidity_ratio: float
    outdoor_co2_ppm: float
    outdoor_tvoc_ppb: float
    outdoor_pm25: float
    outdoor_pm10: float
    occupants: list[Occupant]
    occupant_count: int
    occupant_met: float
    equipment_w: float
    solar_w: float
    lighting_w: float
    infiltration_m3h: float
    ceiling_fans_running: bool
    tvoc_source: float = 0.0  # ppb m3/s
    pm25_source: float = 0.0  # ug/s
    pm10_source: float = 0.0  # ug/s

    @property
    def volume_m3(self) -> float:
        return float(self.zone["floor_area_m2"]) * float(self.zone["ceiling_height_m"])

    @property
    def thermal_capacitance_j_per_k(self) -> float:
        return RHO_AIR * CP_AIR * self.volume_m3 * float(self.zone["thermal_mass_multiplier"])


@dataclass
class ZoneState:
    temperature_c: float
    humidity_ratio: float
    co2_ppm: float
    tvoc_ppb: float
    pm25: float
    pm10: float

    @property
    def rh_pct(self) -> float:
        return relative_humidity(self.temperature_c, self.humidity_ratio)


def occupant_heat_gain_w(met: float) -> tuple[float, float]:
    """(sensible, latent) per person, ASHRAE Handbook of Fundamentals Ch. 18 Table 1."""
    if met <= 1.05:
        return 70.0, 45.0
    if met <= 1.3:
        return 75.0, 55.0
    if met <= 2.0:
        return 75.0, 70.0
    if met <= 2.6:
        return 80.0, 140.0
    return 170.0, 255.0


def co2_generation_m3s(met: float) -> float:
    """Adult CO2 generation, about 0.0052 L/s at 1.2 met (Persily & de Jonge 2017)."""
    return 0.0043 * met / 1000.0


def ventilation_floor_m3h(occupants: int, floor_area_m2: float, space_type: str, effectiveness: float) -> float:
    rp, ra = VRP_RATES_LPS.get(space_type, VRP_RATES_LPS["other"])
    return (rp * occupants + ra * floor_area_m2) / max(effectiveness, 0.1) * 3.6


def required_dilution_m3h(
    c0: float,
    c_out: float,
    c_target: float,
    source: float,
    volume_m3: float,
    horizon_s: float,
    infiltration_m3s: float,
    q_max_m3h: float,
) -> tuple[float, bool]:
    """Smallest outdoor airflow so that both C(H) and the steady state meet the target.

    Solves V dC/dt = Q (C_out - C) + S, i.e. C(t) = C_ss + (C0 - C_ss) e^(-Q t / V)
    with C_ss = C_out + S / Q, by bisection on the outdoor-air part of Q.
    """
    def concentration(q_oa_m3s: float, t: float) -> float:
        q = q_oa_m3s + infiltration_m3s
        if q <= 1e-9:
            return c0 + source * t / volume_m3
        c_ss = c_out + source / q
        return c_ss + (c0 - c_ss) * math.exp(-q * t / volume_m3)

    def meets(q_oa_m3s: float) -> bool:
        q = q_oa_m3s + infiltration_m3s
        steady = c_out + source / q if q > 1e-9 else (c0 if source <= 0 else math.inf)
        return concentration(q_oa_m3s, horizon_s) <= c_target and steady <= c_target

    q_max = q_max_m3h / 3600.0
    if meets(0.0):
        return 0.0, True
    if c_target <= c_out or not meets(q_max):
        return q_max_m3h, False
    low, high = 0.0, q_max
    for _ in range(40):
        mid = (low + high) / 2
        if meets(mid):
            high = mid
        else:
            low = mid
    return high * 3600.0, True


# --------------------------------------------------------------------------
# HVAC plant
# --------------------------------------------------------------------------

def airflow_fraction(mv: dict[str, Any], hvac: dict[str, Any]) -> float:
    if mv.get("fan_mode") == "OFF" or mv.get("fan_speed_mode") == "OFF":
        return 0.0
    airflow = mv.get("airflow_rate_m3h")
    if airflow is not None:
        return max(0.0, min(1.0, float(airflow) / float(hvac["max_airflow_m3h"])))
    tiers = hvac["fan_tier_fraction"]
    return float(tiers.get(mv.get("fan_speed_mode") or "AUTO", tiers["AUTO"]))


def outdoor_air_m3s(mv: dict[str, Any], hvac: dict[str, Any]) -> float:
    if mv.get("ventilation_mode") == "OFF" or airflow_fraction(mv, hvac) <= 0:
        return 0.0
    damper = float(mv.get("fresh_air_damper_pct") or 0)
    return damper / 100.0 * float(hvac["max_fresh_air_m3h"]) / 3600.0


def valve_required_pct(frequency_hz: float, hvac: dict[str, Any]) -> float:
    """Equal-percentage valve opening for the refrigerant flow a frequency needs.

    Flow fraction phi = R^(x - 1)  =>  x = 1 + ln(phi) / ln(R), phi clamped to [1/R, 1].
    """
    if frequency_hz <= 0:
        return 0.0
    rangeability = float(hvac["valve_rangeability"])
    phi = max(1.0 / rangeability, min(1.0, frequency_hz / float(hvac["max_frequency_hz"])))
    return 100.0 * (1.0 + math.log(phi) / math.log(rangeability))


def _coil_airflow_factor(fraction: float) -> float:
    return 0.0 if fraction <= 0 else min(1.05, 0.55 + 0.5 * fraction)


def _mode_factor(mv: dict[str, Any], hvac: dict[str, Any]) -> float:
    mode = mv.get("compressor_mode")
    if mode in ("OFF", "FAN_ONLY"):
        return 0.0
    factor = float(hvac["eco_capacity_fraction"]) if mode == "ECO" or mv.get("eco_mode") else 1.0
    if mv.get("turbo_mode"):
        factor *= float(hvac["turbo_capacity_fraction"])
    return factor


def capacity_w(mv: dict[str, Any], hvac: dict[str, Any]) -> float:
    """Total cooling capacity. Shared by the simulator and the feedforward inversion."""
    frequency = float(mv.get("compressor_frequency_hz") or 0.0)
    fraction = airflow_fraction(mv, hvac)
    factor = _mode_factor(mv, hvac)
    if frequency <= 0 or fraction <= 0 or factor <= 0:
        return 0.0
    required_valve = valve_required_pct(frequency, hvac)
    valve = float(mv.get("valve_open_pct") if mv.get("valve_open_pct") is not None else 100)
    valve_factor = 1.0 if required_valve <= 0 else min(1.0, valve / required_valve)
    return (
        float(hvac["rated_cooling_kw"]) * 1000.0
        * frequency / float(hvac["rated_frequency_hz"])
        * _coil_airflow_factor(fraction) * valve_factor * factor
    )


def frequency_for_capacity(capacity_needed_w: float, mv: dict[str, Any], hvac: dict[str, Any]) -> float:
    """Invert `capacity_w` for frequency, assuming the valve tracks its required opening."""
    per_hz = capacity_w({**mv, "compressor_frequency_hz": 1.0, "valve_open_pct": 100}, hvac)
    if per_hz <= 0:
        return 0.0
    return capacity_needed_w / per_hz


def sensible_heat_ratio(fraction: float, dehumidify: bool) -> float:
    shr = max(0.6, min(0.9, 0.6 + 0.3 * fraction))
    return max(0.55, shr - 0.1) if dehumidify else shr


def coefficient_of_performance(part_load: float, outdoor_c: float, hvac: dict[str, Any]) -> float:
    cop = float(hvac["rated_cop"]) * (1.0 - 0.025 * (outdoor_c - 35.0))
    cop *= 1.0 + 0.3 * (1.0 - max(0.2, min(1.0, part_load)))
    return max(1.5, min(7.0, cop))


def fan_power_kw(fraction: float, hvac: dict[str, Any]) -> float:
    """Fan affinity law: P is proportional to the cube of airflow."""
    return float(hvac["fan_rated_power_kw"]) * fraction**3


def electrical_power_kw(cooling_w: float, fraction: float, outdoor_c: float, hvac: dict[str, Any]) -> float:
    rated_w = float(hvac["rated_cooling_kw"]) * 1000.0
    compressor = 0.0
    if cooling_w > 0:
        cop = coefficient_of_performance(cooling_w / rated_w, outdoor_c, hvac)
        compressor = cooling_w / 1000.0 / cop
    return compressor + fan_power_kw(fraction, hvac)


# --------------------------------------------------------------------------
# Air movement at occupants (spot cooling)
# --------------------------------------------------------------------------

def louver_angles_to(lateral_m: float, forward_m: float, camera: dict[str, Any]) -> tuple[float, float, float]:
    """(pan_deg, tilt_down_deg, distance_m) from the AC outlet to an occupant's torso."""
    dx = lateral_m - float(camera["ac_lateral_m"])
    dz = forward_m - float(camera["ac_forward_m"])
    dh = float(camera["ac_height_m"]) - float(camera["target_height_m"])
    horizontal = math.hypot(dx, dz)
    return math.degrees(math.atan2(dx, dz)), math.degrees(math.atan2(dh, horizontal)), math.hypot(horizontal, dh)


def occupant_air_speed(occupant: Occupant, mv: dict[str, Any], env: Environment) -> float:
    hvac = env.hvac
    fraction = airflow_fraction(mv, hvac)
    fan = float(hvac["ceiling_fan_air_speed_mps"]) if env.ceiling_fans_running else 0.0
    jet = 0.0
    if fraction > 0 and occupant.lateral_m is not None and occupant.forward_m is not None:
        pan, tilt, distance = louver_angles_to(occupant.lateral_m, occupant.forward_m, env.camera)
        louver_pan = float(mv.get("louver_horizontal_deg") if mv.get("louver_horizontal_deg") is not None else 90) - 90.0
        louver_tilt = float(mv.get("louver_vertical_deg") if mv.get("louver_vertical_deg") is not None else 45)
        sigma = float(hvac["louver_beam_half_angle_deg"])
        scale = 1.0
        if mv.get("swing_mode"):
            sigma *= 2.5
            scale = 0.5
        offset = math.hypot(pan - louver_pan, tilt - louver_tilt)
        # Rated throw is the distance at which the jet axis slows to 0.25 m/s; throw scales with outlet velocity.
        axis_speed = 0.25 * float(hvac["louver_throw_m"]) * fraction / max(distance, 0.5)
        jet = scale * axis_speed * math.exp(-0.5 * (offset / sigma) ** 2)
    elif fraction > 0:
        jet = 0.05 * fraction
    return max(0.1, min(0.8, math.sqrt(0.1**2 + fan**2 + jet**2)))


def evaluate_comfort(ta: float, rh: float, mv: dict[str, Any], env: Environment) -> dict[str, Any]:
    if not env.occupants:
        return {"pmv_mean": 0.0, "ppd_mean": 0.0, "pmv_max": 0.0, "draft_soft_pct": 0.0, "per_occupant": []}
    turbulence = float(env.control["draft_turbulence_pct"])
    total_weight = sum(o.weight for o in env.occupants) or 1.0
    pmv_sum = ppd_sum = draft_sum = 0.0
    pmv_max = -math.inf
    per_occupant = []
    for occupant in env.occupants:
        speed = occupant_air_speed(occupant, mv, env)
        pmv, ppd = occupant_pmv(ta, rh, speed, occupant.met, occupant.clo, occupant.radiant_delta_c)
        draft = draft_rate_pct(ta, speed, turbulence) if pmv <= 0 else 0.0
        pmv_sum += occupant.weight * pmv
        ppd_sum += occupant.weight * ppd
        draft_sum += occupant.weight * draft
        pmv_max = max(pmv_max, pmv)
        per_occupant.append({"pmv": round(pmv, 2), "ppd": round(ppd, 1), "air_speed_mps": round(speed, 2), "draft_pct": round(draft, 1)})
    return {
        "pmv_mean": pmv_sum / total_weight,
        "ppd_mean": ppd_sum / total_weight,
        "pmv_max": pmv_max,
        "draft_soft_pct": draft_sum / total_weight,
        "per_occupant": per_occupant,
    }


def comfort_temperature(rh: float, occupants: list[Occupant], speeds: list[float], target_pmv: float) -> float:
    """Air temperature at which the occupant-weighted mean PMV equals the target (bisection)."""
    total_weight = sum(o.weight for o in occupants) or 1.0

    def mean_pmv(ta: float) -> float:
        return sum(
            o.weight * occupant_pmv(ta, rh, v, o.met, o.clo, o.radiant_delta_c)[0]
            for o, v in zip(occupants, speeds)
        ) / total_weight

    low, high = 14.0, 34.0
    for _ in range(32):
        mid = (low + high) / 2
        if mean_pmv(mid) > target_pmv:
            high = mid
        else:
            low = mid
    return (low + high) / 2


# --------------------------------------------------------------------------
# Grey-box zone simulation
# --------------------------------------------------------------------------

@dataclass
class Prediction:
    points: list[dict[str, Any]] = field(default_factory=list)
    energy_kwh: float = 0.0
    mean_power_kw: float = 0.0
    mean_ppd: float = 0.0
    mean_draft_pct: float = 0.0
    final_state: Optional[ZoneState] = None


def simulate(
    env: Environment,
    state: ZoneState,
    mv: dict[str, Any],
    horizon_s: float,
    step_s: float = 10.0,
    sample_s: float = 300.0,
) -> Prediction:
    hvac = env.hvac
    volume = env.volume_m3
    c_th = env.thermal_capacitance_j_per_k
    ua = float(env.zone["envelope_ua_w_per_k"])
    fraction = airflow_fraction(mv, hvac)
    q_oa = outdoor_air_m3s(mv, hvac)
    q_total = q_oa + env.infiltration_m3h / 3600.0
    q_recirc = fraction * float(hvac["max_airflow_m3h"]) / 3600.0
    capacity = capacity_w(mv, hvac)
    shr = sensible_heat_ratio(fraction, bool(mv.get("dehumidification_mode")))
    setpoint = float(mv.get("temperature_c") if mv.get("temperature_c") is not None else state.temperature_c)
    person_sensible, person_latent = occupant_heat_gain_w(env.occupant_met)
    fan_heat = fan_power_kw(fraction, hvac) * 1000.0
    internal = env.equipment_w + env.solar_w + env.lighting_w + env.occupant_count * person_sensible + fan_heat
    latent_gain = env.occupant_count * person_latent
    co2_source = env.occupant_count * co2_generation_m3s(env.occupant_met) * 1e6
    deposition = float(env.zone["particle_deposition_per_hour"]) / 3600.0
    efficiency = float(hvac["filter_efficiency"])

    t = state.temperature_c
    w = state.humidity_ratio
    co2, tvoc, pm25, pm10 = state.co2_ppm, state.tvoc_ppb, state.pm25, state.pm10
    prediction = Prediction()
    energy = 0.0
    ppd_values: list[float] = []
    draft_values: list[float] = []
    steps = max(1, int(round(horizon_s / step_s)))
    sample_every = max(1, int(round(sample_s / step_s)))
    for index in range(1, steps + 1):
        load_sensible = (ua + RHO_AIR * CP_AIR * q_total) * (env.outdoor_temperature_c - t) + internal
        if t > setpoint:
            cooling_sensible = capacity * shr
        else:
            cooling_sensible = min(capacity * shr, max(0.0, load_sensible))
        cooling_latent = cooling_sensible * (1.0 - shr) / shr
        t += (load_sensible - cooling_sensible) / c_th * step_s
        w += (
            RHO_AIR * q_total * (env.outdoor_humidity_ratio - w)
            + (latent_gain - cooling_latent) / H_FG
        ) / (RHO_AIR * volume) * step_s
        w = max(0.001, w)
        co2 += (q_total * (env.outdoor_co2_ppm - co2) + co2_source) / volume * step_s
        tvoc += (q_total * (env.outdoor_tvoc_ppb - tvoc) + env.tvoc_source) / volume * step_s
        pm25 += (q_total * (env.outdoor_pm25 - pm25) - (efficiency * q_recirc + deposition * volume) * pm25 + env.pm25_source) / volume * step_s
        pm10 += (q_total * (env.outdoor_pm10 - pm10) - (efficiency * q_recirc + deposition * volume) * pm10 + env.pm10_source) / volume * step_s
        energy += electrical_power_kw(cooling_sensible + cooling_latent, fraction, env.outdoor_temperature_c, hvac) * step_s / 3600.0
        if index % sample_every == 0 or index == steps:
            rh = relative_humidity(t, w)
            comfort = evaluate_comfort(t, rh, mv, env)
            ppd_values.append(comfort["ppd_mean"])
            draft_values.append(comfort["draft_soft_pct"])
            prediction.points.append({
                "minute": round(index * step_s / 60.0, 1),
                "temperature_c": round(t, 2),
                "rh_pct": round(rh, 1),
                "co2_ppm": round(co2),
                "tvoc_ppb": round(tvoc),
                "pm25_ug_m3": round(pm25, 1),
                "pm10_ug_m3": round(pm10, 1),
                "pmv": round(comfort["pmv_mean"], 2),
                "ppd": round(comfort["ppd_mean"], 1),
            })
    prediction.energy_kwh = energy
    prediction.mean_power_kw = energy / (steps * step_s / 3600.0)
    prediction.mean_ppd = sum(ppd_values) / len(ppd_values) if ppd_values else 0.0
    prediction.mean_draft_pct = sum(draft_values) / len(draft_values) if draft_values else 0.0
    prediction.final_state = ZoneState(t, w, co2, tvoc, pm25, pm10)
    return prediction


def holding_power_kw(env: Environment, mv: dict[str, Any], setpoint_c: float, rh_target_pct: float) -> dict[str, float]:
    """Steady-state electrical power needed to hold a setpoint with a given plan.

    Used for the savings KPI: during a pull-down both the plan and the
    baseline run at full capacity, so only the holding cost separates them.
    """
    hvac = env.hvac
    fraction = airflow_fraction(mv, hvac)
    q_total = outdoor_air_m3s(mv, hvac) + env.infiltration_m3h / 3600.0
    person_sensible, person_latent = occupant_heat_gain_w(env.occupant_met)
    fan_kw = fan_power_kw(fraction, hvac)
    sensible = (
        (float(env.zone["envelope_ua_w_per_k"]) + RHO_AIR * CP_AIR * q_total)
        * (env.outdoor_temperature_c - setpoint_c)
        + env.equipment_w + env.solar_w + env.lighting_w
        + env.occupant_count * person_sensible + fan_kw * 1000.0
    )
    ventilation_latent = RHO_AIR * q_total * (env.outdoor_humidity_ratio - humidity_ratio(setpoint_c, rh_target_pct)) * H_FG
    latent = env.occupant_count * person_latent + max(0.0, ventilation_latent)
    cooling = max(0.0, sensible) + max(0.0, latent)
    total_kw = electrical_power_kw(cooling, fraction, env.outdoor_temperature_c, hvac)
    return {"cooling_load_kw": cooling / 1000.0, "fan_kw": fan_kw, "electrical_kw": total_kw}


def with_occupants(env: Environment, occupants: list[Occupant]) -> Environment:
    return replace(env, occupants=occupants)
