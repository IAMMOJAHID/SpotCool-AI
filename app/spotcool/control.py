"""Physics planner and safety governor.

The planner builds a closed-form feedforward plan, searches a small grid of
variants on the grey-box simulator, and returns three ranked plans. The
governor is the final authority over anything Muse Wonder proposes.
"""

import math
from dataclasses import dataclass, field
from typing import Any, Optional, Sequence

from . import physics
from .physics import Environment, Occupant, ZoneState
from .schemas import MANIPULATED_VARIABLE_NAMES, ManipulatedVariables

FAN_TIERS = ("LOW", "MED", "HIGH")
SETPOINT_OFFSETS = (-1.0, -0.5, 0.0, 0.5, 1.0)
DAMPER_BONUSES = (0.0, 15.0)
INTEGER_FIELDS = ("louver_vertical_deg", "louver_horizontal_deg", "valve_open_pct", "fresh_air_damper_pct")
TARGET_DEFAULTS = {
    "temperature_c": 24.0,
    "humidity_pct": 45.0,
    "co2_ppm": 800.0,
    "pm25_ug_m3": 10.0,
    "pm10_ug_m3": 20.0,
    "tvoc_ppb": 250.0,
}


def normalize_control_variables(raw: dict[str, Any]) -> dict[str, float]:
    """Map producer keys such as `target_temperature_c` or `outdoor_temp` to canonical names."""
    normalized: dict[str, float] = {}
    for key, value in raw.items():
        if isinstance(value, bool) or not isinstance(value, (int, float)):
            continue
        name = key.lower()
        if "temp" in name:
            canonical = "temperature_c"
        elif "humid" in name or name.endswith("rh_pct") or name == "rh":
            canonical = "humidity_pct"
        elif "co2" in name:
            canonical = "co2_ppm"
        elif "pm25" in name or "pm2_5" in name:
            canonical = "pm25_ug_m3"
        elif "pm10" in name:
            canonical = "pm10_ug_m3"
        elif "voc" in name:
            canonical = "tvoc_ppb"
        else:
            continue
        if any(marker in name for marker in ("outdoor", "outside", "ambient")):
            canonical = f"outdoor_{canonical}"
        normalized.setdefault(canonical, float(value))
    return normalized


@dataclass
class Parameters:
    zone: dict[str, Any]
    hvac: dict[str, Any]
    camera: dict[str, Any]
    control: dict[str, Any]
    baseline: dict[str, Any]


@dataclass
class SceneFacts:
    """Perception output the planner consumes."""

    occupancy: int
    occupants: list[Occupant]
    space_type: str = "office"
    screens: int = 0
    solar_gain: str = "none"
    doors_open: int = 0
    windows_open: int = 0
    ceiling_fans_running: bool = False
    voice_sentiments: list[str] = field(default_factory=list)
    lighting: str = "normal"


@dataclass
class TurnContext:
    params: Parameters
    env: Environment
    state: ZoneState
    current: dict[str, float]
    targets: dict[str, float]
    previous_mv: dict[str, Any]
    previous_agent_state: dict[str, Any]
    facts: SceneFacts
    occupied: bool
    setback: bool
    stale: bool
    stale_turns: int
    empty_turns: int
    voice_bias_c: float
    stuffy: bool
    drafty: bool
    space_type: str
    dt_s: float
    clip_end_s: Optional[float]
    horizon_s: float
    pm_high_turns: int
    filter_alert: bool
    compressor_locked_out: bool
    ventilation: dict[str, Any] = field(default_factory=dict)
    aim: dict[str, Any] = field(default_factory=dict)


def previous_agent_state(turn_history: Sequence[dict[str, Any]]) -> dict[str, Any]:
    if turn_history:
        telemetry = turn_history[-1].get("telemetry")
        if isinstance(telemetry, dict) and isinstance(telemetry.get("agent_state"), dict):
            return telemetry["agent_state"]
    return {}


def _clamp(value: float, low: float, high: float) -> float:
    return max(low, min(high, value))


def build_context(
    params: Parameters,
    current_control_variables: dict[str, Any],
    target_control_variables: dict[str, Any],
    previous_mv: dict[str, Any],
    turn_history: Sequence[dict[str, Any]],
    facts: SceneFacts,
    clip_start_s: Optional[float],
    clip_end_s: Optional[float],
) -> TurnContext:
    zone, hvac, control = dict(params.zone), params.hvac, params.control
    targets = {**TARGET_DEFAULTS, **{
        key: value for key, value in normalize_control_variables(target_control_variables).items()
        if not key.startswith("outdoor_")
    }}
    current = normalize_control_variables(current_control_variables)
    for key in TARGET_DEFAULTS:
        current.setdefault(key, targets[key])
    for key, zone_key in (
        ("outdoor_temperature_c", "outdoor_temperature_c"),
        ("outdoor_humidity_pct", "outdoor_rh_pct"),
        ("outdoor_co2_ppm", "outdoor_co2_ppm"),
        ("outdoor_pm25_ug_m3", "outdoor_pm25_ug_m3"),
        ("outdoor_pm10_ug_m3", "outdoor_pm10_ug_m3"),
        ("outdoor_tvoc_ppb", "outdoor_tvoc_ppb"),
    ):
        if key in current:
            zone[zone_key] = current[key]

    prior = previous_agent_state(turn_history)
    snapshot = {key: round(current[key], 3) for key in sorted(TARGET_DEFAULTS)}
    stale = prior.get("last_control_snapshot") == snapshot
    stale_turns = int(prior.get("stale_turns", 0)) + 1 if stale else 0
    occupancy = max(0, int(facts.occupancy))
    empty_turns = int(prior.get("empty_turns", 0)) + 1 if occupancy == 0 else 0
    setback = occupancy == 0 and empty_turns >= int(control["empty_turns_for_setback"])

    step = float(control["voice_bias_step_c"])
    bias = float(prior.get("voice_bias_c", 0.0)) * 0.5
    bias += step * (facts.voice_sentiments.count("too_cold") - facts.voice_sentiments.count("too_hot"))
    bias = _clamp(bias, -float(control["voice_bias_limit_c"]), float(control["voice_bias_limit_c"]))

    space_type = zone["space_type"] if zone.get("space_type", "auto") != "auto" else facts.space_type
    area = float(zone["floor_area_m2"])
    volume = area * float(zone["ceiling_height_m"])
    infiltration = (
        float(zone["infiltration_ach"]) * volume
        + facts.doors_open * float(zone["open_door_airflow_m3h"])
        + facts.windows_open * float(zone["open_window_airflow_m3h"])
    )
    mets = [o.met for o in facts.occupants] or [float(control["design_met"])]
    env = Environment(
        zone=zone,
        hvac=hvac,
        camera=params.camera,
        control=control,
        outdoor_temperature_c=float(zone["outdoor_temperature_c"]),
        outdoor_humidity_ratio=physics.humidity_ratio(float(zone["outdoor_temperature_c"]), float(zone["outdoor_rh_pct"])),
        outdoor_co2_ppm=float(zone["outdoor_co2_ppm"]),
        outdoor_tvoc_ppb=float(zone["outdoor_tvoc_ppb"]),
        outdoor_pm25=float(zone["outdoor_pm25_ug_m3"]),
        outdoor_pm10=float(zone["outdoor_pm10_ug_m3"]),
        occupants=list(facts.occupants),
        occupant_count=occupancy,
        occupant_met=sum(mets) / len(mets),
        equipment_w=facts.screens * float(zone["screen_heat_w"]),
        solar_w=float(zone["solar_gain_w"].get(facts.solar_gain, 0.0)),
        lighting_w=0.0 if facts.lighting == "dark" else float(zone["lighting_w_per_m2"]) * area,
        infiltration_m3h=infiltration,
        ceiling_fans_running=facts.ceiling_fans_running,
    )
    state = ZoneState(
        temperature_c=current["temperature_c"],
        humidity_ratio=physics.humidity_ratio(current["temperature_c"], current["humidity_pct"]),
        co2_ppm=current["co2_ppm"],
        tvoc_ppb=current["tvoc_ppb"],
        pm25=current["pm25_ug_m3"],
        pm10=current["pm10_ug_m3"],
    )
    # Unmeasured TVOC/PM sources, inferred from a quasi-steady state under the
    # ventilation and filtration the previous turn commanded.
    # Stale (resent) telemetry carries no new information, so the previous
    # estimate is kept instead of learning from it.
    sources = prior.get("inferred_sources") if stale else None
    if isinstance(sources, dict):
        env.tvoc_source = float(sources.get("tvoc", 0.0))
        env.pm25_source = float(sources.get("pm25", 0.0))
        env.pm10_source = float(sources.get("pm10", 0.0))
    else:
        q_total = physics.outdoor_air_m3s(previous_mv, hvac) + infiltration / 3600.0
        q_recirc = physics.airflow_fraction(previous_mv, hvac) * float(hvac["max_airflow_m3h"]) / 3600.0
        removal = float(hvac["filter_efficiency"]) * q_recirc + float(zone["particle_deposition_per_hour"]) / 3600.0 * volume
        env.tvoc_source = max(0.0, q_total * (state.tvoc_ppb - env.outdoor_tvoc_ppb))
        env.pm25_source = max(0.0, (q_total + removal) * state.pm25 - q_total * env.outdoor_pm25)
        env.pm10_source = max(0.0, (q_total + removal) * state.pm10 - q_total * env.outdoor_pm10)

    pm_high = state.pm25 > targets["pm25_ug_m3"] * 1.1 or state.pm10 > targets["pm10_ug_m3"] * 1.1
    pm_high_turns = int(prior.get("pm_high_turns", 0))
    if not stale:
        pm_high_turns = pm_high_turns + 1 if pm_high else 0

    off_since = prior.get("compressor_off_since_seconds")
    now = clip_end_s if clip_end_s is not None else float(prior.get("cumulative", {}).get("elapsed_seconds", 0.0))
    locked_out = off_since is not None and now - float(off_since) < float(hvac["compressor_min_off_seconds"])

    if clip_start_s is not None and clip_end_s is not None and clip_end_s > clip_start_s:
        dt_s = clip_end_s - clip_start_s
    else:
        dt_s = 60.0

    context = TurnContext(
        params=params,
        env=env,
        state=state,
        current=current,
        targets=targets,
        previous_mv={name: previous_mv.get(name) for name in MANIPULATED_VARIABLE_NAMES},
        previous_agent_state=prior,
        facts=facts,
        occupied=occupancy > 0,
        setback=setback,
        stale=stale,
        stale_turns=stale_turns,
        empty_turns=empty_turns,
        voice_bias_c=bias,
        stuffy="stuffy" in facts.voice_sentiments,
        drafty="drafty" in facts.voice_sentiments,
        space_type=space_type,
        dt_s=dt_s,
        clip_end_s=clip_end_s,
        horizon_s=float(control["horizon_minutes"]) * 60.0,
        pm_high_turns=pm_high_turns,
        filter_alert=pm_high_turns >= int(control["pm_alert_turns"]),
        compressor_locked_out=locked_out,
    )
    context.ventilation = ventilation_requirement(context)
    context.aim = louver_aim(context)
    return context


def ventilation_requirement(ctx: TurnContext) -> dict[str, Any]:
    env, hvac, zone = ctx.env, ctx.params.hvac, ctx.env.zone
    volume = env.volume_m3
    infiltration_m3s = env.infiltration_m3h / 3600.0
    max_fresh = float(hvac["max_fresh_air_m3h"])
    occupancy = 0 if ctx.setback else env.occupant_count
    floor = physics.ventilation_floor_m3h(
        occupancy, float(zone["floor_area_m2"]), ctx.space_type, float(zone["ventilation_effectiveness"])
    )
    co2_source = occupancy * physics.co2_generation_m3s(env.occupant_met) * 1e6
    co2_m3h, co2_ok = physics.required_dilution_m3h(
        ctx.state.co2_ppm, env.outdoor_co2_ppm, ctx.targets["co2_ppm"], co2_source,
        volume, ctx.horizon_s, infiltration_m3s, max_fresh,
    )
    tvoc_m3h, tvoc_ok = physics.required_dilution_m3h(
        ctx.state.tvoc_ppb, env.outdoor_tvoc_ppb, ctx.targets["tvoc_ppb"], env.tvoc_source,
        volume, ctx.horizon_s, infiltration_m3s, max_fresh,
    )
    outdoor_air = max(floor, co2_m3h, tvoc_m3h)
    if ctx.stuffy:
        outdoor_air *= 1.15
    outdoor_air = min(outdoor_air, max_fresh)

    # Filtration needed to hold PM targets at that outdoor airflow.
    q_total = outdoor_air / 3600.0 + infiltration_m3s
    deposition = float(zone["particle_deposition_per_hour"]) / 3600.0 * volume
    efficiency = max(float(hvac["filter_efficiency"]), 0.05)
    recirculation = 0.0
    for outdoor, source, target in (
        (env.outdoor_pm25, env.pm25_source, ctx.targets["pm25_ug_m3"]),
        (env.outdoor_pm10, env.pm10_source, ctx.targets["pm10_ug_m3"]),
    ):
        needed = ((q_total * outdoor + source) / max(target, 0.1) - q_total - deposition) / efficiency
        recirculation = max(recirculation, needed * 3600.0)
    return {
        "outdoor_air_m3h": round(outdoor_air, 1),
        "ashrae_62_1_floor_m3h": round(floor, 1),
        "co2_dilution_m3h": round(co2_m3h, 1),
        "co2_target_feasible": co2_ok,
        "tvoc_dilution_m3h": round(tvoc_m3h, 1),
        "tvoc_target_feasible": tvoc_ok,
        "pm_recirculation_m3h": round(min(max(recirculation, 0.0), float(hvac["max_airflow_m3h"])), 1),
        "space_type": ctx.space_type,
    }


def louver_aim(ctx: TurnContext) -> dict[str, Any]:
    located = [o for o in ctx.env.occupants if o.lateral_m is not None and o.forward_m is not None]
    previous = ctx.previous_mv
    if not located:
        return {
            "louver_vertical_deg": previous.get("louver_vertical_deg") or 45,
            "louver_horizontal_deg": previous.get("louver_horizontal_deg") or 90,
            "swing_mode": ctx.occupied,
            "reason": "no located occupants; mixing throw",
        }
    total = sum(o.weight for o in located) or 1.0
    angles = [physics.louver_angles_to(o.lateral_m, o.forward_m, ctx.env.camera) for o in located]  # type: ignore[arg-type]
    pan = sum(o.weight * a[0] for o, a in zip(located, angles)) / total
    tilt = sum(o.weight * a[1] for o, a in zip(located, angles)) / total
    spread = math.sqrt(sum(o.weight * (a[0] - pan) ** 2 for o, a in zip(located, angles)) / total)
    beam = float(ctx.params.hvac["louver_beam_half_angle_deg"])
    swing = spread > 1.5 * beam
    reason = f"aim at weighted centroid of {len(located)} occupants, pan spread {spread:.0f} deg"
    if ctx.drafty:
        tilt = max(0.0, tilt - 20.0)
        reason += "; raised 20 deg after a draft complaint"
    return {
        "louver_vertical_deg": int(round(_clamp(tilt, 0, 90))),
        "louver_horizontal_deg": int(round(_clamp(90 + pan, 0, 180))),
        "swing_mode": swing,
        "pan_spread_deg": round(spread, 1),
        "reason": reason,
    }


def comfort_setpoint(ctx: TurnContext, mv: dict[str, Any]) -> dict[str, Any]:
    hvac, control = ctx.params.hvac, ctx.params.control
    target = ctx.targets["temperature_c"]
    low, high = float(hvac["setpoint_min_c"]), float(hvac["setpoint_max_c"])
    if ctx.setback:
        setpoint = _clamp(target + float(control["setback_unoccupied_c"]), low, high)
        return {"setpoint_c": round(setpoint, 2), "offset_c": round(setpoint - target, 2), "reason": "unoccupied setback"}
    if not ctx.env.occupants:
        setpoint = _clamp(target + ctx.voice_bias_c, low, high)
        return {"setpoint_c": round(setpoint, 2), "offset_c": round(setpoint - target, 2), "reason": "no occupant model"}
    rh = ctx.state.rh_pct
    target_pmv = float(control["target_pmv"])
    speeds = [physics.occupant_air_speed(o, mv, ctx.env) for o in ctx.env.occupants]
    occupants_t = physics.comfort_temperature(rh, ctx.env.occupants, speeds, target_pmv)
    design = Occupant(weight=1.0, met=float(control["design_met"]), clo=float(control["design_clo"]))
    design_t = physics.comfort_temperature(rh, [design], [0.1], target_pmv)
    offset = _clamp(occupants_t - design_t, float(control["offset_min_c"]), float(control["offset_max_c"]))
    setpoint = _clamp(target + offset + ctx.voice_bias_c, low, high)
    return {
        "setpoint_c": round(setpoint, 2),
        "offset_c": round(setpoint - target, 2),
        "occupant_comfort_temperature_c": round(occupants_t, 2),
        "design_comfort_temperature_c": round(design_t, 2),
        "voice_bias_c": round(ctx.voice_bias_c, 2),
        "reason": "comfort-equivalent offset: ISO 7730 T* of observed occupants vs design occupant",
    }


def feedforward_plan(
    ctx: TurnContext, setpoint_c: float, fan_tier: str, damper_bonus_pct: float = 0.0
) -> tuple[dict[str, Any], dict[str, Any]]:
    hvac, control, env, state = ctx.params.hvac, ctx.params.control, ctx.env, ctx.state
    mv = dict(ctx.previous_mv)
    max_airflow = float(hvac["max_airflow_m3h"])
    max_fresh = float(hvac["max_fresh_air_m3h"])
    outdoor_air = float(ctx.ventilation["outdoor_air_m3h"])
    target_rh = ctx.targets["humidity_pct"]
    dehumidify = state.rh_pct > target_rh + float(control["dehumidify_margin_pct"])

    airflow = max(
        float(hvac["fan_tier_fraction"][fan_tier]) * max_airflow,
        float(ctx.ventilation["pm_recirculation_m3h"]),
        outdoor_air,
        float(hvac["min_airflow_m3h"]),
    )
    if dehumidify:
        airflow = max(float(hvac["min_airflow_m3h"]), airflow * 0.85)
    airflow = min(airflow, max_airflow)
    # Ventilation or filtration needs can push airflow above the requested
    # tier; report the tier the fan actually runs at.
    tiers = hvac["fan_tier_fraction"]
    fan_tier = next((t for t in FAN_TIERS if airflow / max_airflow <= float(tiers[t]) + 1e-6), "HIGH")
    damper = _clamp(100.0 * outdoor_air / max_fresh + damper_bonus_pct, 0.0, 100.0)
    mv.update({
        "temperature_c": setpoint_c,
        "fan_speed_mode": fan_tier,
        "fan_mode": "ON",
        "airflow_rate_m3h": airflow,
        "louver_vertical_deg": ctx.aim["louver_vertical_deg"],
        "louver_horizontal_deg": ctx.aim["louver_horizontal_deg"],
        "swing_mode": ctx.aim["swing_mode"],
        "compressor_mode": "ECO" if ctx.setback else "COOL",
        "eco_mode": ctx.setback,
        "turbo_mode": False,
        "humidity_target_pct": target_rh,
        "dehumidification_mode": dehumidify,
        "fresh_air_damper_pct": damper,
        "ventilation_mode": "ON" if outdoor_air > 0 else "AUTO",
        "filter_cleaning_alert": ctx.filter_alert,
    })
    if env.outdoor_temperature_c > 5.0:
        mv["defrost_mode"] = False

    fraction = airflow / max_airflow
    q_total = damper / 100.0 * max_fresh / 3600.0 + env.infiltration_m3h / 3600.0
    conductance = float(env.zone["envelope_ua_w_per_k"]) + physics.RHO_AIR * physics.CP_AIR * q_total
    tau = env.thermal_capacitance_j_per_k / conductance
    person_sensible, person_latent = physics.occupant_heat_gain_w(env.occupant_met)
    internal = (
        env.equipment_w + env.solar_w + env.lighting_w
        + env.occupant_count * person_sensible + physics.fan_power_kw(fraction, hvac) * 1000.0
    )
    t0 = state.temperature_c
    supply = float(hvac["supply_air_temp_c"])
    saturated = False
    if t0 > setpoint_c + 0.1:
        # 1R1C closed form: reach the setpoint at the end of the horizon.
        decay = math.exp(-ctx.horizon_s / tau)
        steady_target = (setpoint_c - t0 * decay) / (1.0 - decay)
        if steady_target < supply:
            saturated, steady_target = True, supply
    else:
        steady_target = setpoint_c
    sensible = max(0.0, internal + conductance * (env.outdoor_temperature_c - steady_target))
    shr = physics.sensible_heat_ratio(fraction, dehumidify)
    total = sensible / shr
    if dehumidify:
        w_target = physics.humidity_ratio(setpoint_c, target_rh)
        latent = (
            env.occupant_count * person_latent
            + physics.RHO_AIR * q_total * (env.outdoor_humidity_ratio - state.humidity_ratio) * physics.H_FG
            + physics.RHO_AIR * env.volume_m3 * (state.humidity_ratio - w_target) * physics.H_FG / ctx.horizon_s
        )
        total = max(total, sensible + max(0.0, latent))

    min_hz, max_hz = float(hvac["min_frequency_hz"]), float(hvac["max_frequency_hz"])
    frequency = physics.frequency_for_capacity(total, mv, hvac)
    if t0 <= setpoint_c - 0.5 and frequency < min_hz:
        mv.update({"compressor_mode": "FAN_ONLY", "compressor_frequency_hz": 0.0, "valve_open_pct": 0})
    else:
        if frequency > max_hz and ctx.occupied and t0 - setpoint_c > float(control["turbo_threshold_c"]):
            # Turbo: boosted compressor capacity with the fan at full airflow.
            mv.update({"turbo_mode": True, "eco_mode": False, "fan_speed_mode": "HIGH", "airflow_rate_m3h": max_airflow})
            frequency = physics.frequency_for_capacity(total, mv, hvac)
        frequency = _clamp(frequency, min_hz, max_hz)
        mv["compressor_frequency_hz"] = frequency
        mv["valve_open_pct"] = _clamp(
            physics.valve_required_pct(frequency, hvac) + float(hvac["valve_margin_pct"]), 0.0, 100.0
        )
    if mv.get("turbo_mode") or mv.get("eco_mode"):
        mv["sleep_mode"] = False
    details = {
        "cooling_required_kw": round(total / 1000.0, 2),
        "capacity_kw": round(physics.capacity_w(mv, hvac) / 1000.0, 2),
        "saturated": saturated or frequency >= max_hz,
        "time_constant_h": round(tau / 3600.0, 2),
        "steady_state_target_c": round(steady_target, 2),
    }
    return quantize(mv), details


@dataclass
class Candidate:
    name: str
    mv: dict[str, Any]
    feedforward: dict[str, Any]
    comfort: dict[str, Any]
    prediction: physics.Prediction
    offset_c: float
    costs: dict[str, float] = field(default_factory=dict)


def _move_penalty(mv: dict[str, Any], previous: dict[str, Any], hvac: dict[str, Any]) -> float:
    ranges = {
        "temperature_c": 10.0,
        "compressor_frequency_hz": float(hvac["max_frequency_hz"]),
        "airflow_rate_m3h": float(hvac["max_airflow_m3h"]),
        "valve_open_pct": 100.0,
        "fresh_air_damper_pct": 100.0,
        "louver_vertical_deg": 90.0,
        "louver_horizontal_deg": 180.0,
    }
    total = 0.0
    for name, span in ranges.items():
        if mv.get(name) is not None and previous.get(name) is not None:
            total += abs(float(mv[name]) - float(previous[name])) / span
    return total / len(ranges)


def plan_cost(ctx: TurnContext, candidate: Candidate, weights: dict[str, float]) -> float:
    hvac = ctx.params.hvac
    reference_kwh = (
        float(hvac["rated_cooling_kw"]) / float(hvac["rated_cop"]) + float(hvac["fan_rated_power_kw"])
    ) * ctx.horizon_s / 3600.0
    end = candidate.prediction.points[-1]
    iaq = 0.0
    for key, target_key in (
        ("co2_ppm", "co2_ppm"), ("tvoc_ppb", "tvoc_ppb"),
        ("pm25_ug_m3", "pm25_ug_m3"), ("pm10_ug_m3", "pm10_ug_m3"),
    ):
        target = ctx.targets[target_key]
        iaq += (max(0.0, end[key] - target) / max(target, 1.0)) ** 2
    return (
        weights["ppd"] * candidate.prediction.mean_ppd / 100.0
        + weights["energy"] * candidate.prediction.energy_kwh / reference_kwh
        + weights["iaq"] * iaq
        + weights["draft"] * candidate.prediction.mean_draft_pct / 100.0
        + weights["move"] * _move_penalty(candidate.mv, ctx.previous_mv, hvac)
        + weights["setpoint"] * abs(candidate.offset_c)
    )


def search_plans(ctx: TurnContext) -> dict[str, Any]:
    hvac, weights = ctx.params.hvac, ctx.params.control["weights"]
    low, high = float(hvac["setpoint_min_c"]), float(hvac["setpoint_max_c"])
    candidates: list[Candidate] = []
    seen: set[tuple[Any, ...]] = set()
    for tier in FAN_TIERS:
        probe, _ = feedforward_plan(ctx, ctx.targets["temperature_c"], tier)
        comfort = comfort_setpoint(ctx, probe)
        for offset in SETPOINT_OFFSETS:
            setpoint = round(_clamp(comfort["setpoint_c"] + offset, low, high) * 2) / 2
            for bonus in DAMPER_BONUSES:
                mv, details = feedforward_plan(ctx, setpoint, tier, bonus)
                key = tuple(mv[name] for name in MANIPULATED_VARIABLE_NAMES)
                if key in seen:
                    continue
                seen.add(key)
                prediction = physics.simulate(ctx.env, ctx.state, mv, ctx.horizon_s)
                candidates.append(Candidate(
                    name=f"{tier}/sp{setpoint:g}/oa+{bonus:g}",
                    mv=mv,
                    feedforward=details,
                    comfort=comfort,
                    prediction=prediction,
                    offset_c=setpoint - comfort["setpoint_c"],
                ))
    weight_sets = {
        "recommended": dict(weights),
        "comfort_first": {**weights, "ppd": weights["ppd"] * 4},
        "energy_first": {**weights, "energy": weights["energy"] * 4},
    }
    chosen: dict[str, Candidate] = {}
    for label, weight_set in weight_sets.items():
        for candidate in candidates:
            candidate.costs[label] = plan_cost(ctx, candidate, weight_set)
        chosen[label] = min(candidates, key=lambda c: c.costs[label])
    return {"plans": chosen, "candidates_evaluated": len(candidates)}


def summarize_candidate(candidate: Candidate, label: str) -> dict[str, Any]:
    prediction = candidate.prediction
    return {
        "candidate": candidate.name,
        "cost": round(candidate.costs.get(label, 0.0), 4),
        "manipulated_variables": candidate.mv,
        "feedforward": candidate.feedforward,
        "comfort_setpoint": candidate.comfort,
        "prediction": {
            "points": prediction.points,
            "energy_kwh": round(prediction.energy_kwh, 3),
            "mean_power_kw": round(prediction.mean_power_kw, 2),
            "mean_ppd": round(prediction.mean_ppd, 1),
            "mean_draft_pct": round(prediction.mean_draft_pct, 1),
        },
    }


# --------------------------------------------------------------------------
# Governor
# --------------------------------------------------------------------------

def quantize(mv: dict[str, Any]) -> dict[str, Any]:
    out = dict(mv)
    if out.get("temperature_c") is not None:
        out["temperature_c"] = round(float(out["temperature_c"]) * 2) / 2
    if out.get("compressor_frequency_hz") is not None:
        out["compressor_frequency_hz"] = float(round(float(out["compressor_frequency_hz"])))
    if out.get("airflow_rate_m3h") is not None:
        out["airflow_rate_m3h"] = float(round(float(out["airflow_rate_m3h"]) / 10) * 10)
    if out.get("humidity_target_pct") is not None:
        out["humidity_target_pct"] = float(round(float(out["humidity_target_pct"])))
    for name in INTEGER_FIELDS:
        if out.get(name) is not None:
            out[name] = int(round(float(out[name])))
    return out


def _numeric_limits(ctx: TurnContext) -> dict[str, tuple[float, float]]:
    hvac = ctx.params.hvac
    return {
        "temperature_c": (float(hvac["setpoint_min_c"]), float(hvac["setpoint_max_c"])),
        "compressor_frequency_hz": (0.0, float(hvac["max_frequency_hz"])),
        "airflow_rate_m3h": (0.0, float(hvac["max_airflow_m3h"])),
        "humidity_target_pct": (float(hvac["humidity_target_min_pct"]), float(hvac["humidity_target_max_pct"])),
        "valve_open_pct": (0.0, 100.0),
        "fresh_air_damper_pct": (0.0, 100.0),
        "louver_vertical_deg": (0.0, 90.0),
        "louver_horizontal_deg": (0.0, 180.0),
    }


def _compressor_running(mv: dict[str, Any]) -> bool:
    return mv.get("compressor_mode") in ("COOL", "ECO") and float(mv.get("compressor_frequency_hz") or 0) > 0


def _apply_interlocks(ctx: TurnContext, mv: dict[str, Any], note: Any) -> None:
    hvac = ctx.params.hvac

    def force(name: str, value: Any, rule: str) -> None:
        if mv.get(name) != value:
            note(name, rule, mv.get(name), value)
            mv[name] = value

    if _compressor_running(mv) and not _compressor_running(ctx.previous_mv) and ctx.compressor_locked_out:
        force("compressor_mode", "FAN_ONLY", "compressor_min_off_time")
    if mv.get("compressor_mode") in ("COOL", "ECO") and float(mv.get("compressor_frequency_hz") or 0) <= 0:
        force("compressor_mode", "FAN_ONLY", "zero_frequency_means_off")
    if mv.get("compressor_mode") in ("OFF", "FAN_ONLY"):
        force("compressor_frequency_hz", 0.0, "compressor_off_zero_frequency")
        force("valve_open_pct", 0, "compressor_off_valve_closed")
    elif mv.get("compressor_mode") in ("COOL", "ECO"):
        min_hz = float(hvac["min_frequency_hz"])
        if float(mv.get("compressor_frequency_hz") or 0) < min_hz:
            force("compressor_frequency_hz", min_hz, "inverter_min_frequency")
        if int(mv.get("valve_open_pct") or 0) <= 0:
            force("valve_open_pct", int(round(physics.valve_required_pct(float(mv["compressor_frequency_hz"]), hvac))), "valve_open_when_running")
        if mv.get("fan_mode") == "OFF":
            force("fan_mode", "ON", "fan_required_with_compressor")
        if mv.get("fan_speed_mode") == "OFF":
            force("fan_speed_mode", "LOW", "fan_required_with_compressor")
        if float(mv.get("airflow_rate_m3h") or 0) < float(hvac["min_airflow_m3h"]):
            force("airflow_rate_m3h", float(hvac["min_airflow_m3h"]), "coil_min_airflow")
    if mv.get("turbo_mode"):
        force("eco_mode", False, "turbo_excludes_eco")
        force("sleep_mode", False, "turbo_excludes_sleep")
    if mv.get("eco_mode"):
        force("sleep_mode", False, "eco_excludes_sleep")
    if mv.get("ventilation_mode") == "OFF":
        force("fresh_air_damper_pct", 0, "ventilation_off_damper_closed")
    if ctx.env.outdoor_temperature_c > 5.0:
        force("defrost_mode", False, "no_defrost_above_5c")


def govern(ctx: TurnContext, proposal: dict[str, Any]) -> tuple[dict[str, Any], list[dict[str, Any]]]:
    """Project a proposal onto the hard constraints. Soft goals are scored elsewhere."""
    control = ctx.params.control
    previous = ctx.previous_mv
    adjustments: list[dict[str, Any]] = []

    def note(name: str, rule: str, before: Any, after: Any) -> None:
        adjustments.append({"field": name, "rule": rule, "from": before, "to": after})

    mv = {
        name: proposal.get(name) if proposal.get(name) is not None else previous.get(name)
        for name in MANIPULATED_VARIABLE_NAMES
    }
    for name, (low, high) in _numeric_limits(ctx).items():
        if mv.get(name) is not None:
            bounded = _clamp(float(mv[name]), low, high)
            if bounded != float(mv[name]):
                note(name, "bounds", mv[name], bounded)
            mv[name] = bounded
    for name, limit in control["rate_limits"].items():
        before = previous.get(name)
        if mv.get(name) is None or before is None:
            continue
        step = float(mv[name]) - float(before)
        if abs(step) > float(limit):
            limited = float(before) + math.copysign(float(limit), step)
            note(name, "rate_limit", mv[name], limited)
            mv[name] = limited
    _apply_interlocks(ctx, mv, note)
    mv = quantize(mv)
    for name, band in control["deadbands"].items():
        before = previous.get(name)
        if mv.get(name) is None or before is None:
            continue
        if 0 < abs(float(mv[name]) - float(before)) < float(band):
            mv[name] = before
    # Interlocks win over deadbands: re-check the merged values.
    _apply_interlocks(ctx, mv, note)
    mv = quantize(mv)
    ManipulatedVariables(**mv)
    return mv, adjustments


def decision_delta(final_mv: dict[str, Any], previous_mv: dict[str, Any]) -> dict[str, Any]:
    return {
        name: final_mv.get(name) if final_mv.get(name) != previous_mv.get(name) else None
        for name in MANIPULATED_VARIABLE_NAMES
    }


# --------------------------------------------------------------------------
# Outcomes, anomalies and memory
# --------------------------------------------------------------------------

def baseline_plan(ctx: TurnContext) -> dict[str, Any]:
    """Conventional uniform cooling: operator target, fixed fan tier, fixed design outdoor air."""
    baseline = {
        **ctx.previous_mv,
        "temperature_c": ctx.targets["temperature_c"],
        "compressor_mode": "COOL",
        "turbo_mode": False,
        "eco_mode": False,
        "swing_mode": True,
        "airflow_rate_m3h": None,
    }
    baseline.update(ctx.params.baseline)
    return baseline


def energy_outcome(ctx: TurnContext, final_mv: dict[str, Any]) -> dict[str, Any]:
    rh_target = ctx.targets["humidity_pct"]
    holding_mv = {**final_mv, "turbo_mode": False}
    plan = physics.holding_power_kw(ctx.env, holding_mv, float(final_mv["temperature_c"]), rh_target)
    baseline_mv = baseline_plan(ctx)
    base = physics.holding_power_kw(ctx.env, baseline_mv, float(baseline_mv["temperature_c"]), rh_target)
    plan_comfort = physics.evaluate_comfort(float(final_mv["temperature_c"]), rh_target, holding_mv, ctx.env)
    base_comfort = physics.evaluate_comfort(float(baseline_mv["temperature_c"]), rh_target, baseline_mv, ctx.env)
    savings = 1.0 - plan["electrical_kw"] / base["electrical_kw"] if base["electrical_kw"] > 0 else 0.0
    return {
        "method": "steady-state holding power at setpoint (grey-box model)",
        "plan_kw": round(plan["electrical_kw"], 3),
        "baseline_kw": round(base["electrical_kw"], 3),
        "savings_pct": round(100.0 * savings, 1),
        "plan_cooling_load_kw": round(plan["cooling_load_kw"], 2),
        "baseline_cooling_load_kw": round(base["cooling_load_kw"], 2),
        "plan_ppd_at_setpoint": round(plan_comfort["ppd_mean"], 1),
        "baseline_ppd_at_setpoint": round(base_comfort["ppd_mean"], 1),
        "baseline_manipulated_variables": {
            key: baseline_mv.get(key)
            for key in ("temperature_c", "fan_speed_mode", "fresh_air_damper_pct", "swing_mode")
        },
    }


def detect_anomalies(ctx: TurnContext, sam_count: Optional[int], spark_count: Optional[int]) -> list[dict[str, Any]]:
    anomalies: list[dict[str, Any]] = []
    prior = ctx.previous_agent_state.get("last_control_snapshot") or {}
    if ctx.stale:
        anomalies.append({
            "kind": "telemetry_stale",
            "severity": "warning" if ctx.stale_turns >= 2 else "info",
            "evidence": f"sensor values identical to the previous turn for {ctx.stale_turns} turn(s); learning and fault alerts paused",
        })
    if (
        not ctx.occupied and not ctx.stale and prior
        and ctx.state.co2_ppm > ctx.env.outdoor_co2_ppm + 300
        and ctx.state.co2_ppm >= float(prior.get("co2_ppm", 0))
    ):
        anomalies.append({
            "kind": "co2_without_occupants",
            "severity": "warning",
            "evidence": f"CO2 {ctx.state.co2_ppm:.0f} ppm and not falling with no occupants detected",
        })
    openings = ctx.facts.doors_open + ctx.facts.windows_open
    if openings and ctx.env.outdoor_temperature_c > ctx.state.temperature_c + 2:
        leak_kw = (
            physics.RHO_AIR * physics.CP_AIR
            * (ctx.facts.doors_open * float(ctx.env.zone["open_door_airflow_m3h"])
               + ctx.facts.windows_open * float(ctx.env.zone["open_window_airflow_m3h"])) / 3600.0
            * (ctx.env.outdoor_temperature_c - ctx.state.temperature_c) / 1000.0
        )
        anomalies.append({
            "kind": "envelope_leak",
            "severity": "warning",
            "evidence": f"{openings} opening(s) while cooling; about {leak_kw:.1f} kW of infiltration load",
        })
    if sam_count is not None and spark_count is not None:
        if abs(sam_count - spark_count) > max(2, 0.3 * max(sam_count, spark_count)):
            anomalies.append({
                "kind": "occupancy_uncertain",
                "severity": "info",
                "evidence": f"SAM 3.1 counted {sam_count}, Muse Spark counted {spark_count}; using the larger",
            })
    if ctx.filter_alert:
        anomalies.append({
            "kind": "air_quality_filtration",
            "severity": "warning",
            "evidence": f"PM above target for {ctx.pm_high_turns} consecutive turns with fresh telemetry",
        })
    if (
        not ctx.stale and prior
        and float(prior.get("temperature_c", -99)) - ctx.state.temperature_c < 0.1
        and float(ctx.previous_mv.get("compressor_frequency_hz") or 0) >= 0.8 * float(ctx.params.hvac["max_frequency_hz"])
        and ctx.state.temperature_c > ctx.targets["temperature_c"] + 1.0
    ):
        anomalies.append({
            "kind": "cooling_ineffective",
            "severity": "warning",
            "evidence": "temperature not falling despite at least 80% compressor frequency last turn",
        })
    if not ctx.ventilation["co2_target_feasible"]:
        anomalies.append({
            "kind": "other",
            "severity": "warning",
            "evidence": "maximum outdoor airflow cannot reach the CO2 target within the horizon",
        })
    return anomalies


def next_agent_state(ctx: TurnContext, final_mv: dict[str, Any], energy: dict[str, Any], next_prediction: dict[str, Any]) -> dict[str, Any]:
    prior = ctx.previous_agent_state
    cumulative = dict(prior.get("cumulative") or {})
    hours = ctx.dt_s / 3600.0
    plan_kwh = float(cumulative.get("energy_kwh", 0.0)) + energy["plan_kw"] * hours
    baseline_kwh = float(cumulative.get("baseline_kwh", 0.0)) + energy["baseline_kw"] * hours
    elapsed = float(cumulative.get("elapsed_seconds", 0.0)) + ctx.dt_s
    now = ctx.clip_end_s if ctx.clip_end_s is not None else elapsed
    off_since = prior.get("compressor_off_since_seconds")
    if _compressor_running(final_mv):
        off_since = None
    elif off_since is None:
        off_since = now
    return {
        "version": 1,
        "turn_index": int(prior.get("turn_index", 0)) + 1,
        "last_clip_end_seconds": ctx.clip_end_s,
        "last_control_snapshot": {key: round(ctx.current[key], 3) for key in sorted(TARGET_DEFAULTS)},
        "stale_turns": ctx.stale_turns,
        "empty_turns": ctx.empty_turns,
        "last_occupancy": ctx.facts.occupancy,
        "voice_bias_c": round(ctx.voice_bias_c, 3),
        "pm_high_turns": ctx.pm_high_turns,
        "compressor_off_since_seconds": off_since,
        "inferred_sources": {
            "tvoc": round(ctx.env.tvoc_source, 4),
            "pm25": round(ctx.env.pm25_source, 4),
            "pm10": round(ctx.env.pm10_source, 4),
        },
        "predicted_next_state": next_prediction,
        "cumulative": {
            "elapsed_seconds": round(elapsed, 2),
            "energy_kwh": round(plan_kwh, 4),
            "baseline_kwh": round(baseline_kwh, 4),
            "saved_kwh": round(baseline_kwh - plan_kwh, 4),
        },
    }


def predict_after(ctx: TurnContext, final_mv: dict[str, Any], seconds: float) -> dict[str, Any]:
    prediction = physics.simulate(ctx.env, ctx.state, final_mv, max(seconds, 10.0), sample_s=max(seconds, 10.0))
    point = prediction.points[-1]
    return {"after_seconds": round(seconds, 1), **{k: point[k] for k in ("temperature_c", "rh_pct", "co2_ppm", "tvoc_ppb", "pm25_ug_m3", "pm10_ug_m3")}}
