import asyncio
import json
import logging
import os
from collections import deque
from typing import Any

import aio_pika
from aio_pika import DeliveryMode, Message
from pydantic import BaseModel, Field

from mvp_pipeline import process_window


LOGGER = logging.getLogger("spotcool.worker")
JOB_QUEUE = os.getenv("JOB_QUEUE_NAME", "spotcool.jobs")
RESULT_QUEUE = os.getenv("RESULT_QUEUE_NAME", "spotcool.results")


class WindowJob(BaseModel):
    job_id: str = Field(min_length=1)
    b64_frames: list[str] = Field(min_length=1)
    current_control_state: dict[str, Any]
    current_manipulated_state: dict[str, Any]
    turn_history: list[dict[str, Any]]


def _positive_int_from_env(name: str, default: int) -> int:
    value = os.getenv(name, str(default))
    try:
        parsed = int(value)
    except ValueError as exc:
        raise RuntimeError(f"{name} must be a positive integer.") from exc
    if parsed < 1:
        raise RuntimeError(f"{name} must be a positive integer.")
    return parsed


async def main() -> None:
    concurrency = _positive_int_from_env("WORKER_CONCURRENCY", 2)
    connection = await aio_pika.connect_robust(
        host=os.getenv("RABBITMQ_HOST", "localhost"),
        port=_positive_int_from_env("RABBITMQ_PORT", 5672),
        login=os.getenv("RABBITMQ_USER", "spotcool"),
        password=os.getenv("RABBITMQ_PASSWORD", ""),
        virtualhost=os.getenv("RABBITMQ_VHOST", "/"),
        reconnect_interval=5,
    )

    async with connection:
        channel = await connection.channel()
        await channel.set_qos(prefetch_count=concurrency)

        jobs = await channel.declare_queue(JOB_QUEUE, durable=True)
        await channel.declare_queue(RESULT_QUEUE, durable=True)
        exchange = channel.default_exchange

        async def handle_message(message: aio_pika.abc.AbstractIncomingMessage) -> None:
            correlation_id = message.correlation_id
            if not correlation_id:
                LOGGER.error("Rejecting job message without a correlation_id.")
                await message.reject(requeue=False)
                return

            async with message.process(requeue=True):
                try:
                    job = WindowJob.model_validate_json(message.body)
                    if job.job_id != correlation_id:
                        raise ValueError("job_id must match the AMQP correlation_id.")

                    manipulated_variables, telemetry, response_id = await process_window(
                        deque(job.b64_frames),
                        job.current_control_state,
                        job.current_manipulated_state,
                        deque(job.turn_history),
                    )
                    result: dict[str, Any] = {
                        "job_id": job.job_id,
                        "status": "completed",
                        "result": manipulated_variables,
                        "telemetry": telemetry,
                        "response_id": response_id,
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

                result_body = json.dumps(result, separators=(",", ":")).encode("utf-8")
                result_message = Message(
                    body=result_body,
                    content_type="application/json",
                    correlation_id=correlation_id,
                    delivery_mode=DeliveryMode.PERSISTENT,
                )
                await exchange.publish(result_message, routing_key=RESULT_QUEUE)

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
            JOB_QUEUE,
            RESULT_QUEUE,
            concurrency,
        )
        await asyncio.Future()


if __name__ == "__main__":
    logging.basicConfig(
        level=os.getenv("LOG_LEVEL", "INFO").upper(),
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    )
    asyncio.run(main())
