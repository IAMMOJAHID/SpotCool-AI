import argparse
import asyncio
import json
import os
from pathlib import Path, PurePosixPath
from typing import Any
from uuid import uuid4

import aio_pika
import cv2
from aio_pika import DeliveryMode, Message
from dotenv import load_dotenv


def _positive_int_from_env(name: str, default: int) -> int:
    value = os.getenv(name, str(default))
    try:
        parsed = int(value)
    except ValueError as exc:
        raise RuntimeError(f"{name} must be a positive integer.") from exc
    if parsed < 1:
        raise RuntimeError(f"{name} must be a positive integer.")
    return parsed


def _positive_float_from_env(name: str, default: float) -> float:
    value = os.getenv(name, str(default))
    try:
        parsed = float(value)
    except ValueError as exc:
        raise RuntimeError(f"{name} must be a positive number.") from exc
    if parsed <= 0:
        raise RuntimeError(f"{name} must be a positive number.")
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


def _worker_visible_video_path(local_path: Path) -> str:
    host_directory = os.getenv("VIDEO_HOST_DIRECTORY")
    worker_directory = os.getenv("VIDEO_WORKER_DIRECTORY")
    resolved_video_path = local_path.expanduser().resolve()

    if not worker_directory:
        return str(resolved_video_path)
    if not host_directory:
        normalized_video_path = resolved_video_path.as_posix()
        normalized_worker_directory = PurePosixPath(worker_directory).as_posix()
        if normalized_video_path.startswith(normalized_worker_directory.rstrip("/") + "/"):
            return normalized_video_path
        raise RuntimeError(
            "VIDEO_HOST_DIRECTORY must be set when VIDEO_WORKER_DIRECTORY is set."
        )

    resolved_host_directory = Path(host_directory).expanduser().resolve()
    try:
        relative_video_path = resolved_video_path.relative_to(resolved_host_directory)
    except ValueError as exc:
        raise ValueError(
            f"Video path {resolved_video_path} must be inside "
            f"VIDEO_HOST_DIRECTORY ({resolved_host_directory})."
        ) from exc

    worker_path = PurePosixPath(worker_directory)
    for part in relative_video_path.parts:
        worker_path /= part
    return str(worker_path)


async def submit_and_wait(
    job: dict[str, Any],
    timeout: float,
) -> dict[str, Any]:
    job_id = job.get("job_id")
    if not isinstance(job_id, str) or not job_id.strip():
        raise ValueError("The job must include a non-empty string job_id.")
    if timeout <= 0:
        raise ValueError("timeout must be greater than zero.")

    job_body = json.dumps(job, separators=(",", ":")).encode("utf-8")
    connection = await aio_pika.connect_robust(
        host=os.getenv("RABBITMQ_HOST", "localhost"),
        port=_positive_int_from_env("RABBITMQ_PORT", 5672),
        login=os.getenv(
            "RABBITMQ_USER",
            os.getenv("RABBITMQ_DEFAULT_USER", "spotcool"),
        ),
        password=os.getenv(
            "RABBITMQ_PASSWORD",
            os.getenv("RABBITMQ_DEFAULT_PASS", ""),
        ),
        virtualhost=os.getenv("RABBITMQ_VHOST", "/"),
        reconnect_interval=_positive_float_from_env(
            "RABBITMQ_RECONNECT_INTERVAL_SECONDS",
            5.0,
        ),
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

                    result_job_id = (
                        result.get("job_id") if isinstance(result, dict) else None
                    )
                    if (
                        isinstance(result, dict)
                        and (
                            message.correlation_id == job_id
                            or result_job_id == job_id
                        )
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


def _video_duration_seconds(video_path: Path) -> float:
    capture = cv2.VideoCapture(str(video_path))
    if not capture.isOpened():
        raise RuntimeError(f"Could not open video: {video_path}")
    try:
        fps = capture.get(cv2.CAP_PROP_FPS)
        frame_count = capture.get(cv2.CAP_PROP_FRAME_COUNT)
        if fps <= 0 or frame_count <= 0:
            raise RuntimeError(f"Video has invalid duration metadata: {video_path}")
        return frame_count / fps
    finally:
        capture.release()


async def process_video(video_path: Path, timeout: float) -> None:
    video_path = video_path.expanduser().resolve()
    if not video_path.is_file():
        raise FileNotFoundError(f"Video does not exist: {video_path}")
    duration = _video_duration_seconds(video_path)
    submitted_video_path = _worker_visible_video_path(video_path)
    clip_duration = _positive_float_from_env("CLIP_DURATION_SECONDS", 60.0)
    current_control_variables = _json_object_from_env(
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
    previous_turn_state_file_path: str | None = None

    clip_start_seconds = 0.0
    clip_index = 0
    while clip_start_seconds < duration:
        clip_end_seconds = min(clip_start_seconds + clip_duration, duration)
        job_id = str(uuid4())
        job = {
            "job_id": job_id,
            "video_path": submitted_video_path,
            "clip_start_seconds": clip_start_seconds,
            "clip_end_seconds": clip_end_seconds,
            "previous_turn_state_file_path": previous_turn_state_file_path,
            "current_control_variables": current_control_variables,
        }
        print(
            f"Submitting clip {clip_index + 1} "
            f"({clip_start_seconds:.2f}-{clip_end_seconds:.2f}s)..."
        )
        result = await submit_and_wait(job, timeout=timeout)
        if result.get("status") != "completed":
            raise RuntimeError(
                f"Clip {clip_index + 1} failed: "
                f"{json.dumps(result.get('error', result), indent=2)}"
            )

        saved_file_path = result.get("saved_file_path")
        if not isinstance(saved_file_path, str) or not saved_file_path:
            raise RuntimeError(
                f"Clip {clip_index + 1} returned no saved_file_path: {result}"
            )
        previous_turn_state_file_path = saved_file_path
        print(
            f"Clip {clip_index + 1} completed "
            f"(response_id={result.get('response_id')}, "
            f"saved_file_path={saved_file_path})."
        )

        clip_start_seconds = clip_end_seconds
        clip_index += 1

    print("Video processing completed.")


async def main() -> None:
    load_dotenv()
    parser = argparse.ArgumentParser(
        description="Submit video clips to the SpotCool RabbitMQ worker."
    )
    parser.add_argument(
        "video_path",
        nargs="?",
        default=os.getenv("VIDEO_PATH"),
        help="Local video path; it must be under VIDEO_HOST_DIRECTORY when using Docker.",
    )
    parser.add_argument(
        "--timeout",
        type=float,
        default=_positive_float_from_env("JOB_TIMEOUT_SECONDS", 300.0),
        help="Maximum number of seconds to wait for each clip result.",
    )
    args = parser.parse_args()
    if not args.video_path:
        parser.error("video_path is required (or set VIDEO_PATH in .env).")
    await process_video(Path(args.video_path), timeout=args.timeout)


if __name__ == "__main__":
    asyncio.run(main())
