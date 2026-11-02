# WP11: World assembly, CLI and the stub agent

**Tool:** Claude Code (lead-supervised). **Branch:** `wp/wp11-assembly`, cut from `world/main`. The lead syncs the trunk in, including WP6, before the build starts.
**Depends on:** WP0–WP6, all merged. WP3 (VRF) comes later; leave a clearly marked place for its worker and processor.

## Goal
One command brings up the whole stand-in building from `WorldSettings` or `world.yaml`. It also includes a stub agent, so the world can be run, scored and filmed end to end without Person A.

## Your lane (only these paths)
- `packages/world/src/spotcool_world/runner/assembly.py`, plus `runner/cli.py` for `run` and `replay`, and the logging setup.
- `packages/world/src/spotcool_world/services/plant_factory.py`: pass the scenario's occupancy to the RC plant (decision 4).
- `packages/world/src/spotcool_world/stub_agent/` (decision 6).
- `packages/world/pyproject.toml`, only if the stub agent needs an extra entry; then run `uv lock`.
- `packages/world/AGENTS.md`: add a "Running the world" section (and one for the stub agent). Change nothing else.
- `packages/world/world.example.yaml`, `examples/foundational/10_full_world.py`, `packages/world/tests/assembly/`, and the changelog fragment `packages/world/changelog/+wp11-assembly.added.md`.

`packages/contracts/` is read-only. Library modules from earlier WPs change only through the factory hook above. If one needs more, stop and report it.

## Decisions already made (binding)
1. **Pipeline, in this exact order.** Check the order of the recorder and `BusPublisher` against the WP6 section of AGENTS.md.
   ```
   TickSource(SimClock(compiled.start_s, step_s), end_time_s=compiled.end_s, pacer=speed)
    → ScenarioProcessor(compiled)
    → FramePublisher(bus, (CalendarFrame, ComplaintCueFrame))
    → bms.input()
    → ParallelPipeline[PlantProcessor(spotcool) ║ PlantProcessor(baseline)]
    → bms.output()
    → CameraSceneProcessor(cameras)
    → TelemetryProcessor → KpiProcessor
    → EventEmitterProcessor → RecorderProcessor(settings) → BusPublisher(bus, EVENTS_TOPIC)
   ```
   - Compile the scenario with `compile_scenario(load_scenario(settings.scenario), step_s=settings.step_s)`.
   - The run window is the scenario's `start_s` and `end_s`, not `start_clock`/`end_clock`.
   - `PipelineParams.scenario` is `settings.scenario`, so the manifest records the file's sha256.
2. **Workers and servers.** One `InProcessBus` serves the runner, the publishers and the dashboard app. Run these workers under one `WorkerRunner`:
   - the `PipelineWorker`;
   - the `OccupantWorker`;
   - `HttpServerWorker(create_http_app([cameras_router(cameras), calendar_router(store)]), settings.http_host, settings.http_port)`;
   - `HttpServerWorker(create_dashboard_app(bus, settings.run_dir, dist_dir=<repo>/apps/dashboard/dist), settings.http_host, settings.dashboard_port)`;
   - the stub agent, when it's enabled.
3. **Lifecycle.**
   - **Before the runner:** `await bms.start()` (with `settings.bms_address` and `bms_port`); `await cameras.start()`; create the calendar store's subscription and run `store.follow(sub)` as a task.
   - **On the pipeline worker's `on_pipeline_finished`** (EndFrame or CancelFrame): stop the occupant worker, both server workers and the stub agent. Otherwise `WorkerRunner.run` never returns.
   - **On `on_pipeline_error`:** publish `frame_events(error)` on `EVENTS_TOPIC`, because upstream errors never reach the emitter.
   - **After the runner:** stop the cameras and the BMS, close the store subscription, and cancel its task.
   - **Ctrl+C:** stops everything gracefully, including both BOPTEST tests. A second Ctrl+C cancels.
4. **RC occupancy follows the scenario.** Add an optional `occupancy` parameter to `create_plant`; only the RC plant uses it. Assembly builds it from the compiled scenario: the people per zone of the step that starts at `t`. BOPTEST keeps its own fixed schedule, as the honesty note says.
5. **Logging (Law 6).** The CLI replaces loguru's default handler with stderr at `settings.log_level`, using `diagnose=False` and `backtrace=False`, so tracebacks never print locals that hold audio or frames.
6. **Stub agent** (`stub_agent/`, extra `bacnet`). `StubAgentWorker` is a tiny reference client. It talks real BACnet/IP to the BMS over loopback or the LAN, on its own UDP port, exactly as Person A's agent will. It exists so the world can be scored and filmed without A.
   - **Perception is perfect and openly so.** It reads the scenario's ground truth (`scenario_state` envelopes on `"events"`). Its docstring and the dashboard both label it a stub; Person A's agent must never do this.
   - **Policy:**
     - Each zone's `OCC-SENSOR` (BV 450+i) is written at priority 10, Unoccupied when people = 0 and Occupied otherwise, only on a change.
     - Pre-cooling: from `calendar` envelopes, a booked room's zone is held Occupied from 30 minutes before the booking to its end.
     - At 19:00, and at stop, it releases every priority-10 slot (NULL).
     - It toggles `SPOTCOOL-HEARTBEAT` (BV 901) every 30 s of wall time.
   - **Visibility:** it publishes `perception` (people per zone) and `decision`/`explanation` envelopes with `source: "agent"` on `"events"`, so the dashboard's video mode and agent panel work in stub runs.
   - **On the command line:** `spotcool-world run --stub-agent` (off by default).
7. **CLI.**
   - **`spotcool-world run`.** Flags: `[--config world.yaml] [--plant rc|boptest-local|boptest-hosted] [--scenario NAME] [--speed S] [--run-id ID] [--stub-agent]`.
     - `--speed 0` means as fast as possible (`speed=None`).
     - At start-up it prints the URLs: dashboard, camera catalog, calendar, and the BMS address and port.
     - At the end it prints the run id, the final KPIs and the report command.
   - **`spotcool-world replay <run_id> [--speed S]`.** It re-publishes a recording's envelopes on the bus at the given speed (gaps scaled from `sim_time_s`) and serves the dashboard app. It also runs the camera system with the recorded `camera_scene` events setting the scenes, so the camera wall matches the take. There's no physics and no BACnet. It exists for video takes.
   - Keep the `probe`, `lint` and `report` subcommands working.
8. **Speed advice.** Document it in the "Running the world" section: 300 is the default for development; use 60 or slower for integration and filming, because a clip's action (the 14:15 complaint) needs wall time to play.

## Tests (`packages/world/tests/assembly/`)
- **Build:** assembly builds with every plant provider, using fakes for the network-bound ones.
- **End to end on RC, with the stub agent:**
  - 2 simulated hours at maximum speed finish cleanly;
  - the manifest has `ended: finished` and `dropped_events: 0`;
  - the final KPIs are emitted, and energy saved is above 0;
  - the stub's `bms_write` events appear in the recording;
  - the BOPTEST stop hook is called for the BOPTEST plants (with a fake).
- **Ctrl+C:** a simulated SIGINT mid-run stops cleanly, with no orphaned tasks.
- **Replay:** a recorded run re-publishes its events in order at a high speed.
- No leaked tasks: green with `-W error::RuntimeWarning`.

## Done when
- **The four checks are green** with all extras installed:
  - `uv run ruff check packages`
  - `uv run ruff format --check packages`
  - `uv run pyright packages`
  - `uv run pytest packages`
- `uv run spotcool-world run --plant rc --speed 0 --stub-agent` completes `hybrid_tuesday` and prints the KPIs. The run lands in a temp `run_dir`.
- `10_full_world.py` does the same in under a minute.
- **The BOPTEST checkpoint is for the lead:** with `--plant boptest-local` against the Codespace, the same run completes. The agent writes the steps down.
- The changelog fragment is written, and nothing outside your lane changed except `uv.lock`.
