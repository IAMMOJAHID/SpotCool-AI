# Person B handoff: state of the world build

**Last updated:** 2026-11-02, about 11:00 IST, by the lead Claude Code session, after the context compaction. The WP6.1 reviewer is running; every worktree is committed and pushed.

**State in one breath:**
- **The world runs end to end.** `spotcool-world run --plant rc --speed 0 --stub-agent` plays the whole hybrid Tuesday in about 10 s, with every service live: BMS, plants, cameras, calendar, occupants, recorder and dashboard feed.
- **Merged into `world/main`:** WP0, WP1, WP2, WP4, WP5, WP6, WP8 and WP11.
- **WP6.1 (the energy-measurement fix) is built and pushed; its independent review is running.**
- **WP7 (dashboard) and WP3 (VRF gateway) are staged.**
- **Next:** §8 step 1.

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
- **The lead's per-WP gate flow** (practiced on WP5, WP4, WP6, WP11 and WP6.1):
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
  - this file is edited in the worktree `docs-person-b-handoff`, then merged into `world/main`.
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
| Git | `origin = git@github.com-iammojahid:IAMMOJAHID/SpotCool-AI.git`, identity "Mojahid Hussain" in the local config; don't change it. **World CI** runs on pushes that touch `packages/**`, `pyproject.toml`, `uv.lock` or the workflow. |
| Outside the repo | `C:\Users\hussa\dev\boptest` (BOPTEST clone at `a29024f`, the REST reference); `dev\footage\{raw,clips,audio,datasets}`; `dev\spotcool-archive\` (day-1 probe evidence) |

**Checks every WP must pass**, from the repo root with the env var set:
- `uv run ruff check packages`
- `uv run ruff format --check packages`
- `uv run pyright packages`
- `uv run pytest packages`, also with `-W error::RuntimeWarning`

Pytest deselects `live` tests by default; BACnet tests run on loopback.

## 2. Status board

| WP | Status | Commits | What it is |
|---|---|---|---|
| **Trunk `world/main`** | Pushed, green CI | Last code merge: WP11 `56430cb` (trunk CI `41377191119`). Later merges are this handoff only. | 789 package tests and 45 script tests |
| WP0 core + contracts | Merged | `bac5163`…`e82e7c3`; REST-key and standby fixes `d1a52f7`, `5a4aa23`, `f2d849b` | Frames, processors, pipelines, workers, bus, runner, clocks, observers, settings, the `run_test` harness; contracts (point maps, events, cameras, calendar, occupants) |
| WP8 footage | Merged | `28f2788`; `eas_few` added in `b6b64b4` | Shot list (**14 rows**: 13 zone clips plus the live phone), consent form, `normalize.py`, `extract_audio.py`, WiseNET and MEVA tools |
| WP1 plants | Merged `c6f7204` | `4b1905f`, `f7e953a` | BOPTEST hosted/local, RC (60 s substeps), replay; `PlantProcessor`; the JSONL records |
| WP2 BMS | Merged `f4c147e` | `734a6c9`, `38a6447` | SpotCool-Office-BMS (BACnet device 1001, UDP 47808): priority arrays, COV, occupied-standby (90.1 §6.5.3.8), heartbeat watchdog |
| WP5 scenarios | Merged `00d3d75` | `6235d68` kickoff, `9c59dac`, `fac49f2` | Scenario format, linter and compiler; 3 library days; `ScenarioProcessor`; `FramePublisher`; the calendar router; `OccupantWorker`; `spotcool-world lint` |
| WP4 cameras | Merged `d469ab5` | `e821ad1` kickoff, `b248dff`, `e041637` | `CameraSystem` (sources: own clip, then dataset, then synthetic); snapshot and MJPEG; `HttpServerWorker(app, host, port)`; the live phone proxy; `CameraSceneProcessor`; the privacy test |
| WP6 telemetry | Merged `3c0ca84` | `1959cd7` kickoff, `f702417`, `03f9684` (core cancel fix), `8a6c242` | Telemetry, KPIs (comfort where people were, energy saved), recorder (one event stream plus manifest), the dashboard app (`/ws/events` read-only, `/ws/agent` publish-only), `spotcool-world report` |
| WP11 assembly | Merged `56430cb` | `9fa5ad1` kickoff, `b7a321e`, `eceae05`, `8607ab6` (contracts 0.1.1) | `World` and `Replay`; `run` and `replay`; the **stub agent**; `RunStatusProcessor`; RC occupancy from the scenario |
| **WP6.1 step-mean power** | **Reviewed: approve after fixes; fix round running** | `ac8008e` kickoff, `372c525` trunk sync, **`0b1153c`** build | The fix that makes energy and savings correct (§7) |
| WP7 dashboard | **Staged** | `269e026` kickoff (branch cut from `42e0409`) | React app; the brief is ready |
| WP3 VRF gateway | **Staged** | `d794de5` kickoff (branch cut from `5046340`) | The meeting-room VRF on BACnet, in wall time; the brief is ready |
| WP10 evals + release | Queued | — | Brief exists; needs a kickoff (see §8) |
| WP9 video | Queued | — | HyperFrames, OBS and replay takes, captions, edit |

**Every merged WP went through the whole flow:**
- an independent review whose findings were all fixed with tests:
  - WP5: 1 medium, 8 low;
  - WP4: 2 medium, 7 low;
  - WP6: 2 medium, 1 low, plus a core hang;
  - WP11: 2 high, 1 medium, 5 low;
- green branch and trunk CI on Linux.

**Order (sequential):** ~~WP8~~ → ~~WP1~~ → ~~WP2~~ → ~~WP5~~ → ~~WP4~~ → ~~WP6~~ → ~~WP11~~ → **WP6.1** (review) → the BOPTEST checkpoint → **WP7** → WP3 → WP10 → WP9.

**Why WP7 comes before WP3:** the dashboard is the video's main visual and part of the plan's Nov 2 checkpoint. The VRF louvers are part 2 of the video.

**Schedule (IST):**

| When | Checkpoint | State |
|---|---|---|
| Nov 2, 18:00 | `hybrid_tuesday` on RC end to end with the stub agent, plus the dashboard live | The world part is **done** (WP11). The dashboard (WP7) is pending. |
| Nov 2, 22:00 | The same on BOPTEST | §8 step 2 |
| Nov 3 | Integrate with A's agent; 21:00 first automated take | |
| Nov 4 | Final takes, QA, submit by 21:00 | |

## 3. BOPTEST: what works

- **Hosted `api.boptest.net` is unusable.** Its HTTPS certificate expired on 2026-10-26, and `select` queues past 900 s. It also answers HTTP 500 if `Content-Type: application/json` comes with an empty body.
- **GitHub Codespace `spotcool-boptest-qxxjjj94x672rp`** (repo `ibpsa/project1-boptest`, master; 2-core; 2 workers; images built).
  - It's **stopped**. Restart it in §8 step 2. Its quota is the free 120 core-hours a month; stop it when idle.
  - **Restart:** `gh codespace ssh -c spotcool-boptest-qxxjjj94x672rp -- 'cd /workspaces/project1-boptest && docker compose up -d --scale worker=2 web worker provision && docker compose ps'`
  - **Forward** (background job): `gh codespace ports forward 8000:8000 -c spotcool-boptest-qxxjjj94x672rp`. Then `curl http://127.0.0.1:8000/version` should report `1.0.0-dev`.
  - **In the world:** `--plant boptest-local` with `boptest_local_url: http://127.0.0.1:8000`. For live tests: `SPOTCOOL_LIVE_BOPTEST_URL=http://127.0.0.1:8000`.
  - **If the worker build fails on PyPI timeouts:** in `service/worker/Dockerfile`, use `pip install --default-timeout=120 --retries 10`.
- **Measured facts** (Nov 1; details in `docs/workpackages/README.md` → "Facts"):
  - **Timings:** select about 8 s; warm-up 283 s; `/advance` p50 0.24 s; the default step is 3600 s, so set 900.
  - **Scenario period:** `peak_cool_day` starts at day index 192, and the peak day is index 199.
  - **Baseline schedule:** cooling setpoint 24 °C from 06:00 to 19:00, 30 °C otherwise.
  - **Read keys and latching:** overwritten signals read back under their `_u` keys, and inputs latch until `_activate: 0`.
  - **Stamp lag:** values describe the step that just ended, so schedule edges show one stamp late.
  - **The levers on an all-empty day:** setpoint 28 °C alone saves −29 %; adding a closed damper below 27.5 °C saves −81 %.
- **Results endpoint** (verified in the clone by WP6.1; not yet live):
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
- **VRF gateway** (2001, UDP 47810) and the vendor profiles: not built yet (WP3).
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
- **Energy measurement (WP6.1).** **Energy is measured from step-mean power,** and the end-of-run cross-check against the plant's own `ener_tot` must agree within 2 %. Sampling power at step ends missed the spikes from standby dampers reopening: telemetry claimed 27 % while the truth was 17 %.
- **Contracts 0.1.1:** a `cancelled` run status, because a user's stop is not a failure.
- **Queue order:** WP7 comes before WP3 (see §2).

## 6. Free tools and licences

| Use | Tool | Licence |
|---|---|---|
| BOPTEST | — | BSD-3 |
| BACnet | BACpypes3 (pinned 0.0.110), YABE | MIT |
| Web | FastAPI, uvicorn, httpx, OpenCV, React + Vite, Chart.js | MIT / BSD / Apache-2.0 |
| Report charts | matplotlib (the `report` extra) | PSF-based |
| Recording | OBS | GPL-2 |
| Motion graphics | HyperFrames | Apache-2.0 |
| Narration and captions | Kokoro-82M fallback; faster-whisper | Apache-2.0; MIT |
| Editing | Shotcut or Kdenlive; Clipchamp free | GPL |
| CI and cloud | GitHub Actions; Codespaces (120 core-hours a month) | free |

**Avoid:** CapCut, Coqui XTTS-v2, Edge online TTS, NC/ND/SA music, and HF Docker Spaces.

**Credits:** B may optionally claim B's own $150 Meta credit; the user said B spends nothing.

**Submission rules** (unverified; **confirm on the form**): a 3-minute video, a public repo, contributors listed, built during the event, and third-party licences listed.

## 7. Numbers: what may and may not be quoted

**Published sources, safe to quote:**
1. **Counting vs. presence:** up to 17.8 % vs 5.9 % ([PNNL-22072](https://www.pnnl.gov/main/publications/external/technical_reports/pnnl-22072.pdf)).
2. **Setpoint 22.2 → 25 °C:** −27 % HVAC energy ([CBE 2015](https://escholarship.org/uc/item/13s1q2xc)).
3. **About 6 % of AC electricity per 1 °C** ([BEE](https://www.cseindia.org/a-step-in-the-right-direction-says-cse-of-power-ministry-s-move-to-fix-starting-temperature-of-room-air-conditioners-at-24oc-and-not-lower-to-save-energy-8814)).
4. **HVAC is about 55 % of Indian commercial energy** ([BEE](https://rehva.eu/fileadmin/user_upload/REHVA-ISHRAE_EC_BEE_seminar_S_Kumar_Deepa_.pdf)).
5. **Offices are at 56.1 % attendance, 66.1 % on Tuesdays** ([Kastle](https://www.kastle.com/safety-wellness/getting-america-back-to-work/)).

Supporting: PNNL 2023 (why sensors are rare), BU 2024 (camera counting error of about 7 %), ASHRAE 90.1 §6.5.3.8 (the airflow clause is verified).

**Our own simulation numbers:**
- **BOPTEST, all zones empty all day** (upper bounds; say "in simulation"): setpoint-only −29 %, with standby airflow shut-off −81 %.
- **RC, `hybrid_tuesday`, stub agent, after the WP6.1 fix:**
  - SpotCool 118.06 kWh against the baseline's 142.02 kWh: **16.87 % saved**. The cross-check difference is 0.000 %; the plant's own `ener_tot` gives the same.
  - Comfort where people were: 4.29 K·h against 0. That's the cost of 28 °C empty zones: people arrive to warm rooms.
  - **Caveats:**
    - This is the RC plant, not BOPTEST.
    - At `--speed 0` the stub's BACnet writes land asynchronously, so SpotCool's energy varies by machine. WP11's run gave 134.6 kWh.
    - **Quote only a report whose verdict says "Quotable"** (after WP6.1's fix round: the plants' own saving, from a finished, paced BOPTEST run with no fallbacks, cross-checked within 0.1 %).
    - **A stub-agent BOPTEST run** (§8 step 2) can be quoted only as the policy's **upper bound with perfect perception**, labelled so.
    - **SpotCool's own figure needs Person A's agent** on a paced BOPTEST run (the Nov 3 integration).
- **Superseded figures, never quote:**
  - **18.2 %** (WP11's first stub run, which was sensor-only);
  - **27.1 %** (the sampling error);
  - **32.8 %** (WP6's example, with a stand-in agent and the sampling error).

## 8. Next actions, in order

1. **WP6.1 gates.** Branch `wp/wp61-step-mean-power`, worktree `C:\Users\hussa\dev\wt\wp61-step-mean-power`, venv `wp61-step-mean-power`.
   1. Its branch CI is **green**: run `41391438090` on `0b1153c`. The lead's re-run passed too: 806 tests with `-W error::RuntimeWarning`, plus ruff, format, pyright and the lock.
   2. **Reviewed** (agent `a05b0b2b8cb4d5dcf`, about 20 min): **approve after fixes**, with 1 high, 2 medium and 3 low findings. The measurement itself is right: BOPTEST's own `get_results` through `step_means` and `EnergyMeter` reproduces BOPTEST's `get_energy` to −0.00008 %. The REST shape, the 30 s window (31 samples with both ends), the `ener_tot` scope and the RC means were checked against the clone. **The findings are all about the report's "figure to quote" verdict:**
      - **High:** ±2 % per run doesn't bound the saving (up to about 3.6 pp). Two failed `results` calls produced 8.42 % called quotable, where the plants' own energy gives 7.07 %.
      - **Medium:** `power_fallbacks` never reaches the recording.
      - **Medium:** the verdict ignores the run: a stub agent, a cancelled run, dropped events and RC (where the check is circular) were all called quotable.
      - **Low:** the saving row is read from the manifest, not the energy rows; the tolerances are 2 % on identities that hold to 1e-9; the `results` deadline doesn't cap each attempt.
   3. **Fix round running since about 11:45:** the builder `ae6ae39590ba8953d` was resumed with the lead's decisions:
      - **A. Quote the plants' own saving** (`ener_tot` × area, both runs). The world's step-mean integration is the check, and the report shows both savings.
      - **B. `CROSS_CHECK_PCT` = 0.1 %.** Healthy runs match to about 1e-6. The builder confirms in the clone that `/kpi` doesn't round `ener_tot`.
      - **C. One verdict function, `quote_blockers(run)`,** used by the report and `run`'s printout. It blocks when:
        - the plant isn't BOPTEST;
        - the run didn't finish;
        - events were dropped;
        - a run, a plant energy or the saving is missing;
        - any fallback happened;
        - either run is over 0.1 % off its plant's own;
        - the run was unpaced (speed 0);
        - a **stub agent** ran it: "quote it only as an upper bound with perfect perception, labelled so".
      - **D. Fallbacks surface:** a non-fatal `push_error` plus a metrics sample per run, and a report row.
      - **E. The `results` deadline** bounds the whole call.
      - **F. The three known follow-ups:**
        - the BMS `HVAC-ENERGY` point uses rectangles;
        - the `PlantService.step()` docstring states the step-mean rule;
        - `run`'s printout shows each plant's own energy and the verdict.
      - **G. Tests:** RC tolerances at 1e-9, a saving that's off while both runs pass 2 %, a fallback reaching the report, and one test per blocker.
   4. **Lead:** sync `world/main` into the branch, run the checks, and add a docs commit. Its contents:
      - **Person A handoff:** the BMS power points AI 501–507 and 510 now report the **mean power over the last step**, not an instantaneous sample.
      - **AGENTS.md:** the status cells.
   5. Push, wait for green CI, merge, check the merged tree is identical, push, confirm the trunk's CI, and update this handoff.
2. **The BOPTEST checkpoint** (the lead does it, after WP6.1 merges).
   1. Restart the Codespace and forward port 8000 (§3).
   2. Run `SPOTCOOL_LIVE_BOPTEST_URL=http://127.0.0.1:8000 uv run pytest packages -m live` (including `-k meters_add_up`).
   3. Run `uv run spotcool-world run --plant boptest-local --stub-agent --speed 60`. That's 48 steps at about 15 s each, plus about 5 minutes of warm-up.
   4. **Verify:**
      - the log shows no `no power means` and no `off its plant's own` warnings;
      - `spotcool-world report <run_id>` shows both cross-check differences within 0.1 %, no fallbacks, and a verdict whose only blocker is the stub agent;
      - the BMS and BOPTEST work together (never verified before);
      - the stub's writes are accepted.
   5. **Record** the KPIs here (§7). This is the first quotable number.
   6. **Stop the Codespace:** `gh codespace stop -c spotcool-boptest-qxxjjj94x672rp`.
3. **WP7 dashboard.** Branch `wp/wp7-dashboard` (kickoff `269e026`), worktree `C:\Users\hussa\dev\wt\wp7-dashboard`.
   1. Sync `world/main` into it. Expect a conflict near the Person A handoff's §9 bullets, between the CORS bullet and the `/ws/agent` bullet; keep both.
   2. Then run the full gate flow. The brief is ready. **Facts it relies on:**
     - the dashboard is served by WP6's app at `/` from `apps/dashboard/dist`;
     - stub events carry `stub: true`;
     - the `tick` event comes after its step's results;
     - each complaint produces two `complaint_cue` events;
     - `replay --wait-viewer` exists;
     - `run_status` now carries status, plant and scenario.
4. **WP3 VRF gateway.** Branch `wp/wp3-vrf-gateway`, worktree `C:\Users\hussa\dev\wt\wp3-vrf-gateway`. The kickoff is staged (`d794de5`, pushed); sync `world/main` in, then run the full gate flow. **The brief's binding decisions, and why:**
   - **Wall time, outside the pipeline.** It's a live device for the phone-camera take: a write takes effect at once, whatever `--speed` is. So no pipeline processor: WP11's `input()` placeholder in `World.__init__` goes, and only the worker joins `World.workers`. Its power never enters the plants' energy or the KPIs.
   - **Events published at once:** each write is a `bms_write` (device `VRF-Gateway-MeetingRoom`) and the 20 Hz worker sends `vrf_state` at up to 5 Hz, plus every 5 s. Both go through `frame_events` on `EVENTS_TOPIC`, so they're recorded. A replay plays them per step; this segment is filmed live.
   - **Reuse, never copy:** WP2's `PriorityArray` and `CommandPolicy`, and the BACnet/IP server code moves from `bms.py` into a shared `transports/bacnet/server.py`, with the BMS tests unchanged.
   - **Brand profiles enforced with BACnet's own errors:** `write-access-denied` for an axis the brand lacks, `value-out-of-range` for a state it lacks; releases always accepted.
   - **Louvers slew at 15°/s** (dt clipped to 0.1 s); room τ = 120 s of wall time toward the setpoint (never below it) or 30 °C ambient; illustrative power.
   - **No new run mode:** film the take with `run --plant rc --speed 1` and the live camera set. The stub agent doesn't drive the VRF.
5. **WP10 evals and release.** Kickoff decisions to make:
   - **Stub agent upgrades:**
     - **priority-8 back-off** (stop writing a point an operator holds);
     - **deterministic write timing,** e.g. writes per tick awaited inside the tick, so runs reproduce at any speed.
   - **Golden evals** on saved runs, using `runner/report.py`'s `load_run`, `arrivals` and `comfort_kh`. Three scenarios:
     - `hybrid_tuesday` within a band;
     - `all_hands_day` saving about 0;
     - `empty_friday`.
   - **Checks:** priority 8 always beats 10; a valid run has `ended == "finished"` and `dropped_events == 0`; `stub_agent` is recorded.
   - **Release docs:** README, CREDITS and licences, the honesty note, a security scan.
   - **The root docs** (the root `CLAUDE.md`, `AGENTS.md` and README are stale for B's world): rewrite them, coordinated with A.
   - **CI:** add `scripts` to pytest's `testpaths`, and decide whether the dashboard gets CI.
   - **Optional WP1.1:** an RC slow mass node.
6. **WP9 video.**
   - Storyboard and narration.
   - HyperFrames motion graphics.
   - **OBS takes:** the dashboard in `?mode=video`, then `spotcool-world replay <run_id> --wait-viewer 60` at the chosen speed.
   - Captions (an SRT from the narration), music, then edit in Shotcut.
   - QA: every number has its source, `CREDITS.md`, and the honesty note stated.
7. **User actions.**
   - **Filming** (Nov 2 morning; `docs/footage/shot_list.md`, **14 rows**, including the new `eas_few`: D and E at the East desks).
     - Afterwards: `uv run python scripts/footage/normalize.py`, then `scripts/footage/extract_audio.py raw/south_complaint.m4a ...`, then `uv run spotcool-world lint --clips C:/Users/hussa/dev/footage/clips`.
     - If fewer people were filmed, the lead updates the people counts in the scenario YAMLs.
   - **Manual checks:**
     - YABE against the BMS (steps in AGENTS.md);
     - the phone camera over the hotspot: `SPOTCOOL_WORLD_LIVE_CAMERA_URL`, plus the firewall rule for TCP 8081 and 8090 (AGENTS.md, Windows notes).
   - **Send Person A the updates below,** then follow up on A's answers to their handoff's §16.
   - **Confirm the submission form rules.**
   - **Accounts:** the user switches when a limit hits. One account's weekly limit resets at 20:30 IST.

### Updates for Person A since the first handoff
Everything is in `docs/integration/PERSON_A_HANDOFF.md` on `world/main`, except item 5, which arrives with WP7.
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

## 9. Superseded (don't use)

- **The root docs** (`CLAUDE.md`, `AGENTS.md`, `.claude/rules/*`, README) describe the old single-AC louver twin. They're stale for B's world; `packages/world/AGENTS.md` is authoritative. WP10 rewrites them with A.
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
