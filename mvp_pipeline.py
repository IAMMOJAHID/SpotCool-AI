import asyncio
import os
import re
from collections import deque
from functools import lru_cache
from typing import Any, Literal, Optional, Sequence

from pydantic import BaseModel, Field
from openai import AsyncOpenAI
from openai import BadRequestError


BOX_REGEX = re.compile(r"x1=(\d+);y1=(\d+);x2=(\d+);y2=(\d+);w=(\d+);h=(\d+)")


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

    # Temperature
    temperature_c: Optional[float] = None

    # Fan and airflow
    fan_speed_mode: Optional[
        Literal["LOW", "MED", "HIGH", "AUTO", "OFF"]
    ] = None

    fan_mode: Optional[
        Literal["ON", "AUTO", "OFF"]
    ] = None

    airflow_rate_m3h: Optional[float] = None

    # Louvers and air direction
    louver_vertical_deg: Optional[int] = Field(None, ge=0, le=90)
    louver_horizontal_deg: Optional[int] = Field(None, ge=0, le=180)
    swing_mode: Optional[bool] = None

    # Compressor
    compressor_mode: Optional[
        Literal["COOL", "ECO", "FAN_ONLY", "OFF"]
    ] = None

    compressor_frequency_hz: Optional[float] = None
    valve_open_pct: Optional[int] = Field(None, ge=0, le=100)

    # Energy and comfort
    eco_mode: Optional[bool] = None
    sleep_mode: Optional[bool] = None
    turbo_mode: Optional[bool] = None

    # Humidity
    humidity_target_pct: Optional[float] = Field(None, ge=0, le=100)
    dehumidification_mode: Optional[bool] = None

    # Ventilation and air quality
    fresh_air_damper_pct: Optional[int] = Field(None, ge=0, le=100)

    ventilation_mode: Optional[
        Literal["AUTO", "ON", "OFF"]
    ] = None

    # Maintenance and protection
    filter_cleaning_alert: Optional[bool] = None
    defrost_mode: Optional[bool] = None


class SceneSemantics(BaseModel):
    occupant_count: int
    metabolic_rate_category: Literal["resting", "sedentary", "light_activity", "high_activity"]
    thermal_insulation_level: Literal["minimal", "light", "moderate", "heavy"]
    comfort_indicator: Literal["neutral", "warm_discomfort", "cool_discomfort"]
    envelope_state: Literal["sealed", "open"]


async def run_sam31(b64_frame: str, concept: str) -> list[dict]:
    resp = await get_client().responses.create(
        model="sam-3.1",
        input=[{"type": "message", "role": "user", "content": [
            {"type": "input_text", "text": concept},
            {"type": "input_image", "image_url": f"data:image/jpeg;base64,{b64_frame}"}
        ]}]
    )
    boxes = []
    for m in BOX_REGEX.finditer(resp.output_text):
        x1, y1, x2, y2, w, h = map(int, m.groups())
        boxes.append({"cx": round((x1 + x2) / (2 * w), 3), "cy": round((y1 + y2) / (2 * h), 3),
                      "z_est_m": round(1.8 / max((y2 - y1) / h, 0.1), 2)})
    return boxes

async def run_muse_spark(b64_grids: Sequence[str]) -> dict:
    content = [
        {
            "type": "input_text",
            "text": "Evaluate room thermal load parameters across these frames."
        }
    ]

    for b64_grid in b64_grids:
        content.append({
            "type": "input_image",
            "image_url": f"data:image/jpeg;base64,{b64_grid}"
        })
    
    resp = await get_client().responses.parse(
        model="muse-spark-1.3",
        input=[{"type": "message", "role": "user", "content": content}],
        text_format=SceneSemantics
    )
    return resp.output_parsed.model_dump()

async def process_window(
    b64_frames: deque[str],
    current_control_state: dict[str, Any],
    current_manipulated_state: dict[str, Any],
    turn_history: deque[dict[str, Any]],
) -> tuple[dict[str, Any], dict[str, Any], Optional[str]]:
    # 1. Fire SAM 3.1 (person + door) and Muse Spark in parallel (~1.2s total wall time)
    b64_latest = b64_frames[-1]
    b64_grids = b64_frames
    persons, doors, semantics = await asyncio.gather(
        run_sam31(b64_latest, "humans"),
        run_sam31(b64_latest, "fan"),
        run_muse_spark(b64_grids)
    )

    current_telemetry = {
        "persons": persons,
        "doors": doors,
        "semantics": semantics
    }
    
    target_control_variables ={
        "target_temperature_c": 24,
        "target_humidity_pct": 45,
        "target_co2_ppm": 800,
        "target_set_pm25_ug_m3": 10,
        "target_pm10_ug_m3": 20,
        "target_tvoc_ppb": 250,
    }

    SYSTEM_INSTRUCTIONS = (
        "You are an automated ASHRAE building management controller. "
        "Analyze numeric zone sensor telemetry and active HVAC setpoints. "
        "Return the JSON setpoint delta. Set unchanged parameters to null."
    )

    # 2. Reason & Diff with Muse Wonder
    prompt = f"""
    Recent Turn History (last {len(turn_history)} ticks): {list(turn_history)}
    From your previous suggestion, We have adjusted the Manipulated variables to {current_manipulated_state} and got a control_variables {current_control_state}.
    Our aim is to achieve this state of control variables: {target_control_variables}
    We analysed the last few clips of the camera of the room after your last response and here is the details:
    SAM 3.1 Spatial Persons: {persons} | Doors: {doors}
    Muse Spark Semantics: {semantics}
    Analyse these information throughly and give me a new values of Manipulated variables so that we can achive the {target_control_variables}.
    Output the JSON formate of new value of manipulated variables. Set any variable that does NOT need to change right now to null.
    """
    try:
        decision = await get_client().responses.parse(
            model="muse-wonder-1.0",
            reasoning={"effort": "minimal"},
            input=[{"type": "message", "role": "user", "content": prompt}],
            text_format=ManipulatedVariables
        )
        output_dict = decision.output_parsed.model_dump()
        response_id = decision.id
    except BadRequestError as e:
        print(f"[Warning] Tick filtered or rejected ({e.status_code}), holding previous HVAC state.")
        # Return all None so hardware holds steady and the video loop continues smoothly
        output_dict = ManipulatedVariables().model_dump()
        response_id = None

    return output_dict, current_telemetry, response_id
