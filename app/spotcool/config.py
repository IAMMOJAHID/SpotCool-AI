import copy
import json
import os
import time
from typing import Any, Optional


def positive_float_from_env(name: str, default: float) -> float:
    value = os.getenv(name, str(default))
    try:
        parsed = float(value)
    except ValueError as exc:
        raise RuntimeError(f"{name} must be a positive number.") from exc
    if parsed <= 0:
        raise RuntimeError(f"{name} must be a positive number.")
    return parsed


def positive_int_from_env(name: str, default: int) -> int:
    value = os.getenv(name, str(default))
    try:
        parsed = int(value)
    except ValueError as exc:
        raise RuntimeError(f"{name} must be a positive integer.") from exc
    if parsed < 1:
        raise RuntimeError(f"{name} must be a positive integer.")
    return parsed


def non_negative_int_from_env(name: str, default: int) -> int:
    value = os.getenv(name, str(default))
    try:
        parsed = int(value)
    except ValueError as exc:
        raise RuntimeError(f"{name} must be a non-negative integer.") from exc
    if parsed < 0:
        raise RuntimeError(f"{name} must be a non-negative integer.")
    return parsed


def bool_from_env(name: str, default: bool) -> bool:
    value = os.getenv(name)
    if value is None or not value.strip():
        return default
    normalized = value.strip().lower()
    if normalized in {"1", "true", "yes", "on"}:
        return True
    if normalized in {"0", "false", "no", "off"}:
        return False
    raise RuntimeError(f"{name} must be true or false.")


def choice_from_env(name: str, default: str, choices: set[str]) -> str:
    value = os.getenv(name, default).strip().lower()
    if value not in choices:
        raise RuntimeError(f"{name} must be one of {sorted(choices)}.")
    return value


def json_object_from_env(name: str, default: dict[str, Any]) -> dict[str, Any]:
    value = os.getenv(name)
    if not value:
        return copy.deepcopy(default)
    try:
        parsed = json.loads(value)
    except json.JSONDecodeError as exc:
        raise RuntimeError(f"{name} must contain a JSON object.") from exc
    if not isinstance(parsed, dict):
        raise RuntimeError(f"{name} must contain a JSON object.")
    return parsed


def json_list_from_env(name: str, default: list[str]) -> list[str]:
    value = os.getenv(name)
    if not value:
        return list(default)
    try:
        parsed = json.loads(value)
    except json.JSONDecodeError as exc:
        raise RuntimeError(f"{name} must contain a JSON list of strings.") from exc
    if not isinstance(parsed, list) or not all(isinstance(item, str) for item in parsed):
        raise RuntimeError(f"{name} must contain a JSON list of strings.")
    return [item.strip() for item in parsed if item.strip()]


def parameters_from_env(name: str, defaults: dict[str, Any]) -> dict[str, Any]:
    """Overlay a JSON object from the environment onto documented defaults.

    Nested objects (for example cost weights) are merged key by key so an
    operator can override a single value without restating the whole block.
    """
    merged = copy.deepcopy(defaults)
    for key, value in json_object_from_env(name, {}).items():
        if isinstance(value, dict) and isinstance(merged.get(key), dict):
            merged[key].update(value)
        else:
            merged[key] = value
    return merged


def record_model_call(
    ledger: list[dict[str, Any]],
    stage: str,
    model: str,
    purpose: str,
    started: float,
    ok: bool,
    detail: Optional[str] = None,
) -> None:
    """Append one Meta model call to the turn's ledger (shown in telemetry.models)."""
    entry: dict[str, Any] = {
        "stage": stage,
        "model": model,
        "purpose": purpose,
        "ok": ok,
        "latency_s": round(time.monotonic() - started, 2),
    }
    if detail:
        entry["detail"] = detail
    ledger.append(entry)


DEFAULT_TARGET_CONTROL_VARIABLES: dict[str, Any] = {
    "target_temperature_c": 24,
    "target_humidity_pct": 45,
    "target_co2_ppm": 800,
    "target_set_pm25_ug_m3": 10,
    "target_pm10_ug_m3": 20,
    "target_tvoc_ppb": 250,
}

# Zone envelope and boundary conditions. Values a building API sends with the
# job (for example outdoor_temperature_c) take precedence over these.
ZONE_DEFAULTS: dict[str, Any] = {
    "floor_area_m2": 120.0,
    "ceiling_height_m": 3.0,
    "thermal_mass_multiplier": 8.0,
    "envelope_ua_w_per_k": 250.0,
    "infiltration_ach": 0.3,
    "open_door_airflow_m3h": 400.0,
    "open_window_airflow_m3h": 250.0,
    "outdoor_temperature_c": 32.0,
    "outdoor_rh_pct": 60.0,
    "outdoor_co2_ppm": 420.0,
    "outdoor_pm25_ug_m3": 12.0,
    "outdoor_pm10_ug_m3": 25.0,
    "outdoor_tvoc_ppb": 50.0,
    "space_type": "auto",
    "screen_heat_w": 80.0,
    "lighting_w_per_m2": 8.0,
    "solar_gain_w": {"none": 0.0, "partial": 800.0, "strong": 2000.0},
    "solar_radiant_delta_c": 3.0,
    "ventilation_effectiveness": 1.0,
    "particle_deposition_per_hour": 0.2,
}

# Inverter split / fan-coil capacity model.
HVAC_DEFAULTS: dict[str, Any] = {
    "rated_cooling_kw": 12.0,
    "rated_cop": 3.2,
    "rated_frequency_hz": 60.0,
    "min_frequency_hz": 20.0,
    "max_frequency_hz": 90.0,
    "max_airflow_m3h": 1800.0,
    "min_airflow_m3h": 300.0,
    "fan_rated_power_kw": 0.45,
    "fan_tier_fraction": {"LOW": 0.4, "MED": 0.65, "HIGH": 0.9, "AUTO": 0.65},
    "supply_air_temp_c": 13.0,
    "max_fresh_air_m3h": 1200.0,
    "valve_rangeability": 50.0,
    "valve_margin_pct": 5.0,
    "eco_capacity_fraction": 0.7,
    "turbo_capacity_fraction": 1.15,
    "setpoint_min_c": 18.0,
    "setpoint_max_c": 28.0,
    "humidity_target_min_pct": 30.0,
    "humidity_target_max_pct": 70.0,
    "louver_throw_m": 6.0,
    "louver_beam_half_angle_deg": 15.0,
    "ceiling_fan_air_speed_mps": 0.3,
    "filter_efficiency": 0.5,
    "compressor_min_off_seconds": 180.0,
}

# Camera pose used for ground-plane back-projection and louver aiming. The AC
# position is expressed in the same floor frame as the camera footprint.
CAMERA_DEFAULTS: dict[str, Any] = {
    "mount_height_m": 2.8,
    "tilt_down_deg": 30.0,
    "horizontal_fov_deg": 90.0,
    "ac_lateral_m": 0.0,
    "ac_forward_m": 0.0,
    "ac_height_m": 2.6,
    "standing_height_m": 1.7,
    "seated_height_m": 1.3,
    "target_height_m": 1.1,
}

CONTROL_DEFAULTS: dict[str, Any] = {
    "design_clo": 0.6,
    "design_met": 1.1,
    "target_pmv": 0.0,
    "offset_min_c": -1.0,
    "offset_max_c": 2.0,
    "setback_unoccupied_c": 2.5,
    "empty_turns_for_setback": 2,
    "horizon_minutes": 15.0,
    "turbo_threshold_c": 2.5,
    "dehumidify_margin_pct": 5.0,
    "draft_turbulence_pct": 40.0,
    "pm_alert_turns": 3,
    "voice_bias_step_c": 0.5,
    "voice_bias_limit_c": 1.5,
    "rate_limits": {
        "temperature_c": 1.0,
        "compressor_frequency_hz": 15.0,
        "valve_open_pct": 25,
        "fresh_air_damper_pct": 30,
        "louver_vertical_deg": 30,
        "louver_horizontal_deg": 30,
        "airflow_rate_m3h": 600.0,
        "humidity_target_pct": 10.0,
    },
    "deadbands": {
        "temperature_c": 0.3,
        "compressor_frequency_hz": 2.0,
        "valve_open_pct": 5,
        "fresh_air_damper_pct": 5,
        "louver_vertical_deg": 3,
        "louver_horizontal_deg": 3,
        "airflow_rate_m3h": 50.0,
        "humidity_target_pct": 2.0,
    },
    "weights": {
        "ppd": 2.0,
        "energy": 1.0,
        "iaq": 2.0,
        "draft": 0.5,
        "move": 0.1,
        "setpoint": 0.05,
    },
}

# Conventional uniform-cooling reference used for the savings KPI. A missing
# temperature_c means "the operator's target temperature".
BASELINE_DEFAULTS: dict[str, Any] = {
    "fan_speed_mode": "HIGH",
    "fresh_air_damper_pct": 60,
}
