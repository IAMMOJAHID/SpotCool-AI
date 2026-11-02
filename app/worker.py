import asyncio
import json
import logging
import os
from typing import Optional

import aio_pika
from aio_pika import DeliveryMode, Message
from dotenv import load_dotenv
from pydantic import BaseModel, Field

if __package__:
    from .mvp_pipeline import process_window
else:
    from mvp_pipeline import process_window


load_dotenv()

LOGGER = logging.getLogger("spotcool.worker")


class WindowJob(BaseModel):
    job_id: str = Field(min_length=1)
    video_path: str = Field(min_length=1)
    clip_start_seconds: float = Field(ge=0)
    clip_end_seconds: float = Field(gt=0)
    previous_turn_state_file_path: Optional[str] = None
    current_control_variables: dict[str, object]


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


async def main() -> None:
    concurrency = _positive_int_from_env("WORKER_CONCURRENCY", 2)
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
        await channel.set_qos(prefetch_count=concurrency)

        jobs = await channel.declare_queue(
            os.getenv("JOB_QUEUE_NAME", "spotcool.jobs"),
            durable=True,
        )
        result_queue = os.getenv("RESULT_QUEUE_NAME", "spotcool.results")
        await channel.declare_queue(result_queue, durable=True)
        exchange = channel.default_exchange

        async def handle_message(
            message: aio_pika.abc.AbstractIncomingMessage,
        ) -> None:
            correlation_id = message.correlation_id
            if not correlation_id:
                LOGGER.error("Rejecting job message without a correlation_id.")
                await message.reject(requeue=False)
                return

            async with message.process(requeue=True):
                try:
                    job = WindowJob.model_validate_json(message.body)
                    if job.job_id != correlation_id:
                        raise ValueError(
                            "job_id must match the AMQP correlation_id."
                        )
                    if job.clip_end_seconds <= job.clip_start_seconds:
                        raise ValueError(
                            "clip_end_seconds must be greater than "
                            "clip_start_seconds."
                        )

                    response_id, saved_file_path = await process_window(
                        job.video_path,
                        job.clip_start_seconds,
                        job.clip_end_seconds,
                        job.previous_turn_state_file_path,
                        job.current_control_variables,
                        job.job_id,
                    )
                    result: dict[str, object] = {
                        "job_id": job.job_id,
                        "status": "completed",
                        "response_id": response_id,
                        "saved_file_path": saved_file_path,
                    }
                except Exception as exc:
                    LOGGER.exception("Job %s failed.", correlation_id)
                    result = {
                        "job_id": correlation_id,
                        "status": "failed",
                        "error": {
                            "type": type(exc).__name__,
                            "message": str(exc),
                        },
                    }

                result_body = json.dumps(
                    result,
                    separators=(",", ":"),
                ).encode("utf-8")
                result_message = Message(
                    body=result_body,
                    content_type="application/json",
                    correlation_id=correlation_id,
                    delivery_mode=DeliveryMode.PERSISTENT,
                )
                await exchange.publish(result_message, routing_key=result_queue)

                reply_to = message.reply_to
                if reply_to and reply_to.startswith("amq.gen-"):
                    await exchange.publish(
                        Message(
                            body=result_body,
                            content_type="application/json",
                            correlation_id=correlation_id,
                        ),
                        routing_key=reply_to,
                        mandatory=False,
                    )

        await jobs.consume(handle_message)
        LOGGER.info(
            "Consuming %s; publishing results to %s (prefetch=%s).",
            jobs.name,
            result_queue,
            concurrency,
        )
        await asyncio.Future()


if __name__ == "__main__":
    load_dotenv()
    logging.basicConfig(
        level=os.getenv("LOG_LEVEL", "INFO").upper(),
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    )
    asyncio.run(main())
