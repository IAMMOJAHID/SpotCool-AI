# SpotCool-AI

SpotCool's window-processing pipeline runs as a RabbitMQ worker. RabbitMQ accepts
jobs on `spotcool.jobs`; workers publish durable result records to
`spotcool.results`. Multiple worker containers can consume jobs concurrently.

## Run with Docker Compose

1. Copy `.env.example` to `.env` and set `META_API_KEY` to a valid Meta AI API
   key. Change the example RabbitMQ password before starting.
2. Start RabbitMQ and one worker:

   ```sh
   docker compose up --build -d
   ```

3. Scale processing workers as needed:

   ```sh
   docker compose up --build --scale worker=3 -d
   ```

4. Follow worker logs with `docker compose logs -f worker`.

RabbitMQ's management UI is at <http://localhost:15672>. Its username and
password are `RABBITMQ_DEFAULT_USER` and `RABBITMQ_DEFAULT_PASS` from `.env`.
The AMQP port is `5672`.

The worker calls the existing four-argument `process_window` function with
`b64_frames`, `current_control_state`, `current_manipulated_state`, and
`turn_history`. Set `WORKER_CONCURRENCY` to control each worker's RabbitMQ
prefetch/concurrency. A job is acknowledged after a result is published; a
processing error is returned as a `failed` result. Jobs and results are
persistent. Since a worker may publish a result and disconnect before
acknowledging the job, clients should de-duplicate results by `job_id`.

## Process a local video

The client samples one frame every two seconds and groups samples into
consecutive one-minute clips. Each clip becomes a separate RabbitMQ job and is
submitted only after the previous clip's result arrives. A full minute
typically contains 30 JPEG frames; the final clip can be shorter. The returned
control delta updates the state sent with the next clip, and the most recent
three telemetry/delta pairs are carried forward as turn history.

Run the client from the project environment:

```powershell
.\.venv\Scripts\python.exe -m pip install -r requirements.txt
.\.venv\Scripts\python.exe rabbitmq_client.py "C:\videos\room.mp4" --timeout 300
```

For each clip, the client creates and publishes a payload like this to
`spotcool.jobs` (the JPEG strings are abbreviated here):

```json
{
  "job_id": "unique-id-for-this-clip",
  "clip_index": 0,
  "clip_start_seconds": 0,
  "b64_frames": ["<base64 JPEG>", "<base64 JPEG>"],
  "current_control_state": {"target_temperature_c": 30},
  "current_manipulated_state": {"temperature_c": 24.0},
  "turn_history": []
}
```

The client sets the AMQP `correlation_id` to `job_id`, waits for the response
on a temporary exclusive reply queue, and prints each clip's control update.
The worker also writes every result to the durable `spotcool.results` queue,
so results remain available if a submitter times out or disconnects. The
client uses the RabbitMQ connection settings in `.env`; outside Docker it
connects to `localhost:5672`.

The temporary reply queue returns a response to that submitter without other
clients competing for it. `spotcool.results` is a shared competing-consumer
queue; for recovery or auditing, use one result-dispatching consumer to persist
and route messages by `job_id` rather than having independent clients consume
from it directly.

A successful response has this shape:

```json
{
  "job_id": "b6c7d31a-2f35-4d61-9a52-17e1204f8d3f",
  "status": "completed",
  "result": {
    "temperature_c": 24.0
  },
  "telemetry": {
    "persons": [],
    "doors": [],
    "semantics": {}
  },
  "response_id": "..."
}
```

For unsuccessful processing, `status` is `failed` and `error` includes an
exception type and message. A separate consumer can read `spotcool.results`
using the same `job_id` / `correlation_id` to reconcile timed-out submissions.
