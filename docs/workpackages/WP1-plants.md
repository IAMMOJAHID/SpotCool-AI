# WP1: Plant services (the building's physics)

**Tool:** Claude Code. **Branch:** `wp/wp1-plants`. **Depends on:** WP0.
**Directories:** `packages/world/src/spotcool_world/services/{boptest,rc,replay}/`, `processors/plant_processor.py`, `runner/` (the `probe` subcommand only), `tests/plants/`, `examples/foundational/05_boptest_step.py`.

## Goal
One `PlantService` interface with four interchangeable providers. The rest of the world never knows which one is running.

1. **`boptest/hosted.py`:** the public BOPTEST service.
2. **`boptest/local.py`:** the same client pointed at a local BOPTEST.
   - **Primary target:** Docker Desktop on the laptop, from the clone at `C:/Users/hussa/dev/boptest` (master `a29024f`). Use the compose services from its README.
   - **Fallback:** a GitHub Codespace, with its port forwarded by `gh codespace ports forward`.
3. **`rc/`:** a numpy 5-zone resistance-capacitance model, so tests and demos never depend on the network.
4. **`replay/`:** feeds back a run recorded by WP6, step by step.

## BOPTEST client (shared by hosted and local), with httpx in the `boptest` extra
**Call sequence:**
1. `POST testcases/{name}/select` returns the `testid`.
2. `PUT scenario/{id}` with `{"time_period": "peak_cool_day", "electricity_price": "constant"}` initializes the test and runs its warm-up.
3. `PUT step/{id}` with `{"step": 900}`.
4. `POST advance/{id}` once per step.
5. `GET kpi/{id}` for the KPIs.
6. `PUT stop/{id}` at the end.

Responses are wrapped as `{status, message, payload}`.

**Rules:**
- **Only send `Content-Type: application/json` when there is a body.** The hosted service returns HTTP 500 for an empty JSON body.
- **Inputs latch.** An override stays active until `<x>_activate = 0` is sent. An empty advance body changes nothing: on Nov 1 a core override sent from 10:00 to 11:00 was still applied at midnight. Each `advance` therefore sends, for **every** input the world manages:
  - `<x>_u` and `<x>_activate = 1` when overridden;
  - `<x>_activate = 0` when released.

  Be explicit on every step, and never rely on what an earlier call left behind. Reject unknown names before sending: BOPTEST answers 400 "Unexpected input variable".
- **Overwritten signals are read by their `_u` key.** The REST API drops each override block's `_y` output. `/advance` and `/results` report the value actually applied under the input's `_u` name: the baseline value when released, ours when active. The point map's `read` bindings already use these keys.
- **`PUT stop` returns HTTP 200 with a non-JSON body.** Don't parse it. Afterwards the id answers 400 "Invalid testid".
- **Timeouts** (measured Nov 1 through the Codespace port forward: select about 8 s; scenario 283 s; advance p50 0.24 s, p95 1.0 s, max 5.6 s):
  - select: up to 600 s (the hosted queue can be slow), retried with exponential backoff and jitter;
  - scenario: 900 s;
  - advance: 120 s.
- **Step size:** BOPTEST's default is 3600 s, so always `PUT step` 900 explicitly.
- **Keep-alive:** a test is stopped after 15 idle minutes. If a run pauses, send a no-op request before that.
- **Always `PUT stop`**: in `stop()`, in `finally:`, and on SIGINT. This is a shared, free service.
- **Metrics:** emit `MetricsFrame`s for call latency and BOPTEST's own time ratio.

## RC plant
- **The contract to honour:** it returns the **same raw output names** as BOPTEST for every point the BMS map reads (see `packages/contracts/.../points/office_bms.yaml`): zone temperatures in K, the applied setpoints under their `_u` keys, CO₂, `V_flow`, all 7 power meters, `weaSta_reaWeaTDryBul_y` and `weaSta_reaWeaRelHum_y`.
- **Zones:** 1R1C or 2R1C per zone.
  - Areas follow the DOE medium-office floor: core about 983 m², north and south about 207 m² each, east and west about 131 m² each. Scale them to the total of 1,662.66 m².
  - Internal gains come from the **scenario's** people count (about 120 W sensible per person) plus equipment.
  - Outdoor temperature follows a peak-cooling-day sine (Chicago, 22–33 °C).
- **HVAC:** a VAV cooling-capacity limit. Chiller electric power is cooling ÷ COP 3.5. Fan power is cubic in airflow.
- **Baseline schedule:** cooling setpoint 24 °C occupied (06:00–19:00, measured on BOPTEST) and 30 °C otherwise; heating 21 °C and 15.6 °C.

  Document this in the module docstring. It is the RC stand-in for BOPTEST's controller, and the `_u` setpoint keys report it.
- **Calibration note** (docstring): the RC plant is deliberately simple. It exists for speed and for the honesty bracket, where its "people heat" matches the cameras. It does not replace BOPTEST.

## PlantProcessor (`processors/plant_processor.py`)
- Constructor: `PlantProcessor(service, run: Literal["spotcool","baseline"])`.
- On `StartFrame`: `service.start(...)`.
- On `SetpointCommandFrame` for its own run: store the overrides for the next step.
- On `TickFrame`:
  1. `raw = await service.step(overrides)` (the baseline run always steps with an empty overrides map);
  2. push `PlantMeasurementsFrame(run, raw)` downstream, then the tick.
- On `EndFrame` or `CancelFrame`: `kpis()`, push a `KpiFrame` (BOPTEST KPIs; `provisional=False`), then `stop()`.
- Failures become `push_error(...)`. A BOPTEST failure mid-run is fatal.

## CLI: `spotcool-world probe [--plant boptest-hosted|boptest-local] [--steps 4]`
1. Select the test, set the scenario, print the catalogs' sizes.
2. Step N times, writing one override into the core zone (28 °C).
3. Print latency per call and the KPIs.
4. Always stop.

This is the go/no-go check the plan calls for.

## Tests
- **Contract suite** (`tests/plants/test_plant_contract.py`): one parametrized suite, run on RC always and on BOPTEST with `@pytest.mark.live`. It checks that:
  - every point-map `read` is in the output;
  - while a 28 °C core override is active, the core setpoint's `_u` key reports it (301.15 K);
  - the core temperature with the override is at least the baseline minus 0.05 K. On the RC plant it must rise strictly. On BOPTEST, don't assert a rise: the core VAV box sits near minimum airflow in the morning, and a 4-step override at 10:00 moved the core temperature by 0.00 K;
  - after `_activate = 0`, the core setpoint's `_u` key returns to the schedule (297.15 K in occupied hours);
  - `kpis()` has `ener_tot`;
  - `stop()` is idempotent.
- **BOPTEST client:** unit tests with `httpx.MockTransport`, covering the header rule, explicit `_activate`, retry and stop-on-error.
- **Replay:** reproduces the recorded raw values exactly. This depends on WP6's JSONL format; agree it with WP6 through the lead.

## Done when
- The checks in the WP README are green.
- `uv run pytest -m live` passes against local Docker BOPTEST, or Codespaces, once it is up.
- `05_boptest_step.py` prints one step for each plant.
