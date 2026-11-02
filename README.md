# SpotCool-AI

SpotCool processes video clips through a RabbitMQ worker. Each job contains a
video path and a half-open clip range (`clip_start_seconds` inclusive,
`clip_end_seconds` exclusive); video frames are decoded and sampled by the
worker rather than copied into RabbitMQ messages. The worker saves a complete
JSON record for every turn and publishes its path with the model response ID.

## Run with Docker Compose

1. Copy `.env.example` to `.env`. Set `META_API_KEY` and change the example
   RabbitMQ password.
2. Set `VIDEO_HOST_DIRECTORY` to a host directory that contains the video
   (for example `C:/videos` on Windows). This directory is mounted read-only
   inside the worker at `VIDEO_WORKER_DIRECTORY` (default `/videos`).
3. Start RabbitMQ and one worker:

   ```powershell
   docker compose up --build -d
   ```

4. Scale processing workers as needed:

   ```powershell
   docker compose up --build --scale worker=3 -d
   ```

5. Follow worker logs with `docker compose logs -f worker`.

The worker's `app` directory is mounted into the container, so turn records
written under `app/turn_details` persist on the host. RabbitMQ's management UI
is at <http://localhost:15672>; its credentials are
`RABBITMQ_DEFAULT_USER` and `RABBITMQ_DEFAULT_PASS` from `.env`.

## Process a local video

Put the video under `VIDEO_HOST_DIRECTORY` and submit its host path. The client
maps it to the worker-mounted path, divides it into clips of
`CLIP_DURATION_SECONDS`, and waits for each result before submitting the next
clip. The `saved_file_path` returned by one turn is sent as
`previous_turn_state_file_path` on the next turn. The worker reads prior
manipulated variables and recent turn history from that JSON file.

```powershell
.\.venv\Scripts\python.exe -m pip install -r requirements.txt
.\.venv\Scripts\python.exe rabbitmq_client.py "C:\videos\room.mp4"
```

Each job published to `spotcool.jobs` has this shape:

```json
{
  "job_id": "unique-id-for-this-clip",
  "video_path": "/videos/room.mp4",
  "clip_start_seconds": 0,
  "clip_end_seconds": 60,
  "previous_turn_state_file_path": null,
  "current_control_variables": {
    "target_temperature_c": 30,
    "target_humidity_pct": 35
  }
}
```

The successful RabbitMQ result contains the correlation `job_id`, model
`response_id`, and path to the saved turn details. It intentionally does not
include control variables or telemetry; both can be read from the JSON file.

```json
{
  "job_id": "unique-id-for-this-clip",
  "status": "completed",
  "response_id": "...",
  "saved_file_path": "/app/turn_details/turn_<id>.json"
}
```

For unsuccessful processing, `status` is `failed` and `error` includes the
exception type and message. A separate consumer can read `spotcool.results`
using the same `job_id` / AMQP `correlation_id` to reconcile timed-out
submissions.

## Configuration

All runtime settings are read from environment variables (the defaults below
are also listed in `.env.example`):

| Variable | Purpose |
| --- | --- |
| `META_API_KEY`, `META_API_BASE_URL` | API credentials and endpoint |
| `SAM_MODEL`, `SCENE_MODEL`, `DECISION_MODEL` | Model identifiers |
| `SAM_PERSON_CONCEPT`, `SAM_SECONDARY_CONCEPT`, `SCENE_ANALYSIS_PROMPT`, `CONTROL_SYSTEM_INSTRUCTIONS`, `DECISION_REASONING_EFFORT` | Analysis prompts and inference behavior |
| `TARGET_CONTROL_VARIABLES` | JSON object describing desired environmental values |
| `INITIAL_CONTROL_VARIABLES` | JSON object describing the building/room state submitted with each job |
| `INITIAL_MANIPULATED_VARIABLES` | JSON object used before a first prior-turn file exists |
| `RABBITMQ_HOST`, `RABBITMQ_PORT`, `RABBITMQ_USER`, `RABBITMQ_PASSWORD`, `RABBITMQ_VHOST`, `RABBITMQ_RECONNECT_INTERVAL_SECONDS` | AMQP connection settings |
| `JOB_QUEUE_NAME`, `RESULT_QUEUE_NAME`, `WORKER_CONCURRENCY`, `LOG_LEVEL` | Worker and queue settings |
| `JOB_TIMEOUT_SECONDS`, `CLIP_DURATION_SECONDS` | Client job timeout and video clip size |
| `FRAME_SAMPLE_INTERVAL_SECONDS`, `MAX_FRAMES_PER_CLIP`, `JPEG_QUALITY` | Worker frame sampling/encoding |
| `TURN_HISTORY_LENGTH`, `TURN_DETAILS_DIRECTORY` | Prior-turn context and JSON output location |
| `VIDEO_PATH`, `VIDEO_HOST_DIRECTORY`, `VIDEO_WORKER_DIRECTORY` | Optional default input path and host/container video mapping |

JSON environment variables must contain valid JSON objects. The target setpoints
are worker configuration; the current building/room values are sent with every
job and may be changed independently by the job producer.
