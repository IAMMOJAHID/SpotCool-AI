# WP6: Telemetry, KPIs, recorder, dashboard feed, run report

**Tool:** Claude Code. **Branch:** `wp/wp6-telemetry`. **Depends on:** WP0 and contracts (WP1's `PlantMeasurementsFrame` comes from WP0's frame definitions).
**Directories:** `packages/world/src/spotcool_world/processors/{telemetry,kpi,event_emitter,recorder}.py`, `serializers/`, `transports/websocket/`, `runner/report.py`, `tests/telemetry/`.
**Extras:** `dashboard` (fastapi, uvicorn[standard]); add `report = ["matplotlib>=3.8,<4"]`.

## Goal
Turn simulation frames into the numbers the video quotes and the stream the dashboard draws. Record every run, so any run can be replayed exactly.

## Components
1. **`TelemetryProcessor`:**
   - On `PlantMeasurementsFrame(raw)`, fill `summary: PlantMeasurementsData` in BMS units, using the `office_bms` point map's `read` and `conversion` and `spotcool_world.adapters.units`. That covers zones (temperature, cooling and heating setpoints, CO₂, airflow), `power_kw` by BMS point name, `total_power_kw` (the 7 meters, BOPTEST's ElectricPower basis) and the outdoor temperature.
   - `energy_kwh` is cumulative per run, integrated with the trapezoid rule over `step_s`.
2. **`KpiProcessor`:**
   - Track both runs, and the latest `ScenarioStateFrame`.
   - **Comfort where people were (K·h):** at each step, for each zone with `people > 0`, add `max(0, T − upper) + max(0, lower − T)`, multiplied by the step length in hours.
     - The band is **the building's own schedule**: lower is the baseline run's heating setpoint and upper is its cooling setpoint, at that step.
     - So the score asks: "did people get the comfort the building normally gives them?"
   - **`energy_saved_pct`** = (E_baseline − E_spotcool) / E_baseline × 100.
   - **Recovery time:** for each zone, the minutes from people arriving after a vacancy until T ≤ cooling setpoint + 0.5 °C.
   - Emits a provisional `KpiFrame` every 4 steps. A final one at `EndFrame` merges BOPTEST's KPIs from WP1's `KpiFrame`.
3. **`EventEmitterProcessor`:** converts every domain frame into an `EventFrame` with the contract payload:
   - Tick → TICK (`clock` and `day_of_year` from `SimClock`);
   - RunStatus, ScenarioState, PlantMeasurements (with summary), BmsWrite, Kpi, CameraScene, Calendar, ComplaintCue and Error convert directly;
   - Metrics → one METRICS event per sample.

   It passes the original frames on.
4. **Recorder** (`serializers/jsonl.py` + `processors/recorder.py`). **The format is a contract with WP1's ReplayPlant**; share the reader code.
   - `runs/<run_id>/events.jsonl` holds one record per line, in pipeline order:
     - `{"kind": "event", "envelope": {...}}`
     - `{"kind": "raw", "run": "...", "index": n, "sim_time_s": t, "raw": {...}}`
   - `runs/<run_id>/manifest.json` holds:
     - `run_id`, `created_utc`, `settings` (secrets redacted);
     - `git_sha`, `world_version`, `contracts_version`;
     - the scenario name and its sha256;
     - the plant, the step and the final KPIs (written at the end).
   - Flush once per tick. Write events only: **no image or audio bytes ever** (test this).
5. **`transports/websocket/dashboard.py`:** `DashboardServer` (FastAPI) on `dashboard_port` (8090).
   - `GET /ws/events` broadcasts the bus topic `events` to every client. Each client gets a bounded queue that drops its oldest messages, so a slow browser never blocks the simulation.
   - `GET /runs` and `GET /runs/{id}/events.jsonl` serve recorded runs.
   - `/` serves the built dashboard (`apps/dashboard/dist`) when it exists.
   - `GET /healthz`.
6. **`runner/report.py`:** `spotcool-world report <run_id>` writes `runs/<id>/report.md` plus PNGs:
   - power and cumulative energy for both runs;
   - zone temperatures against setpoints;
   - the occupancy timeline;
   - a KPI table;
   - the honesty note from the plan, §3.6.

## Tests
- Unit conversions and power totals on fixture raw dicts, built from the catalog snapshot names.
- The energy integral on a known profile.
- The comfort score on a synthetic two-zone case: people present vs. absent, inside and outside the band.
- The emitter maps every world `EventType`.
- The recorder round-trips, and its records can be replayed. The no-binary assertion.
- The WebSocket broadcast with FastAPI's `TestClient`, including a slow client dropping oldest messages.
- The report renders from a tiny recorded run.

## Done when
- The README checks are green.
- A dry pipeline (`TickSource` → fake plant → telemetry → KPI → emitter → recorder → bus publisher) writes a valid run that `report` turns into charts.
