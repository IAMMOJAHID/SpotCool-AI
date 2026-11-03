# Person B handoff: state of the world build

**Last updated:** 2026-11-03, about 17:15 IST, by the lead Claude Code session (session `441270a4-d753-4379-ba6d-7751619bd543`), written so that a `/compact` or a fresh session can resume safely.

**State in one breath:**
- **Every WP is merged into `world/main`,** WP9 (the video pipeline) last, and its CI is green (`86de315`, run `41628160225`; five jobs: check, evals, scenarios, dashboard, hygiene): WP0–WP11, WP1.2 and WP6.1.
- **The world runs end to end** with every service:
  - BOPTEST or RC, the BMS and the VRF, cameras, calendar, occupants, recorder, report, and the judges' dashboard;
  - BOPTEST runs survive a dropped tunnel;
  - golden evals guard it in CI.
- **The headline figure** is **19.94 % saved, SpotCool's own** (Nov 4, agent lane): SpotCool's agent with SAM 3.1 on CCTV clips, on a paced BOPTEST run whose report says `Quotable` (`runs/ag3-boptest-agent-s60`). The stub's 19.94 % stays as the perfect-perception upper bound; the agent matched it exactly. See `docs/release/numbers.md` and `docs/agent/HANDOFF.md`. **Never quote 21.14 %, 18.2 %, 27.1 %, 32.8 % or "up to 60 %".**
- **Nothing is building.** The video pipeline is merged, and its draft cut (176 s) builds from placeholders. It waits for the user's footage, takes and narration (§8).
- **Blocked on the user:**
  - **No footage is filmed yet:** `C:\Users\hussa\dev\footage` is empty;
  - **the leaked API keys** on Person A's branches must be rotated;
  - *(done Nov 4: the agent lane built and scored SpotCool's own agent; see `docs/agent/HANDOFF.md`)*

## Resume here (a fresh session or after `/compact`)
1. Read this file, then `packages/world/AGENTS.md`. Don't re-plan: the order and the gate flow are in §0 and §8.
2. **Check what's running:**
   - `git -C C:/Users/hussa/dev/SpotCool-AI worktree list` and `git status` in the main checkout;
   - `gh codespace list`: it should say `Shutdown`. If it's running and nothing needs it, stop it.
3. **The video** (commands in `video/README.md`):
   - when the user's material arrives (clips, takes, narration, music), rebuild with `assemble.py`, run `qa.py` with `SPOTCOOL_WORLD_RUN_DIR` set, and watch the cut;
   - a run with Person A's agent that the report calls `Quotable` changes the results card by itself: re-render `results_card`, then rebuild;
   - agent ids from an earlier session can't be messaged. For new work, start an agent from a brief and a build-prompt template (memory note `day2-session-archive`), then follow the gate flow (§0).
4. **The lead's re-checks:**
   - **the Python checks:** `export UV_PROJECT_ENVIRONMENT=C:/Users/hussa/dev/venvs/<wp>`, then `uv run ruff check packages scripts`, `uv run ruff format --check packages scripts`, `uv run pyright packages`, `uv run pytest -W error::RuntimeWarning`, `uv run pytest -m eval` and `uv lock --check`;
   - **the dashboard** (`apps/dashboard`): `npm ci`, `npm run gen:types`, `npm run build`, `npm run lint`, `npm test` and `npm run test:e2e`.
5. **Before any BOPTEST run:** `docs/runbooks/boptest-codespace.md`. Keep to one network, run the forward in a loop, and stop the Codespace afterwards.
6. **Agents run one at a time.** A reviewer counts as one.

**Audience:** the next Claude Code session (or person) continuing Person B's work. Read this first; it replaces the session history. `packages/world/AGENTS.md` is the authoritative engineering guide; this file is the status and the plan.

---

## 0. Read first

- **Who:** the user is **Person B** on a 2-person team in the Meta AI Developer Hackathon.
  - **Person B** builds the simulated building ("world") and the demo video.
  - **Person A** builds the agent and owns the Meta credits.
  - **Submit by Nov 4, 21:00 IST.** The portal closes Nov 5, 07:30 IST.
- **Approved plan:** `C:\Users\hussa\.claude\plans\pasted-content-id-a80f-role-you-recursive-church.md`. Don't re-plan; execute it.
- **Briefs:** `docs/workpackages/`, indexed in `README.md`. Every brief that has been started carries a lead "kickoff" commit with **binding decisions**.
- **Contracts shared with A:** `packages/contracts/` (now **v0.1.1**). **The world:** `packages/world/`; read its `AGENTS.md` first. It's aligned with pipecat's guide, the user's chosen model.
- **Working rules from the user** (also saved as memory notes):
  1. **Claude Code for everything.** Flag anywhere a browser or a person is really needed.
  2. **One subagent at a time**, because of usage limits.
     - The user switches between accounts when a limit hits.
     - An agent that dies with HTTP 429 ("spend limit") keeps its work. After the switch, resume it with `SendMessage` to its agent id, saying where it stopped. This worked many times on Nov 2.
  3. **Every piece of work gets its own branch**, `wp/<id>-<slug>`, cut from the latest local **`world/main`**, Person B's trunk.
     - Merges are local, with `git merge --no-ff`. There are no PRs between B's branches: a PR triggers paid Claude review workflows.
  4. **Engineering decisions are the lead's.**
     - Still ask the user before destructive or irreversible actions (force-push, deleting branches or data) and before anything outside engineering (spend, people, external communication).
     - Merges into the shared `main` and `dev` are coordinated with Person A through the user.
  5. **Zero spend:** free and open-source tools only.
  6. **Law 6:** never store or log camera frames or audio. Always stop BOPTEST tests you start.
- **The lead's per-WP gate flow** (practiced on WP5, WP4, WP6, WP11, WP6.1, WP7, WP1.2, WP3 and WP10):
  1. **Kickoff commit by the lead.** Rewrite the brief with binding decisions, and update any docs it touches, such as the Person A handoff. Stage the next WP's kickoff while the current WP builds.
  2. **Build agent** (`general-purpose`, run in the background). It gets the brief, AGENTS.md, a reading list, the definition of done and a report format. It commits, but doesn't push.
  3. **Lead re-runs the checks:** ruff, format, pyright, `pytest packages -W error::RuntimeWarning` and `uv lock --check`, plus the scope (lane and contracts).
  4. **Independent reviewer agent.** Tell it about issues already known, so it looks elsewhere. It verifies its findings by experiment and edits nothing. At the same time, push the branch for an early Linux CI run.
  5. **Fix commit by the builder,** resumed with `SendMessage`, carrying the findings and the lead's decided items.
  6. **Lead:** sync `world/main` into the branch, re-run the checks, and add a docs commit (the AGENTS.md status cells, plus brief or handoff notes).
  7. **Push, wait for green World CI on the branch, then merge.** Check that the merged tree is identical to the CI-verified branch (`git diff --quiet wp/<x> HEAD`), push, confirm the trunk's CI, and update this handoff.
- **Where things live:**
  - main checkout `C:\Users\hussa\dev\SpotCool-AI` (on `world/main`);
  - worktrees `C:\Users\hussa\dev\wt\<wp>`;
  - virtualenvs `C:\Users\hussa\dev\venvs\<wp>`;
  - this file is edited in the worktree `docs-person-b-handoff`, then merged into `world/main`;
  - **runs** are recorded in `C:\Users\hussa\dev\SpotCool-AI\runs\` (git-ignored). The key ones:
    - `eval-live-hybrid-20261103-122941`: BOPTEST, the final stub, 19.94 %, the results run;
    - `wp12-live-drop`: the recovery proven live;
    - `checkpoint-boptest-stub-s60-3`: the superseded 21.14 %;
    - the two failed checkpoints, kept as evidence;
  - **footage** lives in `C:\Users\hussa\dev\footage\` (`raw`, `clips`, `audio`, `datasets`), outside the repo. It's empty as of Nov 3, 13:00;
  - **this session's transcript and subagent logs** are under `C:\Users\hussa\.claude\projects\c--Users-hussa-dev-SpotCool-AI\441270a4-d753-4379-ba6d-7751619bd543*`.
- **The old OneDrive copy** of the repo is stale. It was verified on Nov 1 to hold nothing missing from dev or GitHub, so the user can delete it. The day-1 transcripts and scratchpad archive are listed in the memory note `day1-session-archive`.

## 1. Environment (this laptop)

| Item | Value |
|---|---|
| OS / shell | Windows 11, Git Bash (also PowerShell) |
| Python | uv-managed **3.11.11**; uv **0.6.5** |
| Virtualenv | `export UV_PROJECT_ENVIRONMENT="C:/Users/hussa/dev/venvs/<wp>"` before **every** `uv` command. The trunk venv is `spotcool-ai`. Sync with `uv sync --all-packages --all-extras`; CI uses `--frozen`. |
| Node / npm | 24.4.1 / 11.6.2. **Edge** is installed; Playwright must use `channel: "msedge"` and download no browsers. |
| Media | ffmpeg 8.0.1, OBS Studio (`C:\Program Files\obs-studio`) |
| CLIs | gh 2.92 (account `mhussam-ai`, with the codespace scope), AWS CLI, winget |
| Docker | Desktop 29.8.2, with about 41 GB free on C:. A local BOPTEST stack isn't built; the Codespace is used. |
| Git | `origin = git@github.com-iammojahid:IAMMOJAHID/SpotCool-AI.git`, identity "Mojahid Hussain" in the local config; don't change it. **World CI** runs on pushes that touch `packages/**`, `scripts/**`, `apps/dashboard/**`, `pyproject.toml`, `uv.lock`, the workflow, or any path its hygiene job refuses. |
| Outside the repo | `C:\Users\hussa\dev\boptest` (BOPTEST clone at `a29024f`, the REST reference); `dev\footage\{raw,clips,audio,datasets}`; `dev\spotcool-archive\` (day-1 probe evidence) |

**Checks every WP must pass**, from the repo root with the env var set:
- `uv run ruff check packages scripts`
- `uv run ruff format --check packages scripts`
- `uv run pyright packages`
- `uv run pytest -W error::RuntimeWarning`: `packages` and `scripts`
- `uv run pytest -m eval` (the golden evals, about 2 minutes)
- `uv lock --check`
- **The dashboard,** if touched, from `apps/dashboard`:
  - `npm ci`, `npm run gen:types` and `npm run build`;
  - `npm run lint`, `npm test` and `npm run test:e2e`.

Pytest deselects `live` and `eval` tests by default; BACnet tests run on loopback.

## 2. Status board

| WP | Status | Commits | What it is |
|---|---|---|---|
| **Trunk `world/main`** | Pushed, green CI | Last code merge: WP10 `ce23b61`, then the live-eval band (`c6dd2ac`); trunk CI `41595373093` on `c1ef1c7`, five jobs: check, evals, scenarios, dashboard, hygiene. | 973 package and script tests, 11 evals (plus live), 45 script tests; the dashboard's 80 Vitest and 23 Edge e2e tests run locally only |
| WP0 core + contracts | Merged | `bac5163`…`e82e7c3`; REST-key and standby fixes `d1a52f7`, `5a4aa23`, `f2d849b` | Frames, processors, pipelines, workers, bus, runner, clocks, observers, settings, the `run_test` harness; contracts (point maps, events, cameras, calendar, occupants) |
| WP8 footage | Merged | `28f2788`; `eas_few` added in `b6b64b4` | Shot list (**14 rows**: 13 zone clips plus the live phone), consent form, `normalize.py`, `extract_audio.py`, WiseNET and MEVA tools |
| WP1 plants | Merged `c6f7204` | `4b1905f`, `f7e953a` | BOPTEST hosted/local, RC (60 s substeps), replay; `PlantProcessor`; the JSONL records |
| WP2 BMS | Merged `f4c147e` | `734a6c9`, `38a6447` | SpotCool-Office-BMS (BACnet device 1001, UDP 47808): priority arrays, COV, occupied-standby (90.1 §6.5.3.8), heartbeat watchdog |
| WP5 scenarios | Merged `00d3d75` | `6235d68` kickoff, `9c59dac`, `fac49f2` | Scenario format, linter and compiler; 3 library days; `ScenarioProcessor`; `FramePublisher`; the calendar router; `OccupantWorker`; `spotcool-world lint` |
| WP4 cameras | Merged `d469ab5` | `e821ad1` kickoff, `b248dff`, `e041637` | `CameraSystem` (sources: own clip, then dataset, then synthetic); snapshot and MJPEG; `HttpServerWorker(app, host, port)`; the live phone proxy; `CameraSceneProcessor`; the privacy test |
| WP6 telemetry | Merged `3c0ca84` | `1959cd7` kickoff, `f702417`, `03f9684` (core cancel fix), `8a6c242` | Telemetry, KPIs (comfort where people were, energy saved), recorder (one event stream plus manifest), the dashboard app (`/ws/events` read-only, `/ws/agent` publish-only), `spotcool-world report` |
| WP11 assembly | Merged `56430cb` | `9fa5ad1` kickoff, `b7a321e`, `eceae05`, `8607ab6` (contracts 0.1.1) | `World` and `Replay`; `run` and `replay`; the **stub agent**; `RunStatusProcessor`; RC occupancy from the scenario |
| WP6.1 step-mean power | Merged `6159284` | `ac8008e` kickoff, `0b1153c` build, `43af1ed` review fixes, `2fab314` docs | Power meters as step means; the report's quote verdict `quote_blockers` (§7) |
| WP1.2 BOPTEST dropped connection | Merged `1ed3fcb` | `08e5e2e` kickoff, `f8eac86` build, `fca94ea` review fixes, `380f6aa` docs | Recover a lost step from BOPTEST's stored results; `stop()` rides out outages; failed runs say failed; the BMS reports an address change |
| WP7 dashboard | Merged `f184d79` | `269e026` kickoff, `b8662f8` build, `b316faa` review fixes, `0df42e0` docs | React app (operator and video modes); the world replays the run so far to each new viewer; camera CORS for the dashboard's own origin |
| WP3 VRF gateway | Merged `0a6509f` | `d794de5` kickoff, `819454b` build, `5a9386a` review fixes, `fd133c6` docs | The meeting-room VRF (device 2001, UDP 47810), in wall time beside the pipeline; brand profiles; `server.py` shared with the BMS |
| WP10 evals + release | Merged `ce23b61` | `c8e386c` kickoff, `61c449c`…`04ca7a5` build, `17e254e` review fixes, `b071cc3` docs | Stub back-off, golden evals, CI, runbooks, honesty and credits |
| WP9 video | Merged `86de315` | `5512cf2` kickoff, `b2b82bf` `7f6b2d3` `f408d86` build, `200bc28` `f07c157` review fixes, `1da5d0b` docs | A storyboard checked against `numbers.md`, HyperFrames motion, a results card generated from the run's verdict, scripted OBS takes, captions, an ffmpeg assembly, QA; a 176 s draft cut from placeholders |

**Every merged WP went through the whole flow:**
- an independent review whose findings were all fixed with tests:
  - WP5: 1 medium, 8 low;
  - WP4: 2 medium, 7 low;
  - WP6: 2 medium, 1 low, plus a core hang;
  - WP11: 2 high, 1 medium, 5 low;
  - WP6.1: 1 high, 2 medium, 3 low;
  - WP7: 1 high, 3 medium, 6 low;
  - WP1.2: 3 medium, 5 low;
  - WP3: 4 low;
  - WP10: 2 high, 3 medium, 3 low;
- green branch and trunk CI on Linux.

**Order (sequential):** ~~WP8~~ → ~~WP1~~ → ~~WP2~~ → ~~WP5~~ → ~~WP4~~ → ~~WP6~~ → ~~WP11~~ → ~~WP6.1~~ → ~~the BOPTEST checkpoint~~ → ~~WP7~~ → ~~WP1.2~~ → ~~WP3~~ → ~~WP10~~ → ~~WP9~~ → **the user's material and the final cut**.

**Why WP1.2 comes before WP3:** every run with Person A's agent crosses the Codespace tunnel, and on Nov 2 two of three checkpoint runs died when the laptop changed networks.

**Why WP7 comes before WP3:** the dashboard is the video's main visual and part of the plan's Nov 2 checkpoint. The VRF louvers are part 2 of the video.

**Schedule (IST):**

| When | Checkpoint | State |
|---|---|---|
| Nov 2, 18:00 | `hybrid_tuesday` on RC end to end with the stub agent, plus the dashboard live | **Done** (WP11), and the dashboard too (WP7, Nov 3). |
| Nov 2, 22:00 | The same on BOPTEST | **Done at 12:11** with the stub agent: `checkpoint-boptest-stub-s60-3` (§7) |
| Nov 3 | Integrate with A's agent; 21:00 first automated take | **Not yet** as of 17:00: WP9 is merged and the draft cut builds, but no footage is filmed and there's no run with A's agent. |
| Nov 4 | Final takes, QA, submit by 21:00 | |

## 3. BOPTEST: what works

- **Hosted `api.boptest.net` is unusable.** Its HTTPS certificate expired on 2026-10-26, and `select` queues past 900 s. It also answers HTTP 500 if `Content-Type: application/json` comes with an empty body.
- **GitHub Codespace `spotcool-boptest-qxxjjj94x672rp`** (repo `ibpsa/project1-boptest`, master; 2-core; 2 workers; images built).
  - It's **stopped**. To use it, follow `docs/runbooks/boptest-codespace.md`. Its quota is the free 120 core-hours a month; stop it when idle.
  - **Restart:** `gh codespace ssh -c spotcool-boptest-qxxjjj94x672rp -- 'cd /workspaces/project1-boptest && docker compose up -d --scale worker=2 web worker provision && docker compose ps'`
  - **Forward** (background job, in a restart loop): `while :; do gh codespace ports forward 8000:8000 -c spotcool-boptest-qxxjjj94x672rp; sleep 3; done`. Then `curl http://127.0.0.1:8000/version` should report `1.0.0-dev`.
  - **If compose says "Cannot connect to the Docker daemon"** right after boot, wait a few seconds and run it again.
  - **The PC stays on one network for the whole run.** A network change kills the tunnel, and the BMS keeps its old address.
  - **In the world:** `--plant boptest-local` with `boptest_local_url: http://127.0.0.1:8000`. For live tests: `SPOTCOOL_LIVE_BOPTEST_URL=http://127.0.0.1:8000`.
  - **If the worker build fails on PyPI timeouts:** in `service/worker/Dockerfile`, use `pip install --default-timeout=120 --retries 10`.
- **Measured facts** (Nov 1; details in `docs/workpackages/README.md` → "Facts"):
  - **Timings:** select about 8 s; warm-up 283 s; `/advance` p50 0.24 s; the default step is 3600 s, so set 900.
  - **Scenario period:** `peak_cool_day` starts at day index 192, and the peak day is index 199.
  - **Baseline schedule:** cooling setpoint 24 °C from 06:00 to 19:00, 30 °C otherwise.
  - **Read keys and latching:** overwritten signals read back under their `_u` keys, and inputs latch until `_activate: 0`.
  - **Stamp lag:** values describe the step that just ended, so schedule edges show one stamp late.
  - **The levers on an all-empty day:** setpoint 28 °C alone saves −29 %; adding a closed damper below 27.5 °C saves −81 %.
- **Results endpoint** (verified in the clone by WP6.1, then live on Nov 2–3 by the live tests, the checkpoint and the drop test):
  - **Request:** `PUT /results/{testid}` with the body `{"point_names": [...], "start_time": t0, "final_time": t1}`. The answer's payload holds each point's series plus `time`, one sample every 30 s (`ncp = step/30`).
  - **Energy KPI:** BOPTEST's `ener_tot` uses `np.trapezoid` over the same samples, from the initialization time.
- **Test case facts:**
  - **Building:** 5 zones, 1,662.66 m², Chicago weather.
  - **Power:** ElectricPower is the sum of 7 meters.
  - **Inputs:** the world manages 10 of them: the zone cooling setpoints and the zone dampers.

## 4. Contracts (v0.1.1, `packages/contracts`)

- **BMS `office_bms.yaml`:** device 1001, UDP 47808, 51 objects.
  - **Per zone** (i = 1–5 for cor, nor, sou, eas, wes):
    - AI 100+i temperature;
    - AV 200+i `CLG-SP` (commandable, 16–30 °C on a 0.5 grid);
    - AV 250+i heating setpoint;
    - AI 300+i CO₂;
    - AI 350+i airflow;
    - MSV 400+i `OCC-MODE`;
    - BV 450+i `OCC-SENSOR` (commandable; drives occupied-standby).
  - **Building:**
    - AI 501–507: the 7 meters;
    - AI 510 total power and 511 `HVAC-ENERGY`;
    - BI 601 West door;
    - BV 901 heartbeat (120 s watchdog);
    - AV 950 `SIM-CLOCK`, in seconds since 1 January.
- **Priorities:** operator 8, agent **10**, schedules 16. Release with NULL; slots 1–7 are refused.
- **VRF gateway `vrf_gateway.yaml`:** device 2001, UDP 47810, 10 points; the brand profiles are in `vrf_profiles.yaml` (built in WP3).
- **HTTP (port 8081):**
  - **Cameras:** `/cameras`, `/cameras/{id}/snapshot.jpg` and `/mjpeg`.
  - **Calendar:** `/calendar/rooms/meeting-south/schedule` (Graph `getSchedule`) and `.ics`. **Dates are 2026-01-01 00:00 plus `SIM-CLOCK` seconds**, labelled `timeZone: "UTC"`; day 199 is 2026-07-19.
- **Occupants:** `POST {agent}/chat` (`ComplaintText`) and `/voice` (16 kHz mono s16le WAV). The world waits 10 s and retries only connection errors and 5xx.
- **Dashboard (port 8090):**
  - `/ws/events`: read-only, every envelope `{v, type, source, t_ms, sim_time_s, data}`;
  - **`/ws/agent`**: publish-only for A's agent;
  - `/runs`.
- **Run status:** `starting`, `running`, `paused`, `finished`, **`cancelled`** (new in 0.1.1) or `failed`.

## 5. Key decisions (why things are the way they are)

**Product and architecture (day 1):**
- **The product:** SpotCool is a supervisory agent on a building's existing CCTV and BMS. B's world is a stand-in with the same interfaces, so A's code doesn't change on real hardware.
- **The architecture** follows pipecat, with one deliberate deviation: **direct-await lockstep** instead of processor queues, so runs are deterministic.
- **The main lever is occupied-standby with airflow shut-off.** Empty zones marked `OCC-SENSOR` = Unoccupied get their dampers closed by the BMS within a band. The baseline never enters standby. **Fairness:** occupied zones keep the baseline's own setpoint.

**Day 2 decisions** (Nov 1 evening to Nov 2). Details are in the briefs and AGENTS.md.
- **Time model (WP5).**
  - **`day` is BOPTEST's 0-based day index (199).** The old brief's `(day−1)` formula would have run the demo a day before the peak.
  - **All times sit on the 15-minute tick grid,** and points in time satisfy `start <= t < end`. **The demo timeline moved** to match: the door is open 10:00–10:45 and the complaint is at 14:15. Off-grid times like 14:20 would never be sampled.
- **Bus topics (WP5).** **`FramePublisher`** carries internal frames to bus workers on the `"world"` topic, while `BusPublisher` keeps dashboard envelopes on `"events"`. My original brief wrongly assumed `BusPublisher` carried frames; this follows pipecat's bus-bridge idea instead.
- **Cameras (WP4).**
  - **The scene follows sim time; frames follow wall time,** like a real camera, and are never time-compressed. **Film and integrate at speed 60 or slower,** so a clip's action (the complaint at about 10 s) plays.
  - **Sources fall back:** our own clip, then a dataset segment, then synthetic frames.
  - **An `eas_few` clip was added** to the shoot, so `all_hands_day` (the honesty check) shows people in East.
- **Dashboard sockets (WP6).** **`/ws/agent` is publish-only** and never sends world events. That keeps ground truth (the true head-counts) out of A's agent by construction. It's validated, rate-limited, Origin-checked, and refuses images.
- **Recording (WP6).** The recorder records the whole `"events"` stream, so Person A's explanations are in the recordings that replays (and video takes) use.
- **Core (WP6).** An upstream `CancelFrame` only *requests* a cancel. Before this, it could hang the world below a `ParallelPipeline`.
- **Stub agent (WP11).** It lets the world be run, scored and filmed without A.
  - **What it is:** a real BACnet client with openly perfect perception; it reads the script's ground truth.
  - **Its policy:**
    - empty zones get `OCC-SENSOR` Unoccupied plus `CLG-SP` 28 °C at priority 10;
    - occupied zones are released to the baseline;
    - booked rooms are pre-cooled from 30 minutes ahead;
    - everything is released at 19:00.
  - **Labelling:** recorded as `stub_agent: true` in the manifest, and labelled at the top of every report, so a stub result can never pass as A's.
- **Pre-cooling matters.** Without it, the South meeting room floated to 31 °C and never recovered during the meeting.
- **Energy measurement (WP6.1).**
  - **Energy is measured from step-mean power.** Sampling power at step ends missed the spikes from standby dampers reopening: telemetry claimed 27 % while the truth was 17 %.
  - The end-of-run cross-check against the plant's own `ener_tot` must agree within **0.1 %**.
  - The report's verdict, `quote_blockers`, decides what may be quoted.
- **Contracts 0.1.1:** a `cancelled` run status, because a user's stop is not a failure.
- **Queue order:** WP7 comes before WP3 (see §2).

**Day 3 decisions** (Nov 2 afternoon to Nov 3). Details are in the briefs, with the lead's amendments, and in AGENTS.md.
- **The dashboard (WP7).**
  - Power is drawn as steps over the step it describes (Chart.js `stepped: "after"`).
  - Video mode never shows BOPTEST's `tdis_tot` or `idis_tot`, and a stub run's KPIs say "upper bound".
  - URL targets must be local addresses.
  - Agent POSTs are preflighted, so no audio leaves without CORS.
  - The world replays the run so far to each new viewer.
- **BOPTEST resilience (WP1.2).**
  - A step whose answer was lost is read back from BOPTEST's stored results, never guessed.
  - An advance is resent only when BOPTEST certainly never got it.
  - Every step checks BOPTEST's own time.
  - The PC stays on one network.
- **The VRF (WP3)** runs in wall time beside the pipeline, not in it, so a write acts at once. The BACnet server code is shared with the BMS (`server.py`), which a byte-identical trace proved unchanged.
- **Evals (WP10).**
  - The stub backs off from an operator's hold, and commands at each step's close.
  - The world never waits for an agent.
  - Evals judge saved runs through `report.py`.
  - **The honesty check** is a control day with every zone occupied, which saves 0 %.
- **The video (WP9)** is built by code from a storyboard. The results card's label comes from the verdict, so a stub figure can't be called SpotCool's.

## 6. Free tools and licences

| Use | Tool | Licence |
|---|---|---|
| BOPTEST | — | BSD-3 |
| BACnet | BACpypes3 (pinned 0.0.110), YABE | MIT |
| Web | FastAPI, uvicorn, httpx, OpenCV, React + Vite, Chart.js | MIT / BSD / Apache-2.0 |
| Report charts | matplotlib (the `report` extra) | PSF-based |
| Recording | OBS | GPL-2 |
| Motion graphics | HyperFrames `0.8.6` | Apache-2.0 (checked by WP9) |
| Narration and captions | the user's own voice, with a Windows-speech placeholder until it's recorded; captions from `scripts/video/captions.py` | — |
| Editing | none: `scripts/video/assemble.py` builds the cut with ffmpeg (WP9) | LGPL/GPL tool, not shipped |
| CI and cloud | GitHub Actions; Codespaces (120 core-hours a month) | free |

**Avoid:** CapCut, Coqui XTTS-v2, Edge online TTS, NC/ND/SA music, and HF Docker Spaces.

**Credits:** B may optionally claim B's own $150 Meta credit; the user said B spends nothing.

**Submission rules** (unverified; **confirm on the form**): a 3-minute video, a public repo, contributors listed, built during the event, and third-party licences listed.

## 7. Numbers: what may and may not be quoted

**Published sources, safe to quote:**
1. **Counting vs. presence:** 17.8 % vs 5.9 % of *whole-building* energy in a simulated US large office, as a national average (not "up to", and not an HVAC share; most of it is heating) ([PNNL-22072](https://www.pnnl.gov/main/publications/external/technical_reports/pnnl-22072.pdf)).
2. **Setpoint 22.2 → 25 °C:** −27 % HVAC energy ([CBE 2015](https://escholarship.org/uc/item/13s1q2xc)).
3. **About 6 % of AC electricity per 1 °C** ([BEE](https://www.cseindia.org/a-step-in-the-right-direction-says-cse-of-power-ministry-s-move-to-fix-starting-temperature-of-room-air-conditioners-at-24oc-and-not-lower-to-save-energy-8814)).
4. **HVAC is about 55 % of Indian commercial energy** ([BEE](https://rehva.eu/fileadmin/user_upload/REHVA-ISHRAE_EC_BEE_seminar_S_Kumar_Deepa_.pdf)).
5. **US office attendance is 56.1 % of its February 2020 level, 66.1 % on the peak day** (Tuesday 20 October 2026). It's badge swipes against a baseline Kastle calls normal peak occupancy, so it's **not a share of desks**: never say "half-empty" or "every desk" ([Kastle](https://www.kastle.com/safety-wellness/getting-america-back-to-work/)).

Supporting: PNNL 2023 (why sensors are rare), BU 2024 (camera counting error of about 7 %), ASHRAE 90.1 §6.5.3.8 (the airflow clause is verified).

**Our own simulation numbers:**
- **BOPTEST, `hybrid_tuesday`, the final stub agent, speed 60, Nov 3 12:29: the live eval** (`runs/eval-live-hybrid-20261103-122941` in the main checkout; the verdict is blocked only by the stub agent):
  - SpotCool 230.4 kWh against the baseline's 287.8 kWh: **19.94 % saved**, by BOPTEST's own `ener_tot` and by the world's integration alike (difference 0.000 %, no fallbacks). Every one of the 30 stub writes was taken a step after its decision. **Quote it only as the policy's upper bound with perfect perception**, labelled so.
  - Comfort where people were: 2.13 against 0.33 K·h.
  - BOPTEST's own `tdis_tot` is 9.85 against 0.15 K·h per zone, and `idis_tot` 33.3 against 0 ppm·h per zone. BOPTEST's schedule counts every zone as occupied all day, so these include zones the scenario leaves empty. Explain them; never show them bare.
  - `cost_tot` and `emis_tot` fall by about 20 %. Peak demand (`pele_tot`) is unchanged.
  - The quotable list, with every source, is `docs/release/numbers.md`.
- **Superseded BOPTEST figure: 21.14 %** (the Nov 2 checkpoint `checkpoint-boptest-stub-s60-3` and `wp12-live-drop`). It came from the earlier stub, whose writes raced the step. Never quote it.
- **RC golden evals** (CI, speed 1800, final stub), judged by the evals:
  - control day, every zone occupied: 0.0000 %;
  - `all_hands_day`: 2.34 %;
  - `hybrid_tuesday`: 15.45 %, with comfort 4.29 K·h;
  - `empty_friday`: 37.44 %;
  - reproducible to 0.0000 pp.
- **BOPTEST, all zones empty all day** (upper bounds; say "in simulation"): setpoint-only −29 %, with standby airflow shut-off −81 %.
- **RC, `hybrid_tuesday`, the earlier stub:** 16.87 %. Superseded by the final stub's 15.45 % (the evals). RC figures are never quoted, because RC is our own model.

**The rules:**
- **Quote only a report whose verdict says "Quotable":** the plants' own saving, from a finished, paced BOPTEST run with no fallbacks, cross-checked within 0.1 %.
- **A stub-agent BOPTEST run** can be quoted only as the policy's **upper bound with perfect perception**, labelled so.
- **SpotCool's own figure needs Person A's agent** on a paced BOPTEST run.
- **Superseded figures, never quote:**
  - **21.14 %** (the earlier stub on BOPTEST, whose writes raced the step);
  - **16.87 %** (the earlier stub on RC);
  - **"up to 60 %"** (the shared README; no run supports it);
  - **18.2 %** (WP11's first stub run, which was sensor-only);
  - **27.1 %** (the sampling error);
  - **32.8 %** (WP6's example, with a stand-in agent and the sampling error).

## 8. Next actions, in order

1. **Done: WP6.1 merged** (`6159284`; branch CI `41398113867`, trunk CI `41398321768`). Review: approve after fixes (agent `a05b0b2b8cb4d5dcf`). Fix round `43af1ed` (agent `ae6ae39590ba8953d`): the plants' own saving is the one quoted, a 0.1 % cross-check, and the verdict `quote_blockers`. Lead docs `2fab314`.
2. **Done: the BOPTEST checkpoint.**
   - All 6 live tests passed, including `the_meters_add_up_to_the_plants_own_energy`.
   - The third paced run finished: §7.
   - **Runs 1 and 2 died when Windows moved the laptop to another saved Wi-Fi network** (192.168.234.x to 192.168.1.x). The tunnel dropped mid-`/advance`, the BMS kept its old address, and `stop()` couldn't reach BOPTEST. The lead stopped the 4 orphaned tests by hand.
   - **Lessons, now in WP1.2:**
     - the forward runs in a restart loop;
     - Docker in the Codespace may need a few seconds after boot before `docker compose up` works;
     - the PC stays on one network for a whole run.
3. **Done: WP7 merged** (`f184d79`; branch CI `41503652753`, trunk CI `41503993453`).
   - **Review** (agent `aea82254bdb2e3a6f`): approve after fixes, with 1 high, 3 medium and 6 low findings.
     - **The high was the lead's brief:** `stepped: "before"` drew every mean one step late. It's now `"after"`.
     - **The rest:** video-mode clipping, duplicate rows on reconnect, voice sent to an agent without CORS, any host accepted in the URL, weak screenshot tests, stale generated types, one bad payload blanking the page, the type sizes, and CPU at 5 Hz.
   - **Fix round** `b316faa` (builder `aabfde3ea4ebbc64b`), all fixed with tests. Its world side:
     - `EventFeed` replays the run so far to each new `/ws/events` client;
     - the camera server's CORS admits `http://<private IP>:<dashboard_port>`.
   - **Lead docs** `0df42e0`: the brief corrected and amended, and Person A's CORS headers and payload shapes.
   - **Use:**
     - `http://<world>:8090/?mode=video&agent=http://<agent>:8002`, in **one** tab: six MJPEG tiles use Edge's six connections per host;
     - `?cameras=host:port` when the camera server isn't on 8081;
     - `npm run mock` for the mock feed;
     - not checked yet: a real microphone (the tests use Edge's fake one), and the AC panel with real WP3 output.
4. **Done: WP1.2 merged** (`1ed3fcb`; branch CI `41533089785`, trunk CI `41533384879`).
   - **Review** (agent `ad3e3787a72818ed9`): approve after fixes. No way was found to guess a state, or to resend an advance BOPTEST got. **Fix round** `fca94ea` (builder `a75c42553e10fc1fc`):
     - every step checks BOPTEST's own time, so a double step fails loudly;
     - the final KPIs are retried for 60 s;
     - both plants stop concurrently;
     - the recovery's reads are capped at `RECOVERY_S`;
     - HTTP 429 is treated as transient;
     - the BMS address check is debounced and clears;
     - duplicate samples take the last.
   - **Live drop test, Nov 3 03:34** (`runs/wp12-live-drop`: BOPTEST, the stub agent, speed 0). The forward process was killed twice mid-run.
     - **Kill 1** hit an advance in flight on both plants (`ReadError`). Step 21 was read back from BOPTEST's stored results: the 124-name `/results` read works live.
     - **Kill 2** gave a `ConnectError` at step 47, and that step was recovered too.
     - **The run finished:** 2 recoveries per plant, recorded as non-fatal errors and the `plant_connection_recoveries` metric. Both tests were stopped.
     - **Its totals equal the clean paced checkpoint's to the decimal:** 227.0 against 287.8 kWh, 21.14 %, with a 0.000 % difference. So the recovered steps carried BOPTEST's exact values, and a stub run on BOPTEST reproduces at speed 0 too.
5. **Done: WP3 merged** (`0a6509f`; branch CI `41542464466`, trunk CI `41542790318`).
   - **Review** (agent `a8caef26345c2bd0e`): approve. The BMS answered a 60-operation BACnet session byte-identically before and after the move to `server.py`.
   - **Two low fixes** in `5a9386a` (builder `adb8aa9fe786129a4`):
     - the objects are attached before the socket binds;
     - a failing VRF tick is logged, and the worker carries on.
   - **Lead docs** `fd133c6`: Person A's §6 marked built, with:
     - write only on change, at most about once a second;
     - the general refusals come first;
     - `IDU-VANE-H` exists on every brand;
     - LG's release freezes the louver;
     - the start-up state;
     - `IDU-PWR` is illustrative.
   - **For WP9:**
     - film the louver segment live, with Mitsubishi;
     - start the world a few minutes early, so the room settles from 30 °C;
     - stop with Ctrl+C, since the recorder writes only on ticks.
6. **WP10 evals and release.** Branch `wp/wp10-evals-release`, worktree `C:\Users\hussa\dev\wt\wp10-evals-release`, venv `wp10-evals-release`.
   - **Done: merged** `ce23b61` (branch CI `41592899498`, trunk CI `41593242466`, five jobs each).
   - **Review** (agent `a494d1ebdaf5f2563`): approve after fixes. **Fix round** `17e254e` (builder `a8df75212933982d9`):
     - the take runbooks set `SPOTCOOL_WORLD_FOOTAGE_DIR` and check the clips;
     - the evals check every stub write's lag;
     - the live eval is paced at 60 and keeps its run;
     - the booking and 19:00 rules apply to the commanded step;
     - hygiene exempts `.env.example` and covers raw video.
   - **Live eval** run by the lead on Nov 3 (19.94 %, above). The band was re-centred in `fix/live-eval-band`.
   - **The kickoff's binding decisions** (`c8e386c`):
     - the stub backs off from a priority 1–9 hold, and says so;
     - golden evals on RC at speed 1800, judged from the saved run through `report.py`:
       - `hybrid_tuesday` within a measured band;
       - `all_hands_day` saving under 2 %;
       - `empty_friday` saving more than `hybrid_tuesday`;
       - reproducible within 0.5 pp;
     - a live variant that the lead runs;
     - CI jobs for the evals, scenario lint, the dashboard and a hygiene check;
     - runbooks in `docs/runbooks/`;
     - the honesty note covers `tdis_tot`/`idis_tot`;
     - `docs/release/numbers.md`;
     - `CREDITS.md` read from the lock files;
     - a security pass.
   - **Shared root files stay as they are.** The world's README section is drafted in `docs/release/` for the user to agree with Person A. Release merges into `dev`/`main` and the tag are the user's call with A.
7. **WP9 video: merged** (`86de315`). Branch `wp/wp9-video`, worktree `C:\Users\hussa\dev\wt\wp9-video`, venv `wp9-video`. Builder `ae720b5f7f358a6f3`, reviewer `a8809d6a146261338`. Claude Code built it, overriding the old Antigravity note.
   - **The brief's binding decisions:**
     - `video/storyboard.yaml` is the source of truth, checked by `storyboard_check.py` (under 2:58, no gaps or overlaps, at most 150 words a minute, every number sourced).
     - **Beats:**
       - the hook;
       - how it plugs in;
       - YABE with the priority-8 override and the stub backing off;
       - the hybrid Tuesday on the dashboard (a replay of the live eval run);
       - the louvers following a person (filmed live);
       - the BOPTEST results;
       - rollout and privacy;
       - the close.
     - **The results card is generated** from the saved run through `report.py`. Its label comes from `quote_blockers`, so a stub run says "Upper bound with perfect perception (stub agent, in simulation)".
     - **The narration** is `video/narration.md`. The user records it. A Windows-speech placeholder covers it until then.
     - **Motion graphics:** HyperFrames.
     - **Takes:** OBS takes scripted by `take.py`, an obs-websocket v5 client on `websockets`.
     - **Captions:** `captions.py`.
     - **Assembly:** `assemble.py`, with ffmpeg and loudnorm to −14 LUFS. A missing input becomes a labelled slate. There's no manual edit.
     - **QA:** `qa.py` plus `video/QA.md`.
   - **The review caught a wrong hook.**
     - Kastle's 56.1 % is badge swipes against February 2020, not a share of desks, and 66.1 % was one peak day.
     - The lead then checked PNNL as well: its 17.8 % against 5.9 % is a national average of whole-building energy, mostly heating.
     - `numbers.md` was corrected (`af2efc6`) and the hook rewritten to match. Never say "half-empty", "every desk", "on Tuesdays" or "up to 17.8 %".
   - **Other fixes:**
     - signed numbers and ranges are checked;
     - the card is checked against the never-quote list, its run's source and the results narration;
     - `voice.py` refuses ambiguous cuts;
     - a take's cue clock starts when OBS reports the recording started;
     - the replay plays 07:00 to 19:00 at speed 2700, in 16 s.
   - **Not yet tried against a real OBS.** The first take is also its test, so rehearse with `--dry-run`.
   - **Next:** the user's material goes in (clips, takes, narration, music), then a re-assembly and QA.
   - **Upload** (unlisted on YouTube) is the user's step. So is the submission form.
8. **User actions, in priority order** (deadline **Nov 4, 21:00 IST**):
   1. **URGENT: rotate the leaked Baseten and Meta API keys** with Person A (update 15 below). The repo is public.
   2. **Film the 14 shots** in `docs/footage/shot_list.md`, the critical path. Then:
      - `uv run python scripts/footage/normalize.py`;
      - `scripts/footage/extract_audio.py raw/south_complaint.m4a ...`;
      - `uv run spotcool-world lint --clips C:/Users/hussa/dev/footage/clips`.

      If fewer people were filmed, the lead updates the people counts in the scenario YAMLs.
   3. **Book a BOPTEST run with Person A's agent.** It's the only figure without the "upper bound" label. Use `docs/runbooks/boptest-codespace.md`, with A's agent on the BMS at UDP 47808 and the VRF at 47810.
   4. **Agree with Person A:**
      - drop the shared README's "up to 60 %";
      - agree `docs/release/README-world-section.md`;
      - CORS for the dashboard;
      - the agent's event fields;
      - the release merges into `dev` and `main`, and the tag.
   5. **For the video** (commands in `video/README.md`):
      - set up OBS once: import `video/obs/scene_collection.json`, enable the WebSocket server, and set `OBS_WS_PASSWORD`;
      - record the three takes with `scripts/video/take.py`: `yabe_override`, `dashboard_replay`, and `phone_vrf`, which is filmed live with Person A's agent (`docs/runbooks/video-takes.md`);
      - record the narration from `video/narration.md` at about 140 words a minute (the hook changed on Nov 3);
      - pick one YouTube Audio Library or Pixabay track, and log it in `CREDITS.md`;
      - confirm the closing slide's repo, `github.com/IAMMOJAHID/SpotCool-AI`;
      - **the louvers beat needs A's agent.** The lead proposes cutting that beat if A's agent can't drive the louvers by Nov 4, 12:00 IST (the user to confirm);
      - upload the final cut unlisted;
      - fill in the submission form, confirming its rules first.
   6. **Network:**
      - keep the laptop on one network for every run;
      - set the other saved Wi-Fi profiles to manual: `netsh wlan set profileparameter name="<profile>" connectionmode=manual`;
      - the phone must be on the same network.
   7. **Manual checks:**
      - YABE against the BMS and the VRF (`docs/runbooks/yabe.md`);
      - the phone camera (`docs/runbooks/phone-camera.md`);
      - the firewall (`docs/runbooks/windows-firewall.md`).
   8. **Accounts:** switch when a limit hits, then say "continue"; the lead resumes the stopped agent.

### Updates for Person A since the first handoff
Everything is in `docs/integration/PERSON_A_HANDOFF.md` on `world/main`. Send the user's message to A with items 15 (the keys) and 14 (the README) first.
1. **The timeline moved onto the 15-minute grid:** the West door is open 10:00–10:45, and the complaint is at **14:15**.
2. **Calendar dates** are 2026-01-01 00:00 plus `SIM-CLOCK` seconds, labelled `timeZone: "UTC"`. Day 199 is 2026-07-19.
3. **`/chat` and `/voice` must answer within 10 s:** accept first, then process. The world doesn't retry a request that timed out.
4. **Publish agent events on `ws://<world>:8090/ws/agent`**, which is publish-only.
   - Each envelope has `source: "agent"` and the type `perception`, `decision`, `explanation` or `chat`.
   - Each message is 64 KB at most, at no more than 20 per second.
   - **No images:** no `data:` URIs, and no string longer than 8192 characters.
   - The dashboard reads `/ws/events`.
5. **Allow CORS on `/chat` and `/voice`** from the dashboard's origin (`http://<world>:8090`, and `http://localhost:5173` in development).
6. **Contracts 0.1.1:** `run_status` can now be `cancelled`. It's additive; A doesn't consume it.
7. **After WP6.1:** the BMS power points are **step-mean** kW.
8. **Policy advice,** from our runs:
   - **pre-cool booked rooms** about 30 minutes ahead; without it, South hit 31 °C;
   - for empty zones, set `OCC-SENSOR` Unoccupied plus `CLG-SP` 28 °C;
   - release everything at 19:00.
9. **The VRF gateway's behaviour is settled** (§6), so A can build against it before WP3 lands:
   - refusals by brand (`write-access-denied`, `value-out-of-range`);
   - real time, whatever the simulation's speed;
   - the address: the BMS's IP on UDP 47810, which a Who-Is on 47808 won't reach;
   - its `vrf_state` and `bms_write` events.
10. **The handoff's status tables are current:** the BMS, cameras, complaints, `/ws/agent` and `run` are built.
11. **CORS for the dashboard** (§9): allow `POST` with the headers `content-type` and `x-spotcool-client` from the dashboard's origin. The dashboard sends `X-SpotCool-Client: dashboard`, so until A allows it, chat and voice are not sent.
12. **Confirm the agent's event fields** (§9). The dashboard reads the stub's shapes until A says otherwise:
    - `perception`: `people`, and optional `masks` in frame pixels;
    - `decision`: `zone`, `point`, `value`, `priority`, `reason`;
    - `explanation` and `chat`: `text`.
13. **The VRF gateway is built** (§6):
    - write a point only when it changes;
    - `IDU-VANE-H` exists on every brand but refuses values on Daikin and LG;
    - on LG, releasing Swing freezes the louver.
14. **The shared README claims "up to 60 %".** No run of ours supports it: the BOPTEST stub upper bound is 19.94 %. Agree a wording before submission, and agree `docs/release/README-world-section.md` for the world's part.
15. **URGENT: rotate the Baseten and Meta API keys.** A commit in the history of A's branches contains them, and the repo is public. The details went to the user directly, and aren't repeated here because this file is public too. Rotating is the fix. Purging the history, a force-push on A's branches, is A's call, after rotating.
16. **Book the louvers take with the integration run.** The video's louvers shot is filmed live: your agent aims the meeting room's VRF louvers at a person on the phone camera (`live`) while `scripts/video/take.py phone_vrf --agent http://<agent>:8002` records. Allow about 15 minutes after the paced BOPTEST run.
17. **Published figures:** use `docs/release/numbers.md`'s wording. Kastle's 56.1 % isn't a share of desks, and PNNL's 17.8 % is whole-building energy, on average.

## 9. Superseded (don't use)

- **The root docs** (`CLAUDE.md`, `AGENTS.md`, `.claude/rules/*`, README) describe the old single-AC louver twin. They're stale for B's world; `packages/world/AGENTS.md` is authoritative. They stay as they are: the world's README section is drafted in `docs/release/README-world-section.md` for the user to agree with A.
- **`docs/designs/goldfish_review_report.md`** reviewed an earlier plan.
- **Old BOPTEST notes:**
  - "`_activate` resets each step" (inputs latch);
  - "read setpoints as `_y`" (use `_u`);
  - "standby is only +0.5 °C" (it also shuts off airflow).
- **Old WP5 brief statements:**
  - `(day−1)*86400`;
  - "`BusPublisher` publishes frames";
  - the complaint at 14:20 and the door until 10:40;
  - a 14:45 South "meeting" entry.
- **Savings figures 18.2 %, 27.1 % and 32.8 %:** measurement or policy artefacts (§7).
