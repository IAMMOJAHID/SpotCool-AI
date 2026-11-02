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
# SpotCool AI Blueprint

> **Meta Global AI Developer Hackathon Project**  
> Eliminating up to 60% of HVAC energy waste through real-time, human-centric 3D spot cooling.

---

## System Overview

Traditional air conditioning wastes up to 60% of its energy by cooling unoccupied space or ignoring individual thermal comfort differences. **SpotCool AI** replaces passive, uniform room cooling with an autonomous multi-agent edge system powered by Meta's AI ecosystem (SAM 3, Llama Vision, Llama Orchestration).

Instead of maintaining a constant cooling load across entire cubic volumes, SpotCool AI tracks occupants in 3D space, determines clothing insulation and comfort signals, and dynamically actuates smart louvers and fan speeds directly toward occupied zones.

```
[Webcam Feed] ──► Visual & Spatial Agent (SAM 3 + Depth + VLM) ──┐
                                                                 ▼
[Mic Stream]  ──► Acoustic Agent (Silero VAD + Intent Parsing) ─► [Canonical JSON State Bus]
                                                                 │
          ┌──────────────────────────────────────────────────────┘
          ▼
[Thermodynamic Policy Orchestrator (Meta Llama)] ──► "Acts as Human with Remote"
          │
          ├──► [AC Digital Twin (Appliance Emulator / Stepper Slew & Telemetry)]
          └──► [Judge Telemetry Dashboard (Real-to-Sim Mirror & Wattage Comparison)]
```

### Core Agents

1. **Visual & Spatial Agent (SAM 3 + Monocular Depth):**
   * Segments human occupants from camera feeds.
   * Computes median depth and back-projects 2D pixels into $(X, Y, Z)$ real-world coordinates:
     $$X = \frac{(u - c_x) \cdot Z}{f_x}, \quad Y = \frac{(v - c_y) \cdot Z}{f_y}$$
   * Calculates dynamic louver angles: $\theta_{\text{pan}} = \arctan2(X, Z)$, $\theta_{\text{tilt}} = \arctan2(Y, Z)$.
   * Evaluates clothing insulation (`Clo` values) and flags open door/window energy leaks via Llama Vision.

2. **Acoustic & Implicit Feedback Agent (VAD + Audio Classification):**
   * Continuously monitors microphone input using Silero Voice Activity Detection.
   * Transcribes implicit thermal remarks (e.g., *"It's freezing in here"*, *"Turn it up"*) to dynamic temperature offsets and fan overrides.
   * Updates per-user in-context comfort memory.

3. **Thermodynamic Policy Orchestrator (Meta Llama):**
   * Acts as the autonomous decision-maker ("the human with the smart remote").
   * Integrates spatial coordinates, clothing insulation, and ambient temperature into ASHRAE Standard 55 / Fanger PMV thermal comfort models.
   * Dispatches structured control payloads (`POST /ac-twin/command`) to actuate blade angles, fan speed tiers, and eco modes.

4. **AC Digital Twin & Telemetry:**
   * A stateful software emulator representing the physical split-AC unit.
   * Simulates mechanical stepper motor slew rates (15°/sec), fan RPM spin-up curves, and compressor electrical load.
   * Generates live energy telemetry comparing baseline uniform cooling against SpotCool AI in real time.

### Tech Stack

* **Computer Vision:** Meta Segment Anything 3 (SAM 3), Depth Anything V2 / MiDaS, OpenCV
* **Language & Vision Models:** Meta Llama 3.3 / Llama 4, Meta Llama Vision
* **Audio & Speech:** Silero VAD, Whisper / Fast Audio Intent Parsing
* **Backend:** Python, FastAPI, WebSockets, asyncio state loop
* **Frontend / Digital Twin:** React, Three.js / SVG Interactive Twin, Web Audio API
* **Cloud & Model Hosting:** Baseten, Fireworks AI, Modal

---

## Subdirectories & System Boundaries

- [docs](./docs/README.md): Central documentation directory containing workflow guides, architectural specifications, and approved EGM design records.
  - [docs/workflow](./docs/workflow/README.md): Claude Code and AI assistant master workflows, prompts, and best-practice protocols.
- [.claude](./.claude/rules/backend.md): Path-scoped agentic rules and environment configuration.

---

## Global Invariants

- **Canonical JSON State Bus:** All agents exchange state via strictly typed Pydantic contracts reflecting the 4 process variable classes (Set, Manipulated, Disturbance, Controlled).
- **Appliance-Only Twin:** The digital twin models only the AC appliance ("AC Clone") receiving remote commands; the real room is observed via sensors.
- **Actuation Slew Rate:** Actuator transitions are constrained to $15^\circ/\text{sec}$ stepper motor slew rate without instantaneous angle jumps.
- **Thermodynamic Grounding:** Comfort scores and setpoint adjustments must be computed deterministically via Fanger's PMV model (ASHRAE 55) rather than unconstrained LLM estimates.

---

## Global Side Effects

- Dispatches remote actuation commands to the AC Digital Twin emulator (`POST /ac-twin/command`).
- Broadcasts real-time kinematic (10 Hz) and thermodynamic (0.5 Hz) telemetry over WebSockets (`/ws/twin-state`).
- Accesses local webcam video stream and microphone audio stream for real-time spatial and acoustic perception.

---

## System Verification

- Run: `pytest tests/` (or `pytest tests/test_state_bus.py -k "test_louver_angle"`)
