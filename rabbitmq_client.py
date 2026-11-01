import argparse
import asyncio
import base64
import json
import os
from collections import deque
from io import BytesIO
from pathlib import Path
from typing import Any
from uuid import uuid4

import aio_pika
from aio_pika import DeliveryMode, Message
import cv2
from dotenv import load_dotenv
from PIL import Image


async def submit_and_wait(
    job: dict[str, Any],
    timeout: float = 300,
) -> dict[str, Any]:
    load_dotenv()
    job_id = job.get("job_id")
    if not isinstance(job_id, str) or not job_id.strip():
        raise ValueError("The job must include a non-empty string job_id.")
    if timeout <= 0:
        raise ValueError("timeout must be greater than zero.")

    job_body = json.dumps(job, separators=(",", ":")).encode("utf-8")
    connection = await aio_pika.connect_robust(
        host=os.getenv("RABBITMQ_HOST", "localhost"),
        port=int(os.getenv("RABBITMQ_PORT", "5672")),
        login=os.getenv(
            "RABBITMQ_USER",
            os.getenv("RABBITMQ_DEFAULT_USER", "spotcool"),
        ),
        password=os.getenv(
            "RABBITMQ_PASSWORD",
            os.getenv("RABBITMQ_DEFAULT_PASS", ""),
        ),
        virtualhost=os.getenv("RABBITMQ_VHOST", "/"),
        reconnect_interval=5,
    )

    async with connection:
        channel = await connection.channel()
        jobs = await channel.declare_queue(
            os.getenv("JOB_QUEUE_NAME", "spotcool.jobs"),
            durable=True,
        )
        results = await channel.declare_queue(
            os.getenv("RESULT_QUEUE_NAME", "spotcool.results"),
            durable=True,
        )
        pending_messages: list[aio_pika.abc.AbstractIncomingMessage] = []
        try:
            await channel.default_exchange.publish(
                Message(
                    body=job_body,
                    content_type="application/json",
                    correlation_id=job_id,
                    delivery_mode=DeliveryMode.PERSISTENT,
                    message_id=str(uuid4()),
                ),
                routing_key=jobs.name,
                mandatory=True,
            )

            deadline = asyncio.get_running_loop().time() + timeout
            while True:
                # Check the currently-ready messages once per pass so unrelated,
                # requeued results cannot prevent this job's result being reached.
                queue_state = await results.declare()
                for _ in range(queue_state.message_count):
                    message = await results.get(fail=False)
                    if message is None:
                        break

                    try:
                        result = json.loads(message.body)
                    except (UnicodeDecodeError, json.JSONDecodeError):
                        pending_messages.append(message)
                        continue

                    result_job_id = result.get("job_id") if isinstance(result, dict) else None
                    if (
                        message.correlation_id == job_id
                        or result_job_id == job_id
                    ):
                        await message.ack()
                        return result

                    pending_messages.append(message)

                remaining = deadline - asyncio.get_running_loop().time()
                if remaining <= 0:
                    raise TimeoutError(
                        f"Timed out waiting for job {job_id} on result queue "
                        f"{results.name!r}."
                    )
                await asyncio.sleep(min(0.5, remaining))
        finally:
            for message in pending_messages:
                await message.nack(requeue=True)


def frame_to_b64(frame: Any) -> str:
    rgb = cv2.cvtColor(frame, cv2.COLOR_BGR2RGB)
    image = Image.fromarray(rgb)
    buffer = BytesIO()
    image.save(buffer, format="JPEG", quality=85)
    return base64.b64encode(buffer.getvalue()).decode("utf-8")


async def process_video(video_path: Path, timeout: float) -> None:
    capture = cv2.VideoCapture(str(video_path))
    if not capture.isOpened():
        raise RuntimeError(f"Could not open video: {video_path}")

    fps = capture.get(cv2.CAP_PROP_FPS)
    if fps <= 0:
        capture.release()
        raise RuntimeError(f"Video has an invalid frame rate: {video_path}")

    current_control_state = {
        "target_temperature_c": 30,
        "target_humidity_pct": 35,
        "target_co2_ppm": 900,
        "target_set_pm25_ug_m3": 15,
        "target_pm10_ug_m3": 25,
        "target_tvoc_ppb": 300,
    }
    current_manipulated_state: dict[str, Any] = {
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
    }
    turn_history: deque[dict[str, Any]] = deque(maxlen=3)
    clip_frames: list[str] = []
    clip_index = 0
    next_sample_time = 0.0
    frame_index = 0

    async def submit_clip(index: int, frames: list[str]) -> None:
        if not frames:
            return

        job_id = str(uuid4())
        job = {
            "job_id": job_id,
            "clip_index": index,
            "clip_start_seconds": index * 60,
            "b64_frames": frames,
            "current_control_state": current_control_state,
            "current_manipulated_state": current_manipulated_state,
            "turn_history": list(turn_history),
        }
        print(f"Submitting clip {index + 1} ({len(frames)} sampled frames)...")
        result = await submit_and_wait(job, timeout=timeout)
        if result.get("status") != "completed":
            raise RuntimeError(
                f"Clip {index + 1} failed: "
                f"{json.dumps(result.get('error', result), indent=2)}"
            )

        delta = result.get("result")
        telemetry = result.get("telemetry")
        if not isinstance(delta, dict) or not isinstance(telemetry, dict):
            raise RuntimeError(f"Clip {index + 1} returned an invalid result: {result}")

        current_manipulated_state.update(
            {key: value for key, value in delta.items() if value is not None}
        )
        turn_history.append({"telemetry": telemetry, "decision_delta": delta})
        print(
            f"Clip {index + 1} completed "
            f"(response_id={result.get('response_id')})."
        )
        print(f"Control update: {delta}")

    try:
        while True:
            success, frame = capture.read()
            if not success:
                break

            current_time = frame_index / fps
            current_clip_index = int(current_time // 60)
            if current_clip_index != clip_index:
                await submit_clip(clip_index, clip_frames)
                clip_frames = []
                clip_index = current_clip_index

            if current_time >= next_sample_time:
                clip_frames.append(frame_to_b64(frame))
                next_sample_time += 2.0

            frame_index += 1

        await submit_clip(clip_index, clip_frames)
    finally:
        capture.release()

    print("Video processing completed.")


async def main() -> None:
    load_dotenv()

    video_path="C:/Users/ch720/OneDrive/Desktop/SpotCool_AI/SpotCool-AI/videos/2.mp4"

    await process_video(video_path, timeout=300)


if __name__ == "__main__":
    asyncio.run(main())
