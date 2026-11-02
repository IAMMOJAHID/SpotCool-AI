"""Closed-loop demo client for the SpotCool worker.

Like rabbitmq_client.py, but after every turn it feeds the zone's predicted
response back as the next job's `current_control_variables`, so successive
turns show the room actually cooling. The building response comes from the
worker's own grey-box prediction (`telemetry.plan.final_prediction`) plus
optional noise; replace `next_control_variables` with a call to your virtual
building API to close the loop against the real simulator.

Each clip stands for `--minutes-per-turn` minutes of building time (5, 10 or
15, the prediction points the worker saves), which compresses a long pull-down
into a short video.
"""

import argparse
import asyncio
import json
import os
import random
from pathlib import Path, PurePosixPath
from typing import Any, Optional
from uuid import uuid4

from dotenv import load_dotenv

from rabbitmq_client import (
    _json_object_from_env,
    _positive_float_from_env,
    _video_duration_seconds,
    _worker_visible_video_path,
    submit_and_wait,
)

KEY_MAP = {
    "temperature_c": "target_temperature_c",
    "rh_pct": "target_humidity_pct",
    "co2_ppm": "target_co2_ppm",
    "pm25_ug_m3": "target_set_pm25_ug_m3",
    "pm10_ug_m3": "target_pm10_ug_m3",
    "tvoc_ppb": "target_tvoc_ppb",
}


def _host_path(saved_file_path: str, host_app_directory: Path) -> Path:
    """Map the worker's /app/... path onto the bind-mounted ./app directory."""
    posix = PurePosixPath(saved_file_path)
    if posix.parts[:2] == ("/", "app"):
        return host_app_directory.joinpath(*posix.parts[2:])
    return Path(saved_file_path)


def next_control_variables(
    turn: dict[str, Any],
    current: dict[str, Any],
    minutes_per_turn: float,
    noise: float,
) -> dict[str, Any]:
    points = turn["telemetry"].get("plan", {}).get("final_prediction") or []
    if not points:
        return current
    point = min(points, key=lambda p: abs(float(p["minute"]) - minutes_per_turn))
    updated = dict(current)
    for source, target in KEY_MAP.items():
        if target in updated and source in point:
            value = float(point[source]) * (1.0 + random.gauss(0.0, noise))
            updated[target] = round(value, 1)
    return updated


def _summary(index: int, turn: dict[str, Any]) -> str:
    telemetry = turn["telemetry"]
    if telemetry.get("pipeline_mode") != "cortex":
        return f"turn {index}: pipeline_mode={telemetry.get('pipeline_mode')}"
    current = turn["current_control_variables"]
    comfort = telemetry["comfort"]["now"]
    energy = telemetry["energy"]
    mv = turn["manipulated_variables"]
    return (
        f"turn {index:>2}: T={current.get('target_temperature_c')}C "
        f"RH={current.get('target_humidity_pct')}% CO2={current.get('target_co2_ppm')}ppm | "
        f"occupants={telemetry['semantics']['occupant_count']} PMV={comfort['pmv_mean']:+.2f} "
        f"PPD={comfort['ppd_mean']:.0f}% | setpoint={mv['temperature_c']}C fan={mv['fan_speed_mode']} "
        f"{mv['compressor_frequency_hz']}Hz damper={mv['fresh_air_damper_pct']}% | "
        f"{energy['plan_kw']:.2f}kW vs {energy['baseline_kw']:.2f}kW baseline | "
        f"saved {energy['cumulative']['saved_kwh']:.3f} kWh | "
        f"Wonder: {telemetry['supervisor'].get('base_plan')}"
    )


async def run(video_path: Path, clip_seconds: float, minutes_per_turn: float, noise: float, timeout: float, host_app_directory: Path) -> None:
    video_path = video_path.expanduser().resolve()
    duration = _video_duration_seconds(video_path)
    submitted_path = _worker_visible_video_path(video_path)
    current = _json_object_from_env(
        "INITIAL_CONTROL_VARIABLES",
        {
            "target_temperature_c": 30,
            "target_humidity_pct": 35,
            "target_co2_ppm": 900,
            "target_set_pm25_ug_m3": 15,
            "target_pm10_ug_m3": 25,
            "target_tvoc_ppb": 300,
        },
    )
    previous: Optional[str] = None
    start = 0.0
    index = 1
    while start < duration:
        end = min(start + clip_seconds, duration)
        job_id = str(uuid4())
        result = await submit_and_wait(
            {
                "job_id": job_id,
                "video_path": submitted_path,
                "clip_start_seconds": start,
                "clip_end_seconds": end,
                "previous_turn_state_file_path": previous,
                "current_control_variables": current,
            },
            timeout=timeout,
        )
        if result.get("status") != "completed":
            raise RuntimeError(f"Turn {index} failed: {json.dumps(result.get('error', result))}")
        previous = result["saved_file_path"]
        with _host_path(previous, host_app_directory).open("r", encoding="utf-8") as handle:
            turn = json.load(handle)
        print(_summary(index, turn), flush=True)
        current = next_control_variables(turn, current, minutes_per_turn, noise)
        start = end
        index += 1
    print("Closed-loop demo completed.")


async def main() -> None:
    load_dotenv()
    parser = argparse.ArgumentParser(description="Closed-loop SpotCool demo over RabbitMQ.")
    parser.add_argument("video_path", nargs="?", default=os.getenv("VIDEO_PATH"))
    parser.add_argument("--clip-seconds", type=float, default=15.0)
    parser.add_argument("--minutes-per-turn", type=float, default=5.0, choices=[5.0, 10.0, 15.0])
    parser.add_argument("--noise", type=float, default=0.002, help="Relative Gaussian sensor noise.")
    parser.add_argument("--timeout", type=float, default=_positive_float_from_env("JOB_TIMEOUT_SECONDS", 300.0))
    parser.add_argument("--host-app-directory", type=Path, default=Path(__file__).resolve().parent / "app")
    args = parser.parse_args()
    if not args.video_path:
        parser.error("video_path is required (or set VIDEO_PATH in .env).")
    await run(Path(args.video_path), args.clip_seconds, args.minutes_per_turn, args.noise, args.timeout, args.host_app_directory)


if __name__ == "__main__":
    asyncio.run(main())
