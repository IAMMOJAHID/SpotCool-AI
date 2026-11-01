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

uv run pytest packages/world              # all world tests (plant tests need --extra boptest --extra rc)
uv run pytest packages/world/tests/test_parallel_pipeline.py -k branch_order
SPOTCOOL_LIVE_BOPTEST_URL=http://127.0.0.1:8000 uv run pytest packages -m live   # live BOPTEST
uv run ruff check packages
uv run ruff format --check packages       # `uv run ruff format packages` to fix
uv run pyright packages

uv run python packages/world/examples/foundational/03_pipeline.py
uv run spotcool-world version
uv run spotcool-world run --dry-run       # print resolved settings
uv run spotcool-world probe --plant rc    # go/no-go check of a plant (boptest-local, boptest-hosted)
cd packages/world && uv run towncrier build --draft   # preview the changelog
```

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
| **Service** (abstract base + one sub-package per provider) | Swappable engines | `services/plant_service.py`, `services/camera_service.py` | bases done; plants done (WP1); cameras WP4 |
| **Transport** (`input()` / `output()` processors) | Every protocol the agent touches | `transports/base_transport.py` | base done; BACnet WP2-3, HTTP WP4-5, WebSocket WP6 |
| **Adapter** | Translators to vendor and standard formats | `adapters/units.py` | K/W/m3s/fraction done; Haystack, vendor maps later |
| **Settings** | pydantic-settings + `world.yaml` | `services/settings.py` | done |
| **Extras** | The core stays lean; every adapter is opt-in | `pyproject.toml` | declared: `boptest`, `bacnet`, `cameras`, `rc`, `dashboard`, `video` |

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

Only `TickSource`, `ParallelPipeline`, `PlantProcessor` and `BusPublisher` exist so far; the other
processors are the later work packages.

### Lifecycle

1. `WorkerRunner.run(worker)` gives the worker the shared bus and runs it.
2. `PipelineWorker.run()` calls `setup()` on every processor (shared clock, bus, observers,
   task manager), then pushes the `StartFrame`: the first queued frame if it is one, otherwise
   `params.start_frame()`. `on_pipeline_started` fires.
3. `TickSource` starts its tick task on `StartFrame`. Each tick is pushed and awaited through
   the whole pipeline, then paced, then the clock advances. At `end_time_s` it pushes `EndFrame`.
4. The worker finishes when an `EndFrame` leaves the bottom of the pipeline
   (`on_pipeline_finished`, state `finished`). It cancels when a `CancelFrame` is queued, sent by
   `stop()`, pushed up to the top by a processor, or follows a fatal `ErrorFrame` reaching the
   top. The `CancelFrame` then goes through the whole pipeline (state `cancelled`).
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
  outside the flow of frames (like `PipelineWorker.stop()`), and say so in its docstring.
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
  Tasks are cancelled on `CancelFrame` and in `cleanup()`. An exception escaping such a task
  becomes a fatal `ErrorFrame`.
- **Errors.** Report with `await self.push_error(message, fatal=...)`; it logs and sends an
  `ErrorFrame` upstream. The worker fires `on_pipeline_error`; a fatal error cancels the run
  (unless `PipelineParams(fatal_error_cancels=False)`). Do not raise from `process_frame` for
  expected failures: an exception there fails the run (or kills the tick task).
- **Ask for a cancel from inside** by pushing a `CancelFrame` upstream.
- **ParallelPipeline output is deterministic.** Each downstream frame goes to every branch at
  once; what the branches emit is pushed on in branch order after all branches finish, then the
  original frame (by id) once, if any branch passed it on. Upstream frames entering from below
  go into every branch and leave the top deduplicated by id. Frames a branch pushes from a
  background task, outside any entry, pass straight through (unordered).
- **Sim time is the only clock inside processors.** Use `frame.sim_time_s` or `self.clock`,
  never `time.time()`, for anything simulated. Pass the same `SimClock` to `TickSource` and
  `PipelineParams(clock=...)` so every processor sees the time the ticks advance. Wall time is
  for pacing and metrics only, measured with `time.perf_counter()` (see Windows notes).
- **Events.** Declare with `self.register_event_handler("on_x")` in `__init__`; users attach with
  `@obj.event_handler("on_x")`; fire with `await self._call_event_handler("on_x", *args)`.
  Handlers run as background tasks unless `sync=True`, receive the object first, and a failing
  handler is logged, never raised.
- **Bus.** `InProcessBus` gives each subscriber a bounded queue that drops the oldest message
  when full (`subscription.dropped` counts). Publish immutable values; messages are shared.
  `BusPublisher(bus, topic)` publishes every `EventFrame.envelope`.
- **Never store camera frames**: memory only, never disk or logs (WP4 adds a test that asserts
  it). **Always stop BOPTEST tests** in `PlantService.stop()`: the hosted service is shared and
  free.
- **Contracts are the boundary.** Reuse `spotcool_contracts` models and point maps; never copy
  or redefine them here. Units on the wire to A are BMS units; convert with `adapters.units`.
- **Plants speak BOPTEST's REST names.** `create_plant(settings, run)` picks the provider. Every
  plant checks override names against the catalog (`services/boptest/catalog.py`) and reports
  an overwritten signal under its input's `_u` key, as BOPTEST's REST API does. BOPTEST inputs
  latch, so the BOPTEST plant sends `_activate` for all ten managed inputs (each zone's cooling
  setpoint and damper) on every step.

## Key directories

| Path | Contents |
|---|---|
| `src/spotcool_world/frames/` | Frame classes |
| `src/spotcool_world/processors/` | `FrameProcessor`, `TickSource`, `BusPublisher`, `PlantProcessor`; future processors |
| `src/spotcool_world/serializers/` | Run-record JSONL format (`RunWriter`, `read_records`) |
| `src/spotcool_world/pipeline/` | `Pipeline`, `ParallelPipeline`, `PipelineParams`, source/sink edges |
| `src/spotcool_world/workers/` | `BaseWorker`, `PipelineWorker`; future device and occupant workers |
| `src/spotcool_world/bus/` | `Bus` protocol, `InProcessBus` |
| `src/spotcool_world/runner/` | `WorkerRunner`, the `spotcool-world` CLI |
| `src/spotcool_world/clocks/` | `SimClock`, `WallPacer` |
| `src/spotcool_world/observers/` | `BaseObserver`, `LogObserver`, `StepTimingObserver` |
| `src/spotcool_world/services/` | `PlantService`, `CameraService`, `WorldSettings`, `create_plant`; providers in `boptest/`, `rc/`, `replay/`, `clips/`, `datasets/`, `live/` |
| `src/spotcool_world/transports/` | `BaseTransport`; providers in `bacnet/`, `http/`, `websocket/` |
| `src/spotcool_world/devices/` | Equipment models (`vrf/`) |
| `src/spotcool_world/scenarios/` | Scenario DSL, linter, compiler, library |
| `src/spotcool_world/adapters/` | Unit conversion; Haystack and vendor maps later |
| `src/spotcool_world/utils/` | `BaseObject` (names, tasks, events), `TaskManager`, `SeenIds` |
| `src/spotcool_world/testing.py` | `run_test`, `CaptureProcessor` |
| `examples/foundational/` | One runnable lesson per concept (`01_clock.py` ...) |
| `tests/` | Unit tests; contract suites per service base arrive with the providers |
| `changelog/` | towncrier fragments |

## Code style

- Google-style docstrings. Document `__init__` arguments in the class docstring under `Args:`;
  dataclass and model fields under `Parameters:`. Every public module, class, method and
  property has a docstring (ruff `D`, not enforced in tests and examples).
- Ruff with line length 100 (rule families ASYNC, B, D, E, F, I, UP) and `ruff format`.
- Type hints everywhere; pyright in standard mode must be clean, tests and examples included.
- Frames are `@dataclass(kw_only=True)`; repeat the decorator on every subclass.
- Pydantic `BaseModel` for config, parameters, metrics and wire models.
- Absolute imports (`from spotcool_world.frames import TickFrame`). Optional dependencies are
  imported only inside their provider sub-package, never by the core.
- Strings printed or logged are ASCII (`->`, `degC`): Windows consoles may not be UTF-8.
- Comment only the why the code cannot show. A known shortcut gets
  `# shortcut: <the limit>, <when to upgrade>`.

## Writing for future readers

Docs and docstrings describe what the code does now, not how it got there. Keep prose in
proportion to the code. Leave out rejected alternatives and verification notes unless a future
reader needs them to avoid a trap. When behaviour changes, update this file in the same PR.

## Adding a service, transport or adapter

1. Subclass the base (`PlantService`, `CameraService`, `BaseTransport`) in its provider
   sub-package (e.g. `services/boptest/hosted.py`) and implement every abstract method.
2. Put third-party dependencies in an extra in `pyproject.toml` (`uv lock`, then
   `uv sync --all-packages --extra <name>`). The core must import without them.
3. Wrap it in a processor that handles frames; report failures with `push_error`; emit
   `MetricsFrame`s for latency where useful; release everything in `stop()`.
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
- Wall-clock assertions need margins for Windows timers (see below).
- Golden evals with a stub agent (a tiny BACnet client acting like A's agent) live in
  `evals/` and arrive with WP10.

## Definition of done

- From the repo root, with `UV_PROJECT_ENVIRONMENT` set, all green:
  `uv run ruff check packages`, `uv run ruff format --check packages`,
  `uv run pyright packages`, `uv run pytest packages`.
- Every example in `examples/foundational/` runs in seconds.
- Docs (this file, docstrings) and a changelog fragment
  (`changelog/+<slug>.<added|changed|fixed|removed|security|other>.md`) are updated.
- `packages/contracts` is untouched unless the lead approved the change.
- A reviewer agent that did not write the code has approved it.

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
- The firewall may block BACnet: allow UDP 47808-47810 or use loopback addresses.
- Git Bash and PowerShell set environment variables differently (`export X=...` vs
  `$env:X = "..."`).
