# AGENTS.md: spotcool-agent

Every agent working in `packages/agent/` follows this guide. The repo-wide rules in the root
`AGENTS.md` still apply, and so does the code style of `packages/world/AGENTS.md` (Google
docstrings, types everywhere, ASCII logs, `time.perf_counter` for durations, comments only for
the why).

## Overview

`spotcool-agent` is SpotCool's agent: what runs in a building. Each BMS step it snapshots the
five zone cameras, counts people (SAM 3.1 on the Meta AI API, or offline a colour-blob
counter), and drives each zone's occupancy sensor and cooling setpoint over BACnet/IP at
priority 10 with the stub agent's policy. It publishes its perception, decisions and
explanations to the dashboard and acknowledges complaints on `/chat` and `/voice`.

It speaks only the building's interfaces (`docs/integration/PERSON_A_HANDOFF.md` sections
4-9), as defined in `packages/contracts`. **It never imports `spotcool_world`**: its tests and
evals may, to run the world on loopback.

The design follows [pipecat](https://github.com/pipecat-ai/pipecat)'s shape: service base
classes with one sub-package per provider, transports for every protocol, small pure
processors, observers, a runner. **One deliberate deviation: loops instead of a frame
pipeline.** Each BMS step is one bounded request and response (perceive, decide, act) with a
hard deadline, not a stream of frames, so a plain loop per concern is simpler and easier to
time: the building loop per step, the complaint loop, and in AG4 a wall-time VRF loop.

## Commands

From the repo root, with `UV_PROJECT_ENVIRONMENT` set to a venv outside OneDrive:

```bash
uv sync --all-packages --all-extras
uv run pytest packages/agent                     # unit and integration tests (loopback only)
uv run pytest packages/agent/evals -m eval       # the end-to-end day on RC, about 3 minutes
uv run spotcool-agent version
uv run spotcool-agent run --vision blob --dry-run   # the resolved settings, the key masked
uv run spotcool-agent run --vision blob             # against a running world, synthetic frames
uv run spotcool-agent run --vision meta --env-file C:/Users/hussa/dev/secrets/spotcool-agent.env
uv run spotcool-agent probe --vision meta --env-file C:/Users/hussa/dev/secrets/spotcool-agent.env
```

Start the agent first, then the world without its stub (`spotcool-world run --plant rc`).
`--world-host` defaults to this machine's primary IPv4 address, where the world's BMS binds by
default; pass `--world-host 127.0.0.1` for a world on loopback. If `SIM-CLOCK` goes unanswered
for 10 s the agent warns once. Ctrl+C releases every slot the agent holds, then stops. The run
log goes to stderr from a thread of its own (loguru's `enqueue`), so a console paused by
QuickEdit never stalls the loop.

## Architecture

```text
SIM-CLOCK (AV 950) --StepClock, 4 Hz--> BuildingLoop: settle -> 5 snapshots -> 5 detections
   -> occupancy -> AgentPolicy -> Actuator (BACnet writes) -> DashboardPublisher (/ws/agent)
POST /chat, /voice --AgentServer (202 at once)--> queue --> ComplaintLoop -> chat reply
```

### The building loop and its timing (`loops/building.py`)

The world never waits for the agent, so each round fits in the step it starts in:

1. `StepClock` (`transports/bacnet.py`) sees `SIM-CLOCK` change to S: the step [S - step, S)
   has ended, its measurements are on the points and its scene on the cameras. A reading with
   the fault flag set or no answer is ignored. A run's initial state, which the BMS shows with
   `HVAC-ENERGY` at exactly 0 before the first step, is no step's end. `SIM-CLOCK` going
   backwards, or an initial state after steps, is a new run: the actuator releases and forgets
   its points, the policy forgets its state, and the call cap and camera catalog start again.
2. It waits `settle_s` (1.0 s; the eval uses 0.3 at speed 300) from that moment, so each
   camera renders the step's scene. If `SIM-CLOCK` moved on meanwhile, the newer step is
   taken.
3. It fetches the five snapshots at once, then makes one `detect(jpeg, "humans")` per zone at
   once, each with a timeout of `min(8 s, deadline - now)`, where the deadline is
   `step_wall_s - max(2.5 s, 0.4 * step_wall_s)` after the change was seen (at speed 60, 9 s
   after it; at speed 300, 0.5 s). `step_wall_s` is the median of the last
   five measured intervals between changes, from three of them on (15 s before), so one late
   reading cannot set it. A result after the deadline is discarded; a zone without a count is
   unknown, whatever went wrong with it (a provider's bug included).
4. The policy decides for the step that starts at S; the actuator writes what changed. The
   BMS takes the writes at its next tick, so they take effect from that step.
5. The `perception`, `decision` and `explanation` events are stamped with the step's start
   (S - step), as the stub stamps its own with the state it acted on. The world's `write_lags`
   then pairs each decision with the write the BMS took one step later.

At speed 300 a round takes about 0.35 s with the blob counter (0.3 s of it the settle); with
SAM 3.1 (p50 1.9 s, p95 3.2 s for five calls at once) it needs speed 60 or slower. One bad step
never ends the day: a step that raises is logged once per kind of failure and the loop goes
on; only a cancel stops it.

### The policy (`processors/policy.py`)

The stub's, constants unchanged (`PRECOOL_S` 1800, `EMPTY_SETPOINT_C` 28.0, 19:00): an empty
zone is Unoccupied at 28 degC, a zone with people is Occupied with its setpoint released, a
booked room is held from 30 minutes before to the booking's end, and from 19:00 everything is
released. A test drives the stub's policy and this one with the scenario's own counts over
three library days and requires the same command for every zone at every step. What only a
real agent meets: an unknown zone keeps its command for one step, then counts as occupied; a
step with no count at all, or the model-call cap used up, releases everything.

## Services and providers

| Provider | Where | For |
|---|---|---|
| `VisionService` | `services/vision.py` | the base: `detect(jpeg, concept, timeout_s, *, camera_id)` -> `Detections`, or `VisionError` |
| `SamVision` | `services/meta/vision.py` | SAM 3.1: Person A's request, boxes below 0.3 dropped, one retry on HTTP 429 only |
| `MetaClient` | `services/meta/client.py` | one `AsyncOpenAI` per process, made in the running loop; 5 calls at once; a ledger (model, purpose, latency, ok; never pixels); the run's call cap (`--sam-call-cap`, 600) |
| `BlobVision` | `services/fake/blob.py` | the world's synthetic frames: saturated blobs over 400 pixels, exact there |
| `ScriptedVision` | `services/fake/scripted.py` | fixed counts per camera, for unit tests and dry runs |

`parse_sam_output` and `Detection` (`domain/geometry.py`) are Person A's, near-verbatim.

## Transports

- **`BacnetClient`** (`transports/bacnet.py`): device `SpotCool-Agent`, instance 1910, its own
  UDP port (47820 by default; any free one with `bacnet_port: 0`), on the address that reaches
  the world. It addresses the BMS directly (no Who-Is). Requests go one at a time: with
  BACpypes3 0.0.110, several requests to a device that does not answer yet leave the client
  deaf to it for good, which bit the agent when it started before the world.
- **`WorldHttp`** (`transports/world_http.py`): the camera catalog, snapshots (memory only)
  and the room calendar, each checked against its contract model.
- **`DashboardPublisher`** (`transports/dashboard.py`): `/ws/agent` only. At most 64 KB per
  envelope, a token bucket of 10 refilled at 9 a second (at most 19 a second, under the
  world's 20), a queue of 500 that drops its oldest, reconnect with backoff; `publish()` never
  blocks, and a message whose send failed is never sent twice.
- **`AgentServer`** (`transports/server.py`): FastAPI on `127.0.0.1:8002`: `POST /chat`,
  `POST /voice` (multipart, at most 1,000,000 bytes, a 16 kHz mono 16-bit WAV), `GET /healthz`.
  A valid complaint is queued and answered 202 at once; a full queue answers 503. CORS lets the
  world's dashboard page and `http://localhost:5173` post with `content-type` and
  `x-spotcool-client`.

## Rules

- **Privacy.** Snapshots and audio live in memory only; nothing in `spotcool_agent` writes a
  file, and the run log goes to stderr. A voice body stays below Starlette's 1 MiB spool, so
  the form parser never moves it to disk. Logs carry exception types and messages, never
  tracebacks' locals (`diagnose=False`).
- **Ground truth.** The agent connects to `/ws/agent` only, never `/ws/events`, and reads no
  `scenario_state` or `camera_scene`. It perceives through the cameras like any building's
  agent; the report compares its perception with the truth after the run.
- **Actuation.** Priority 10 only; write on a change only, releases first within a zone (a zone
  filling up gets its setpoint back before its sensor says Occupied); read the priority array
  before every zone write and back off from slots 1-9 for a value; never release a slot that
  is not the agent's (slot 10 must hold its value, or one whose write was cut off in flight);
  toggle `SPOTCOOL-HEARTBEAT` every 30 s; release everything at 19:00, on a normal stop, on
  Ctrl+C and on a crash (`Agent.run`'s `finally` stops the loops first, then releases, then
  releases the heartbeat's slot last).
- **Deviation from the stub: releases do not back off.** A NULL at priority 10 can never override
  a more urgent slot, so the agent releases its own slot 10 even while an operator holds slot
  8: at 19:00, at stop and on a zone release. The stub backs off from those too, which leaves
  its value in force under the operator's once the operator lets go.
- **The sim epoch** (`SIM_EPOCH`, 2026-01-01) is the agent's own constant; a test checks it
  against the world's.

## Testing

- `tests/`: the parser on a real SAM answer (`fixtures/meva_cafe_1280_humans.txt`), SAM with a
  fake client (timeout, 429, the cap, 5 at once), the policy table and its equivalence with
  the stub's, the actuator against a fake BACnet seam, the step clock, the blob counter on the
  world's synthetic frames, the dashboard transport, the server, the settings and the dry run,
  and integration tests against the world's real BMS, camera, calendar and dashboard servers on
  loopback (`bacnet`-marked where they open UDP sockets).
- `tests/test_boundaries.py`: every agent module imported in a subprocess without
  `spotcool_world`; no ground-truth feed named in the source; an audit hook on `open` scoped to
  stacks with agent code, while the agent works against the world's servers, sees no file
  opened to write, and the logs and console hold no JPEG bytes.
- `evals/` (marker `eval`, CI's evals job): `hybrid_tuesday` on RC with the agent at speed 300
  and the blob counter, against a stub run of the same day. It checks the saving against the
  stub's, the write lags, the report's agent activity and agreement, no heartbeat release,
  and every slot released by 19:15.
- No test calls the Meta API.

## Known limitations

- **SAM sees nobody in the synthetic silhouettes**, so a live run with `--vision meta` needs
  real footage on the cameras; CI and the eval use the blob counter.
- **An unpaced or very fast run** leaves no time for a round: the deadline (step wall time minus
  2.5 s) is in the past and every zone is unknown, so the agent releases everything.
- **A run starting with zero HVAC energy** (a night with the plant off) looks like an initial
  state until the plant uses energy, so those steps are not acted on; the occupied day always
  uses energy.
- **Voice is acknowledged, not understood**: AG4 adds transcription and replies with Meta
  models, and the VRF loop.
