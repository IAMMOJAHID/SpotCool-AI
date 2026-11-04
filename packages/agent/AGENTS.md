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
explanations to the dashboard. With `--vrf` it also runs the meeting room's spot cooling: the
live phone camera's people aim the VRF unit's louvers, in wall time. Complaints on `/chat` and
`/voice` are transcribed (`muse-voice-transcribe-1.0`), understood (keywords, then
`muse-wonder-1.0`), acted on in the meeting room's VRF unit only, and answered in plain words.
AG8 added `muse-spark-1.3` (the scene: clothing, activity, doors) for the meeting room's
comfort setpoint and for explanation-only scene notes on the zones, and muse-wonder's
one-sentence narration of each step that wrote. AG9 added `muse-image-1.0`'s privacy renders
of the cameras (`--render`, off by default), served at `/privacy/<camera>.jpg` for the
dashboard's `?privacy=on`, display only. **None of it changes a BMS write.**

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
# the louvers demo: the meeting room's VRF loop, voice and chat on Meta's models
uv run spotcool-agent run --vrf --vision meta --stt meta --llm meta --env-file C:/Users/hussa/dev/secrets/spotcool-agent.env
uv run spotcool-agent run --vrf --vision blob --stt fake --llm off   # offline, no Meta calls
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
POST /chat, /voice --AgentServer (202 at once)--> queue --> ComplaintLoop: STT (voice) ->
   intent (keywords, then LLM) -> VrfLoop.act (meeting room only) -> reply (LLM or template)
--vrf, every 0.25 s: VrfLoop: cam-meeting-live snapshot -> detect (2 in flight) -> locate ->
   louver aim -> brand's vane states -> its own Actuator (VRF gateway, UDP 47810)
   every 60 s with people seen: snapshot -> Muse Spark -> PMV 0 (domain/comfort.py) -> IDU-SP
after a step's writes, in the background: SceneNotes (Muse Spark per occupied zone, every 4
   steps) and Narrator (muse-wonder, one sentence) -> explanations only
--render, every 180 s, a task apart: RenderLoop: each zone camera, then cam-meeting-live:
   snapshot -> Muse Image (1 at a time) -> latest render in memory -> GET /privacy/<id>.jpg
```

### The meeting room's VRF loop (`loops/vrf.py`)

Off by default (`--vrf`), and off in the scored run: it never touches the BMS zones, and
nothing it does can hold up the building loop. It has its own BACnet client and socket
(`SpotCool-Agent-VRF`, instance 1911, UDP `--vrf-bacnet-port` 47821) and its own SAM budget
(2 calls at once, `--vrf-call-cap` 3000). The unit runs on the wall clock, so the loop does
too, every 0.25 s:

1. **Brand.** It reads the gateway's `Model_Name` (device 2001), which the world sets to
   `SpotCool simulated VRF gateway (<profile label>)`, and matches the label in
   `load_vrf_profiles()`. While the gateway does not answer it asks again every 5 s and writes
   no vane. `--vrf-brand` is used only when the gateway answers with a name that is no known
   brand's; without it then, the vanes are left alone (complaints still change the setpoint and
   fan).
2. **State.** Once the gateway has answered, a task of its own reads `IDU-ROOM-T`, `IDU-SP`,
   `IDU-FAN` and the vanes every 2 s, stopping at the first read without an answer. Replies
   quote these last-known values and leave out what is unknown; they never wait on the gateway.
3. **Look.** `cam-meeting-live` from the catalog; at most 2 looks (snapshot and
   `detect(jpeg, "humans")`) in flight, at most the camera's 2 frames a second. A result older
   than one already applied is dropped. Each result is a `perception` with
   `masks: {"cam-meeting-live": [...]}` and `people: {}`. When its call cap is used up, it stops
   looking and releases the vanes; the building is not touched.
4. **Aim** (`domain/louvers.py`, Person A's `locate`, `louver_angles_to` and `louver_aim`):
   every person seen, weighted by box area; the camera's pose from `--vrf-camera-height-m`,
   `--vrf-camera-tilt-deg`, `--vrf-camera-hfov-deg` (A's 2.8 m, 30 deg, 90 deg; the unit under
   it at 2.6 m, aiming at 1.1 m). Vertical = -tilt and horizontal = pan (x -1 with
   `--vrf-mirror`), clamped and snapped to the profile's nearest position; when the pan spread
   exceeds 1.5 x the 15 deg beam, Swing, on the horizontal axis, or on the vertical one when the
   brand has no horizontal axis (A's swing is one flag). An axis the brand lacks is never
   written (Daikin and LG horizontal); LG gets Swing or a release (its default, Pos3, is swing
   off). Nobody seen for `--vrf-hold-s` (10 s): the vanes are released.
5. **Write**, through an `Actuator` of the gateway's points (`IDU-VANE-V`, `IDU-VANE-H`,
   `IDU-SP`, `IDU-FAN`): priority 10, on a change only, backing off from slots 1-9, releasing
   only its own slot, and at most one write per point a second. Each write is a `decision`
   (zone `sou`, the reason "aim at 1 person at 3.2 m, 1.2 m right"); an aim change is
   explained at most every 10 s. VRF events carry no `sim_time_s`: the world's report pairs
   decisions with BMS steps, and these belong to none.
6. **Comfort** (AG8; with a scene reader, `--no-comfort` turns it off). Every
   `comfort_every_s` (60) while people are seen, a task of its own snapshots the live camera
   and asks the scene reader (Muse Spark, `--scene`) who is there, with SAM's count as a hint.
   Person A's `comfort_temperature` (`domain/comfort.py`) gives the air temperature at PMV 0
   in still air (0.1 m/s) at 50 % RH (the gateway has no RH point), put on the 0.5 grid within
   16-30 degC and written to `IDU-SP` by the same actuator rules (reason "comfort: PMV 0 for
   the people seen"), explained when it changes. Precedence: the operator (back-off), then a
   complaint's override, then comfort; with nobody seen for `--vrf-hold-s` it is released. A
   failed or invalid reading changes nothing.
7. **Stop** cancels the looks and a comfort reading, lets a state read under way finish, and
   releases every VRF slot held, after the BMS's release and within 5 s.

### Complaints (`loops/complaints.py`)

`/chat` and `/voice` still answer 202 at once; the loop then takes each complaint in turn:

1. **Heard.** Voice is transcribed by `STTService` (6 s); without text the occupant is asked
   to type it. The voice `meta` text is never used as what was said.
2. **Understood** (`processors/intents.py`): `cold`, `hot`, `draft`, `stuffy`. Keywords with
   simple synonyms settle a complaint that names exactly one; the rest go to `LLMService` with
   a JSON-only prompt (4 s); anything else is `none`.
3. **Acted on**, the meeting room's VRF only, with `--vrf`: cold -> `IDU-SP` +1 degC (16-30, 0.5
   grid) and the fan a step down; hot -> -1 degC and a step up; stuffy -> a step up (Auto counts
   as Mid); draft -> the vertical vane to Pos1, the aiming paused for `--draft-hold-s` (600).
   Setpoint and fan overrides last `--complaint-hold-s` (900 wall seconds), then are released.
   Before claiming a change, `act()` reads the point's priority array, waiting at most 1.5 s
   (the read is left to finish, never cut off): a point a slot 1-9 holds is left, and the
   reply says it is held by the building operator (or the priority); one that does not answer
   is left too, and the reply says so.
   A complaint is about the meeting room when its `room_hint` is `meeting-south`, or a real
   occupant (`source: occupant`, the dashboard) gives no room; any other, or no intent, changes
   nothing. **The BMS zones are never written from a complaint.**
4. **Answered.** `LLMService` writes at most two sentences (8 s) from A's `state_card` idea:
   the last perception's zone counts, the unit's `IDU-ROOM-T`, `IDU-SP`, fan and vanes, and the
   action; otherwise a template ("Thanks, I've raised the meeting room's setpoint to 25.0 degC
   and turned its fan down to Low."). One `chat` and one `explanation` per complaint; the
   writes are the VRF actuator's `decision`s.

`--stt` and `--llm` default to `meta` with `--vision meta` and to `fake` and `off` otherwise,
so an offline run (the eval's) makes no Meta call. `FakeSTT` hears nothing unless given words,
so with `--stt fake` a voice message is answered "Sorry, I couldn't hear that; please type it."
(the agent warns once at start with `--vrf`). The transcriber and the language model share a
budget of their own: 2 calls at once, `--llm-call-cap` (200).

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
6. Only then, in the background and never awaited by the step: with a scene reader,
   `SceneNotes` (`loops/scene.py`) reads each occupied zone's snapshot of that step at most
   every `scene_every_steps` (4), skipping a zone still being read (30 s each), and publishes
   "Muse Spark, West: 1 person, typical office clothing; a door is open", plus A's cross-check
   ("SAM 3.1 saw N, Muse Spark M: the policy keeps SAM's") when |Spark - SAM| > max(2, 0.3 *
   max). With `--llm meta`, `Narrator` (`loops/narration.py`) makes one call (8 s, one at a
   time) when the step's writes changed what the actuator holds, grounded on each written
   zone's count and reason, and publishes the first sentence as "Muse Wonder: ...". A failure
   says nothing. Both get the counts after the policy used them; `run()`'s `finally` cancels
   them.

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
| `MetaClient` | `services/meta/client.py` | one budget: an `AsyncOpenAI` made in the running loop, a limiter, a ledger (model, purpose, latency, ok; never pixels or audio) and a call cap, through `call()`. One per budget: the building's SAM (5 at once, `--sam-call-cap` 600, AG1's), the VRF loop's SAM (2, `--vrf-call-cap` 3000), the language services' (2, `--llm-call-cap` 200), the scene's (3, `--scene-call-cap` 300) and the renders' (1, `--render-call-cap` 60) |
| `BlobVision` | `services/fake/blob.py` | the world's synthetic frames: saturated blobs over 400 pixels, exact there |
| `ScriptedVision` | `services/fake/scripted.py` | fixed counts per camera, for unit tests and dry runs |
| `STTService` | `services/stt.py` | the base: `transcribe(wav, timeout_s)` -> text, or `STTError` |
| `MetaSTT` | `services/meta/stt.py` | `muse-voice-transcribe-1.0`: Person A's multipart request with httpx, 6 s, no retries, the audio in memory; the reply's `transcript` (or its turns'); a ledger line per call |
| `FakeSTT` | `services/fake/stt.py` | a fixed transcript; by default none (it hears nothing), for tests and `--stt fake` |
| `LLMService` | `services/llm.py` | the base: `complete(prompt, timeout_s, *, purpose)` -> text, or `LLMError` |
| `MetaLLM` | `services/meta/llm.py` | `muse-wonder-1.0` on the language services' `MetaClient`; 8 s |
| `FakeLLM` | `services/fake/llm.py` | scripted answers by purpose (`classify`, `reply`, `narrate`), for tests and `--llm fake` |
| `SceneService` | `services/scene.py` | the base: `assess(jpeg, sam_count, timeout_s, *, camera_id)` -> `Scene` (`domain/scene.py`, A's enums, extra keys forbidden), or `SceneError` |
| `SparkScene` | `services/meta/scene.py` | `muse-spark-1.3`: A's `SCENE_INSTRUCTIONS`, adapted to one frame and a JSON-only answer; 30 s, no retries; on a `MetaClient` of its own (3 at once, `--scene-call-cap` 300) |
| `FakeScene` | `services/fake/scene.py` | a scripted scene (one seated office worker by default); `None` fails |
| `RenderService` | `services/render.py` | the base: `render(jpeg, timeout_s, *, camera_id)` -> JPEG bytes, or `RenderError` (`CallCapReached` at the cap) |
| `MuseImageRender` | `services/meta/render.py` | `muse-image-1.0`: Person A's `images.edit` with `PRIVACY_RENDER_PROMPT`, `n=1`; 45 s, no retries; the `b64_json` checked and kept as a JPEG; on a `MetaClient` of its own (1 at once, `--render-call-cap` 60) |
| `FakeRender` | `services/fake/render.py` | a small solid JPEG for every render |

No service logs what was said, asked or answered, only lengths. Each Meta request logs a
`META ->` line when sent and a `META <-` or `META x` line when it ends (`MetaClient`'s trace,
`--no-meta-trace`): the endpoint, the `x-request-id` (also in the ledger's `Call`), the rate
limit left, and sizes, box pixels, confidences and intent labels only. `parse_sam_output` and
`Detection` (`domain/geometry.py`), the louver geometry (`domain/louvers.py`) and the comfort
physics and its clo and met tables (`domain/comfort.py`) are Person A's, near-verbatim. Muse
Spark's trace gives the people, a clothing tally and the doors; the narration's, its length;
Muse Image's, the frame's and the render's sizes, never the image or its base64.

## Transports

- **`BacnetClient`** (`transports/bacnet.py`): device `SpotCool-Agent`, instance 1910, its own
  UDP port (47820 by default; any free one with `bacnet_port: 0`), on the address that reaches
  the world. It addresses one device directly (no Who-Is). Requests go one at a time: with
  BACpypes3 0.0.110, several requests in flight while a device does not answer yet leave the
  socket deaf for good, to every device (the review measured it on this PC; separate clients
  recover). So each device has a client and a socket of its own: the BMS
  (`SpotCool-Agent`, 1910, UDP 47820) and, with `--vrf`, the VRF gateway at
  `<world_host>:47810` (`SpotCool-Agent-VRF`, 1911, UDP `--vrf-bacnet-port` 47821). It reads
  present values in BMS units (multi-state points by state text) and a device's `Model_Name`.
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
  the form parser never moves it to disk, and goes to Meta from memory. Logs carry exception
  types and messages, never tracebacks' locals (`diagnose=False`), and a complaint only as its
  length, its intent and the action, never its words.
- **Ground truth.** The agent connects to `/ws/agent` only, never `/ws/events`, and reads no
  `scenario_state` or `camera_scene`. It perceives through the cameras like any building's
  agent; the report compares its perception with the truth after the run.
- **Actuation.** Priority 10 only; write on a change only, releases first within a zone (a zone
  filling up gets its setpoint back before its sensor says Occupied); read the priority array
  before every zone write and back off from slots 1-9 for a value; never release a slot that
  is not the agent's (slot 10 must hold its value, or one whose write was cut off in flight);
  toggle `SPOTCOOL-HEARTBEAT` every 30 s; release everything at 19:00, on a normal stop, on
  Ctrl+C and on a crash (`Agent.run`'s `finally` stops the loops first, then releases the
  BMS's zones and the heartbeat's slot last, AG1's order, then, in its own `try`/`finally` and
  within 5 s, the VRF gateway's slots, so the VRF can never skip or delay the BMS's release).
  The VRF loop's actuator follows the same rules, never beats the heartbeat, and writes one
  point at most once a second.
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
- AG4: A's geometry with A's numbers and the vanes per brand (`test_louvers.py`); the VRF loop
  on a fake gateway and clock: aim, hold and release, the write rate, stale looks, each
  override and its decay, the draft pause (`test_vrf_loop.py`); intents, other-zone
  complaints, the reply and its template fallback (`test_complaints.py`); `MetaSTT` on the
  measured reply (`fixtures/asr_reply.json`, synthetic speech) through a mock transport, its
  timeout and no file written, and `MetaLLM` on a fake client (`test_language_services.py`);
  the whole agent with `--vrf` against the world's real VRF gateway (Mitsubishi, Daikin), live
  camera and dashboard, with no BMS answering (`test_vrf_integration.py`).
- `tests/test_boundaries.py`: every agent module imported in a subprocess without
  `spotcool_world`; no ground-truth feed named in the source; an audit hook on `open` scoped to
  stacks with agent code, while the agent works against the world's servers, sees no file
  opened to write, and the logs and console hold no JPEG bytes.
- AG8: A's comfort functions against values computed by A's own `physics.py`
  (`test_comfort.py`); the scene schema, `SparkScene` on a fake client (its trace, an invalid
  or late answer), the scene notes' cadence and cross-check, the narration
  (`test_scene.py`); the comfort setpoint, its precedence, its clamp, and no write on an
  invalid scene or `--no-comfort` (`test_vrf_loop.py`); **the building loop's BMS writes,
  decisions and explanations identical with and without the scene notes and narration**
  (`test_building_loop.py`); Spark in the audit-hook test.
- AG9: `MuseImageRender` on a fake client (A's prompt, the trace's sizes, no base64, a PNG
  kept as a JPEG; no image, bad base64, not an image, late), the render loop's order, skips, retries
  and call cap, `/privacy` (404, the latest, `no-store`, CORS), `--render` off by default
  (`test_render.py`); **the BMS writes identical with and without the render loop**
  (`test_building_loop.py`); the renders through the real world's cameras and `/privacy` in
  the audit-hook test; the runner's tests with `--render fake`.
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
- **The VRF loop's budget runs out**: at about a look a second, its 3000 calls last about 50
  minutes; then it stops aiming. Keep `--vrf` off in a scored run all the same.
- **Keywords are crude**: one keyword settles a complaint, so "not cold at all" reads as cold.
- **The scene schema is strict**: an answer with any key the prompt did not ask for is invalid,
  so a chatty model costs a reading, never a wrong value. The comfort setpoint assumes still
  air and 50 % RH, and its comfort effect is not measured.
- **The narration shares the language budget** (`--llm-call-cap` 200) with complaints: a day
  of about 40 writing steps uses about 40 of it.
- **The renders are slow and few**: about 24 s each, so a round of six cameras takes about 2.5
  minutes, and the 60-call cap lasts about 30 minutes. A render is an illustration, never
  evidence, and SAM's boxes drawn over it are the raw frame's, so they may not match it exactly.
- **Not verified live**: `muse-spark-1.3`, `muse-voice-transcribe-1.0`, `muse-wonder-1.0` and `muse-image-1.0` through the agent,
  the real phone in the room, and the louvers on another brand than the one the world
  emulates; the tests use fakes and the world's own gateway.
