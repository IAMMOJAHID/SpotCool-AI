# Person B handoff: state of the world build

**Last updated:** 2026-11-01, about 21:45 IST, by the lead Claude Code session. **Day 2's work started early, on the evening of Nov 1: WP5 is being built.** State:
- **`world/main` (`a24ec06`) holds WP0, WP8, WP1 and WP2**, every merge with green World CI. The trunk is green on Windows and Linux (§2).
- **WP5 is in progress** on `wp/wp5-scenarios`. The lead's kickoff commit `6235d68` holds the binding decisions, and one build agent is implementing it (§8).
- **The BOPTEST Codespace is stopped** and stays off until a WP needs it (WP5 doesn't). Restart it with the steps in §8.
- **Person A's integration handoff** is at `docs/integration/PERSON_A_HANDOFF.md`, and the user is sending it to A.
**Audience:** the next Claude Code session (or person) continuing Person B's work. Read this first; it replaces the session history.

---

## 0. Read first

- **Who:** the user is **Person B** on a 2-person team in the Meta AI Developer Hackathon.
  - **Person B** builds the simulated building ("world") and the demo video.
  - **Person A** builds the agent and owns the Meta credits.
  - **Submit by Nov 4, 21:00 IST.** The portal closes Nov 5, 07:30 IST, which is Nov 4, 21:00 ET.
- **Approved plan:** `C:\Users\hussa\.claude\plans\pasted-content-id-a80f-role-you-recursive-church.md` ("Person B Plan"). Don't re-plan; execute it.
- **Work-package briefs:** `docs/workpackages/` (index in `README.md`). Each WP brief is self-contained.
- **Shared contracts with A:** `packages/contracts/` (v0.1, read its `README.md`). **Core framework:** `packages/world/` (read its `AGENTS.md`).
- **Working rules from the user** (also saved as Claude memory notes):
  1. **Claude Code for everything.** Flag anywhere Antigravity, a browser or a person is really needed.
  2. **One subagent at a time, sequentially**, because of usage limits. The user has several accounts and switches when a limit hits. If an agent dies with HTTP 429 "spend limit", resume it with `SendMessage` after the switch; its work is saved.
  3. **Every piece of work goes on its own new branch named after the work** (`wp/<id>-<slug>`).
     - **Person B's trunk is `world/main`** (user, Nov 1: "for person B we will have a world main branch… after each worker finishes the work, merge in main of person B, and stack the sequential work").
     - **The flow:** cut each WP from the latest local `world/main`, verify it and have it reviewed, push it, then `git merge --no-ff` it into `world/main`, re-run the checks on the trunk, and push. The roles of `main`, `dev`, `world/main`, `wp/*` and the handoff branch are in `docs/workpackages/README.md` → "Branches".
     - **Merges are local; there are no PRs between B's branches.** A PR runs the Claude review workflows, which spend `ANTHROPIC_API_KEY` credits.
     - **Where things are checked out:** the main checkout `C:\Users\hussa\dev\SpotCool-AI` is on `world/main`. This handoff is edited in the worktree `C:\Users\hussa\dev\wt\docs-person-b-handoff`.
  4. **Engineering decisions are the lead's call** (user, Nov 1: "it's engineering decision, and you are leading the engineering part"). That covers architecture, and branch integration such as fast-forwarding or merging between B's WP branches. Push `wp/*` branches after their checks pass.
     - **Still ask the user** for destructive or irreversible actions (force-push, deleting branches or data) and for anything outside engineering (spend, people, external communication).
     - Coordinate merges into the shared `main`/`dev` with Person A through the user.
  5. **Zero spend** for B: free, open-source and free-tier tools only.
  6. Never store camera frames or audio. Always stop BOPTEST tests you start.
- **The repo moved** out of OneDrive to **`C:\Users\hussa\dev\SpotCool-AI`**.
  - The old copy at `C:\Users\hussa\OneDrive\Desktop\Spot-cool-ai\SpotCool-AI` is **stale** (behind by at least `e82e7c3`). Don't use it.
  - Checked on Nov 1 at 21:07 IST: it holds nothing that isn't in dev or on GitHub, so the user can delete it.
  - The lead session moved to dev with `/cd` (it's now session `441270a4…`). The day-1 transcripts and the scratchpad archive (BOPTEST probe scripts and raw results) are listed in the memory note `day1-session-archive`.

## 1. Environment (this laptop)

| Item | Value |
|---|---|
| OS / shell | Windows 11, Git Bash (also PowerShell) |
| Python | uv-managed **3.11.11**; uv **0.6.5**; system Python 3.13 is not used |
| Virtualenv | `export UV_PROJECT_ENVIRONMENT="C:/Users/hussa/dev/venvs/spotcool-ai"` before **every** `uv` command (one venv per worktree: `C:/Users/hussa/dev/venvs/<wp>`) |
| Node / npm | 24.4.1 / 11.6.2 |
| Media | ffmpeg 8.0.1 (gyan full build), OBS Studio (`C:\Program Files\obs-studio`) |
| Other CLIs | AWS CLI 2.36, gh 2.92 (accounts: `mhussam-ai`, active and with the **codespace** scope; `hussam-zeng`), winget |
| Docker | Desktop 29.8.2. C: now has about 41 GB free, so a local BOPTEST stack is possible, but it isn't built yet because the Codespace works (§3) |
| Git remote | `origin = git@github.com-iammojahid:IAMMOJAHID/SpotCool-AI.git`, identity "Mojahid Hussain" (local config; don't change it) |
| Folders outside the repo | `C:\Users\hussa\dev\boptest` (BOPTEST clone, 813 MB); `dev\footage\{raw,clips,audio,datasets}`; `dev\venvs\`; `dev\wt\` (worktrees) |

**Checks every WP must pass**, run from the repo root with the env var set:
- `uv run ruff check packages`
- `uv run ruff format --check packages`
- `uv run pyright packages`
- `uv run pytest packages`

Pytest uses `--import-mode=importlib` and deselects `live` tests by default.

## 2. Status board

| WP | Status | Branch / location | Notes |
|---|---|---|---|
| **Trunk** | **`world/main` `f4c147e`, pushed** | Holds:<br>- WP0 (with the contracts fixes);<br>- WP8 (merge `b71ff99`);<br>- the branching docs (`6289312`);<br>- WP1 (`c6f7204`);<br>- the POSIX SIGINT fix (`fbe5cdd`);<br>- the CI-gate docs (`ce3e749`);<br>- the WP2 brief decisions (`c9386aa`);<br>- the Person A handoff (`351e189`);<br>- **WP2 (`f4c147e`)**;<br>- this handoff at each milestone. | Trunk checks with `--all-extras`: ruff and format (`packages` + `scripts`), pyright, 403 package tests and 45 script tests, all green. **World CI on GitHub is green** since `fbe5cdd`, after it had failed on every push from WP0 on with a Linux-only SIGINT bug. It contains all of shared `main` and `dev` (0 behind). |
| **WP0** contracts + core | **Done, pushed** | `wp/wp0-core-contracts`, commits `bac5163`, `d670e67`, `e8cc7a8`, `e82e7c3` | Contracts v0.1 (19 tests). World core: 132 tests in total; ruff and pyright clean; examples 01–04 run. Reviewed by the lead. |
| **WP8** footage | **Done, pushed** | `wp/wp8-footage` commit `28f2788` (worktree `C:\Users\hussa\dev\wt\wp8-footage`) | 45 tests; ruff clean. Shot list (`docs/footage/shot_list.md`, 13 clips) and consent form are print-ready for filming on Nov 2 morning. **Decisions adopted** (WP5 must use them): file names use zone ids (`sou_meeting.mp4`, not `south_meeting`); the extra clip `wes_empty`; people counts `cor_many` 4, `cor_few` 1, `nor_few` 2, `sou_meeting`/`sou_complaint` 5, `wes_few` 1. **The shoot needs 4–5 people** (roles A–E); with fewer, film the real count and update WP5's numbers. Follow-ups: WP10 adds `scripts` to pytest `testpaths`. 4TU was unreachable, so WiseNET's CC0 and download URL are unconfirmed. MEVA indoor keys are listed in `scripts/footage/meva_fetch.py`. |
| **WP0 fix** contracts REST keys + standby | **Done, pushed** | `wp/wp0-contracts-rest-keys` (worktree `C:\Users\hussa\dev\wt\wp0-contracts-rest-keys`). Commits: `d1a52f7` (16 BOPTEST read bindings `_y` → `_u`, test and docstring), `c0ca27e` (measured facts and WP1 brief), `5a4aa23` (90.1 occupied-standby airflow shut-off: OCC-SENSOR text and WP1/WP2 briefs) | 132 tests; ruff and pyright clean. A's BACnet interface is unchanged (objects, names, units). Also `f2d849b` (the recipe base). **`wp/wp0-core-contracts` was fast-forwarded to `f2d849b` and pushed (lead's decision, Nov 1)**, so both branches are identical, and Person A's branch has the fixes. |
| **WP1 plants** | **Done, pushed, merged into `world/main` (`c6f7204`)** | `wp/wp1-plants`:<br>- `4b1905f` (build);<br>- `f7e953a` (review fixes);<br>- `714e5e2` (brief amendments);<br>- on top of the cherry-picked fixes `a4e4bb2 694b0b5 91de2bc`.<br><br>Worktree `C:\Users\hussa\dev\wt\wp1-plants`. | Lead re-ran everything after the fixes:<br>- ruff, format and pyright clean;<br>- 191 tests (with `-W error::RuntimeWarning`, and in the CI-equivalent `ci-check` venv);<br>- live suite 5 passed in 119 s.<br><br>The reviewer agent's 1 medium and 10 low findings are fixed, each with a regression test. Decisions:<br>- measurements and raw records are stamped with the end of the step;<br>- start latencies are logged.<br><br>Lead's changes:<br>- RC heating now 20/12 °C, as BOPTEST;<br>- CI installs `--all-extras` (otherwise `tests/plants` and pyright fail on GitHub).<br><br>Key API: `create_plant(settings, run)`, `PlantProcessor(service, run, warmup_s=3 days)`, `BoptestClient`, `RcPlant(occupancy)`, `ReplayPlant(path, run)`, `serializers.jsonl` (`RunWriter`, `read_records`), `services.boptest.catalog` (`MANAGED_INPUTS`, `zone_damper`, `bms_signal`), and `spotcool-world probe`.<br><br>**Known gap (follow-up WP1.1):** on an all-empty day the RC plant shows −32 to −38 % for the damper lever vs BOPTEST's −81 %, because BOPTEST's heavy mass absorbs a day of gains. A slow mass node per zone would close it. |
| **WP2 BMS (BACnet)** | **Done, pushed, merged into `world/main` (`f4c147e`)** | `wp/wp2-bms-bacnet`:<br>- `734a6c9` (build);<br>- `38a6447` (review fixes);<br>- `8a68f0f` (trunk synced in).<br><br>Worktree `C:\Users\hussa\dev\wt\wp2-bms-bacnet`. | `SpotCool-Office-BMS` (1001, UDP 47808) from the point map, with priority arrays, COV and the command policy. Plus:<br>- **occupied-standby** with airflow shut-off. The hysteresis sits **above** the setpoint; the reviewer found the brief's rule worked only once (H1). It is guarded by the occupied hours 06:00–19:00, because BOPTEST reports values one stamp late (M1);<br>- the heartbeat watchdog;<br>- fault flags until data;<br>- plants publish their initial state, and ParallelPipeline keeps the `StartFrame` first.<br><br>Lead verified: 403 tests (17 bacnet), the CI-equivalent run, example 06, and green CI (run `41319431833`).<br><br>Measured on RC, standby alone with South empty: damper shut on 55 % of standby ticks, airflow −30 %.<br><br>**Not verified:** YABE (manual; steps in `packages/world/AGENTS.md`), LAN discovery from another PC, BOPTEST with the BMS. |
| WP5 scenarios | **In progress** | `wp/wp5-scenarios` (worktree `C:\Users\hussa\dev\wt\wp5-scenarios`) | Kickoff `6235d68` rewrote the brief with binding decisions:<br>- `day` is BOPTEST's 0-based index;<br>- times sit on the 15-minute grid;<br>- calendar dates are 2026-01-01 plus the sim seconds;<br>- bookings have `booked_at`;<br>- the audio rides on `ComplaintCueFrame`;<br>- `footage_dir` and the `calendar`/`occupants` extras;<br>- the calendar is a router only.<br><br>The demo timeline moved onto the grid: the door is open 10:00–10:45 and the complaint is at 14:15. |
| WP4 cameras | Queued | `wp/wp4-cameras` | Same as WP5 |
| WP6 telemetry | Queued | `wp/wp6-telemetry` | |
| WP11 assembly | Queued | `wp/wp11-assembly` | |
| WP3 VRF | Queued | `wp/wp3-vrf-gateway` | Uses WP2's `priority.py` |
| WP7 dashboard | Queued | `wp/wp7-dashboard` | Playwright uses the installed Edge (`channel: "msedge"`) |
| WP10 evals + release | Queued | `wp/wp10-evals-release` | |
| WP9 video | Queued | `wp/wp9-video` | HyperFrames, OBS websocket and Shotcut. People: narration, review |

**Order (sequential):** ~~WP8~~ → **WP1** → WP2 → WP5 → WP4 → WP6 → WP11 → WP3 → WP7 → WP10 → WP9.

**BOPTEST Codespace build: resolved (Nov 1, 17:09 IST).**
- **History:** the first build of the `worker` image failed at its Miniconda/pip step. The cause was `ReadTimeoutError`s from PyPI inside the Codespace, not the conda terms of service. The second `docker compose up -d web worker provision` succeeded (worker image 5.02 GB).
- **Fix if it recurs** (inside the Codespace only, not upstream): in `service/worker/Dockerfile`, make the pip step `python -m pip install --default-timeout=120 --retries 10 -r /boptest/production.txt`, then rebuild.

**How to start a WP:** follow the recipe in `docs/workpackages/README.md` ("Starting a WP"), then hand the agent its brief.

**Schedule checkpoints (IST):**

| When | Checkpoint |
|---|---|
| Nov 2, 18:00 | `spotcool-world run hybrid_tuesday --plant rc` end to end with the stub agent |
| Nov 2, 22:00 | The same on BOPTEST |
| Nov 3 | Integrate with A's agent; 21:00 first automated take |
| Nov 4 | Final takes, QA, submit by 21:00 |

## 3. BOPTEST: what works and what doesn't

**Hosted `api.boptest.net`: unusable today.**
- Its HTTPS certificate expired on 2026-10-26; plain HTTP answers `/version` and `/testcases`.
- `POST /testcases/multizone_office_simple_air/select` **timed out at 300 s and again at 900 s** on Nov 1, because the service has no worker capacity.
- **Bug to remember:** the service returns HTTP 500 "Unexpected end of JSON input" if `Content-Type: application/json` is sent with an empty body. Send the header only with a body.

**GitHub Codespace: built, 2 workers. Stopped on the evening of Nov 1 (restart: §8 item 1).**
- Name **`spotcool-boptest-qxxjjj94x672rp`**: repo `ibpsa/project1-boptest`, branch `master`, 2-core `basicLinux32gb` (7.8 GB RAM), idle timeout 90 minutes, paid from `mhussam-ai`'s free quota (120 core-hours and 15 GB-month per month; blocked at the limit, never billed).
- **Images are built.** worker 5.02 GB, web 1.17 GB, provision 682 MB. All 12 test cases are provisioned, and `/version` is `1.0.0-dev`.
- **Concurrency:** one worker runs one test. The second worker was added with `docker compose up -d --no-recreate --scale worker=2 worker`. Check with:
  - `gh codespace list`
  - `gh codespace ssh -c spotcool-boptest-qxxjjj94x672rp -- 'cd /workspaces/project1-boptest && docker compose ps'`
- **After a Codespace stop and start**, bring the stack back with `docker compose up -d --scale worker=2 web worker provision`. It reuses the built images.
- **To use it from the laptop:** `gh codespace ports forward 8000:8000 -c spotcool-boptest-qxxjjj94x672rp` (keep it running; at handover it ran as background job `bcpdg0vce` of the lead session). BOPTEST is then at **`http://127.0.0.1:8000`**. In the world: `plant: boptest-local`, `boptest_local_url: http://127.0.0.1:8000`. For live tests: `SPOTCOOL_LIVE_BOPTEST_URL=http://127.0.0.1:8000`.
- **Save quota:** `gh codespace stop -c <name>` when idle; delete it after the hackathon.

**Local Docker:** the clone at `C:\Users\hussa\dev\boptest` is on master `a29024f` (2026-10-26). That commit fixes MinIO; release v0.9.0 pulls MinIO images that no longer exist.
- Compose services: `web` (port 8000), `worker`, `redis`, `minio` (9000/9001), `mc`, `provision`.
- Start with `docker compose up -d --scale worker=2 web worker provision`.
- C: has about 41 GB free, so this is now possible. It isn't built yet, because the Codespace works. It's the planned primary plant for the recording runs (no tunnel latency).

**Live probes (Nov 1, 17:15–17:40 IST).** Scripts and outputs are in the session scratchpad: `probe_local.py` and `probe_levers.py`, outputs in `boptest_probe_local/` and `boptest_probe_levers/`. They're temporary; their jobs move into `spotcool-world probe` (WP1) and WP10's evals. The measured facts are recorded permanently in `docs/workpackages/README.md` → "Facts" (on `wp/wp0-contracts-rest-keys`). The headlines:
- **Speed:**
  - select about 8 s;
  - `PUT /scenario` warm-up 283 s;
  - `/advance` p50 0.24 s, p95 1.0 s, max 5.6 s, about 1,900 times real time;
  - the default step is 3600 s, so set 900 explicitly.
- **Scenario period:** `peak_cool_day` starts at day index 192, 00:00 (`time` 16,588,800 s), 7 days before the peak day, and lasts 14 days.
- **Baseline schedule:** cooling setpoint 24 °C from 06:00 to 19:00 and 30 °C otherwise. The first day uses about 195 kWh (`ener_tot` 0.1166 kWh/m²), with a peak of about 18 kW at 14:00.
- **`_u` read keys:** the override blocks' `_y` outputs aren't in `/advance` or `/results`. The applied value comes back as the input's `_u` key. The contracts are fixed (`d1a52f7`).
- **Inputs latch.** An empty `/advance` body keeps the last override active; send `_activate: 0` to release. (The earlier note "defaults to 0 on every advance" was wrong.)
- **`PUT /stop`** returns 200 with a non-JSON body.
- **Savings levers**, with every zone empty all day:
  - setpoint 28 °C alone: **−29 %**. The VAV boxes sit at minimum airflow, and the zones float only to about 26 °C.
  - plus the zone dampers closed (`hvac_oveZonAct<Z>_yDam_u = 0`) while below 27.5 °C: **−81 %**.
  - The core zone alone barely responds to a setpoint (0.00 °C after a 1-hour override at 10:00).
  - → Decision in §5.

**Test-case facts**, verified from the clone's `wrapped.fmu` (`multizone_office_simple_air`). The full catalog snapshot is `packages/contracts/src/spotcool_contracts/data/boptest_multizone_office_simple_air.json`.
- **Building:** 5 zones (`cor, nor, sou, eas, wes`), 1,662.66 m², Chicago weather. Default step 3600 s; warm-up 0.
- **Scenario days:** the peak days are `peak_cool_day` 199, `typical_cool_day` 144, `peak_heat_day` 26, `typical_heat_day` 18 and `mix_day` 84. Each scenario's 14-day period starts 7 days earlier (measured for `peak_cool_day`: day index 192).
- **29 inputs.** The world manages ten of them:
  - the cooling setpoints `hvac_oveZonSup{Cor,Nor,Sou,Eas,Wes}_TZonCooSet_u` (K, written from the BMS priority arrays);
  - the zone dampers `hvac_oveZonAct{Z}_yDam_u` (0–1, closed by the BMS's occupied-standby);
  - each with `_activate`.

  The others (heating setpoints, AHU, reheat) are not used.
- **Override semantics:** inputs **latch**. Every step, send `_activate` (1 or 0) for every managed input, and `_u` for the active ones. Unknown input names return 400.
- **Outputs used:**
  - zone temperature `hvac_reaZon{Z}_TZon_y` (K);
  - applied setpoints under the `_u` keys `hvac_oveZonSup{Z}_TZon{Coo,Hea}Set_u` (K);
  - CO₂ `hvac_reaZon{Z}_CO2Zon_y` (ppm);
  - airflow `hvac_reaZon{Z}_V_flow_y` (m³/s);
  - weather `weaSta_reaWeaTDryBul_y` (K) and `weaSta_reaWeaRelHum_y` (fraction);
  - AHU `hvac_reaAhu_TSup_y` and `hvac_oveAhu_yFan_u` (the applied fan speed).
- **BOPTEST's ElectricPower KPI** sums 7 meters (W): `chi_reaPChi_y`, `chi_reaPPumDis_y`, `heaPum_reaPHeaPum_y`, `heaPum_reaPPumDis_y`, `hvac_reaAhu_PFanSup_y`, `hvac_reaAhu_PPumCoo_y`, `hvac_reaAhu_PPumHea_y`.
- **BOPTEST's own BACnet bridge** (`bacnet/BopTestProxy.py`, legacy bacpypes, needs Python 3.11 or older) is a raw mapping:
  - device 599;
  - analog-output 1 = `advance`;
  - inputs as analog outputs and outputs as analog inputs, in K.
  - It's a reference only. **Our** BMS (WP2) is the realistic one.
- **Pinned by the live probes** (see above): the baseline occupied cooling setpoint is 24 °C (06:00–19:00); the step latency is measured; two tests run at once with 2 workers. All of this is recorded in `docs/workpackages/README.md` → "Facts" and in the WP1 brief.

## 4. What the contracts say (v0.1, `packages/contracts`)

- **`office_bms.yaml`:** device `SpotCool-Office-BMS`, instance **1001**, UDP **47808**, **51 objects**.
  - **Per zone** (i = 1–5 for cor, nor, sou, eas, wes):
    - AI 100+i `ZN-<Z>-T` (°C);
    - AV 200+i `ZN-<Z>-CLG-SP` (**commandable**, 16–30 °C on a 0.5 grid; relinquish default comes from the baseline run's `hvac_oveZonSup<Z>_TZonCooSet_u`);
    - AV 250+i `ZN-<Z>-HTG-SP` (read-only);
    - AI 300+i `ZN-<Z>-CO2`;
    - AI 350+i `ZN-<Z>-SA-FLOW` (L/s);
    - MSV 400+i `ZN-<Z>-OCC-MODE` (Occupied / Standby / Unoccupied, set by the BMS);
    - BV 450+i `ZN-<Z>-OCC-SENSOR` (commandable; relinquish default Occupied, i.e. fail to comfort). It drives the BMS's occupied-standby (ASHRAE 90.1-2019 §6.5.3.8, G36): 5 minutes after the zone empties during scheduled occupancy, the cooling setpoint goes up 0.5 K and the heating setpoint back 0.5 K, and **the zone airflow is shut off** while the zone temperature is between them (`5a4aa23`).
  - **Building:**
    - AI 501–507: the 7 power meters (kW);
    - AI 510 `HVAC-TOTAL-PWR`, AI 511 `HVAC-ENERGY` (kWh);
    - AI 520 `OA-T`, AI 521 `OA-RH`, AI 530 `AHU-SAT`, AI 531 `AHU-SF-SPD`;
    - BI 601 `WES-ENTRANCE-DOOR` (scenario-driven);
    - BV 901 `SPOTCOOL-HEARTBEAT` (the watchdog releases priority 10 after 120 s without a toggle);
    - AV 950 `SIM-CLOCK`.
- **`vrf_gateway.yaml`:** `VRF-Gateway-MeetingRoom`, instance **2001**, UDP **47810**. Points: ON/OFF, SP, MODE, FAN, VANE-V, VANE-H (Pos1–5 plus Swing), ROOM-T, PWR, and both vane angles.
- **`vrf_profiles.yaml`:**
  - Mitsubishi: both axes; vertical angles 0/−15/−30/−45/−60, horizontal −40…40.
  - Daikin: vertical only.
  - LG: swing only.
- **Priorities:** operator 8, agent **10**, schedules 16; release with NULL. The agent never overwrites slots at 8 or more urgent.
- **Cameras:** `GET /cameras` (catalog) and `GET /cameras/{id}/snapshot.jpg`, port **8081**. Ids: `cam-cor`, `cam-nor`, `cam-sou`, `cam-eas`, `cam-wes`, plus the live phone `cam-meeting-live`.
- **Calendar:** Microsoft Graph `getSchedule` shape at `/calendar/rooms/{room}/schedule` and `.ics`; room `meeting-south`.
- **Occupants:** `POST {agent}/chat` (`ComplaintText`) and `POST {agent}/voice` (16 kHz mono s16le WAV).
- **Dashboard events:** envelope `{v, type, source, t_ms, sim_time_s, data}`. World payload models live in `events.py`; agent types are reserved (perception, decision, explanation, chat). Ground-truth fields (`people`, camera scenes) must not be read by the agent.
- **World ports:** BMS 47808, VRF 47810, HTTP (cameras and calendar) 8081, dashboard 8090, agent 8002 (A).

## 5. Key decisions (why things are the way they are)

- **Product:** SpotCool is a **supervisory agent that plugs into a building's existing CCTV and BMS** (BACnet), with no new hardware. It cools occupied zones, relaxes empty ones, takes complaints, and explains itself.
  - **Main demo:** a 5-zone office.
  - **Part 2:** louver aiming on a VRF unit.
- **B's world = a stand-in with the same interfaces as a real building.** A's code must not change when it's swapped for real hardware.
  - **Physics:** BOPTEST twice (SpotCool and baseline).
  - **BMS:** our own BACpypes3 device with real priority arrays.
  - **Cameras:** catalog plus snapshots.
  - **Calendar, occupants, telemetry and recorder/replay.**
- **Architecture:** pipecat patterns (frames, processors, pipelines, workers, bus, observers, services and transports with providers, extras). One deliberate deviation: **direct-await lockstep push** instead of processor queues, so runs are deterministic.
- **Fairness:** SpotCool uses the baseline's own occupied setpoint where people are, so savings come only from empty zones.
- **Occupied-standby with airflow shut-off is the main lever** (decided Nov 1, 17:45, from the lever probe in §3).
  - **What the BMS does:** WP2's BMS implements ASHRAE 90.1-2019 §6.5.3.8. When SpotCool's camera counts mark a zone `OCC-SENSOR` = Unoccupied, the BMS closes that zone's damper (BOPTEST `hvac_oveZonAct<Z>_yDam_u = 0`) while the temperature is inside its setpoints, with hysteresis.
  - **The baseline** is the same BMS without occupancy sensors, so it never enters standby.
  - **The story:** code-compliant BMSs already support standby, but zone occupancy sensors are rarely installed because of cost (PNNL 2023). SpotCool turns existing CCTV into those sensors, with no new hardware and no direct actuator control by the agent.
  - **For Person A:** writing `OCC-SENSOR` = Unoccupied for empty zones is now SpotCool's biggest lever. Raising their setpoints on top of that widens the band, which is the measured −81 % case. Pre-cooling from the calendar covers recovery before meetings.
  - **No interface change for A.** `OCC-SENSOR` was already in contracts v0.1; only its description became exact.
- **Honesty note** (README and video): BOPTEST's occupant heat follows its fixed schedule, so savings in zones the cameras show empty can be overstated. The answers:
  - the **comfort-where-people-were** metric;
  - the **all_hands_day** scenario (savings about 0);
  - the RC plant as a bracket.
- **Footage:**
  - own recordings (shot list from WP8);
  - **WiseNET** (CC0; 6 indoor cameras on one floor, plus BIM);
  - **MEVA** (CC BY 4.0);
  - Pexels for b-roll only.
  - Not allowed: EPFL lab, CITIC-MTMC, PIROPO.
  - WiseNET is a single 10.7 GB zip, so extract it in a Codespace.
- **Video:** under 3:00, uploaded unlisted to YouTube.
  - **Motion graphics:** HyperFrames (Apache-2.0; skills installed).
  - **Takes:** OBS with an obs-websocket script replaying a recorded run.
  - **Captions:** an SRT generated from the narration text.
  - **Music:** Pixabay or the YouTube Audio Library.
  - **Edit:** Shotcut.

## 6. Free tools and licences

| Use | Tool | Licence or limit |
|---|---|---|
| BOPTEST | — | BSD-3 |
| BACnet | BACpypes3, BAC0, YABE (BACnet explorer) | MIT, LGPL-3, MIT |
| Web and charts | FastAPI, OpenCV, MediaMTX, React + Vite, Chart.js | MIT, Apache-2.0, MIT, MIT, MIT |
| Recording | OBS | GPL-2 |
| Motion graphics | HyperFrames | Apache-2.0, Node 22+ |
| Narration and captions | Kokoro-82M TTS fallback; faster-whisper or whisper.cpp | Apache-2.0; MIT |
| Editing | Shotcut, Kdenlive; Clipchamp free | GPL; 1080p, no watermark |
| CI | GitHub Actions | Free for public repos |
| Cloud | Codespaces | 120 core-hours and 15 GB per month free |

**Avoid:**
- CapCut (licence terms);
- Coqui XTTS-v2 (non-commercial);
- Edge online TTS;
- FMA tracks marked NC, ND or SA;
- Hugging Face Docker/Gradio Spaces (now need PRO).

**Credits:** B could claim B's **own** $150 Meta Model API credit (a card pre-authorization is released; set a $150 spend cap). This is optional; the user said B spends nothing. Modal, Baseten and Fireworks credits are inference-only and no use to B.

**Submission rules** (from a community wiki, so unverified; **confirm on the form**):
- a 3-minute video;
- a public open-source repo;
- all contributors listed;
- only work built during the event;
- third-party tools and licences listed in the README.

## 7. Numbers safe to put in the video (with sources)

1. **People counting vs. presence sensing: 17.8% vs 5.9%** of office energy ([PNNL-22072](https://www.pnnl.gov/main/publications/external/technical_reports/pnnl-22072.pdf)). Say "up to": the savings are mostly heating, and Miami came out at 5%.
2. **Raising the cooling setpoint from 22.2 to 25 °C cut HVAC energy 27%** (29% of cooling) with no loss of comfort ([CBE, Hoyt et al. 2015](https://escholarship.org/uc/item/13s1q2xc)).
3. **About 6% of AC electricity saved per 1 °C** ([BEE / Ministry of Power](https://www.cseindia.org/a-step-in-the-right-direction-says-cse-of-power-ministry-s-move-to-fix-starting-temperature-of-room-air-conditioners-at-24oc-and-not-lower-to-save-energy-8814)). The 24 °C default has been mandatory on star-labelled room ACs since 2020.
4. **HVAC is about 55% of a typical Indian commercial building's energy** ([BEE](https://rehva.eu/fileadmin/user_upload/REHVA-ISHRAE_EC_BEE_seminar_S_Kumar_Deepa_.pdf)).
5. **US offices are at 56.1% of pre-pandemic attendance, 66.1% on peak Tuesdays** ([Kastle, week of Oct 21, 2026](https://www.kastle.com/safety-wellness/getting-america-back-to-work/)).

**Supporting figures:**
- **Why occupancy sensors are rare:** occupancy-based control often isn't cost-effective because sensors cost too much ([PNNL 2023](https://www.pnnl.gov/publications/adopting-occupancy-based-hvac-controls-commercial-building-energy-codes-analysis-cost)). That's SpotCool's case for reusing CCTV.
- **Camera counting accuracy:** about 7% error per person ([BU, Konrad 2024](https://sites.bu.edu/mcl/files/2026/02/konrad2024-OA.pdf)).
- **Vendor claims (label them as such):** BrainBox AI (Trane) up to 25% HVAC cost; Honeywell Forge 10%; JCI OpenBlue up to 30%.
- **Standards:** ASHRAE 90.1-2019 §6.5.3.8 occupied-standby (also in G36).
  - **The airflow clause** is verified from an [ASHRAE addendum](https://www.ashrae.org/file%20library/technical%20resources/standards%20and%20guidelines/standards%20addenda/90_1_2019_bm_20220121.pdf): "all airflow supplied to the zone shall be shut off whenever the space temperature is between the active heating and cooling set points". It applies to zones whose rooms need occupancy-sensed lighting, where ASHRAE 62.1 allows zero ventilation in standby.
  - **The 5-minute timing and the 1 °F (0.5 K) setback** come from a [secondary summary](https://www.lightnowblog.com/2026/06/lighting-systems-save-more-energy-through-hvac-integration/). The matching cooling set-up isn't verified, so check the standard text before quoting it.
- **Our own simulation numbers** (BOPTEST `multizone_office_simple_air`, peak cooling day, every zone empty all day, so upper bounds): setpoint-only −29 %, with standby airflow shut-off −81 % of HVAC energy. Quote them as "in simulation", and quote the `hybrid_tuesday` result (WP6/WP10) as the realistic number.
- **BACnet priority conventions:** 8 for operators, 14–16 for schedules ([Haystack](https://project-haystack.org/doc/lib-phIoT/writable-point)).
- **Meta:** SAM 3.1 API $2.50 per 1,000 images; `muse-spark-1.3` $1.25/$4.25 per million tokens ([dev.meta.ai](https://dev.meta.ai/docs/models.md)). The hackathon themes are "Build for Your World" and "Push the Frontier", which names energy ([event page](https://dev.meta.ai/events/global-hackathon)).

## 8. Next actions, in order

1. **Bring BOPTEST back when a WP needs it.** Lead's call: WP5 doesn't need it, so it stays off to save quota. The next users are WP11's BOPTEST checkpoint and WP10's live evals. The Codespace was stopped on the evening of Nov 1, and its images and state are kept:
   - `gh codespace ssh -c spotcool-boptest-qxxjjj94x672rp -- 'cd /workspaces/project1-boptest && docker compose up -d --scale worker=2 web worker provision && docker compose ps'`. This also starts the Codespace.
   - Then run `gh codespace ports forward 8000:8000 -c spotcool-boptest-qxxjjj94x672rp` as a background job, and check `curl http://127.0.0.1:8000/version`.
   - Only needed for live tests and BOPTEST runs. The RC plant needs nothing.
2. **WP5 (scenarios, calendar and occupants): in progress.** It was cut and kicked off on the evening of Nov 1, and its binding decisions are in the brief (`6235d68`). The inputs that fed them:
   - WP8's decisions: zone-id file names (`sou_meeting.mp4`); the `wes_empty` clip; the counts `cor_many` 4, `cor_few` 1, `nor_few` 2, `sou_meeting`/`sou_complaint` 5, `wes_few` 1. Use the real counts if Nov 2 filming has fewer people.
   - The `hybrid_tuesday` timeline in `docs/footage/shot_list.md`, which matches §12 of the Person A handoff.
   - The demo day is day 199 (`start_clock` 07:00).
   - Calendar models in `calendar.py`.
   - `OccupantWorker` posts per `occupants.py` (`/chat` JSON; `/voice` multipart with `audio` and `meta`).
   - The scenario's door state drives BI 601 through `ScenarioStateFrame`, which WP2 already reads.
3. **Then the queue, one agent at a time:** WP4 → WP6 → WP11 → WP3 → WP7 → WP10 → WP9.
   - Each WP goes through the same gates: lead checks, reviewer agent, fixes, **green CI on the branch**, merge, green trunk CI.
   - **WP6 kickoff notes:**
     - Raw records start with the initial state (index 0 at the start time), then step k at index k (start + k·step, stamped at the end of the step).
     - Comfort-where-people-were for the step ending at `t` must use the scenario state of the step that just ended, `state_at(t − step)`. A tick's `ScenarioStateFrame` describes the step it starts.
   - **WP4 kickoff notes:**
     - **Clip timing.** At speed 300, a 15-minute scene lasts 3 s of wall time, but `sou_complaint.mp4` shows its action between 5 and 15 s. Restarting the clip at the scene's start isn't enough. Choose between a slower pace around cues (WP11's pacer) and playing the scene's clip time-compressed.
     - **East clip.** `all_hands_day` needs an occupied East clip, but WP8 filmed only `eas_empty`. Use a dataset clip or a synthetic one.
     - **Settings.** `footage_dir` already arrives with WP5.
   - **WP11 wiring:** `TickSource → scenario → bms.input() → ParallelPipeline[spotcool ∥ baseline] → bms.output() → …`. Call `bms.start()` before the worker runs and `stop()` after. Pass `settings.bms_address` and `bms_port`.
   - **Follow-up WP1.1** (RC slow mass node) is queued after WP11.
   - **Schedule risk:** the Nov 2, 18:00 checkpoint (`hybrid_tuesday` on RC end to end) needs WP5, WP4, WP6 and WP11 in one day, alongside filming.
4. **Optional:** build the local Docker BOPTEST stack (`C:\Users\hussa\dev\boptest`, ≈ 7.5 GB of images) for lower-latency recording runs. Do it in a quiet moment, as it uses the laptop's network and CPU for a while.
5. **User actions pending:**
   - **Person A:** the user is sending `docs/integration/PERSON_A_HANDOFF.md` (on `world/main`). It folds in the earlier occupancy-sensor note and its addendum. Follow up on A's answers to its §16: policy, endpoints, dashboard event fields, extra read points, and the Nov 3 integration slot.
   - **YABE manual check** of the BMS (a person, on Windows): the steps are in `packages/world/AGENTS.md`. Run example 06, browse the 52 objects, then write at priority 8, at 10, and NULL.
   - reopen VS Code and Claude Code in `C:\Users\hussa\dev\SpotCool-AI`, then delete the OneDrive copy;
   - film per the shot list (Nov 2 morning);
   - confirm the submission form rules;
   - optionally claim B's own Meta credit;
   - Docker prune is no longer needed (41 GB free).

## 9. Things that are superseded

- **Old architecture.** Root `CLAUDE.md`, `AGENTS.md`, `.claude/rules/*` and `README.md` still describe the **old single-AC louver twin** (10 Hz loop, Llama, Silero, η_spot). They are stale for B's world. `packages/world/AGENTS.md` is authoritative for `packages/world/`. Rewriting the root docs belongs to WP10, coordinated with Person A.
- **Old review report.** `docs/designs/goldfish_review_report.md` reviewed an **earlier** plan and is obsolete.
- **Wrong BOPTEST notes from before the live probes:**
  - "`_activate` defaults to 0 on every advance" (inputs latch);
  - "read the effective setpoints as `_y`" (use the `_u` keys);
  - "G36 standby is just +0.5 °C" (90.1 standby also shuts the zone airflow off).

  All three are fixed on `wp/wp0-core-contracts` (fast-forwarded to `f2d849b`).
