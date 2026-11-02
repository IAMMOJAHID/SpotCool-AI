import asyncio
import base64
import json
import os
import re
from functools import lru_cache
from io import BytesIO
from pathlib import Path
from typing import Any, Literal, Optional, Sequence
from uuid import uuid4

import cv2
from openai import AsyncOpenAI, BadRequestError
from PIL import Image
from pydantic import BaseModel, Field


BOX_REGEX = re.compile(r"x1=(\d+);y1=(\d+);x2=(\d+);y2=(\d+);w=(\d+);h=(\d+)")


def _positive_float_from_env(name: str, default: float) -> float:
    value = os.getenv(name, str(default))
    try:
        parsed = float(value)
    except ValueError as exc:
        raise RuntimeError(f"{name} must be a positive number.") from exc
    if parsed <= 0:
        raise RuntimeError(f"{name} must be a positive number.")
    return parsed


def _positive_int_from_env(name: str, default: int) -> int:
    value = os.getenv(name, str(default))
    try:
        parsed = int(value)
    except ValueError as exc:
        raise RuntimeError(f"{name} must be a positive integer.") from exc
    if parsed < 1:
        raise RuntimeError(f"{name} must be a positive integer.")
    return parsed


def _json_object_from_env(name: str, default: dict[str, Any]) -> dict[str, Any]:
    value = os.getenv(name)
    if not value:
        return default.copy()
    try:
        parsed = json.loads(value)
    except json.JSONDecodeError as exc:
        raise RuntimeError(f"{name} must contain a JSON object.") from exc
    if not isinstance(parsed, dict):
        raise RuntimeError(f"{name} must contain a JSON object.")
    return parsed


@lru_cache(maxsize=1)
def get_client() -> AsyncOpenAI:
    api_key = os.getenv("META_API_KEY")
    if not api_key:
        raise RuntimeError("META_API_KEY must be set to call the Meta AI API.")

    return AsyncOpenAI(
        base_url=os.getenv("META_API_BASE_URL", "https://api.meta.ai/v1"),
        api_key=api_key,
    )


class ManipulatedVariables(BaseModel):
    temperature_c: Optional[float] = None
    fan_speed_mode: Optional[Literal["LOW", "MED", "HIGH", "AUTO", "OFF"]] = None
    fan_mode: Optional[Literal["ON", "AUTO", "OFF"]] = None
    airflow_rate_m3h: Optional[float] = None
    louver_vertical_deg: Optional[int] = Field(None, ge=0, le=90)
    louver_horizontal_deg: Optional[int] = Field(None, ge=0, le=180)
    swing_mode: Optional[bool] = None
    compressor_mode: Optional[Literal["COOL", "ECO", "FAN_ONLY", "OFF"]] = None
    compressor_frequency_hz: Optional[float] = None
    valve_open_pct: Optional[int] = Field(None, ge=0, le=100)
    eco_mode: Optional[bool] = None
    sleep_mode: Optional[bool] = None
    turbo_mode: Optional[bool] = None
    humidity_target_pct: Optional[float] = Field(None, ge=0, le=100)
    dehumidification_mode: Optional[bool] = None
    fresh_air_damper_pct: Optional[int] = Field(None, ge=0, le=100)
    ventilation_mode: Optional[Literal["AUTO", "ON", "OFF"]] = None
    filter_cleaning_alert: Optional[bool] = None
    defrost_mode: Optional[bool] = None


class SceneSemantics(BaseModel):
    occupant_count: int
    metabolic_rate_category: Literal["resting", "sedentary", "light_activity", "high_activity"]
    thermal_insulation_level: Literal["minimal", "light", "moderate", "heavy"]
    comfort_indicator: Literal["neutral", "warm_discomfort", "cool_discomfort"]
    envelope_state: Literal["sealed", "open"]


def _encode_frame(frame: Any) -> str:
    rgb = cv2.cvtColor(frame, cv2.COLOR_BGR2RGB)
    image = Image.fromarray(rgb)
    buffer = BytesIO()
    image.save(
        buffer,
        format="JPEG",
        quality=_positive_int_from_env("JPEG_QUALITY", 85),
    )
    return base64.b64encode(buffer.getvalue()).decode("utf-8")


def _read_clip_frames(
    video_path: str | Path,
    clip_start_seconds: float,
    clip_end_seconds: float,
) -> list[str]:
    if clip_start_seconds < 0 or clip_end_seconds <= clip_start_seconds:
        raise ValueError("Clip timestamps must satisfy 0 <= start < end.")

    capture = cv2.VideoCapture(str(video_path))
    if not capture.isOpened():
        raise RuntimeError(f"Could not open video: {video_path}")

    try:
        fps = capture.get(cv2.CAP_PROP_FPS)
        if fps <= 0:
            raise RuntimeError(f"Video has an invalid frame rate: {video_path}")

        sample_interval = _positive_float_from_env(
            "FRAME_SAMPLE_INTERVAL_SECONDS",
            2.0,
        )
        max_frames = _positive_int_from_env("MAX_FRAMES_PER_CLIP", 30)
        clip_duration = clip_end_seconds - clip_start_seconds
        sample_step = max(sample_interval, clip_duration / max_frames)

        frames: list[str] = []
        sample_time = clip_start_seconds
        while sample_time < clip_end_seconds and len(frames) < max_frames:
            capture.set(cv2.CAP_PROP_POS_MSEC, sample_time * 1000)
            success, frame = capture.read()
            if not success:
                if not frames:
                    raise RuntimeError(
                        f"Could not read video frame at {sample_time:.3f}s "
                        f"from {video_path}."
                    )
                break
            frames.append(_encode_frame(frame))
            sample_time += sample_step

        if not frames:
            raise RuntimeError(
                f"No video frames found between {clip_start_seconds} and "
                f"{clip_end_seconds} seconds in {video_path}."
            )
        return frames
    finally:
        capture.release()


async def run_sam31(b64_frame: str, concept: str) -> list[dict[str, Any]]:
    response = await get_client().responses.create(
        model=os.getenv("SAM_MODEL", "sam-3.1"),
        input=[{
            "type": "message",
            "role": "user",
            "content": [
                {"type": "input_text", "text": concept},
                {
                    "type": "input_image",
                    "image_url": f"data:image/jpeg;base64,{b64_frame}",
                },
            ],
        }],
    )
    boxes = []
    for match in BOX_REGEX.finditer(response.output_text):
        x1, y1, x2, y2, width, height = map(int, match.groups())
        boxes.append({
            "cx": round((x1 + x2) / (2 * width), 3),
            "cy": round((y1 + y2) / (2 * height), 3),
            "z_est_m": round(1.8 / max((y2 - y1) / height, 0.1), 2),
        })
    return boxes


async def run_muse_spark(b64_grids: Sequence[str]) -> dict[str, Any]:
    content: list[dict[str, Any]] = [{
        "type": "input_text",
        "text": os.getenv(
            "SCENE_ANALYSIS_PROMPT",
            "Evaluate room thermal load parameters across these frames.",
        ),
    }]
    content.extend(
        {
            "type": "input_image",
            "image_url": f"data:image/jpeg;base64,{b64_grid}",
        }
        for b64_grid in b64_grids
    )

    response = await get_client().responses.parse(
        model=os.getenv("SCENE_MODEL", "muse-spark-1.3"),
        input=[{"type": "message", "role": "user", "content": content}],
        text_format=SceneSemantics,
    )
    if response.output_parsed is None:
        raise RuntimeError("Scene analysis returned no parsed response.")
    return response.output_parsed.model_dump()


async def process_frames(
    b64_frames: Sequence[str],
    current_control_variables: dict[str, Any],
    current_manipulated_variables: dict[str, Any],
    turn_history: Sequence[dict[str, Any]],
) -> tuple[dict[str, Any], dict[str, Any], Optional[str]]:
    if not b64_frames:
        raise ValueError("At least one sampled video frame is required.")

    persons, secondary_objects, semantics = await asyncio.gather(
        run_sam31(
            b64_frames[-1],
            os.getenv("SAM_PERSON_CONCEPT", "humans"),
        ),
        run_sam31(
            b64_frames[-1],
            os.getenv("SAM_SECONDARY_CONCEPT", "fan"),
        ),
        run_muse_spark(b64_frames),
    )
    telemetry = {
        "persons": persons,
        "doors": secondary_objects,
        "semantics": semantics,
    }

    target_control_variables = _json_object_from_env(
        "TARGET_CONTROL_VARIABLES",
        {
            "target_temperature_c": 24,
            "target_humidity_pct": 45,
            "target_co2_ppm": 800,
            "target_set_pm25_ug_m3": 10,
            "target_pm10_ug_m3": 20,
            "target_tvoc_ppb": 250,
        },
    )
    system_instructions = os.getenv(
        "CONTROL_SYSTEM_INSTRUCTIONS",
        "You are an automated ASHRAE building management controller. "
        "Analyze numeric zone sensor telemetry and active HVAC setpoints. "
        "Return the JSON setpoint delta. Set unchanged parameters to null.",
    )
    prompt = f"""
    {system_instructions}
    Recent Turn History (last {len(turn_history)} ticks): {list(turn_history)}
    The current manipulated variables are {current_manipulated_variables}; current building/room control variables are {current_control_variables}.
    Our aim is to achieve this state of control variables: {target_control_variables}
    We analysed the latest clip of the room and here are the details:
    SAM spatial persons: {persons} | Additional detected objects: {secondary_objects}
    Muse Spark semantics: {semantics}
    Analyse this information thoroughly and recommend new manipulated variables
    to achieve the target control variables. Return JSON and set every variable
    that does not need to change right now to null.
    """
    response_id: Optional[str]
    try:
        decision = await get_client().responses.parse(
            model=os.getenv("DECISION_MODEL", "muse-wonder-1.0"),
            reasoning={"effort": os.getenv("DECISION_REASONING_EFFORT", "minimal")},
            input=[{"type": "message", "role": "user", "content": prompt}],
            text_format=ManipulatedVariables,
        )
        if decision.output_parsed is None:
            raise RuntimeError("HVAC decision returned no parsed response.")
        output = decision.output_parsed.model_dump()
        response_id = decision.id
    except BadRequestError as exc:
        print(
            f"[Warning] Clip filtered or rejected ({exc.status_code}); "
            "holding previous HVAC state."
        )
        output = ManipulatedVariables().model_dump()
        response_id = None

    return output, telemetry, response_id


def _load_previous_state(
    previous_turn_state_file_path: Optional[str],
) -> tuple[dict[str, Any], list[dict[str, Any]]]:
    if previous_turn_state_file_path is None:
        initial_variables = _json_object_from_env(
            "INITIAL_MANIPULATED_VARIABLES",
            {
                "temperature_c": 24.0,
                "fan_speed_mode": "AUTO",
                "fan_mode": "AUTO",
                "airflow_rate_m3h": 500.0,
                "louver_vertical_deg": 60,
                "louver_horizontal_deg": 90,
                "swing_mode": False,
                "compressor_mode": "COOL",
                "compressor_frequency_hz": 40.0,
                "valve_open_pct": 50,
                "eco_mode": False,
                "sleep_mode": False,
                "turbo_mode": False,
                "humidity_target_pct": 50.0,
                "dehumidification_mode": False,
                "fresh_air_damper_pct": 0,
                "ventilation_mode": "AUTO",
                "filter_cleaning_alert": False,
                "defrost_mode": False,
            },
        )
        return initial_variables, []

    state_path = Path(previous_turn_state_file_path).expanduser()
    if not state_path.is_file():
        raise FileNotFoundError(
            f"Previous turn state file does not exist: {state_path}"
        )
    with state_path.open("r", encoding="utf-8") as state_file:
        previous_state = json.load(state_file)
    if not isinstance(previous_state, dict):
        raise ValueError("Previous turn state file must contain a JSON object.")

    manipulated_variables = previous_state.get("manipulated_variables")
    turn_history = previous_state.get("turn_history", [])
    if not isinstance(manipulated_variables, dict):
        raise ValueError(
            "Previous turn state file has no manipulated_variables object."
        )
    if not isinstance(turn_history, list) or not all(
        isinstance(turn, dict) for turn in turn_history
    ):
        raise ValueError("Previous turn state file has invalid turn_history.")
    return manipulated_variables, turn_history


async def process_window(
    video_path: str,
    clip_start_seconds: float,
    clip_end_seconds: float,
    previous_turn_state_file_path: Optional[str],
    current_control_variables: dict[str, Any],
    job_id: str,
) -> tuple[Optional[str], str]:
    frames = _read_clip_frames(
        video_path,
        clip_start_seconds,
        clip_end_seconds,
    )
    current_manipulated_variables, previous_history = _load_previous_state(
        previous_turn_state_file_path
    )
    max_history = _positive_int_from_env("TURN_HISTORY_LENGTH", 3)
    previous_history = previous_history[-max_history:]

    decision_delta, telemetry, response_id = await process_frames(
        frames,
        current_control_variables,
        current_manipulated_variables,
        previous_history,
    )
    updated_manipulated_variables = current_manipulated_variables.copy()
    updated_manipulated_variables.update(
        {
            key: value
            for key, value in decision_delta.items()
            if value is not None
        }
    )
    turn_history = previous_history + [{
        "clip_start_seconds": clip_start_seconds,
        "clip_end_seconds": clip_end_seconds,
        "telemetry": telemetry,
        "decision_delta": decision_delta,
    }]
    turn_history = turn_history[-max_history:]

    configured_output_dir = os.getenv("TURN_DETAILS_DIRECTORY")
    if configured_output_dir:
        output_dir = Path(configured_output_dir).expanduser()
        if not output_dir.is_absolute():
            output_dir = Path(__file__).resolve().parent / output_dir
    else:
        output_dir = Path(__file__).resolve().parent / "turn_details"
    output_dir.mkdir(parents=True, exist_ok=True)

    saved_file = output_dir / f"turn_{uuid4().hex}.json"
    details = {
        "job_id": job_id,
        "video_path": video_path,
        "clip_start_seconds": clip_start_seconds,
        "clip_end_seconds": clip_end_seconds,
        "sampled_frame_count": len(frames),
        "previous_turn_state_file_path": previous_turn_state_file_path,
        "current_control_variables": current_control_variables,
        "target_control_variables": _json_object_from_env(
            "TARGET_CONTROL_VARIABLES",
            {
                "target_temperature_c": 24,
                "target_humidity_pct": 45,
                "target_co2_ppm": 800,
                "target_set_pm25_ug_m3": 10,
                "target_pm10_ug_m3": 20,
                "target_tvoc_ppb": 250,
            },
        ),
        "previous_manipulated_variables": current_manipulated_variables,
        "decision_delta": decision_delta,
        "manipulated_variables": updated_manipulated_variables,
        "telemetry": telemetry,
        "response_id": response_id,
        "turn_history": turn_history,
    }
    with saved_file.open("w", encoding="utf-8") as details_file:
        json.dump(details, details_file, indent=2, ensure_ascii=False)
        details_file.write("\n")

    return response_id, str(saved_file.resolve())
