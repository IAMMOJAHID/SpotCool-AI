# AGENTS.md: spotcool-world

Every agent working in `packages/world/` (Claude Code reads it through `CLAUDE.md`, Antigravity
reads it directly) follows this guide. The repo-wide rules in the root `AGENTS.md` still apply.

## Overview

`spotcool-world` is Person B's simulated building: everything Person A's agent connects to when
there is no real building. That means two copies of a physics-grade office (BOPTEST), a BACnet
BMS, a VRF gateway, CCTV cameras, room bookings, scripted occupants, the telemetry and the
recorder. The test of success: swap the world's adapters for real hardware and A's code does
not change. Everything that crosses to A is defined in `packages/contracts` (only the lead
edits it).

The package follows [pipecat](https://github.com/pipecat-ai/pipecat)'s architecture: frames
flow through processors, a pipeline orders one tick of work, workers run on a bus, a runner
owns their lifecycle, observers watch.

**One deliberate deviation from pipecat: processors have no input queues.** `push_frame`
awaits the neighbour's `process_frame` directly. The world is a lockstep co-simulation: a tick
must flow through the whole pipeline, both building plants included, before the next tick
starts, and the order of work must be identical on every run (the recorder and the video
depend on it).

WP0 (this code) is the core: frames, processors, pipelines, workers, bus, runner, clock,
observers, settings, the service and transport base classes, the unit adapter, the test
harness and the CLI skeleton. Plants, BACnet, cameras, scenarios, telemetry and the dashboard
arrive as later work packages, under the bases described here.

## Commands

The venv lives outside OneDrive, so set `UV_PROJECT_ENVIRONMENT` before every `uv` command.
Run everything from the repo root.

```bash
export UV_PROJECT_ENVIRONMENT="C:/Users/hussa/dev/venvs/spotcool-ai"   # PowerShell: $env:UV_PROJECT_ENVIRONMENT = "..."

uv lock                                   # after editing any pyproject.toml
uv sync --all-packages                    # install workspace packages (no extras)
uv sync --all-packages --extra boptest    # add an extra your WP needs (disk is tight); a later
                                          # sync without --extra removes it again (sync is exact)

uv run pytest packages/world              # all world tests (plant tests need --extra boptest --extra rc,
                                          # BACnet tests --extra bacnet)
uv run pytest packages/world/tests/test_parallel_pipeline.py -k branch_order
uv run pytest packages -m bacnet          # only the loopback BACnet tests (they run by default too)
SPOTCOOL_LIVE_BOPTEST_URL=http://127.0.0.1:8000 uv run pytest packages -m live   # live BOPTEST
uv run pytest                             # packages and scripts (testpaths); live and eval deselected
uv run pytest -m eval                     # the golden evals on RC, about 2 minutes (evals/)
SPOTCOOL_LIVE_BOPTEST_URL=http://127.0.0.1:8000 uv run pytest -m "eval and live"   # one on BOPTEST
uv run ruff check packages scripts        # what CI runs
uv run ruff format --check packages scripts   # `uv run ruff format packages scripts` to fix
uv run pyright packages

uv run python packages/world/examples/foundational/03_pipeline.py
uv run python packages/world/examples/foundational/06_bacnet_device.py --ticks 4   # the BMS
uv run spotcool-world version
uv run spotcool-world run --dry-run       # print resolved settings
uv run spotcool-world probe --plant rc    # go/no-go check of a plant (boptest-local, boptest-hosted)
uv run spotcool-world lint                # lint the scenario library (or: lint hybrid_tuesday --clips DIR)
uv run python packages/world/examples/foundational/08_scenario_lint.py   # the compiled hybrid Tuesday
cd packages/world && uv run towncrier build --draft   # preview the changelog

uv run python scripts/video/storyboard_check.py   # the video's storyboard: length, pace, every number sourced
SPOTCOOL_WORLD_RUN_DIR=C:/Users/hussa/dev/SpotCool-AI/runs uv run python scripts/video/qa.py video/renders/spotcool_draft.mp4
```

The video's full pipeline (renders, takes, narration, assembly, QA) is in `video/README.md`.

## Architecture

### Concept map

| pipecat concept | In the world | Where | Status |
|---|---|---|---|
| **Frame** (`@dataclass`) | One piece of simulation news | `frames/frames.py` | done |
| **FrameProcessor** (`process_frame` -> `push_frame`) | One small step of the tick | `processors/frame_processor.py` | done |
| **Pipeline / ParallelPipeline** | The order of work in a tick; the two plants run in parallel | `pipeline/` | done |
| **PipelineWorker / WorkerRunner / bus** | Runnable units on an in-process pub/sub bus; the runner handles signals and shutdown | `workers/`, `runner/`, `bus/` | done |
| **Observer** | Watches pushes without changing anything | `observers/` | done (log, step timing) |
| **Clock** | Simulation time (authoritative) and wall-clock pacing | `clocks/`, `processors/tick_source.py` | done |
| **Service** (abstract base + one sub-package per provider) | Swappable engines | `services/plant_service.py`, `services/camera_service.py` | bases done; plants done (WP1), power meters as step means (WP6.1); cameras done (WP4) |
| **Transport** (`input()` / `output()` processors) | Every protocol the agent touches | `transports/base_transport.py` | base done; BACnet BMS done (WP2); HTTP calendar router (WP5) and cameras (WP4) done; WebSocket dashboard feed done (WP6), replaying the run so far to each new viewer (WP7); VRF gateway done (WP3), beside the pipeline in wall time |
| **Adapter** | Translators to vendor and standard formats | `adapters/units.py` | K/W/m3s/fraction done; Haystack, vendor maps later |
| **Serializer** | Frames to and from wire formats | `serializers/jsonl.py` | run records (WP1) and manifests (WP6) done; dashboard events use the contract envelope |
| **Settings** | pydantic-settings + `world.yaml` | `services/settings.py` | done |
| **Evals** (scripted scenarios with expectations, judged again from saved runs) | Golden runs of the scenario library with a stub agent | `evals/` | done (WP10) |
| **Extras** | The core stays lean; every adapter is opt-in | `pyproject.toml` | declared: `boptest`, `bacnet`, `calendar`, `occupants`, `cameras`, `rc`, `dashboard`, `report`, `video` |

### One tick of the world (target pipeline)

Simulation time is authoritative. Wall-clock pacing is best effort; at `speed=300`, 15
sim-minutes take about 3 s.

```text
TickSource -> ScenarioProcessor -> bms.input() -> ParallelPipeline[ PlantProcessor(spotcool) || PlantProcessor(baseline) ]
 (TickFrame)   (who is where,      (agent's BACnet    (spotcool applies the effective setpoints;
               door contacts,       writes -> resolved  baseline runs BOPTEST's own controller)
               bookings)            SetpointCommandFrames)
     -> bms.output() -> cameras.output() -> TelemetryProcessor -> RecorderProcessor -> dashboard.output()
        (temps, power    (which clip each    (power, energy,       (JSONL + manifest;     (WebSocket JSON)
         -> BACnet AIs)   camera shows)       KPIs, comfort)        replayable)
```

Every processor in this pipeline exists, and `runner/assembly.py` (`World`) assembles them into
`spotcool-world run`, with `RunStatusProcessor` right after the `TickSource`. The VRF gateway
is not in it: it lives in wall time beside the pipeline (The VRF gateway).

### Lifecycle

1. `WorkerRunner.run(worker)` gives the worker the shared bus and runs it.
2. `PipelineWorker.run()` calls `setup()` on every processor (shared clock, bus, observers,
   task manager), then pushes the `StartFrame`: the first queued frame if it is one, otherwise
   `params.start_frame()`. `on_pipeline_started` fires.
3. `TickSource` starts its tick task on `StartFrame`. Each tick is pushed and awaited through
   the whole pipeline, then paced, then the clock advances. At `end_time_s` it pushes `EndFrame`.
4. The worker finishes when an `EndFrame` leaves the bottom of the pipeline
   (`on_pipeline_finished`, state `finished`). It cancels when a `CancelFrame` is queued, sent by
   `stop()`, pushed up to the top by a processor (only a request: processors do nothing with it
   on its way up), or follows a fatal `ErrorFrame` reaching the top. The worker then sends the
   `CancelFrame` down the whole pipeline, and each processor cancels its background tasks as it
   passes (state `cancelled`).
5. `cleanup()` runs on every processor; leftover tasks are cancelled.

### Frames

| Family | Frames |
|---|---|
| `SystemFrame` | `StartFrame`, `CancelFrame`, `ErrorFrame`, `MetricsFrame` |
| `ControlFrame` | `TickFrame` (carries `sim_time_s`), `EndFrame` |
| `DataFrame` | `RunStatusFrame`, `ScenarioStateFrame`, `SetpointCommandFrame`, `PlantMeasurementsFrame`, `BmsWriteFrame`, `KpiFrame`, `VrfStateFrame`, `CameraSceneFrame`, `CalendarFrame`, `ComplaintCueFrame`, `EventFrame` |

Payloads are the contract models from `spotcool_contracts.events`. `SetpointCommandFrame` and
`PlantMeasurementsFrame` carry BOPTEST names and units; `None` in `overrides` releases an input
(`_activate = 0`).

## Important patterns

- **Frames are the public interface.** Add a public method only for work that must happen
  outside the flow of frames (like `PipelineWorker.stop()`), and say so in its docstring. A
  method that does the same work as a frame names that frame in its docstring.
- **The `process_frame` shape.** Call `await super().process_frame(frame, direction)` first,
  handle the frame, then push it on **in the direction it arrived**. The base class never
  pushes: a processor that forgets `push_frame` swallows the frame, and a swallowed `EndFrame`
  means the worker never finishes. A bare `FrameProcessor()` is not a pass-through; use
  `Pipeline([])`.
- **Lockstep.** `push_frame` returns only after the rest of the pipeline has handled the frame.
  `TickSource` is the only producer after start. Await what the tick needs (a plant step must
  finish before the tick moves on); move work the tick does not need (posting to the agent,
  writing files, broadcasting to the dashboard) into `self.create_task()`.
- **Order inside a tick.** Push derived frames (scenario state, setpoint commands) before you
  pass the tick on, so processors further down already have them when the tick arrives.
- **Background work goes through `self.create_task(coro, name)`**, never `asyncio.create_task`.
  Tasks are cancelled on a downstream `CancelFrame` and in `cleanup()`. An exception escaping
  such a task becomes a fatal `ErrorFrame`.
- **Errors.** Report with `await self.push_error(message, fatal=...)`; it logs and sends an
  `ErrorFrame` upstream. The worker fires `on_pipeline_error`; a fatal error cancels the run
  (unless `PipelineParams(fatal_error_cancels=False)`). Do not raise from `process_frame` for
  expected failures: an exception there fails the run (or kills the tick task).
- **Errors are non-fatal by default**, as in pipecat's services. A failure the run can survive
  (a camera snapshot, a post to the agent, the dashboard) uses `fatal=False`. Use `fatal=True`
  only when the run's results would be wrong, such as a failed plant step. A worker outside
  the pipeline logs a failure, reports it in its result, and carries on.
- **Ask for a cancel from inside** by pushing a `CancelFrame` upstream. It only asks: nothing
  is cancelled on its way up, because the task carrying it there (the tick task, or a branch
  task of a `ParallelPipeline`) must deliver it to the worker. The worker then sends the
  cancel down, and processors cancel their tasks when the downstream `CancelFrame` passes.
- **ParallelPipeline output is deterministic.** Each downstream frame goes to every branch at
  once; what the branches emit is pushed on in branch order after all branches finish, then the
  original frame (by id) once, if any branch passed it on. A `StartFrame` is the exception: it
  goes first, before the branches' output (a plant's initial state), so nothing downstream gets
  data before it has started. Upstream frames entering from below go into every branch and
  leave the top deduplicated by id. Frames a branch pushes from a background task, outside any
  entry, pass straight through (unordered).
- **Sim time is the only clock inside processors.** Use `frame.sim_time_s` or `self.clock`,
  never `time.time()`, for anything simulated. Pass the same `SimClock` to `TickSource` and
  `PipelineParams(clock=...)` so every processor sees the time the ticks advance. Wall time is
  for pacing and metrics only, measured with `time.perf_counter()` (see Windows notes).
- **Events.** Declare with `self.register_event_handler("on_x")` in `__init__`; users attach with
  `@obj.event_handler("on_x")`; fire with `await self._call_event_handler("on_x", *args)`.
  Handlers run as background tasks unless `sync=True`, receive the object first, and a failing
  handler is logged, never raised. A `sync=True` handler runs inline, so keep it fast.
- **Bus.** `InProcessBus` gives each subscriber a bounded queue that drops the oldest message
  when full (`subscription.dropped` counts). Publish immutable values; messages are shared.
  There are two topics:
  - `BusPublisher(bus, "events")` publishes every `EventFrame.envelope`, for the dashboard.
  - `FramePublisher(bus, frame_types)` publishes chosen internal frames (the frame objects
    themselves) on `WORLD_FRAMES_TOPIC` (`"world"`), for workers outside the pipeline. This is
    pipecat's bus-bridge idea.

  Subscribe before the run starts, so the first tick's frames aren't missed.
- **Never store camera frames**: memory only, never disk or logs (WP4 adds a test that asserts
  it). **Always stop BOPTEST tests** in `PlantService.stop()`: the hosted service is shared and
  free. `BoptestClient.stop` retries no answer and HTTP 429/5xx with backoff for up to
  `STOP_RETRY_S` (60 s); a test it still cannot stop is logged as a warning with the command
  that stops it (`curl -X PUT <boptest_url>/stop/<testid>`). `BoptestClient.kpis`, read-only,
  retries the same failures, starting none more than `KPI_RETRY_S` (60 s) after the call, so
  a forward restarting as the run ends does not lose the final KPIs (the quote's energy).
- **No tracebacks where audio or frames are in scope.** loguru's default `diagnose=True` prints a
  traceback's local variables, so `logger.exception` (or `opt(exception=...)`) can leak audio or
  camera bytes into the log. Log `type(exc).__name__` and the message instead.
- **Contracts are the boundary.** Reuse `spotcool_contracts` models and point maps; never copy
  or redefine them here. Units on the wire to A are BMS units; convert with `adapters.units`.
- **Plants speak BOPTEST's REST names.** `create_plant(settings, run)` picks the provider. Every
  plant checks override names against the catalog (`services/boptest/catalog.py`) and reports
  an overwritten signal under its input's `_u` key, as BOPTEST's REST API does. BOPTEST inputs
  latch, so the BOPTEST plant sends `_activate` for all ten managed inputs (each zone's cooling
  setpoint and damper) on every step.
- **A step's values describe the step that just ended.** The seven ElectricPower meters
  (`catalog.electric_power`) are their means over the step, in W; every other value is the one
  at the end of the step. An initial state has no step, so its meters are instantaneous, and
  telemetry never integrates them. Power spikes inside a step when a VAV box opens wide, so a
  value at the end of the step would miss it.
  - The BOPTEST plant reads each step's meters from `PUT /results` over `[t - step, t]`
    (`BoptestClient.results`) and averages BOPTEST's 30-second samples with the
    time-weighted trapezoid (`step_means`). The call retries transient failures within
    `QUICK_TIMEOUT_S` (30 s) in all: each attempt's timeout is capped at the time left. If it
    fails, the meters keep their values at the end of the step: a warning and one more
    `PlantService.power_fallbacks` (0 on the other plants), never fatal.
  - `PlantProcessor` surfaces a fallback in the recording: when a plant's count rises, a
    non-fatal error naming the run and the step, and the run's count as a
    `plant_power_fallbacks` metric (`POWER_FALLBACKS_METRIC`).
  - The RC plant divides each meter's energy over its 60-second sub-steps by the step.
- **A BOPTEST step survives a dropped connection, and is never guessed or applied twice.**
  BOPTEST's web layer finishes an advance it got even when its client is gone, and stores
  every point at each step's end. So when an advance fails on the connection (no answer, or
  HTTP 502, 503 or 504 from a proxy) or is throttled (429), `BoptestPlant.step` recovers it;
  HTTP 400 and 500 are BOPTEST's own answers and fail the step at once. The step ends at
  `t_next`, the last state's time plus the step.
  - An advance that never reached BOPTEST (`ConnectError`, `ConnectTimeout`: the client's
    `unsent`), or that it refused with 429, is resent: inputs latch, so the same inputs make
    the same step.
  - Any other may have reached it. The plant reads BOPTEST's stored results (one meter, to a
    step past `t_next`). If they reach `t_next`, the step ran: its state is read back with
    `BoptestClient.state_at` (`PUT /results` over `[t_next, t_next]`, every key an advance
    answers; of several samples at `t_next`, the last, as `advance` answers). If they stop at
    the step's start, in an answer received at least `ADVANCE_TIMEOUT_S` (120 s) after the
    advance was sent, BOPTEST never got it, and it is resent. Otherwise it reads again, with
    backoff; a read throttled with 429 is read again too.
  - Every answer's time is checked, a normal step's included: BOPTEST more than half a step
    past `t_next` means a step applied twice (an advance that turned up after its resend), and
    fails the step. A step the scenario's end cut short passes.
  - A step waits at most `RECOVERY_S` (300 s) of wall time from its first failure: no try
    starts later, and each read's and resend's timeout ends by then. Then it fails, fatally as
    before, with a message that says what was tried. The step means follow a recovered step
    as any other: a step's usual read, within `QUICK_TIMEOUT_S`. Tests inject the plant's
    and client's `clock` and `sleep`, so none waits in real time.
  - Every recovery is a warning and one more `PlantService.connection_recoveries` (0 on the
    other plants). `PlantProcessor` records it like a power fallback: a non-fatal error naming
    the run and the step, and a `plant_connection_recoveries` metric
    (`CONNECTION_RECOVERIES_METRIC`). It never blocks the quote: the state is BOPTEST's own.
  - A transport error names the exception, and a timeout the seconds it allowed
    (`ReadTimeout after 120 s`).

## The BMS on BACnet/IP

`transports/bacnet/bms.py`'s `BacnetBmsTransport` is SpotCool-Office-BMS (device 1001), built
only from `load_point_map("office_bms")`: 51 objects with the map's names, units and state texts.
Commandable points carry a real priority array, relinquish default and current command priority;
every object answers COV. The rules are pure, unit-tested modules: `priority.py`
(`PriorityArray`, `CommandPolicy`) and `bms_logic.py` (`ZoneStandby`, `HeartbeatWatchdog`). The
BACnet/IP server code it shares with the VRF gateway is `server.py`: the points and their
objects, the application that names each write's client, the write rules both devices share
(`screen_write`), the write records (`record_write`) and the socket (`open_application`).

- **Lifecycle.** `await bms.start()` before the run binds the socket and serves the device in a
  background task, which also checks the heartbeat every second; `await bms.stop()` closes it.
  `start()` raises `OSError` at once if the port is taken (BACpypes3 alone retries forever).
- **In the tick.** `bms.input()` goes ahead of the plants. On each tick it runs the BMS logic,
  pushes a `BmsWriteFrame` for every command since the last tick, then the SpotCool run's
  `SetpointCommandFrame` with all ten managed inputs, then the tick. `bms.output()` goes after
  the plants: it shows the SpotCool run's measurements on the points (BMS units), takes the
  baseline's setpoints as the schedule (the cooling setpoints' relinquish defaults) and the
  West door contact from the scenario. The baseline run never gets a command. `HVAC-ENERGY`
  adds each step's mean power times the step, like the telemetry (`EnergyMeter`).
- **Commands** are network writes of a present value (WriteProperty or WritePropertyMultiple) at
  priority 8 (operators) to 16; the agent writes at 10, and a write without a priority is at 16.
  Priorities 1-7, read-only points, other properties, an array index, and analog values off
  range or off the resolution grid are refused (never clamped). Every write, release and
  refusal, and every release the BMS makes itself, becomes a `BmsWriteFrame` stamped with the
  BMS's sim time: the last measurement's.
- **Setpoint command.** A cooling setpoint goes to BOPTEST in K whenever a slot is active or
  standby raised the schedule; `None` only for the plain schedule, which BOPTEST applies itself.
  A damper is `0.0` (airflow shut off) or `None`.
- **Occupied-standby** (ASHRAE 90.1-2019 section 6.5.3.8). A step is in scheduled occupancy when
  it starts within 06:00-19:00 (`OCCUPIED_HOURS`; BOPTEST reports a setpoint a step late, so the
  baseline's 24 degC at the 19:00 stamp does not count) and the baseline's cooling setpoint is
  below 26 degC. Then a zone whose `OCC-SENSOR` has read Unoccupied for 300 sim-s enters standby
  at the next tick: its relinquish default rises 0.5 K, and its damper shuts while the zone is
  within its active setpoints (the heating one set back 0.5 K), reopens 0.5 K beyond either, and
  holds in between, so a zone the VAV box holds at the setpoint duty-cycles. Any Occupied value
  ends standby at once and resets the vacancy timer; the end of the occupied hours ends standby
  but keeps the timer. `OCC-MODE` shows Occupied, Standby or Unoccupied.
- **No data, no value.** A point the BMS has no value for yet (the run's first measurements, the
  schedule, the scenario's door) reads reliability COMMUNICATION_FAILURE, so its status flags
  show fault, as on a real BMS; the first value clears both. Each `StartFrame` begins a new run:
  measurements, energy meter, schedule and standby states start afresh, while the priority
  arrays and the heartbeat watchdog carry on.
- **Heartbeat watchdog** (wall time, injectable clock): once `SPOTCOOL-HEARTBEAT` has been
  written, 120 s without a change of its value releases every priority-10 slot, each recorded
  as an accepted `bms_write` with reason "heartbeat timeout" (`HEARTBEAT_TIMEOUT_REASON`) and
  the BMS's own address as the client. Slot 8 is never touched.
- **Network.** By default the machine's primary IPv4 address (its default route's) with /24, UDP
  47808; `address` (`192.168.1.20` or `192.168.1.20/24`) and `port` override it, from
  `WorldSettings.bms_address` and `bms_port`. Tests marked `bacnet` use 127.0.0.1 and ports from
  47900, and skip when a port is taken. macOS cannot run them: BACpypes3 opens a broadcast
  endpoint on 127.0.0.255, which macOS cannot bind.
- **The PC's address.** The BMS binds its address once, at `start()`. On each tick `input()`
  checks the address is still the PC's (`is_local_ipv4`: a socket can bind it; tests inject
  `has_address`). Once it has been gone for `ADDRESS_LOST_TICKS` (2) ticks in a row (one is a
  blip), a non-fatal error says `the PC's network changed: the BMS at <ip>:<port> is
  unreachable; restart the run on one network`, once. When the address is back (a reconnect to
  the same network), an info line says `the PC's address <ip> is back`, and a later loss is
  reported again. The plants and the recording go on.
- **BACpypes3 is pinned to 0.0.110** (the `bacnet` extra). `server.py` works around two of its
  bugs (a multi-state value gets the analog COV criteria, so COV subscriptions to it crash; a
  refused WritePropertyMultiple is raised instead of answered) and waits on its private
  `server._transport_tasks` in `open_application`. Re-check all three before unpinning.

### Checking the BMS with YABE (manual)

[YABE](https://sourceforge.net/projects/yetanotherbacnetexplorer/) is a free BACnet explorer for
Windows. Menu names are those of current YABE; older builds say "Add device" and "Add".

1. Start the device with a step every 15 s (a day in 12 minutes). It prints its address, such
   as `10.20.22.190:47808`:

   ```bash
   uv run python packages/world/examples/foundational/06_bacnet_device.py --speed 60
   ```

2. On another PC in the same /24 (allow the firewall rule below on the device's PC): Functions >
   Add BACnet Channel. In "BACnet/IP V4 & V6 over Udp", Port 47808, Local endpoint this PC's
   address, then Start. The Devices tree shows `10.20.22.190:47808 - 1001`. On the same PC, use
   the loopback workaround in the Windows notes instead.
3. Click the device. The Objects tree lists 52 entries: the 51 points of `office_bms.yaml` and
   the device. Click a point: Properties shows its name, Present Value and units or state texts;
   commandable points (`*-CLG-SP`, `*-OCC-SENSOR`, `SPOTCOOL-HEARTBEAT`) also show Priority Array,
   Relinquish Default and Current Command Priority.
4. The priority array, on ANALOG_VALUE:201 `ZN-COR-CLG-SP`:
   1. Press Ctrl+Alt+8 (a beep; the Log says "WritePriority change to level 8"), or set Options >
      Settings > DefaultWritePriority to 8. Write Present Value 27: it reads back 27, Priority
      Array holds 27 at 8, Current Command Priority is 8.
   2. Set DefaultWritePriority to 10 in Options > Settings (Ctrl+Alt stops at 9). Write 26.5:
      Present Value stays 27; Priority Array holds 27 at 8 and 26.5 at 10.
   3. Still at 10, clear the Present Value field and press Enter: YABE writes NULL. Slot 10
      empties; Present Value stays 27.
   4. Ctrl+Alt+8, then clear the field again: slot 8 empties, Present Value falls back to the
      Relinquish Default (the schedule: 24 by day, 30 at night) and Current Command Priority
      empties.
   5. Refusals: at priority 5 (Ctrl+Alt+5), or at 10 with 26.3 or 35, YABE says "Couldn't write
      property". The device's console logs every write as accepted or rejected, with the reason.
5. COV: drag `ZN-COR-T` from the Objects tree into the Subscriptions panel; it updates every
   step.

## The VRF gateway

`transports/bacnet/vrf.py`'s `BacnetVrfGateway` is VRF-Gateway-MeetingRoom (device 2001, UDP
47810): the BACnet gateway of the South meeting room's VRF indoor unit, built from
`load_point_map("vrf_gateway")` (10 objects) and behaving like the real gateway of the brand
`vrf_profile` names (`load_vrf_profiles()`: `mitsubishi`, `daikin` or `lg`). Person A's agent
aims its louvers at a person on the live phone camera; the dashboard's AC panel animates it.

- **Wall time, outside the pipeline.** It is a live device for the phone-camera take: a write
  takes effect on the unit at once, and the louvers move at 15 deg/s whatever `--speed` is. It
  adds no processor, and its power never enters the plants' energy, the KPIs or a quote.
- **The unit** (`devices/vrf/model.py`, `VrfUnit`) is pure: `advance(dt_s)` moves it on, with
  `dt_s` clipped to 0.1 s so a stall never makes a jump, and its command attributes take the
  commands in force.
  - A louver position is the profile's angle (vertical 0 to -60 deg, horizontal -40 to 40);
    the angle slews toward it at 15 deg/s at most, so 0 to -60 deg takes 4 s. `Swing` sweeps
    the axis's range, reversing at each end. LG's vertical louver, which has swing but no
    positions, sweeps 0 to -60 deg (`SWING_ONLY_RANGE_DEG`, illustrative). Both louvers start
    at 0 deg.
  - Off: the louvers hold, swing stops and the room drifts toward ambient.
  - The room is first order, tau = 120 s of wall time, starting at ambient, 30 degC. Cool, Dry
    and Auto cool it toward the setpoint, never below it; Fan mode and off let it drift toward
    ambient.
  - Power: a compressor term, 0.5 kW per K of room above the setpoint times the fan's level,
    capped at 2.2 kW, plus the fan (30-80 W) while the unit is on. Every constant is
    illustrative: VRF power is never quoted as a saving.
  - It starts at the gateway's relinquish defaults: On, Cool, 24 degC, fan Auto, both louvers
    Pos3.
- **Commands** follow the BMS's rules (`CommandPolicy`: priorities 8-16, the agent at 10, the
  operator at 8 wins, NULL releases, values off range or off the grid refused, never clamped),
  then the brand's (`brand_refusal`), each refusal with BACnet's own error and a reason that
  names the brand:
  - a louver axis the brand does not expose (`IDU-VANE-H` on Daikin and LG):
    write-access-denied, "Daikin's gateway exposes no horizontal louver";
  - a louver state or fan speed the brand lacks (Pos1-Pos5 on LG): value-out-of-range;
  - a release is always accepted. A louver whose state in force the brand lacks (LG's
    relinquish default, Pos3) holds its angle with swing off.
- **Points.** `IDU-ROOM-T`, `IDU-PWR` and the louver angles show the unit, refreshed on every
  tick. The angle of an axis the brand does not expose reads Reliability no-sensor (status
  flags fault), and `vrf_state` carries `None` for that axis's state and angle.
- **Events.** Every write, accepted or refused, is published at once as a `bms_write` event
  with `device: "VRF-Gateway-MeetingRoom"` (`frame_events` of a `BmsWriteFrame` on
  `EVENTS_TOPIC`), stamped with the shared `SimClock`'s time. `VrfWorker`
  (`workers/vrf_worker.py`) ticks the unit at 20 Hz of wall time (injectable `clock` and
  `sleep`; a late tick skips the ones it missed) and publishes `vrf_state`: at the start, at
  most 5 times a second while the payload changes (compared after its rounding: angles to
  0.1 deg, temperatures to 0.01 degC, power to 0.001 kW), and every 5 s otherwise, so a
  dashboard that connects late catches up within 5 s. Both land in the recording. A tick that
  fails is logged (its type and message; again only when the message changes, and once when
  ticks succeed again) and the worker ticks on: a VRF fault never ends the run.
- **Network.** The BMS's address (`bms_address`, resolved as the BMS resolves it) on
  `vrf_port`. A Who-Is sent to 47808 does not reach it: address it directly. The stub agent
  does not drive it. For the phone-camera take, run the world at any speed with
  `SPOTCOOL_WORLD_LIVE_CAMERA_URL` set (`spotcool-world run --plant rc --speed 1`), and stop it
  after the take.

### Checking the VRF with YABE (manual)

1. Start the world on loopback, with the brand to check (the default is `mitsubishi`). It
   prints `VRF 127.0.0.1:47810 (BACnet/IP)`:

   ```bash
   SPOTCOOL_WORLD_BMS_ADDRESS=127.0.0.1 SPOTCOOL_WORLD_VRF_PROFILE=mitsubishi \
     uv run spotcool-world run --plant rc --speed 1
   ```

2. In YABE, add a channel with Local endpoint `127.0.0.1` (type it in), Start, then Functions >
   IP Services > Add Remote Bacnet/IP node: `2001 - 127.0.0.1:47810`, as the BMS's loopback
   workaround does (Windows notes). From another PC, use this PC's address instead, with the
   firewall rule.
3. Click the device: the Objects tree lists 11 entries, the 10 points and the device. Drag
   `IDU-VANE-V-ANGLE` (ANALOG_INPUT:9) and `IDU-VANE-H-ANGLE` (ANALOG_INPUT:10) into the
   Subscriptions panel.
4. Set Options > Settings > DefaultWritePriority to 10. On `IDU-VANE-V` (MULTI_STATE_VALUE:5)
   write Present Value 5 (Pos5): the vertical angle slews from -30 to -60 over 2 s. Write 6
   (Swing): it sweeps 0 to -60 and back. On `IDU-VANE-H` (MULTI_STATE_VALUE:6) write 1: the
   horizontal angle slews to -40 in under 3 s.
5. The operator: press Ctrl+Alt+8 and write 1 on `IDU-VANE-V`: the angle slews to 0 and
   Current Command Priority is 8; a write at 10 now changes only slot 10. Clear the field
   (NULL) at 8: the agent's value at 10 is back.
6. Fan and setpoint: at 10, write `IDU-FAN` (MULTI_STATE_VALUE:4) 4 (High) and `IDU-SP`
   (ANALOG_VALUE:2) 27: `IDU-ROOM-T` levels off at 27 and `IDU-PWR` falls. 26.3 or 35 is
   refused ("Couldn't write property"), and so is any write at priority 5.
7. Brands: restart with `SPOTCOOL_WORLD_VRF_PROFILE=daikin`. A write to `IDU-VANE-H` is
   refused, the console logs "Daikin's gateway exposes no horizontal louver", and
   `IDU-VANE-H-ANGLE` reads Reliability no-sensor. With `lg`, `IDU-VANE-V` takes only 6
   (Swing); clearing it holds the louver where it is.
8. The dashboard's AC panel (`http://127.0.0.1:8090/`) shows the brand and both louvers moving
   with each write, and its BACnet log lists every write with the VRF's device name.

## Scenarios, calendar and occupants

A scenario (`scenarios/library/*.yaml`) is the film script of a day. Per zone, it says who is
there and which scene the cameras show. It also covers the West door, room bookings, complaint
cues and clothing. `load_scenario`, `lint` and `compile_scenario` turn it into a
`CompiledScenario`.

- **Time.**
  - `day` is BOPTEST's 0-based day index (`peak_cool_day` = 199), so a scenario time is
    `day * 86400` plus the seconds since midnight.
  - Every time sits on the step grid, 15 minutes by default.
  - Points in time satisfy `start <= t < end`. The world ticks while the time is before `end`,
    so nothing at `end` would happen. Only a `to` may equal `end`.
- **State.** `state_at(t)` is the state of the step that starts at `t`; an entry applies from its
  `at`.
  - It lists all five zones in `Zone` order.
  - `door_open` is a bool only for a zone with a door contact in the point map (West).
  - `clothing` holds until the next clothing event, and is `None` when nobody is there.
  - People counts are the occupants visible in that zone's clip.
- **Ticks.** For each tick, `ScenarioProcessor` pushes these frames, each stamped with the tick's
  time:
  - the `ScenarioStateFrame`;
  - a `CalendarFrame` per room, on the first tick and whenever that room's visible bookings
    change;
  - a `ComplaintCueFrame` per cue in `(previous tick, this tick]` (the first tick's window is
    `[start, start]`);
  - then the tick itself.

  A run that starts before the scenario is a fatal error.
- **Calendar** (`transports/http/calendar.py`, extra `calendar`).
  - `CalendarStore.follow(sub)` keeps the latest `CalendarData` per room from a
    `WORLD_FRAMES_TOPIC` subscription.
  - `calendar_router(store)` serves `/calendar/rooms/{room}/schedule` (Graph `getSchedule` shape)
    and `.ics`. WP4's HTTP server mounts it.
  - A calendar date is 2026-01-01 00:00 plus the simulation seconds (day 199 is 2026-07-19),
    labelled `timeZone: "UTC"`. This matches the BMS's `SIM-CLOCK`.
- **Occupants** (`workers/occupant_worker.py`, extra `occupants`).
  - `OccupantWorker` posts each cue to the agent's `/chat` or `/voice`. Voice audio is a WAV from
    `footage_dir/audio/`, checked against `VOICE_FORMAT`; if anything is wrong, it falls back to
    chat.
  - It publishes each result as a `complaint_cue` envelope on `"events"`. A failure is never
    fatal: it's reported as `delivered: false`, with the reason.
  - It retries only connection errors and 5xx, twice, so a complaint is never sent twice.
  - It runs until `stop()`. Stop it from the pipeline worker's `on_pipeline_finished`; it reports
    any cue still queued as not sent.
- **Lint.** `spotcool-world lint [SCENARIO ...] [--clips DIR] [--step-s S]` prints
  `file:line: level: message` and exits 1 on any error. YAML anchors and aliases are refused.

## Cameras

The camera system plays a video recorder for A's agent: a catalog, one snapshot per camera, and
an MJPEG stream for the dashboard. The providers need the `cameras` extra; the base, the
dataset indexes and `CameraSceneProcessor` need only the core.

- **Pieces.**
  - `CameraService` (`services/camera_service.py`) is one camera's provider: `ZoneCamera`
    (`services/clips/`) for each zone, `PhoneCamera` (`services/live/`) for the live phone.
  - `CameraSystem` runs the cameras from `start()` to `stop()`. `create_cameras(settings)` builds
    the five zone cameras, plus `cam-meeting-live` when `live_camera_url` is set.
  - `CameraSceneProcessor` sets every zone camera's scene and people count from each
    `ScenarioStateFrame`. It pushes a `CameraSceneFrame` per camera whose scene changed, and for
    every camera on a run's first state.
  - `create_http_app([cameras_router(cameras), calendar_router(store)])` is the world's one HTTP
    app. `HttpServerWorker(app, host, port)` (`workers/http_server.py`) serves any FastAPI app
    with uvicorn, WebSocket routes and lifespan handlers included: this one on
    `http_host:http_port`, and WP6's dashboard on `http_host:dashboard_port`.
- **Where a zone camera's frames come from**, per scene; the first source that opens wins:
  1. our clip `footage_dir/clips/<zone>_<scene>.mp4`;
  2. the camera and scene's segment in a dataset index, `footage_dir/datasets/<name>/index.yaml`
     (format in `scripts/footage/wisenet_index.py`). Indexes are tried in folder-name order, and
     segments whose file is missing are left out. An index with a time that is not finite, or a
     file that leads outside its folder (links followed), is refused;
  3. synthetic frames: one coloured silhouette per person, with the camera and scene as caption.

  The catalog's `source` is `dataset` for a dataset segment, else `scenario`. The choice is
  logged once per camera and scene.
- **Playback.** The scene follows sim time, the frames follow wall time. A new scene starts at its
  clip's first frame, plays in real time and loops. Clips are not time-compressed: at
  `speed=300` a 15-minute step lasts 3 s, so a scene shows only its first seconds. A run that
  must show a clip's action, such as the 14:15 complaint (5-15 s into `sou_complaint.mp4`),
  runs at `speed=60` or slower.
- **Load.** Each zone camera makes `snapshot_fps` (5) frames a wall second in a worker thread,
  because OpenCV blocks and the event loop runs the lockstep tick. It skips the frames in
  between with `grab()` and keeps only its latest JPEG (1280x720, quality 80). A camera has no
  frame until its first scene.
- **HTTP.**
  - `GET /cameras`: the catalog, with URLs built from the request.
  - `GET /cameras/{id}/snapshot.jpg`: `image/jpeg` with `Cache-Control: no-store`. It answers 404
    for an unknown id, 422 for a malformed one and 503 before the first frame, and sets
    `X-Camera-Stale: 1` on a frame 5 s old or older.
  - `GET /cameras/{id}/mjpeg`: new frames only, at most 5 a second. It ends when the client
    disconnects or the server stops.
  - `GET /healthz`. CORS lets `http://127.0.0.1:*` and `http://localhost:*` read every GET,
    including the stale header, and so does the dashboard's own page on a private address:
    `http://<host>:<dashboard_port>` with `<host>` a loopback, RFC 1918 or link-local IP
    (`create_http_app(routers, dashboard_port=...)`, `is_dashboard_origin`), so a dashboard
    opened from another PC on the LAN reads the camera catalog.
- **The live phone.** `PhoneCamera` polls `live_camera_url` at 2 fps with a 2 s timeout. It serves
  only an HTTP 200 JPEG under 5 MB that decodes, as it came, and reports its size in the
  catalog. It keeps the last good frame. It has no zone: it films the real room of the VRF
  demo. A failing phone is logged when it starts and stops failing, and is never fatal. Logs
  show the URL without its user name and password.
- **Privacy (Law 6).** Frames stay in memory; nothing writes them, and camera code logs
  `type(exc).__name__` and the message, never a traceback. `tests/cameras/test_privacy.py`
  serves 50 snapshots and three MJPEG parts while `open` and `io.open` in write modes, `os.open`
  with `O_WRONLY`, `O_RDWR` or `O_CREAT`, `cv2.imwrite` and `cv2.VideoWriter` all fail. It then
  checks that none was called, and that the loguru records, the stdlib log records and the
  process's stdout and stderr hold no JPEG bytes: raw, escaped (`\xff\xd8\xff`), hex
  (`ffd8ff`) or base64 (`/9j/`).
- **Wiring (WP11).**
  - `cameras = create_cameras(settings)`; `await cameras.start()` before the runner, and
    `await cameras.stop()` after it.
  - Pass the same object to `CameraSceneProcessor` (after `bms.output()`) and `cameras_router`.
  - Run `HttpServerWorker(app, settings.http_host, settings.http_port)` under the runner and
    stop it from `on_pipeline_finished`, like the occupant worker. A port another program holds,
    on any address, fails its `run()` at once; Windows needs a check of its own for that
    (`SO_EXCLUSIVEADDRUSE` and a probe of the wildcard address).

## Telemetry, recording and the dashboard feed

These pieces turn the simulation into the numbers the video quotes, the stream the dashboard
draws and a record any run can be replayed from. The processors need only the core; the
dashboard server needs the `dashboard` extra and the report the `report` extra (matplotlib).

- **Order.** `... -> CameraSceneProcessor -> TelemetryProcessor -> KpiProcessor ->
  EventEmitterProcessor -> RecorderProcessor -> BusPublisher(bus)`. The KPI processor needs the
  telemetry's summaries. The recorder goes after the emitter, before or after `BusPublisher`:
  lockstep publishes a frame's events before the next frame reaches the recorder.
- **`TelemetryProcessor`** fills each `PlantMeasurementsFrame.summary` (`metered_summary`):
  every zone's temperature, setpoints, CO2 and airflow, the seven meters in kW by BMS point name
  (`power_kw`, BOPTEST's ElectricPower basis: each step's means), their total, and the run's
  energy: each step's mean power times the step (`EnergyMeter`, a rectangle per step), from the
  run's first frame with all seven meters (normally its initial state, whose power is never
  counted). BOPTEST leaves out values it reports as null, such as the `_u`
  setpoints of an initial state: a frame lacking a value keeps no summary, its power still
  counts if it has the meters, and that is logged once per run. The KPIs and the report skip
  such frames.
- **`KpiProcessor`** scores both runs at each tick, once both runs' measurements stamped `t`
  (the end of the step) have passed.
  - Comfort where people were (K h, `comfort_kh`): the people of the latest
    `ScenarioStateFrame` (the step that just ended, never `state_at(t)`), the band of the
    baseline's applied setpoints at `t`; both runs get the same people and band.
  - Energy saved (%): both runs' cumulative energy at the same stamp, on the SpotCool run only.
  - A provisional `KpiFrame` per run every 4 steps, ahead of the tick, with `boptest` empty.
  - At the first `EndFrame` or `CancelFrame`, one final `KpiFrame` per run that merges the
    plant's own final one (BOPTEST's KPIs); the plants' frames are not passed on.
  - The cross-check, at that end: each run's energy against its plant's own, `ener_tot`
    (kWh/m2) x the floor area, 1662.66 m2 (`plant_energy_kwh`). A difference of more than
    `CROSS_CHECK_PCT` (0.1 %) is logged as a warning. A healthy run agrees to about 1e-4 %
    (BOPTEST's J-to-kWh factor); more means power inside steps was missed.
  - Recovery times are not a KPI (the contract's `KpiData` has no field for them): the report
    computes them.
- **`EventEmitterProcessor`** pushes an `EventFrame` for every event a frame becomes
  (`frame_events`), ahead of the frame: tick (clock and day of year), run status, scenario
  state, plant measurements (the summary), BMS write, KPI, VRF state, camera scene, calendar,
  complaint cue (`data` only: the audio file name stays in the world), error, and one metrics
  event per sample.
  - **Within a tick the `tick` event comes last** (for WP7): the tick stamped `T` follows the
    step's results stamped `T + step` (measurements, metrics, KPIs), because derived frames
    travel ahead of their tick. So `sim_time_s` is not monotonic inside a tick; order by
    arrival, or treat each `tick` event as the close of its step.
  - `ErrorFrame`s travel upstream, so the errors of processors above the emitter never reach
    it. Turn them into events with `frame_events(error)` in the pipeline worker's
    `on_pipeline_error`, which runs as a background task: a fatal one may arrive after the
    recorder has finished (the manifest's `end_reason` then holds its message).
- **One stream.** The topic `EVENTS_TOPIC` (`"events"`) carries the pipeline's events, the
  occupant worker's delivery results (a second `complaint_cue` per cue, with `delivered` and
  `detail`) and the agent's events from `/ws/agent`.
- **`RecorderProcessor(settings)`** writes `settings.run_dir/<run_id>/`.
  - `events.jsonl` (`serializers/jsonl.py`, the format `ReplayPlant` reads): every envelope on
    the topic as an event record and every `PlantMeasurementsFrame` as a `RawRecord` (`index`
    0: the initial state), in pipeline order. It subscribes in `setup()` (100 000 messages)
    and, on every frame, moves the waiting events into the tick's batch one by one, so a
    cancel never loses one it has taken. A background task writes and flushes each tick's
    batch in a worker thread.
  - `manifest.json` (`RunManifest`): the run id, when it started (UTC), how it ended and why,
    the settings (redacted: fields named like secrets, a URL's user name and password, and its
    `token`, `key`, `secret`, `password` and `auth` query parameters), the git sha, both
    versions, the scenario's name and file sha256, the plant and step, the start and end sim
    times, the final KPIs, and `dropped_events` (0 in a valid record, a warning otherwise).
  - The first `EndFrame` or `CancelFrame` finishes the record as one task, which the frame
    awaits through `asyncio.shield`: `stop()` while the `EndFrame` passes cannot cut it short,
    and a later end frame waits for the same task. `ended` follows the end frame that reached
    the recorder first, by `RunStatusProcessor`'s rule: `finished` after an `EndFrame`; after a
    `CancelFrame`, `failed` if the last `RunStatusFrame` said so (a fatal error caused it),
    else `cancelled` (stopped from outside, such as Ctrl+C). The task writes what the writing
    task left if that task is gone.
  - Every recording failure is fatal: a run id outside `RUN_ID_PATTERN`, a directory that
    already holds a record (never appended to), a failed write (no manifest then). A run that
    never reaches an end frame keeps its flushed records, without a manifest; `cleanup()`
    closes the file.
  - Events published after the end frame has passed the recorder are not recorded: a delivery
    still in flight, the occupant worker's "not sent" results at its `stop()`.
  - Records are JSON: no image or audio bytes (`tests/telemetry/test_recorder.py` checks).
- **Dashboard server**: `create_dashboard_app(bus, run_dir, dist_dir=None)`
  (`transports/websocket/dashboard.py`).
  - `/ws/events`: every envelope as JSON text. A client that joins mid-run first gets the run
    so far, then the live events, each once and in order (`EventFeed`). The feed subscribes when
    the app is made and runs in the app's lifespan (uvicorn and `HttpServerWorker` run it). Its
    history keeps the current run's events, except `vrf_state` (the latest only) and `metrics`
    (the latest of each metric, by name and labels); a `run_status` of another run clears it;
    it holds at most `HISTORY_LIMIT` (50,000) events, dropping the oldest with one warning.
    Each client has its own queue (1000 messages, the oldest dropped first), joined before its
    handshake ends; what it sends is ignored. `replay` serves the same app.
  - `/ws/agent`: publish-only. One `EventEnvelope` per JSON text message, `source: "agent"`,
    type perception, decision, explanation or chat, at most 64 KB and 20 messages a second per
    connection, and no image: no string starting `data:` and none over 8192 characters
    (perception sends counts, boxes and polygons; Law 6, since the recorder keeps what is
    sent). An invalid message is dropped and answered with `{"error": "..."}`. A browser page
    whose `Origin` is not `http://127.0.0.1:*` or `http://localhost:*` is refused (403), so a
    web page cannot publish fake agent events; programs send no `Origin`. It never sends world
    events.
  - `GET /runs` (`{"runs": [...]}`); `GET /runs/{run_id}/events.jsonl` (422 for an id outside
    `RUN_ID_PATTERN`, 404 for an unknown run); `GET /healthz`; `/` serves `apps/dashboard/dist`
    if it exists when the app is made. CORS lets this machine's pages (`LOCAL_ORIGINS`) read
    the GET routes.
  - A failing socket ends only its own connection; `HttpServerWorker.stop()` closes them all.
- **Report**: `spotcool-world report <run_id>` (`runner/report.py`) writes `report.md`,
  `power_energy.png`, `zone_temperatures.png` and `occupancy.png` into the run's directory under
  the settings' `run_dir`. The series come from the raw records through the telemetry's
  functions, the people from the `scenario_state` events, the KPIs from the manifest.
  - The KPI table (`check_energy`) shows each run's energy by the step-mean integration and
    by its plant's own, their difference (%), energy saved both ways (the manifest's saving is
    never shown) and the steps whose power fell back (the last `plant_power_fallbacks` metric).
  - The verdict follows it (`verdict`): `Quotable: X % saved`, the plants' own saving, or `Not
    quotable:` with every blocker (`quote_blockers`, one sentence each). It blocks a plant that
    is not BOPTEST (RC's cross-check is circular), a run that did not finish, dropped events, a
    missing run or plant energy, any power fallback, a run more than `CROSS_CHECK_PCT` off its
    plant's own, an unpaced run (speed 0), a run no agent acted on (no accepted priority-10
    write to a zone point), any heartbeat-timeout release, an agent run whose perception is
    missing, covers less than `MIN_COVERAGE` (95 %) of the run's zone-steps or agrees on less
    than `MIN_AGREEMENT` (90 %) of them (a stub run is blocked as a stub instead), and a
    stub-agent run (an upper bound at best). A step recovered after a dropped connection does not: its values are
    BOPTEST's own.
  - Agent activity (`agent_activity`), the proof that an agent acted, from the BMS's own
    `bms_write` records (`RecordedRun.writes`) and the agents' `perception` events
    (`RecordedRun.perceptions`): the accepted priority-10 writes to `ZN-*-OCC-SENSOR` and
    `ZN-*-CLG-SP` per client (the stub's count too), the slots the heartbeat watchdog released
    (reason `HEARTBEAT_TIMEOUT_REASON`), and the occupancy agreement: the share of zone-steps
    where an agent's people count and the scenario agree on occupied or empty, aligned by sim
    time (a perception is stamped with the start of the step it saw; a zone it leaves out is
    not compared), with how many of the run's zone-steps were compared; and the
    decision-to-write lags (`write_lags`, the golden evals' measure): the share of the writes
    the BMS took 1, 2 and 3+ steps after their decision, and never.
  - Recovery: for each zone and arrival (people after a step with none), the minutes until the
    zone is no warmer than the baseline's cooling setpoint + 0.5 K, from the arrival's stamp
    on. Arrivals that never recover are listed.
  - The honesty note closes the report.
- **Wiring (WP11).**
  - One `InProcessBus` for `WorkerRunner(bus=...)`, `BusPublisher(bus)` and
    `create_dashboard_app(bus, settings.run_dir)`. Serve the app with
    `HttpServerWorker(app, settings.http_host, settings.dashboard_port)` under the runner and
    stop it from `on_pipeline_finished`.
  - Set `PipelineParams.scenario` to `settings.scenario` (a library name or a `.yaml` path), so
    the manifest gets the file's sha256, and keep run ids inside `RUN_ID_PATTERN`.
- **Example** `09_record_and_report.py` runs `hybrid_tuesday` on two RC plants with a stand-in
  for the agent (it reads ground truth and puts empty zones on standby), records the run and
  writes its report, in about 5 s. Keep runs out of the repo: `SPOTCOOL_WORLD_RUN_DIR=<dir>`.

## Running the world

`spotcool-world run` brings the whole stand-in building up from `WorldSettings` and plays the
scenario once, from its `start` to its `end` (not `start_clock`/`end_clock`). Settings come from
the flags, then `SPOTCOOL_WORLD_*`, then `--config FILE` or `./world.yaml`; `world.example.yaml`
lists every key with its default.

```bash
export SPOTCOOL_WORLD_RUN_DIR="C:/Users/hussa/dev/runs"   # keep runs out of the repo
uv run spotcool-world run                                   # the settings' plant and scenario, speed 300
uv run spotcool-world run --plant rc --speed 0 --stub-agent # hybrid_tuesday at full speed, scored by the stub
uv run spotcool-world run --config world.yaml --scenario all_hands_day --speed 60 --run-id take-3
uv run spotcool-world replay <run_id> --speed 60 --wait-viewer 60   # a recorded run again, for a video take
uv run spotcool-world report <run_id>
uv run python packages/world/examples/foundational/10_full_world.py   # the same run, loopback only
```

- **Assembly** (`runner/assembly.py`). `World(settings, run_id=None)` builds everything and
  starts nothing; `World.run()` runs it once.
  - The pipeline: `TickSource -> RunStatusProcessor -> ScenarioProcessor ->
    FramePublisher(CalendarFrame, ComplaintCueFrame) -> bms.input() ->
    ParallelPipeline[PlantProcessor(spotcool) || PlantProcessor(baseline)] -> bms.output() ->
    CameraSceneProcessor -> TelemetryProcessor -> KpiProcessor -> EventEmitterProcessor ->
    RecorderProcessor -> BusPublisher(EVENTS_TOPIC)`. `PipelineParams.scenario` is
    `settings.scenario`, so the manifest hashes its file.
  - `RunStatusProcessor` (`processors/run_status.py`) gives the dashboard the run's status as
    `run_status` events: `running` after the start, then `finished` before the `EndFrame`, or,
    before a `CancelFrame`, `failed` with the fatal error's message, or else `cancelled` with
    the cancel's reason (contracts 0.1.1).
  - Beside it, on one `InProcessBus`: the occupants, the HTTP server (cameras and calendar,
    `http_host:http_port`), the dashboard server (`http_host:dashboard_port`, which serves
    `apps/dashboard/dist` at `/` once it is built), the VRF worker and, with `stub_agent`, the
    stub agent.
  - Both plants come from `create_plant`. The RC plant's occupant heat follows the scenario
    (`scenario_occupancy`: the people of the step under way, nobody during the warm-up);
    BOPTEST keeps its own fixed schedule (the report's honesty note).
  - The VRF gateway (`World.vrf`, on the BMS's address and `vrf_port`) and its `VrfWorker`,
    one of `World.workers`, live beside the pipeline in wall time (The VRF gateway).
- **Run ids.** A run id names the run's directory in `run_dir`; one that already holds a record
  is refused before anything starts. The default is the date, time and scenario, and a stub run's
  ends in `-stub`.
- **Output.** At start-up it prints the URLs: the dashboard, its event stream, the camera
  catalog, the calendar, and the BMS's and the VRF's addresses and ports. A server on a wildcard host (`0.0.0.0`,
  `::`) shows this PC's LAN address, then its loopback one. At the end, from the recording: the
  run id, how it ended (as the manifest says: `finished`, `failed` or `cancelled`), each plant
  run's energy (the step-mean integration, its plant's own and
  their difference) and comfort where people were, energy saved both ways, the report's verdict,
  and the report and replay commands. After a `--config` run those carry
  `SPOTCOOL_WORLD_RUN_DIR=<dir>` and `--config <file>`. The exit code is 0 when the pipeline
  finished, 1 otherwise; invalid settings
  (such as `bms_address: localhost` or `log_level: VERBOSE`) print `run failed: ...` and exit 1.
- **Lifecycle.** Before the runner, the BMS starts (`bms_address`, `bms_port`), then the VRF
  gateway (`bms_address`, `vrf_port`), then the cameras, and the calendar store follows
  `WORLD_FRAMES_TOPIC`. One `WorkerRunner` runs the pipeline,
  then the others. The pipeline's `on_pipeline_finished` stops the other workers, so the runner
  returns; its `on_pipeline_error` publishes errors raised above the emitter as `error` events
  (the emitter has made the others events already). After the runner, everything is stopped in
  reverse order, the plants last and both at once.
- **Ctrl+C** stops the run: the pipeline cancels first, the plants stop (both BOPTEST tests, at
  once), the recorder writes a `cancelled` manifest with the KPIs so far, then the other
  workers stop. A second Ctrl+C cancels everything at once; `cleanup()` then stops the plants,
  both at once too (`ParallelPipeline.cleanup` cleans its branches up side by side). A BOPTEST
  stop request already sent runs on: the plant keeps it as a task, and its next `stop()`,
  which `World.run()` makes last, waits up to 65 s (`STOP_WAIT_S`: the request's 60 s of
  retries and a margin) for it before closing the client, so `run()` never returns while one
  is under way. During a BOPTEST plant start the first Ctrl+C waits for the start (Known
  limitations).
- **Speed.** 300, the default, is for development: a step every 3 s, the day in 2.5 minutes.
  Use 60 or slower for integration and filming, because a clip's action needs wall time: the
  14:15 complaint plays 5-15 s into its clip. `--speed 0` runs as fast as possible: the RC day in
  about 2 s.
- **Ports.** UDP `bms_port` (47808) and `vrf_port` (47810) on the primary IPv4 address, TCP
  `http_port` (8081) and `dashboard_port` (8090) on `http_host` (0.0.0.0). A port another
  program holds fails the run: the BMS's or the VRF's before anything starts (`BMS UDP
  <address>:<port> is in use ...`, `VRF UDP ...`), a server's as
  soon as the runner starts (the run is then cancelled, after the plants' start). For a run on
  this PC only, with no firewall prompt, set `SPOTCOOL_WORLD_BMS_ADDRESS=127.0.0.1` and
  `SPOTCOOL_WORLD_HTTP_HOST=127.0.0.1`. For A's agent or a browser on another PC, allow the
  ports through the firewall (Windows notes).
- **Logging.** `run` and `replay` log to stderr at `log_level`, with `diagnose=False` and
  `backtrace=False`, so a traceback never prints local variables that hold audio or frames.
- **Without A's agent**, nothing answers the occupants' complaints: each is reported
  `delivered: false`. On Windows a refused connection takes about 2 s per attempt, so a run at
  full speed ends about 7 s after the 14:15 complaint, while the occupant worker finishes it.
- **Replay** (`Replay` in `runner/assembly.py`). `spotcool-world replay <run_id> [--config FILE]
  [--speed S] [--wait-viewer SECONDS]` (the settings' speed by default) puts the record's events
  back on a bus, in their recorded order and as recorded (their wall-clock `t_ms` included), and
  serves the dashboard and the cameras on the usual ports: no physics, no BACnet. With
  `--wait-viewer`, it waits up to that long for a first `/ws/events` client (the dashboard
  app's `state.viewers`) before the first event, so an OBS take starts with the dashboard
  open. Each recorded scenario state goes through a `CameraSceneProcessor`, so the camera wall
  shows the take's scenes and people. Each step lasts `step_s / speed` after its `tick` event,
  as in the live run; the servers stop at the record's end.
- **The PC stays on one network for the whole run.** The BMS binds the PC's IPv4 address when
  the run starts, and the port forward to the Codespace dies with the network. Windows may move
  a laptop to another saved Wi-Fi network mid-run (on 2 November, from 192.168.234.x to
  192.168.1.x): the BMS then reports that its address is gone, A's agent can no longer reach
  it, and the run must start again. Before a run, set every other saved network to connect
  manually (`netsh wlan show profiles` lists them):

  ```powershell
  netsh wlan set profileparameter name="<profile>" connectionmode=manual
  ```

- **Supervise the port forward.** BACnet is UDP and a Codespace forward carries only TCP, so
  every run with A's agent runs the world on the laptop and reaches BOPTEST through
  `gh codespace ports forward`. A forward that exits (a dropped tunnel) is restarted by a loop
  in its own terminal, outside the world:

  ```bash
  while :; do gh codespace ports forward 8000:8000 -c <codespace>; sleep 3; done
  ```

  The world rides out the gap: a step whose connection fails is recovered from BOPTEST's own
  stored results within 300 s (Important patterns), and a stop retries for 60 s. A recovered
  step at speed 60 only delays the run; the pacing resumes with the next tick.
- **The BOPTEST checkpoint** (the lead runs it; not part of the test suite):
  1. Bring the Codespace's stack up, and forward its port through the supervised loop (in a
     separate terminal):

     ```bash
     gh codespace ssh -c spotcool-boptest-qxxjjj94x672rp -- 'cd /workspaces/project1-boptest && docker compose up -d --scale worker=2 web worker provision && docker compose ps'
     while :; do gh codespace ports forward 8000:8000 -c spotcool-boptest-qxxjjj94x672rp; sleep 3; done
     curl http://127.0.0.1:8000/version
     ```

  2. Check the plant: `uv run spotcool-world probe --plant boptest-local` (both workers free).
  3. Run the day with the stub agent, into a run directory outside the repo:

     ```bash
     export SPOTCOOL_WORLD_RUN_DIR="C:/Users/hussa/dev/runs"
     uv run spotcool-world run --plant boptest-local --speed 0 --stub-agent --run-id boptest-checkpoint
     ```

     The start (select, then a 3-day warm-up per test) takes a minute or two; then each step
     takes a BOPTEST round trip. Expect `run boptest-checkpoint finished (end time reached)`,
     the stub label, both runs' energy and comfort, and energy saved. The log shows
     `BOPTEST start: test=<id>` twice and `BOPTEST: stopped test <id>` for both ids, and no
     `no power means` or `off its plant's own` warning. A tunnel that dropped shows `lost its
     connection` for each recovered step, and the run still finishes. Each run's difference
     from its plant's own is about 0.000 %, and the verdict's only blockers are the unpaced run
     and the stub agent.
  4. Ctrl+C: start the same run with `--run-id boptest-ctrl-c`, press Ctrl+C after a few steps
     (after the start has returned). Expect `cancelled (SIGINT)` and both tests stopped in the
     log. Then run step 2 again: it must get a worker at once, which shows no test was left.
  5. `uv run spotcool-world report boptest-checkpoint`, and
     `uv run spotcool-world replay boptest-checkpoint --speed 0`. The report shows 0 power
     fallbacks for both runs and the same verdict as the run.
  6. Stop the Codespace to save its quota: `gh codespace stop -c spotcool-boptest-qxxjjj94x672rp`.

## The stub agent

`stub_agent/` (extra `bacnet`) stands in for Person A's agent, so the world can be run, scored
and filmed without A. It is a stub and says so everywhere: A's agent must never read ground
truth.

- **Turning it on.** `spotcool-world run --stub-agent`, or the setting `stub_agent`
  (`SPOTCOOL_WORLD_STUB_AGENT=true`), which the manifest records. A stub run's id ends in
  `-stub`, and its printout and the top of its report say `STUB_AGENT_NOTE`: "(stub agent:
  perfect perception from the scenario script, not Person A's agent)".
- **A real BACnet/IP client.** `StubAgentWorker(device)`, given the BMS's `ip:port`, is a
  BACpypes3 device (`SpotCool-Stub-Agent`, instance 1900) on its own UDP port, any free one on
  the BMS's address, writing over loopback or the LAN exactly as A's agent will. Each request
  waits 1 s for its answer and is resent once (`APDU_TIMEOUT_MS`, `APDU_RETRIES`), so one the
  BMS never answers costs about 2 s, not BACpypes3's 12.
- **Perfect perception, openly.** It reads the scenario's ground truth: the `scenario_state` and
  `calendar` events on `"events"`.
- **Policy** (`stub_agent/policy.py`, pure), at priority 10 and written only on a change:
  - an empty zone: `ZN-<Z>-OCC-SENSOR` (BV 450+i) Unoccupied and `ZN-<Z>-CLG-SP` (AV 200+i)
    28.0 degC (the BMS then runs occupied-standby);
  - a zone with people: `OCC-SENSOR` Occupied and `CLG-SP` released (NULL), so the building
    schedule applies, as in the baseline run;
  - a booked room's zone is held like a zone with people from 30 minutes before the booking to
    its end (pre-cooling);
  - from 19:00 every slot is released; at `stop()` every slot it holds, the heartbeat's
    included;
  - `SPOTCOOL-HEARTBEAT` (BV 901) is toggled every 30 s of wall time, On first.
  A write the BMS refuses or never answers is logged and tried again at the next step.
- **Timing: at each step's close.** It commands the zones when the step's `tick` event arrives:
  the people from that step's state, the bookings and 19:00 judged at the next step's start,
  the first step the commands can take effect in (`StubPolicy.commands(state, t_s)`). So a
  booking pre-cools from 30 minutes before it, is released at its end, and everything is
  released from 19:00, as the BMS takes them. The tick event comes after `bms.input()` has
  taken the step's commands, so every write takes effect from the next step in a paced run
  whose step leaves time for the stub's round trips; at speed 0 they lag by 2-4 steps. The
  world never waits for an agent: Person A's faces the same world. The evals check each
  write's lag from the saved record. Don't move the commands to the `scenario_state`: that
  event races the tick through the pipeline, so some writes would take effect a step sooner,
  depending on BACnet latency.
- **It backs off from an operator.** Before writing a zone point, a release at 19:00 or at
  `stop()` included, it reads the point's priority array (one ReadProperty):
  - a slot more urgent than 10 (1-9; an operator writes at 8) holds a value: it does not write,
    publishes one `decision` and one `explanation` saying it backed off and who holds the point
    (once per hold and wanted value, though it tries again at every step), and resumes when the
    hold is released;
  - it never releases a slot it did not write: a release goes out only while slot 10 still holds
    the value the stub wrote. Another client's write at 10, or the BMS's own release (the
    heartbeat watchdog), is left alone, and the stub forgets the point.

  A slot 10 it backed off from releasing stays under the hold: after the operator lets go it is
  in force until the stub's next step releases it, or, after `stop()`, until the heartbeat
  watchdog releases every slot 10 within 120 s. The heartbeat itself is written without the
  check: it is the stub's own liveness signal, not a command.
- **Visibility.** It publishes on `"events"`, with `source: "agent"`, a `perception` event per
  scenario state, a `decision` per point written and an `explanation` per zone changed. WP7 can
  rely on their data:
  - `perception`: `{"stub": true, "people": {"cor": 4, "nor": 2, ...}}`;
  - `decision`: `{"stub": true, "zone": "sou", "point": "ZN-SOU-CLG-SP", "value": 28.0,
    "priority": 10, "reason": "the zone is empty"}`; `value` is a state text, a setpoint in degC,
    or null for a release;
  - `explanation`: `{"stub": true, "text": "South: the zone is empty -> ZN-SOU-OCC-SENSOR
    Unoccupied, ZN-SOU-CLG-SP 28.0 at priority 10"}`;
  - a back-off: the `decision` carries the value it wanted, at 10, with `"reason": "backed off:
    held at 23.0 by an operator at priority 8"`, and the `explanation` says `"South:
    ZN-SOU-CLG-SP is held at 23.0 by an operator at priority 8 -> backed off from writing 28.0;
    trying again each step"` (`releasing it` for a release; no retry at `stop()`).

  `stub: true` is how the dashboard labels a stub run.
- **Lifecycle.** It subscribes when it starts, under the world's runner, and runs until the
  world's `on_pipeline_finished` stops it; it releases its writes while the BMS still serves,
  by the same rules, with a `decision` per release (reason "the stub is stopping"). Those come
  after the recorder has finished, so the recording does not hold them.

## Key directories

| Path | Contents |
|---|---|
| `src/spotcool_world/frames/` | Frame classes |
| `src/spotcool_world/processors/` | `FrameProcessor`, `TickSource`, `RunStatusProcessor`, `ScenarioProcessor`, `PlantProcessor`, `CameraSceneProcessor`, `TelemetryProcessor`, `KpiProcessor`, `EventEmitterProcessor`, `RecorderProcessor`, `FramePublisher`, `BusPublisher` |
| `src/spotcool_world/serializers/` | Run-record JSONL format (`RunWriter`, `read_records`) and `RunManifest` |
| `src/spotcool_world/pipeline/` | `Pipeline`, `ParallelPipeline`, `PipelineParams`, source/sink edges |
| `src/spotcool_world/workers/` | `BaseWorker`, `PipelineWorker`, `OccupantWorker`, `HttpServerWorker` (serves any FastAPI app on a host and port), `VrfWorker` (the VRF unit in wall time) |
| `src/spotcool_world/bus/` | `Bus` protocol, `InProcessBus` |
| `src/spotcool_world/runner/` | `WorkerRunner`, the `spotcool-world` CLI (`run`, `replay`, `report`, `lint`, `probe`), `assembly.py` (`World`, `Replay`), `report.py` (run reports) |
| `src/spotcool_world/stub_agent/` | `StubAgentWorker`: a BACnet client with openly perfect perception, for runs without Person A's agent |
| `src/spotcool_world/clocks/` | `SimClock`, `WallPacer` |
| `src/spotcool_world/observers/` | `BaseObserver`, `LogObserver`, `StepTimingObserver` |
| `src/spotcool_world/services/` | `PlantService`, `CameraService`, `CameraSystem`, `WorldSettings`, `create_plant`, `create_cameras`; providers in `boptest/`, `rc/`, `replay/`, `clips/`, `datasets/`, `live/` |
| `src/spotcool_world/transports/` | `BaseTransport`; providers in `bacnet/` (the BMS, the VRF gateway and the server code they share), `http/` (`create_http_app`, the cameras and calendar routers), `websocket/` (`create_dashboard_app`: `/ws/events`, `/ws/agent`, `/runs`) |
| `src/spotcool_world/devices/` | Equipment models: `vrf/` (`VrfUnit`) |
| `src/spotcool_world/scenarios/` | Scenario DSL, linter, compiler, library |
| `src/spotcool_world/adapters/` | Unit conversion; Haystack and vendor maps later |
| `src/spotcool_world/utils/` | `BaseObject` (names, tasks, events), `TaskManager`, `SeenIds` |
| `src/spotcool_world/testing.py` | `run_test`, `CaptureProcessor` |
| `examples/foundational/` | One runnable lesson per concept (`01_clock.py` ...) |
| `evals/` | Golden evals (marker `eval`): whole-world runs of the library, judged from their records |
| `tests/` | Unit tests; contract suites per service base arrive with the providers |
| `changelog/` | towncrier fragments |

Outside this package, at the repo root:

| Path | Contents |
|---|---|
| `apps/dashboard/` | The judges' dashboard (React, WP7) |
| `scripts/footage/` | The footage pipeline: normalize clips, extract audio, dataset indexes (WP8) |
| `scripts/video/`, `video/` | The demo video built from code: storyboard, narration, sources, motion graphics, takes, assembly, QA (WP9; start at `video/README.md`) |
| `docs/release/numbers.md` | Every figure the video may show, with its source |
| `docs/status/HANDOFF.md` | Person B's status, decisions and next actions |

## Code style

- Google-style docstrings. Document `__init__` arguments in the class docstring under `Args:`;
  dataclass and model fields under `Parameters:`. Every public module, class, method and
  property has a docstring (ruff `D`, not enforced in tests and examples).
- Ruff with line length 100 (pipecat's rule families ASYNC, B, D, F, I, UP, plus E and RUF006,
  which flags a task created without keeping a reference) and `ruff format`.
- Type hints everywhere; pyright in standard mode must be clean, tests and examples included.
- Frames are `@dataclass(kw_only=True)`; repeat the decorator on every subclass.
- Pydantic `BaseModel` for config, parameters, metrics and wire models.
- Absolute imports (`from spotcool_world.frames import TickFrame`). Optional dependencies are
  imported only inside their provider sub-package, never by the core.
- Strings printed or logged are ASCII (`->`, `degC`): Windows consoles may not be UTF-8.
- Comment only the why the code cannot show. A known shortcut gets
  `# shortcut: <the limit>, <when to upgrade>`.
- **No deprecation shims.** When you rename or change an API, update every caller in the same
  WP and delete the old name. Nothing outside this repo imports `spotcool_world`: Person A
  depends only on `packages/contracts`, which is versioned and changed only by the lead.
  (Pipecat's deprecation rules exist for its external users.)

## Writing for future readers

This covers comments, docstrings, commit messages, changelog fragments and PR descriptions.
- Describe what the code does now, not how it got there. Leave out rejected alternatives,
  earlier behaviour, work-in-progress shorthand and the verification you did.
- Keep prose in proportion to the code: routine code needs a short note or none.
- The test: every sentence answers "what does the code do now?".
- Keep a rationale only as a short trailing note, where a reader would otherwise be puzzled or
  walk into a trap.
- When behaviour changes, update this file in the same PR.

## Adding a service, transport or adapter

1. Subclass the base (`PlantService`, `CameraService`, `BaseTransport`) in its provider
   sub-package (e.g. `services/boptest/hosted.py`) and implement every abstract method. Copy
   the shape of an existing provider: `services/boptest/` for a service (a client with
   timeouts and retries, and a `stop()` that always runs) or `transports/bacnet/bms.py` for a
   transport (`input()` and `output()` processors around the protocol).
2. Put third-party dependencies in an extra in `pyproject.toml` (`uv lock`, then
   `uv sync --all-packages --extra <name>`). The core must import without them.
3. Wrap it in a processor that handles frames and pushes each one on in the direction it
   arrived. Report failures with `push_error` (non-fatal unless the run's results would be
   wrong), emit `MetricsFrame`s for latency where useful, and release everything in `stop()`.
4. Convert units with `adapters.units`, take point names from `spotcool_contracts`.
5. Pass the shared contract suite for that base (RC runs always; BOPTEST tests are marked
   `@pytest.mark.live` and skipped by default).
6. Add a numbered example in `examples/foundational/` and a changelog fragment.

## Testing

- Async tests need `@pytest.mark.asyncio` (pytest-asyncio runs in strict mode here).
- Test a processor with the harness; it runs inside a real worker and runner:

  ```python
  from spotcool_world.testing import run_test

  down, up = await run_test(
      MyProcessor(), frames_to_send=[TickFrame(index=0, step_s=900.0, sim_time_s=0.0)]
  )
  ```

  `down` and `up` are the frames that left the processor at its bottom and top, without the
  `StartFrame` and `EndFrame` the harness sent. `send_end=False` lets the processor end the run
  itself (e.g. a `TickSource`); `params=PipelineParams(...)` passes a clock or observers.
- Tests must not leak tasks: `uv run pytest packages -W error::RuntimeWarning` stays green.
- Tests that open sockets are marked `@pytest.mark.bacnet`. They run by default, on 127.0.0.1 and
  ports from 47900, and skip when a port is taken. `tests/bacnet/conftest.py` has the device and
  client fixtures.
- Wall-clock assertions need margins for Windows timers (see below).
- Pipecat's `SleepFrame` isn't needed here. With lockstep push, a frame is fully handled when
  `push_frame` returns, so tests assert on order directly.
- **Evals** (`evals/`, marker `eval`, deselected by default; CI's `evals` job runs them) follow
  pipecat's behavioral evals: run once, save, judge the saved run.
  - `conftest.py`: one session-scoped fixture per run. Each runs the whole `World` once with
    the stub agent on loopback (free ports, a temp `run_dir`, no `SPOTCOOL_WORLD_*` variables,
    the occupants posting to the world's own HTTP server), at speed 1800: a step every 0.5 s,
    the RC day in about 25 s. It returns `load_run`'s reading of the record, nothing in memory.
  - `test_golden.py` judges only through `runner/report.py` (`check_energy`, `quote_blockers`,
    the manifest's final KPIs), plus two things read from the same record: the connection
    recoveries, and each stub write's lag (`write_lags`: the steps between its decision and the
    tick the BMS took it at). Every run must be valid: finished, no dropped events, the stub
    recorded, both integrations within `CROSS_CHECK_PCT` of the plants' own, no fallbacks or
    recoveries, exactly the RC and stub quote blockers, and every write taken one step after
    its decision.
  - Then, on RC: `hybrid_tuesday`'s saving and comfort in bands around the measured values;
    the honesty check, `evals/scenarios/all_day_occupied.yaml` (every zone occupied 07:00-19:00,
    linted by its fixture) with |saving| < 0.5 %; `all_hands_day`'s saving, from its empty
    hours before 08:30 and after 18:00, within 1 pp of the measured 2.34 %; the order control <
    `all_hands_day` < `hybrid_tuesday` < `empty_friday`; and two `hybrid_tuesday` runs within
    0.5 pp.
  - Each band's docstring gives the measured values and why the band is that wide. Measure
    before you move one; a reproducibility spread is a finding, never a band to widen.
  - `-m "eval and live"` runs `hybrid_tuesday` on `SPOTCOOL_LIVE_BOPTEST_URL` at speed 60, the
    paced checkpoint's, with the same checks (the stub the only blocker); it skips without the
    variable. With `SPOTCOOL_LIVE_RUN_DIR` set, the run is kept there as
    `eval-live-hybrid-<date>-<time>`, and the test prints its path, saving, energies, lags and
    report command.

## Definition of done

- From the repo root, with `UV_PROJECT_ENVIRONMENT` set, all green:
  `uv run ruff check packages`, `uv run ruff format --check packages`,
  `uv run pyright packages`, `uv run pytest packages scripts`, and `uv run pytest -m eval` for a
  change that can move a run's numbers (the stub, the plants, the BMS, telemetry, scenarios).
- Every example in `examples/foundational/` runs in seconds.
- Docs (this file, docstrings) and a changelog fragment
  (`changelog/+<slug>.<added|changed|fixed|removed|security|other>.md`) are updated.
- `packages/contracts` is untouched unless the lead approved the change.
- A reviewer agent that did not write the code has approved it.

## Known limitations

- **Ctrl+C during a plant start.** The first Ctrl+C queues a `CancelFrame`, which takes effect
  only after the `StartFrame`, and so the plant's `start()`, returns. On BOPTEST that can be
  minutes: the select and the warm-up. A second Ctrl+C cancels the start at once, and the start
  path still stops its BOPTEST test: the plant's stop request runs on as a task of its own, and
  its next `stop()` waits for it. Letting the first Ctrl+C interrupt a start needs a change to
  the WP0 worker.
- **Ctrl+C on a dead BOPTEST connection.** The first Ctrl+C cancels a step still recovering
  at once (the tick is `TickSource`'s task, which the `CancelFrame` cancels as it passes). The
  plants' final KPIs and their stops still follow, and on a dead connection each waits its
  bound: the KPIs retry for up to `KPI_RETRY_S` (60 s, plus an attempt under way, up to
  120 s), then the stops wait up to `STOP_WAIT_S` (65 s). So the first Ctrl+C can take about
  four minutes to end such a run; a second one skips the KPIs, and the stops still wait. A
  frame queued on the worker by hand (tests do) does wait behind a tick in flight, up to
  `RECOVERY_S` for a recovering step.

- **A replay plays the VRF back per step.** The recording holds every `vrf_state` and VRF
  `bms_write`, but a replay paces only at `tick` events: a step's VRF events go out together
  after it, so the louvers jump once a step instead of slewing. The video films the louver
  segment live, so it never needs a replay.

## Windows notes

- Keep the venv outside OneDrive (`UV_PROJECT_ENVIRONMENT`); OneDrive sync locks files. uv's
  "failed to hardlink" warning is harmless (`export UV_LINK_MODE=copy` silences it).
- Windows event loops have no `loop.add_signal_handler`. `WorkerRunner` falls back to
  `signal.signal` and hands the signal to the loop with `call_soon_threadsafe`; Ctrl+C stops
  workers gracefully, a second Ctrl+C cancels them.
- On Python 3.11, `time.monotonic()` ticks every 15.6 ms on Windows. Measure durations with
  `time.perf_counter()` (frames and observers already do).
- asyncio timers on Windows can fire up to one 15.6 ms tick early, so `asyncio.sleep(0.05)` may
  return after about 0.035 s. Give timing assertions that margin.
- **The firewall blocks BACnet/IP from other PCs.** Allow it once, in an elevated PowerShell
  (add `Public` to `-Profile` if Windows calls the network public; undo with
  `Remove-NetFirewallRule -DisplayName "SpotCool BACnet/IP"`). Loopback traffic is not filtered.

  ```powershell
  New-NetFirewallRule -DisplayName "SpotCool BACnet/IP" -Direction Inbound -Protocol UDP -LocalPort 47808-47810 -Action Allow -Profile Domain,Private
  ```

- **YABE on the same PC: the loopback workaround.** YABE keeps a shared socket on UDP 47808,
  which can swallow unicasts meant for a device on the same address. Serve the device on
  loopback on another port: `06_bacnet_device.py --address 127.0.0.1 --port 47809`. In YABE, add
  a channel with Local endpoint `127.0.0.1` (type it in), Start, then Functions > IP Services >
  Add Remote Bacnet/IP node: `1001 - 127.0.0.1:47809`. No Who-Is broadcast is needed.
- A port already in use fails `BacnetBmsTransport.start()` with `[WinError 10048] Only one usage
  of each socket address`: another BACnet program (or YABE in exclusive-socket mode) holds it.
- **The firewall blocks the HTTP servers from other PCs**: A's agent and a browser there. Allow
  TCP 8081 (cameras and calendar) and 8090 (the dashboard) once, in an elevated PowerShell (add
  `Public` to `-Profile` if Windows calls the network public, as it usually does a phone's
  hotspot; undo with `Remove-NetFirewallRule -DisplayName "SpotCool HTTP"`). The phone needs no
  rule: the world polls it.

  ```powershell
  New-NetFirewallRule -DisplayName "SpotCool HTTP" -Direction Inbound -Protocol TCP -LocalPort 8081,8090 -Action Allow -Profile Domain,Private
  ```

- **The live phone camera (manual).**
  1. On the Android phone, install "IP Webcam" (by Pavel Khlebovich) from Google Play. In its
     settings, set Video preferences > Video resolution to 1280x720.
  2. Put the phone and this PC on one network: turn the phone's hotspot on and join it from the
     PC, or join both to the same Wi-Fi. A's PC joins that network too, to reach the world.
  3. Plug the phone into a charger, then tap "Start server" at the bottom of the app's settings.
     The screen shows the phone's address, such as `http://192.168.43.1:8080`. Newer Android
     versions pick a new hotspot subnet each time, so read it every time.
  4. On this PC, open `http://<address>/shot.jpg` in a browser: it shows one photo.
  5. Try it with `07_camera_catalog.py --live-url http://<address>/shot.jpg`: the catalog lists
     `cam-meeting-live` and its snapshot is the phone's view. For a world run, set
     `SPOTCOOL_WORLD_LIVE_CAMERA_URL` (or `live_camera_url` in `world.yaml`) to the same URL.
  6. Stop the app's server for 10 s, then start it again: meanwhile the snapshot keeps the last
     frame and carries `X-Camera-Stale: 1` after 5 s, and the console warns once.
- Git Bash and PowerShell set environment variables differently (`export X=...` vs
  `$env:X = "..."`).
