# WP6: Telemetry, KPIs, recorder, dashboard feed, run report

**Tool:** Claude Code. **Branch:** `wp/wp6-telemetry`, cut from `world/main`. The lead syncs the trunk in, including WP4, before the build starts.
**Depends on:**
- WP0 and contracts (`events.py`, the `office_bms` point map);
- WP1: `PlantMeasurementsFrame`, the plants' final `KpiFrame`, and `serializers/jsonl.py`, which WP1's `ReplayPlant` reads;
- WP4: `HttpServerWorker`;
- WP5: `ScenarioProcessor`, `FramePublisher`, and the occupant worker's results on `"events"`.

## Goal
Turn simulation frames into the numbers the video quotes and the stream the dashboard draws. Record every run, Person A's events included, so any run can be replayed for a video take.

## Your lane (only these paths)
- `packages/world/src/spotcool_world/processors/telemetry.py`, `kpi.py`, `event_emitter.py`, `recorder.py`, and their exports in `processors/__init__.py`.
- `serializers/` (extend `jsonl.py`, keeping WP1's `RawRecord` and `read_records` compatible).
- `transports/websocket/` (`dashboard.py`).
- `runner/report.py`, and in `runner/cli.py` the `report` subcommand only.
- `packages/world/pyproject.toml`: add `report = ["matplotlib>=3.8,<4"]`, then run `uv lock` and commit `uv.lock`.
- `packages/world/AGENTS.md`: add a "Telemetry, recording and the dashboard feed" section. Change nothing else.
- `packages/world/tests/telemetry/`, `examples/foundational/09_record_and_report.py`, and the changelog fragment `packages/world/changelog/+wp6-telemetry.added.md`.

`packages/contracts/` is read-only. If you think a contract must change, stop and report it.

## Decisions already made (binding)
1. **Comfort where people were (K·h)** is computed on each SpotCool `PlantMeasurementsFrame`, which is stamped `t`, the end of its step.
   - **People** come from the latest `ScenarioStateFrame`. In a tick it arrives before the plants step, so it's the state of the step that just ended, `state_at(t − step)`. Never look the scenario up at `t`.
   - **The band** is the baseline run's applied heating and cooling setpoints from the same step: its `_u` keys at stamp `t`, which describe the step that just ended.
   - **The formula:** for each zone with `people > 0`, add `max(0, T − upper) + max(0, lower − T)` times the step length in hours.
   - **The same score for the baseline run,** with the same people and band, so the KPI table compares both.
2. **Energy.**
   - `energy_kwh` is cumulative per run: the trapezoid rule over the samples at the stamps, starting from the plant's initial state (index 0, at the start time).
   - `total_power_kw` sums the 7 meters (BOPTEST's ElectricPower basis).
   - `energy_saved_pct` = (E_baseline − E_spotcool) / E_baseline × 100, at the same stamp.
3. **KPI frames.**
   - A provisional `KpiFrame` per run every 4 steps.
   - At `EndFrame`, a final one (`provisional=False`) that merges BOPTEST's KPIs from both runs' final `KpiFrame`s (WP1 pushes them at `EndFrame`).
4. **Recovery time is computed by `report`, not by `KpiProcessor`:** the contract's `KpiData` has no field for it.
   - **Definition:** for each zone and each arrival (people > 0 after at least one step with 0), the minutes until T ≤ the baseline's cooling setpoint + 0.5 K.
   - The report also lists arrivals that never recovered.
5. **`EventEmitterProcessor`** converts every domain frame into an `EventFrame` carrying the contract payload, and passes the original frame on.
   - **Ticks:** Tick → TICK, with `clock` and `day_of_year` from `SimClock`.
   - **Converted directly:** RunStatus, ScenarioState, PlantMeasurements (with its summary), BmsWrite, Kpi, VrfState, CameraScene, Calendar, ComplaintCue and Error.
   - **Metrics:** Metrics → one METRICS event per sample.
   - **Complaints:** ComplaintCue converts `frame.data` only. `ComplaintCueFrame.audio` never reaches an envelope.
6. **One event stream.** The bus topic `"events"` carries everything:
   - the pipeline's events, through `BusPublisher` at the end of the pipeline;
   - the occupant worker's delivery results;
   - Person A's events from `/ws/agent` (decision 8).
7. **Recorder** (`processors/recorder.py` with `serializers/jsonl.py`). **The format is a contract with WP1's `ReplayPlant`;** share the reader code.
   - **Raw records** come from the pipeline: every `PlantMeasurementsFrame`'s raw dict, as WP1's `RawRecord` (`index` 0 is the initial state).
   - **Events:** every envelope comes from a subscription to `"events"`. The recorder subscribes in `setup()`, before the run starts, with a large queue (for example 100 000) so nothing is dropped. A background task writes them.
   - **At `EndFrame` or `CancelFrame`,** it drains the subscription and writes `manifest.json`. By then everything the pipeline published has passed `BusPublisher`, because of lockstep.
   - **`runs/<run_id>/events.jsonl`** holds one record per line:
     - `{"kind": "event", "envelope": {...}}`
     - `{"kind": "raw", ...}`
   - **`runs/<run_id>/manifest.json`** holds:
     - `run_id`, `created_utc`, `settings` (secrets redacted);
     - `git_sha`, `world_version`, `contracts_version`;
     - the scenario name and sha256;
     - the plant and step;
     - the final KPIs;
     - `dropped_events`, which must be 0 (log a warning otherwise).
   - Flush once per tick. **No image or audio bytes ever** (test this).
8. **Dashboard server** (`transports/websocket/dashboard.py`). `create_dashboard_app(bus, run_dir, dist_dir=None)` returns a FastAPI app; WP11 runs it with WP4's `HttpServerWorker` on `http_host:dashboard_port` (8090).
   - **`/ws/events`** is a read-only broadcast of `"events"` for dashboards.
     - Each client has its own subscription with a bounded queue that drops its oldest messages, so a slow browser never blocks the simulation.
     - Inbound messages are ignored.
   - **`/ws/agent`** is publish-only, for Person A's agent.
     - Each message must be a valid `EventEnvelope` with `source: "agent"` and an agent type (`perception`, `decision`, `explanation` or `chat`).
     - It must be 64 KB or less, at no more than 20 messages per second per connection.
     - A valid message is published on `"events"`. An invalid one gets `{"error": "..."}` back and is dropped.
     - This socket never sends world events. That keeps ground truth out of the agent by construction.
   - **Other routes:**
     - `GET /runs` and `GET /runs/{id}/events.jsonl` (the id must match a safe pattern; no path traversal);
     - `/` serves `apps/dashboard/dist` when it exists;
     - `GET /healthz`.
   - CORS allows `http://127.0.0.1:*` and `http://localhost:*` (the Vite dev server).
9. **Report** (`runner/report.py`, extra `report`). `spotcool-world report <run_id>` writes `runs/<id>/report.md` and PNG charts, using matplotlib's Agg backend.
   - **Charts:** power and cumulative energy for both runs; zone temperatures against setpoints; the occupancy timeline.
   - **Tables:** the KPI table (both runs' comfort, energy, saved % and BOPTEST's KPIs) and the recovery times (decision 4).
   - **The honesty note,** word for word: "BOPTEST's occupant heat follows its fixed schedule. Savings in zones that the cameras show empty may therefore be overstated by up to the occupants' heat. The RC plant and the `all_hands_day` scenario bracket this."
   - The core and the CLI import without matplotlib.
10. **Example** `09_record_and_report.py` runs `TickSource → ScenarioProcessor(hybrid_tuesday) → ParallelPipeline[RC spotcool ∥ RC baseline] → telemetry → KPI → emitter → BusPublisher`, with the recorder subscribed. It then writes the report, in seconds. WP11's full world becomes `10_full_world.py`.
11. **Privacy and errors.**
    - No `logger.exception` where frames or audio are in scope.
    - A dashboard or agent socket failing is never fatal to the run.
    - A recorder write failure is fatal: a run without its record is invalid.

## Tests (`packages/world/tests/telemetry/`)
- **Telemetry:** unit conversions and power totals on fixture raw dicts built from the catalog snapshot names.
- **Energy:** the integral on a known profile, from the initial state on.
- **Comfort:** a synthetic two-zone case (people present and absent, inside and outside the band), using the state of the step that just ended, not the one at the stamp.
- **KPI frames:** provisional every 4 steps; the final one merges both runs' BOPTEST KPIs.
- **Emitter:** maps every world `EventType`; the audio never reaches an envelope.
- **Recorder:**
  - round-trips;
  - WP1's `ReplayPlant` can replay a recorded run;
  - an agent event and an occupant result published on the bus land in the recording;
  - `dropped_events` is 0;
  - the no-binary assertion.
- **Dashboard:**
  - `/ws/events` broadcast, with a slow client dropping its oldest messages;
  - `/ws/agent` accepts a valid agent envelope and rejects world types, a bad source, oversize and over-rate messages, and invalid JSON;
  - `/ws/agent` never sends world events;
  - the `/runs` path guard.
- **Report:** renders from a tiny recorded run, recovery times included.
- No leaked tasks: green with `-W error::RuntimeWarning`.

## Done when
- **The four checks are green** with all extras installed:
  - `uv run ruff check packages`
  - `uv run ruff format --check packages`
  - `uv run pyright packages`
  - `uv run pytest packages`
- `09_record_and_report.py` writes a valid run, and `spotcool-world report <run_id>` turns it into `report.md` and charts.
- The changelog fragment is written, and nothing outside your lane changed except `uv.lock`.
