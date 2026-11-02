# SpotCool-AI

## Closed-loop turn pipeline (`run_turn.py`)

Each recorded clip is one turn. The turn's processing runs in two stages:

1. `sam-3.1` tracks "person" and "open door" on a 1 FPS, 720p copy of the clip, while `muse-voice-transcribe-1.0` transcribes any audio.
2. `muse-spark-1.3` reads a 3x2 storyboard of keyframes labelled with the SAM IDs.

From there:

- Python estimates each occupant's position and the louver angles.
- It computes a grounded comfort reward from what it observed.
- `muse-wonder-1.0` acts as critic and policy for the next turn's settings.
- A deterministic safety layer applies the overrides, EMA smoothing, the ±1.5 °C clamp, the compressor restart lockout and the VRF quantization.

The code is in `app/spotcool_agent/`.

```powershell
.\.venv\Scripts\python.exe -m pip install -r requirements-dev.txt
.\.venv\Scripts\python.exe run_turn.py --video videos\sample_1.mp4 --turn 1        # live (META_API_KEY in .env)
.\.venv\Scripts\python.exe run_turn.py --video c1.mp4 c2.mp4 c3.mp4                # one turn per clip, in order
.\.venv\Scripts\python.exe run_turn.py --video videos\2.mp4 --segment-seconds 60   # long clip -> consecutive turns
.\.venv\Scripts\python.exe run_turn.py --mode mock --reset --video videos\sample_1.mp4
.\.venv\Scripts\python.exe -m pytest
```

**Modes.**

- `--mode live` calls the Meta API. Add `--record` to save the raw responses (masks stripped) under `runs/<episode>/cassette/`.
- `--mode replay --cassette DIR` replays those responses offline.
- `--mode mock` returns canned responses and uses the rule-based policy, so it needs no network.
- `tests/fixtures/cassettes/demo_beats` holds hand-written cassettes for four demo beats: neutral, a cold complaint, the door propped open, and the door closed.

**Clip extras.** Each clip can carry two sidecar files:

- `<clip>.wav`: complaint audio;
- `<clip>.txt`: typed complaints, one per line, like the world's `POST /chat`.

`--audio` and `--complaint "..."` attach the same to the first clip. Clips can be any length; turns that are not back to back take `--gap-s`. `ffmpeg` comes from `imageio-ffmpeg` when it is not on PATH.

**Outputs** in `runs/<episode>/`:

- `turn_NNN.json`: S_t, A_{t-1}, the critic's and the grounded reward, the proposed and applied settings with reasons, the overrides, and `vrf_command`;
- `turn_NNN_grid.jpg` and `turn_NNN_hud.jpg` (`--no-previews` turns these off);
- `episode_history.json`: every turn, with the controller state before and after it. Re-running a turn starts from turn t-1's saved state and drops the later turns; `--reset` starts the episode again.

**Calibration.** `--calibration calib.json` takes partial `settings`, `calibration` and `limits` objects (see `app/spotcool_agent/config.py`). Defaults:

- camera: HFOV 70°, mounted at 2.5 m, tilted 25° down;
- AC: at the camera, facing the same way;
- VRF: the Mitsubishi profile.

`vrf_command` follows the teammate contract (`VRF-Gateway-MeetingRoom`): the setpoint on a 0.5 °C grid and vane positions Pos1–Pos5 or Swing for the chosen brand profile.

## RabbitMQ worker

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

The client sets the AMQP `correlation_id` to `job_id` and waits for the matching
message directly on the durable `spotcool.results` queue. It acknowledges the
matching result and prints each clip's control update. Results belonging to
other jobs are left unacknowledged while the client searches, then requeued so
they remain available. The client uses the RabbitMQ connection settings in
`.env`; outside Docker it connects to `localhost:5672`.

`spotcool.results` is a shared competing-consumer queue. For multiple
simultaneous video clients, use a single result-dispatching consumer to route
messages by `job_id`; otherwise clients can temporarily reserve unrelated
results while waiting for their own.

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
