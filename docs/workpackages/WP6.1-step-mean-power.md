# WP6.1: Step-mean power, so energy and savings are measured right

**Tool:** Claude Code. **Branch:** `wp/wp61-step-mean-power`, cut from `world/main`. The lead syncs the trunk in, including WP11, before the build starts.
**Depends on:** WP1 (plants), WP6 (telemetry, KPIs, report) and WP11 (the stub agent and the run that exposed the defect).

## Why this exists
WP11's full run on the RC plant showed the defect.
- **Telemetry's figure:** SpotCool saved **18.2 %**.
- **The plant's own 60-second integration:** **5.2 %** (134.6 against 142.0 kWh).
- **Cause:** telemetry integrates power sampled only at step ends, once every 15 minutes.
  - The BMS's occupied-standby closes and reopens dampers within a step. On reopening, the VAV jumps to high airflow, so power spikes inside the step, and end-of-step samples miss them.
  - The baseline's power is smooth, so only SpotCool's number is wrong.

A wrong savings figure is the worst defect the video could ship, so this fix comes before any number is quoted.

## Your lane (only these paths)
- `packages/world/src/spotcool_world/services/boptest/client.py` (add `results`) and `services/boptest/plant.py`.
- `services/rc/plant.py` and `services/replay/plant.py` (only if a replay needs a change).
- `processors/telemetry.py` and `processors/kpi.py`, plus `runner/report.py`.
- The matching tests under `packages/world/tests/` (`plants/`, `telemetry/`), and the relevant lines of `packages/world/AGENTS.md`.
- The changelog fragment `packages/world/changelog/+step-mean-power.fixed.md`.

`packages/contracts/` is read-only. If you think a contract must change, stop and report it.

## Decisions already made (binding)
1. **The seven ElectricPower meters report the mean over the step that just ended,** in W, under their usual BOPTEST names. Every other measurement stays the value at the end of the step. This is the "values describe the step that just ended" rule applied correctly to power. The initial state (index 0) has no step, so its meters stay instantaneous and are never integrated.
2. **BOPTEST plant.**
   - After each `/advance`, call BOPTEST's results endpoint for the seven meters over `[t − step, t]`. Average each one with the time-weighted trapezoid over the returned samples; BOPTEST stores results every 30 s (`testcase.py`, `ncp`). Replace the meters in the step's raw dict with these means.
   - Add `BoptestClient.results(testid, point_names, start_time, final_time)`. Read the exact REST shape in the local clone (`C:/Users/hussa/dev/boptest`: `testcase.py` `get_results` and the service's REST layer). Use the client's existing retries and timeouts.
   - If the results call fails, fall back to the instantaneous values, log a warning, and mark it with a `MetricsFrame` or a count. Never fail the run for it.
   - The cost is one extra REST call per step per run, which is fine at 15 s or more of wall time per step.
3. **RC plant.** Accumulate each meter's energy over the 60-second substeps, and report each meter's step mean (energy / step_s).
4. **Telemetry.**
   - `energy_kwh` is the sum of (step-mean total power × `step_s`) from step 1 on: a rectangle per step, not a trapezoid over end-of-step samples.
   - `power_kw` and `total_power_kw` show the step means.
5. **Cross-check at the end of the run.**
   - Compare each run's integrated `energy_kwh` with the plant's own KPI: `ener_tot` (kWh/m²) × the floor area, 1662.66 m², from `PlantService.kpis()` / the merged final KPIs.
   - If they differ by more than 2 %, log a warning. Show both numbers, and their difference, in the report's KPI table. The manifest records them too, if that fits naturally.
6. **The savings figure the video may quote** is the cross-checked one. The report says which number it uses.

## Tests
- **A fake plant whose power spikes within a step:** the step mean, the integrated energy, and an end-of-step sample that would have been wrong.
- **RC:** the meter means equal the substep energy / step_s. The cross-check holds within 2 % on a full `hybrid_tuesday` run with the stub agent.
- **BOPTEST client `results`** against `httpx.MockTransport`: the request shape, the time-weighted mean, and the fallback on failure.
- **Telemetry:** the rectangle integration from step 1, and the initial state never integrated.
- **Report:** the cross-check columns, and the warning above 2 %.

## Done when
- **The four checks are green** with all extras installed:
  - `uv run ruff check packages`
  - `uv run ruff format --check packages`
  - `uv run pyright packages`
  - `uv run pytest packages`
- `spotcool-world run --plant rc --speed 0 --stub-agent` reports a saving that matches the RC plant's own `ener_tot` ratio within 2 %. Put both numbers in your report.
- The changelog fragment is written. The lead runs the live BOPTEST check at the BOPTEST checkpoint.
