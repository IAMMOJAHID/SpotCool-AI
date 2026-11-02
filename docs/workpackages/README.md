# Person B work packages

Person B's half of SpotCool is the **world**: the simulated building that the agent connects to, and the demo video. It's split into work packages (WPs) so that many AI agents can build it at the same time without stepping on each other.

- **One WP = one agent = one git worktree = one branch** (`wp/<id>-<slug>`).
- The lead (Person B with the main Claude Code session) owns `packages/contracts/`, integration and merges.

## Branches

| Branch | Role | Who writes it |
|---|---|---|
| `main` | Release. Shared by Person A and Person B. | Merged from `dev` at release, agreed by both |
| `dev` | Integration of the agent (A) and the world (B). Shared. | Merged from `world/main` and A's trunk at agreed integration points |
| **`world/main`** | **Person B's trunk**: every finished and verified world WP, merged in order | The lead only, by merge |
| `wp/<id>-<slug>` | One work package. Cut from the latest `world/main`. | One agent, in its own worktree |
| `docs/person-b-handoff` | The running status document `docs/status/HANDOFF.md` | The lead; merged into `world/main` at milestones |

**The flow for each WP:**
1. **Cut** `wp/<id>-<slug>` from the current `world/main`, so the WP builds on every WP finished before it. That is the sequential stack.
2. **The agent builds it.** The lead re-runs the checks (the four in the definition of done, plus the live suite where it applies), and a separate reviewer agent reviews it. Fixes go in as their own commit.
3. **The lead pushes the WP branch and waits for its World CI run to be green** (`gh run list -R IAMMOJAHID/SpotCool-AI --branch wp/<id>-<slug>`). GitHub's Ubuntu runner is the second platform: the Windows laptop never takes POSIX-only paths. For example, a Linux-only SIGINT bug made CI fail from WP0 to WP2 unnoticed.
   - **Merge only on green**, with `git merge --no-ff` into `world/main`, so the WP stays one revertible unit (`git revert -m 1 <merge>`).
   - Then re-run the checks on the merged trunk, push `world/main`, and confirm its CI run is green too.
4. **The handoff** branch is merged into `world/main` at the same milestone.

**No pull requests between B's own branches.** Merges are local. A PR triggers the Claude review workflows, which spend `ANTHROPIC_API_KEY` credits. Every push still runs the free world CI. The PR from `world/main` into `dev` is the integration point, scheduled with Person A.
- Every agent follows [`packages/world/AGENTS.md`](../../packages/world/AGENTS.md).

## Dependency graph

```
WP0 core + contracts (lands first)
 ├─► WP1 plants ─────────────┐
 ├─► WP2 BMS (BACnet) ───────┤
 ├─► WP3 VRF gateway ────────┤
 ├─► WP4 cameras ◄── WP8 ────┤
 ├─► WP5 scenarios ──────────┼─► WP11 assembly (spotcool-world run) ─► WP10 evals + release
 ├─► WP6 telemetry/recorder ─┤                                    └─► WP9 video
 └─► WP7 dashboard (mock feed first) ┘
WP8 footage: no code dependency, starts immediately (people + scripts)
```

| WP | Brief | Tool | Branch |
|---|---|---|---|
| WP0 | core framework + contracts (done) | Claude Code (lead) | `wp/wp0-core-contracts` |
| WP1 | [plants](WP1-plants.md) | Claude Code | `wp/wp1-plants` |
| WP2 | [BMS over BACnet](WP2-bms-bacnet.md) | Claude Code | `wp/wp2-bms-bacnet` |
| WP3 | [VRF gateway](WP3-vrf-gateway.md) | Claude Code | `wp/wp3-vrf-gateway` |
| WP4 | [cameras](WP4-cameras.md) | Claude Code | `wp/wp4-cameras` |
| WP5 | [scenarios, calendar, occupants](WP5-scenarios.md) | Claude Code | `wp/wp5-scenarios` |
| WP6 | [telemetry, KPIs, recorder, dashboard feed](WP6-telemetry.md) | Claude Code | `wp/wp6-telemetry` |
| WP7 | [dashboard app](WP7-dashboard.md) | Antigravity | `wp/wp7-dashboard` |
| WP8 | [footage pipeline](WP8-footage.md) | Claude Code + people | `wp/wp8-footage` |
| WP9 | [video production](WP9-video.md) | Antigravity + people | `wp/wp9-video` |
| WP10 | [evals, CI, docs, release](WP10-evals-release.md) | Claude Code | `wp/wp10-evals-release` |
| WP11 | [world assembly and CLI](WP11-assembly.md) | Claude Code (lead) | `wp/wp11-assembly` |
| WP6.1 | [step-mean power](WP6.1-step-mean-power.md) | Claude Code | `wp/wp61-step-mean-power` |

## Starting a WP (copy-paste)

Worktrees live **outside OneDrive**, so syncing doesn't lock files mid-build. Each worktree gets its own virtualenv; uv hardlinks packages from its cache, so extra venvs cost little disk.

Cut every WP from the latest `world/main` (see "Branches").

```bash
WP=wp2-bms-bacnet                               # change per WP
git -C "C:/Users/hussa/dev/SpotCool-AI" worktree add \
    "C:/Users/hussa/dev/wt/$WP" -b "wp/$WP" world/main   # local trunk: no upstream tracking
# when it's done: git -C "C:/Users/hussa/dev/wt/$WP" push -u origin "wp/$WP"
cd "C:/Users/hussa/dev/wt/$WP"
export UV_PROJECT_ENVIRONMENT="C:/Users/hussa/dev/venvs/$WP"
uv sync --all-packages              # add --extra <name> for the extras your WP needs
```

Then give the agent its brief:
- **Claude Code:** "Read `docs/workpackages/WPn-*.md` and `packages/world/AGENTS.md`, then implement the brief. Don't commit until all checks pass. Don't push."
- **Antigravity:** open the worktree folder and paste the same instruction.

## Rules for every WP

1. **Stay in your lane.** Only touch the directories your brief lists. A change to `packages/contracts/` goes to the lead as a request, never as an edit.
2. **Contract first.** Code against `spotcool_contracts` and the WP0 base classes. Mock the other WPs at their interfaces, not inside them.
3. **Definition of done:**
   - green `uv run ruff check packages`, `uv run ruff format --check packages`, `uv run pyright packages` and `uv run pytest packages`;
   - your example runs;
   - a changelog fragment in `packages/world/changelog/`;
   - the brief's own checks pass.
4. **Review:** a reviewer from the *other* tool reviews every WP before merge. Claude Code reviews Antigravity's work with `/code-review`; Antigravity reviews Claude Code's.
5. **Never store camera frames or audio on disk** (AGENTS.md Law 6). **Always stop BOPTEST tests** you start.
6. **Disk is tight.** Don't install heavy optional dependencies outside your WP's extras, and keep media out of the repo (`.gitignore` covers it).

## Facts every WP can rely on (verified Nov 1)

- **BOPTEST office test case** `multizone_office_simple_air` (BOPTEST master `a29024f`):
  - 5 zones (cor, nor, sou, eas, wes), 1,662.66 m², Chicago weather.
  - Scenario `peak_cool_day` is centred on day 199 (about 18 July).
  - The full input and output catalog is `packages/contracts/src/spotcool_contracts/data/boptest_multizone_office_simple_air.json`.
  - BOPTEST's ElectricPower KPI sums `chi_reaPChi_y`, `chi_reaPPumDis_y`, `heaPum_reaPHeaPum_y`, `heaPum_reaPPumDis_y`, `hvac_reaAhu_PFanSup_y`, `hvac_reaAhu_PPumCoo_y` and `hvac_reaAhu_PPumHea_y` (all in W).
- **Hosted BOPTEST** (`http://api.boptest.net`, plain HTTP: the HTTPS certificate expired 2026-10-26):
  - `/version` and `/testcases` answer.
  - `POST /testcases/<name>/select` queued past a 300 s timeout on Nov 1, and again past a 900 s timeout, so the hosted service can't be relied on.
  - Local Docker (`C:/Users/hussa/dev/boptest`, master branch) and GitHub Codespaces are the real paths.
- **The service rejects an empty body sent as `Content-Type: application/json`** (HTTP 500 "Unexpected end of JSON input"). Send the header only when there is a body.
- **Measured on our own stack** (GitHub Codespace, 2 cores, BOPTEST `1.0.0-dev`; Nov 1, 17:15 IST; probe: two tests in lockstep, 96 steps of 900 s on `peak_cool_day`):
  - **Concurrency:** one worker runs one test. With `docker compose up -d --no-recreate --scale worker=2 worker`, the SpotCool and baseline runs go side by side.
  - **Timings** (laptop → `gh codespace ports forward` → Codespace):
    - select: about 8 s;
    - `PUT /scenario` (initialization and warm-up): 283 s, both tests in parallel;
    - `/advance`: p50 0.24 s, p95 1.0 s, max 5.6 s, about 1,900 times faster than real time;
    - `/results`: 0.3 s.
  - **Step size:** the default is 3600 s; set 900 s explicitly.
  - **Scenario period:** `peak_cool_day` starts at `time` 16,588,800 s (day index 192, 00:00), 7 days before the peak day, and lasts 14 days.
  - **Baseline schedule** (all five zones): cooling setpoint 24 °C from 06:00 to 19:00, 30 °C otherwise. HVAC power peaks at about 18 kW (14:00) and is about 0 at night. The first day uses about 195 kWh (`ener_tot` 0.1166 kWh/m²).
  - **Overwritten signals are read by their `_u` key.**
    - `/advance` and `/results` don't contain the override blocks' `_y` outputs.
    - The value actually applied (baseline or override) comes back under the input's `_u` name.
    - The contracts use these keys from `d1a52f7` (branch `wp/wp0-contracts-rest-keys`).
  - **Inputs latch.** An empty `/advance` body keeps the last override active, so send `<x>_activate: 0` to release it.
  - **Values describe the step that just ended.** At a stamp, an applied setpoint (`_u`) reports the value that was in force during the preceding step, so schedule edges show one stamp late. The baseline core reads 30.0 °C at 06:00 and 24.0 °C from 06:15, then 24.0 °C at 19:00 and 30.0 °C from 19:15. HVAC power behaves the same: 10.5 kW at the 19:00 stamp, 0 from 19:15. Anything that needs the schedule for the coming step must use the occupied hours (06:00–19:00), not the last setpoint.
  - **`PUT /stop/{id}`** returns 200 with a non-JSON body. Afterwards `/status/{id}` answers 400 "Invalid testid".
  - **KPI keys:** `cost_tot`, `emis_tot`, `ener_tot`, `idis_tot`, `pdih_tot` (null), `pele_tot`, `pgas_tot` (null), `tdis_tot`, `time_rat`.
  - **Savings levers.** Every zone was treated as empty all day; the baseline uses 195.3 kWh for that day.
    - **Setpoint only** (28 °C in every zone): 138.3 kWh, **−29 %**.
      - The VAV boxes drop to minimum airflow: damper 0.174 in core, north and south and 0.15 in east and west. Flows are core 879 L/s, north and south 185, east 154, west 120.
      - So the zones float only to 24.7–26.1 °C.
      - The core zone alone barely responds, because it already runs near minimum airflow. A core override from 10:00 to 11:00 moved it 0.00 °C. Left on until midnight, it gave +0.2 °C and −1.3 % for the building.
    - **Plus airflow shut-off** (zone damper `hvac_oveZonAct<Z>_yDam_u = 0` while the zone is below 27.5 °C, released above): 37.3 kWh, **−81 %**.
      - Zones reach 28.1 °C.
      - Leakage at damper 0 is core 112 L/s and 17–24 L/s elsewhere.
      - This is ASHRAE 90.1-2019 §6.5.3.8 occupied-standby, which WP2's BMS implements.
    - **BOPTEST's `tdis_tot` and `idis_tot`** count discomfort on BOPTEST's own fixed occupancy schedule, so they penalize zones the scenario says are empty. Judge comfort with the world's comfort-where-people-were metric.
