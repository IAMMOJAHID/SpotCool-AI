# Person B handoff: state of the world build

**Last updated:** 2026-11-01, about 16:40 IST, by the lead Claude Code session (written before a context compression).
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
  3. **Every piece of work goes on its own new branch named after the work** (`wp/<id>-<slug>`). Cut WP branches from `wp/wp0-core-contracts`.
  4. **Pushing `wp/*` branches after their checks pass is allowed.** PRs and merges need the user's OK.
  5. **Zero spend** for B: free, open-source and free-tier tools only.
  6. Never store camera frames or audio. Always stop BOPTEST tests you start.
- **The repo moved** out of OneDrive to **`C:\Users\hussa\dev\SpotCool-AI`**.
  - The old copy at `C:\Users\hussa\OneDrive\Desktop\Spot-cool-ai\SpotCool-AI` is **stale** (behind by at least `e82e7c3`). Don't use it.
  - Delete it once the user confirms. The pre-compression session was still running from it.

## 1. Environment (this laptop)

| Item | Value |
|---|---|
| OS / shell | Windows 11, Git Bash (also PowerShell) |
| Python | uv-managed **3.11.11**; uv **0.6.5**; system Python 3.13 is not used |
| Virtualenv | `export UV_PROJECT_ENVIRONMENT="C:/Users/hussa/dev/venvs/spotcool-ai"` before **every** `uv` command (one venv per worktree: `C:/Users/hussa/dev/venvs/<wp>`) |
| Node / npm | 24.4.1 / 11.6.2 |
| Media | ffmpeg 8.0.1 (gyan full build), OBS Studio (`C:\Program Files\obs-studio`) |
| Other CLIs | AWS CLI 2.36, gh 2.92 (accounts: `mhussam-ai`, active and with the **codespace** scope; `hussam-zeng`), winget |
| Docker | Desktop 29.8.2 running, but **blocked by disk** (see §2) |
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
| **WP0** contracts + core | **Done, pushed** | `wp/wp0-core-contracts`, commits `bac5163`, `d670e67`, `e8cc7a8`, `e82e7c3` | Contracts v0.1 (19 tests). World core: 132 tests in total; ruff and pyright clean; examples 01–04 run. Reviewed by the lead. |
| **WP8** footage | **Done, pushed** | `wp/wp8-footage` commit `28f2788` (worktree `C:\Users\hussa\dev\wt\wp8-footage`) | 45 tests; ruff clean. Shot list (`docs/footage/shot_list.md`, 13 clips) and consent form are print-ready for filming on Nov 2 morning. **Decisions adopted** (WP5 must use them): file names use zone ids (`sou_meeting.mp4`, not `south_meeting`); the extra clip `wes_empty`; people counts `cor_many` 4, `cor_few` 1, `nor_few` 2, `sou_meeting`/`sou_complaint` 5, `wes_few` 1. **The shoot needs 4–5 people** (roles A–E); with fewer, film the real count and update WP5's numbers. Follow-ups: WP10 adds `scripts` to pytest `testpaths`. 4TU was unreachable, so WiseNET's CC0 and download URL are unconfirmed. MEVA indoor keys are listed in `scripts/footage/meva_fetch.py`. |
| WP1 plants | Next | `wp/wp1-plants` | Needs BOPTEST running (Codespace, §3). |
| WP2 BMS (BACnet) | Queued | `wp/wp2-bms-bacnet` | |
| WP5 scenarios | Queued | `wp/wp5-scenarios` | Originally Antigravity; the user wants Claude Code |
| WP4 cameras | Queued | `wp/wp4-cameras` | Same as WP5 |
| WP6 telemetry | Queued | `wp/wp6-telemetry` | |
| WP11 assembly | Queued | `wp/wp11-assembly` | |
| WP3 VRF | Queued | `wp/wp3-vrf-gateway` | Uses WP2's `priority.py` |
| WP7 dashboard | Queued | `wp/wp7-dashboard` | Playwright uses the installed Edge (`channel: "msedge"`) |
| WP10 evals + release | Queued | `wp/wp10-evals-release` | |
| WP9 video | Queued | `wp/wp9-video` | HyperFrames, OBS websocket and Shotcut. People: narration, review |

**Order (sequential):** ~~WP8~~ → **WP1** → WP2 → WP5 → WP4 → WP6 → WP11 → WP3 → WP7 → WP10 → WP9.

**BOPTEST Codespace build failure (Nov 1, 16:52 IST).** The `worker` image failed at its Miniconda step (`service/worker/Dockerfile`: `conda update -n base -c defaults conda`, then `conda create …`, exit 1).
- **Hypothesis:** conda's update to 25.x brings Anaconda's terms-of-service gate, so the next non-interactive `conda create` on `defaults` aborts.
- **Status:** a diagnostic rebuild was running to capture the actual error line.
- **Fix to try (inside the Codespace only, not upstream):**
  - delete the `conda update -n base -c defaults conda` line;
  - add `-c conda-forge --override-channels` to both `conda create` and `conda install`;
  - or set `CONDA_PLUGINS_AUTO_ACCEPT_TOS=yes`.

  conda-forge-only avoids Anaconda's terms altogether. Then run `docker compose up -d web worker provision`.

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

**GitHub Codespace: started at handover.**
- Name **`spotcool-boptest-qxxjjj94x672rp`**: repo `ibpsa/project1-boptest`, branch `master`, 2-core `basicLinux32gb`, idle timeout 90 minutes, paid from `mhussam-ai`'s free quota (120 core-hours and 15 GB-month per month; blocked at the limit, never billed).
- A background job was running `docker compose up -d web worker provision` inside it. Check with:
  - `gh codespace list`
  - `gh codespace ssh -c spotcool-boptest-qxxjjj94x672rp -- 'cd /workspaces/project1-boptest && docker compose ps'`
- **To use it from the laptop:** `gh codespace ports forward 8000:8000 -c spotcool-boptest-qxxjjj94x672rp` (keep it running), then BOPTEST is at **`http://127.0.0.1:8000`**. In the world: `plant: boptest-local`, `boptest_local_url: http://127.0.0.1:8000`.
- **Save quota:** `gh codespace stop -c <name>` when idle; delete it after the hackathon. Pulling or building BOPTEST's images takes several minutes on first start.

**Local Docker:** the clone at `C:\Users\hussa\dev\boptest` is on master `a29024f` (2026-10-26). That commit fixes MinIO; release v0.9.0 pulls MinIO images that no longer exist.
- Compose services: `web` (port 8000), `worker`, `redis`, `minio` (9000/9001), `mc`, `provision`.
- Start with `docker compose up -d web worker provision`.
- **Blocked until about 15 GB more is free on C:.**

**Test-case facts**, verified from the clone's `wrapped.fmu` (`multizone_office_simple_air`). The full catalog snapshot is `packages/contracts/src/spotcool_contracts/data/boptest_multizone_office_simple_air.json`.
- **Building:** 5 zones (`cor, nor, sou, eas, wes`), 1,662.66 m², Chicago weather. Default step 3600 s; warm-up 0.
- **Scenario days:** `peak_cool_day` is 199, `typical_cool_day` 144, `peak_heat_day` 26, `typical_heat_day` 18, `mix_day` 84.
- **29 inputs.** The ones SpotCool uses: `hvac_oveZonSup{Cor,Nor,Sou,Eas,Wes}_TZonCooSet_u` (K) plus `_activate`. The others (heating setpoints, AHU, dampers, reheat) are not used.
- **Per-call override semantics:** `_activate` defaults to 0 (the building's own control) on **every** `/advance`. Resend each active override every step, and send `_activate: 0` explicitly to release. Unknown input names return 400.
- **Outputs used:**
  - zone temperature `hvac_reaZon{Z}_TZon_y` (K);
  - effective setpoints `hvac_oveZonSup{Z}_TZon{Coo,Hea}Set_y` (K);
  - CO₂ `hvac_reaZon{Z}_CO2Zon_y` (ppm);
  - airflow `hvac_reaZon{Z}_V_flow_y` (m³/s);
  - weather `weaSta_reaWeaTDryBul_y` (K) and `weaSta_reaWeaRelHum_y` (fraction);
  - AHU `hvac_reaAhu_TSup_y` and `hvac_oveAhu_yFan_y`.
- **BOPTEST's ElectricPower KPI** sums 7 meters (W): `chi_reaPChi_y`, `chi_reaPPumDis_y`, `heaPum_reaPHeaPum_y`, `heaPum_reaPPumDis_y`, `hvac_reaAhu_PFanSup_y`, `hvac_reaAhu_PPumCoo_y`, `hvac_reaAhu_PPumHea_y`.
- **BOPTEST's own BACnet bridge** (`bacnet/BopTestProxy.py`, legacy bacpypes, needs Python 3.11 or older) is a raw mapping:
  - device 599;
  - analog-output 1 = `advance`;
  - inputs as analog outputs and outputs as analog inputs, in K.
  - It's a reference only. **Our** BMS (WP2) is the realistic one.
- **Still to pin, from the first Codespace run:**
  - the baseline's occupied cooling setpoint (read `hvac_oveZonSupCor_TZonCooSet_y` at 10:00 on day 199);
  - the real step latency;
  - whether two tests can run at once (SpotCool and baseline).

  Put the results into `docs/workpackages/README.md` → "Facts", and into the WP1 brief.

## 4. What the contracts say (v0.1, `packages/contracts`)

- **`office_bms.yaml`:** device `SpotCool-Office-BMS`, instance **1001**, UDP **47808**, **51 objects**.
  - **Per zone** (i = 1–5 for cor, nor, sou, eas, wes):
    - AI 100+i `ZN-<Z>-T` (°C);
    - AV 200+i `ZN-<Z>-CLG-SP` (**commandable**, 16–30 °C on a 0.5 grid; relinquish default comes from the baseline run's `_y`);
    - AV 250+i `ZN-<Z>-HTG-SP` (read-only);
    - AI 300+i `ZN-<Z>-CO2`;
    - AI 350+i `ZN-<Z>-SA-FLOW` (L/s);
    - MSV 400+i `ZN-<Z>-OCC-MODE` (Occupied / Standby / Unoccupied, set by the BMS);
    - BV 450+i `ZN-<Z>-OCC-SENSOR` (commandable; relinquish default Occupied, i.e. fail to comfort; drives G36 standby, +0.5 °C after 5 minutes empty).
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
- **Standards:** ASHRAE G36 occupied-standby (+0.5 °C after 5 minutes empty) and ASHRAE 90.1 §6.5.3.8.
- **BACnet priority conventions:** 8 for operators, 14–16 for schedules ([Haystack](https://project-haystack.org/doc/lib-phIoT/writable-point)).
- **Meta:** SAM 3.1 API $2.50 per 1,000 images; `muse-spark-1.3` $1.25/$4.25 per million tokens ([dev.meta.ai](https://dev.meta.ai/docs/models.md)). The hackathon themes are "Build for Your World" and "Push the Frontier", which names energy ([event page](https://dev.meta.ai/events/global-hackathon)).

## 8. Next actions, in order

1. **BOPTEST Codespace.**
   - When the background compose job finishes, check `docker compose ps` in the Codespace.
   - Start `gh codespace ports forward 8000:8000 -c spotcool-boptest-qxxjjj94x672rp` (keep it running).
   - Probe `http://127.0.0.1:8000`: select the office, set `peak_cool_day`, step 900 s, read the `_y` setpoints, override the core zone to 28 °C, read KPIs, stop.
   - Record the facts listed at the end of §3.
   - The scratchpad probe script is temporary. Its job becomes `spotcool-world probe` in WP1.
2. **WP8.** When the agent reports:
   - run `uv run ruff check scripts`, `ruff format --check scripts` and `pytest scripts` in the worktree (with `UV_PROJECT_ENVIRONMENT=C:/Users/hussa/dev/venvs/wp8-footage`);
   - review `shot_list.md` and `consent.md`;
   - commit on `wp/wp8-footage` and push;
   - tell the user the shot list is ready to print for filming.
3. **WP1, then the rest** of the queue in §2, one agent at a time, each in its own worktree and branch.
4. **User actions pending:**
   - free disk (target ≥ 20 GB), and decide whether to prune Docker (about 8 GB reclaimable, but other projects' data);
   - reopen VS Code and Claude Code in `C:\Users\hussa\dev\SpotCool-AI`, then delete the OneDrive copy;
   - film per the shot list (Nov 2 morning);
   - confirm the submission form rules;
   - optionally claim B's own Meta credit;
   - share `wp/wp0-core-contracts` (contracts v0.1) with Person A.

## 9. Things that are superseded

- **Old architecture.** Root `CLAUDE.md`, `AGENTS.md`, `.claude/rules/*` and `README.md` still describe the **old single-AC louver twin** (10 Hz loop, Llama, Silero, η_spot). They are stale for B's world. `packages/world/AGENTS.md` is authoritative for `packages/world/`. Rewriting the root docs belongs to WP10, coordinated with Person A.
- **Old review report.** `docs/designs/goldfish_review_report.md` reviewed an **earlier** plan and is obsolete.
