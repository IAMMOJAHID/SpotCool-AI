# SpotCool: cooling that follows people

SpotCool is an agent for an office building's existing CCTV and BACnet building management system (BMS). It sees which zones have people in them, using Meta's SAM 3.1. Empty zones ease off and their airflow shuts off, and the zones with people stay as the building set them. In the meeting room, the air conditioner's louvers aim at whoever is there, and complaints by text or voice adjust the unit.

Built for the Meta Global AI Developer Hackathon, submitted 4 November 2026. Every figure on this page comes from [`docs/release/numbers.md`](docs/release/numbers.md), with its source.

**Video:** `<YouTube link>`

---

## The problem

- **Fewer people come in.** By Kastle's badge swipes, US office attendance's 10-city weekly average was **56.1 %** of its February 2020 level, and **66.1 %** on the peak day, Tuesday 20 October 2026. February 2020 is what Kastle calls normal peak occupancy, not capacity, so this isn't a share of desks. ([Kastle](https://www.kastle.com/safety-wellness/getting-america-back-to-work/); the page updates weekly, read 3 November 2026.)
- **HVAC is the largest share.** In a typical commercial building in India, HVAC uses **about 55 %** of the energy. ([BEE](https://rehva.eu/fileadmin/user_upload/REHVA-ISHRAE_EC_BEE_seminar_S_Kumar_Deepa_.pdf), slide "Typical Commercial Building Energy Use".)
- **Occupancy-based control saves energy, in simulation.** In PNNL's simulated US large office, occupancy-based control of the lights and VAV boxes saved **17.8 %** of whole-building energy with sensors that count occupants, against **5.9 %** with sensors that only detect presence. That's a national average weighted by construction volume, not a maximum, and not a share of HVAC energy; most of the saving is heating. ([PNNL-22072](https://www.pnnl.gov/main/publications/external/technical_reports/pnnl-22072.pdf), executive summary and section 3.3.)

SpotCool uses the cameras a building already has to see, zone by zone, whether anyone is there.

## What SpotCool does

- **The building.** Each BMS step, the agent snapshots every zone's CCTV camera and asks Meta's **SAM 3.1** to find the people. Then it drives the BMS over BACnet/IP:
  - **an empty zone** gets its occupancy sensor set to Unoccupied and a 28 °C cooling setpoint, at BACnet priority 10, so the BMS's standby mode shuts its airflow off;
  - **an occupied zone** has its occupancy sensor held at Occupied and its cooling setpoint released, so the building's own schedule applies;
  - **a booked room** is pre-cooled from 30 minutes before its booking;
  - **at 19:00** everything is released;
  - **an operator** writing at priority 8 always wins: the agent reads each point's priority array first, and backs off from any slot 1 to 9.
- **The meeting room.** The VRF unit's louvers follow a person on the room's camera (in the demo, a recorded clip played as that camera; SAM 3.1 and the louvers run live on its frames). The agent locates them from the camera's pose and aims the vanes, for each brand's vane positions (Mitsubishi, Daikin and LG are modelled). Every 60 s, Meta's **`muse-spark-1.3`** reads what the people wear and do, and Person A's PMV comfort physics turns that into the unit's setpoint, within 23-26 °C. A complaint or an operator comes first.
- **Complaints.** An occupant types or says "it's too cold in here". Voice is transcribed by Meta's **`muse-voice-transcribe-1.0`**. Keywords, then **`muse-wonder-1.0`**, work out the intent (cold, hot, draft, stuffy). The agent adjusts the meeting room's unit (setpoint, fan or vane), and muse-wonder answers in plain words, with a template as the fallback. Complaints never touch the BMS zones.
- **Explanations and the privacy view.** Muse Spark adds scene notes on occupied zones, **`muse-wonder-1.0`** sums up each step's decisions in one sentence, and **`muse-image-1.0`** can redraw each camera as an illustration for the dashboard. All of them are display only: they never write to the BMS or delay a building step.
- **Outside the measured result.** The louvers, complaints, comfort setpoint, scene notes, narration and privacy view are a live demo, not part of the 19.94 %, and the VRF unit's power enters no saving.
- **Privacy.** Frames and audio are held in memory, and the agent writes no file. With the Meta providers, each snapshot, each voice recording and each complaint's text go to the Meta AI API; the offline providers send nothing. To the dashboard the agent publishes counts, people's boxes, decisions, explanations and chat replies. Logs hold a complaint's length and intent, never its words. A test with an audit hook checks that no file is opened for writing.

## The result

**SpotCool saved (SAM 3.1 perception, in simulation): 19.94 % less HVAC energy than the baseline.**

| | SpotCool | Baseline |
|---|---|---|
| HVAC energy (BOPTEST's own `ener_tot`) | **230.4 kWh** | 287.8 kWh |
| Discomfort where people were (lower is better) | 2.13 K·h | 0.33 K·h |

- **The run.** BOPTEST's `multizone_office_simple_air`, the scenario `hybrid_tuesday` (07:00 to 19:00 on BOPTEST's peak cooling day, in 15-minute steps), paced at speed 60. SpotCool's agent perceived each zone with SAM 3.1 on CCTV clips and drove the world's BACnet BMS. Recorded 3 Nov 18:43 UTC, commit `1689f76`; the record is the run `ag3-boptest-agent-s60`, kept outside the repo (`runs/` is git-ignored).
- **The baseline** is the same building on the same day with no agent. Without an occupancy sensor the BMS can't tell that a zone is empty, so every zone runs its normal occupied schedule. **`hybrid_tuesday` is a day we scripted:** half the staff work from home, the East team is remote all day, and the South meeting room is used from 14:00 to 15:00. So the figure is for one such day, not an average. It isn't comparable with PNNL's figures above, which are whole-building national averages; ours is HVAC energy only.
- **The report's verdict is `Quotable`.** BOPTEST's own energy and the world's integration agree (0.000 %, no fallbacks, no events dropped). The agent made 40 accepted priority-10 zone writes from its own address, every write landed one step after its decision, and no heartbeat-timeout release happened.
- **Perception.** Occupancy was right on **240 of 240** zone-steps.
- **The cost, said plainly.** Comfort where people were got worse: 2.13 against 0.33 K·h.
- **Cost about -20 %, emissions about -20 %; peak demand unchanged** (BOPTEST's `cost_tot`, `emis_tot` and `pele_tot`, same run).
- **Again, live, with the other Meta models on.** The run `live-quad-boptest-s60` (Nov 5) repeated the day with Muse Spark, Muse Wonder and the louvers running. Its report also says `Quotable`, with the same energy and comfort figures: occupancy agreed on 239 of the 239 zone-steps it perceived (239 of 240), and all 30 writes landed one step after their decision. That's the run the video shows.
- **The control day.** On `all_hands_day`, with everyone in from 08:30 to 18:00, the same agent saved **5.68 %** (`ag3-boptest-allhands-s60`, also `Quotable`; comfort 2.99 against 0.56 K·h; occupancy right on 239 of 240 zone-steps, the one miss a single person SAM didn't see). The saving comes almost entirely from the empty hours before 08:30 and after 18:00. The agent saves little when the building is full.
- **It matches the perfect-perception upper bound.** The stub agent, which reads the scenario's script instead of the cameras, also saves 19.94 % on the same day, plant and speed: the "upper bound with perfect perception (stub agent, in simulation)". They match because the policy is the same and SAM's occupancy was right on every step.

**The caveats:**

- This is a simulation, not a real building.
- BOPTEST's occupant heat follows its own fixed schedule, not the cameras, so savings in zones the cameras show empty may be overstated by up to the occupants' heat. The control day brackets this.
- The cameras played public **MEVA** dataset clips (CC BY 4.0), one per zone and scene.
- Only occupied or empty is scored, not exact head counts.

## How it's built

### The world and the agent

The repo has two halves that share only [`packages/contracts`](packages/contracts/README.md). **The world** stands in for a real building and serves the interfaces a real one has. **The agent** speaks only those interfaces, and never imports the world. Occupied-standby, the airflow shut-off and the heartbeat watchdog are the world's emulation of a BMS. A real building would need the same points (`packages/contracts`) and those two features, and then the agent's code wouldn't change. So far it has run only against the world.

```text
 THE WORLD (packages/world)                             THE AGENT (packages/agent)
 +---------------------------------------------+       +---------------------------------------------+
 | Plant, run twice in lockstep:               |       | Building loop, once per BMS step:           |
 |   SpotCool run + baseline run               |       |   5 snapshots -> SAM 3.1 -> occupied/empty  |
 |   (BOPTEST multizone office, or RC model)   |       |   -> policy -> writes at priority 10        |
 |        ^ setpoints, dampers                 |       |                                             |
 | BACnet BMS, device 1001, UDP 47808  <-------+-------+-- zone writes, heartbeat (client 1910)      |
 | VRF gateway, device 2001, UDP 47810 <-------+-------+-- VRF loop, every 0.25 s (client 1911):     |
 | CCTV cameras, live phone, HTTP 8081 --------+------>|   phone camera -> SAM 3.1 -> louver aim     |
 | Room calendar, HTTP 8081 -------------------+------>|   (booked rooms are pre-cooled)             |
 | Scripted occupants ----- /chat, /voice -----+------>| Complaint loop, HTTP 8002:                  |
 | Recorder, KPIs, report                      |       |   muse-voice -> intent -> VRF -> muse-wonder|
 | Dashboard, HTTP 8090 <------ /ws/agent -----+-------+-- perception, decisions, explanations       |
 +---------------------------------------------+       | Meta services behind provider adapters:     |
                                                       |   meta (api.meta.ai/v1) or fake (offline)   |
                                                       +---------------------------------------------+
```

Both plant runs see the same weather, building and BMS; only the SpotCool run gets the agent's writes. The agent never reads the world's ground-truth feed (`/ws/events`); the report compares what it perceived with the script after the run.

### The repo

| Path | What it is |
|---|---|
| [`packages/contracts`](packages/contracts/README.md) | Everything both sides share: the BMS and VRF point maps, the priorities, the camera catalog, the calendar, complaints and the dashboard's event envelope |
| [`packages/world`](packages/world/AGENTS.md) | The stand-in building: the plants (BOPTEST or RC), the BACnet BMS, the VRF gateway, cameras, calendar, occupants, recorder, report, and the stub agent |
| [`packages/agent`](packages/agent/README.md) | SpotCool's agent: the building loop, the VRF loop and complaints, with Meta and offline providers |
| [`apps/dashboard`](apps/dashboard/README.md) | The judges' dashboard (React and Vite), served by the world on port 8090 |
| [`scripts/footage`](scripts/footage/) | Fetching and normalizing camera footage (MEVA, own clips) |
| [`scripts/video`](scripts/video/) | Building the demo video from its storyboard: the takes, motion graphics, captions, assembly and QA |
| [`video/`](video/README.md) | The video's storyboard, narration, sources, HyperFrames compositions and OBS scenes |
| [`docs/`](docs/README.md) | The live status, the numbers we may quote, runbooks and work-package briefs |

### Design

Both packages follow [pipecat](https://github.com/pipecat-ai/pipecat)'s architecture: service base classes with one sub-package per provider (the agent's Meta or offline fakes; the world's BOPTEST, RC or replay), a transport per protocol, small pure processors, observers and a runner. In the world, frames flow through a pipeline of processors, one tick at a time, and workers run on a bus. Each package records one deliberate deviation. The world's processors have no input queues, because a lockstep co-simulation must finish each tick, both plants included, in the same order every run. The agent uses plain loops instead of a frame pipeline, because each BMS step is one bounded request and response with a hard deadline.

### Honest by construction

`spotcool-world report` says a run is `Quotable` only when it is a finished, paced BOPTEST run:

- with no events lost and no power fallbacks;
- with the world's integration within 0.1 % of BOPTEST's own energy;
- where an agent acted (an accepted priority-10 zone write) and its heartbeat never lapsed;
- where the agent perceived at least 95 % of the zone-steps and agreed with the scenario on at least 90 % of them.

So it refuses a run where no agent acted (silent), whose agent saw nothing or too little (blind, partial), or whose heartbeat lapsed (cut off). A run driven by the stub agent is labelled an upper bound with perfect perception, never SpotCool's own. Every other reason it refuses is listed in the report.

## Run it

You need Python 3.11, [uv](https://docs.astral.sh/uv/) and Node.js. On Windows, keep the venv outside OneDrive (`export UV_PROJECT_ENVIRONMENT=<a folder outside it>`).

```bash
uv sync --all-packages --all-extras
cd apps/dashboard && npm ci && npm run build && cd ../..   # the world serves dist/ on port 8090
```

### Offline, no key

The RC plant, the world's synthetic camera frames, and the agent's colour-blob counter in place of SAM: no Meta call is made. **Start the agent first**, then the world, both on loopback:

```bash
# terminal 1: the agent
uv run spotcool-agent run --world-host 127.0.0.1 --vision blob --settle-s 0.3

# terminal 2: the world, the hybrid Tuesday on the RC plant at speed 300 (about 2.5 minutes)
SPOTCOOL_WORLD_BMS_ADDRESS=127.0.0.1 SPOTCOOL_WORLD_HTTP_HOST=127.0.0.1 uv run spotcool-world run --plant rc
```

Open <http://127.0.0.1:8090/> for the dashboard. The world prints the run's id, energy and verdict when the day ends; then stop the agent with Ctrl+C, which releases every point it holds. `uv run spotcool-world report <run_id>` writes the run's report and charts into `runs/<run_id>/`. An RC run is never quotable: RC is our own reduced model, for testing the world.

`--settle-s 0.3` fits the agent's snapshots into speed 300's 3-second steps. Leave `SPOTCOOL_WORLD_FOOTAGE_DIR` unset, so the cameras draw the synthetic frames the blob counter reads.

### Live, with Meta's models

```bash
uv run spotcool-agent run --vision meta --env-file <a file outside the repo with META_API_KEY>
SPOTCOOL_WORLD_FOOTAGE_DIR=<your footage folder> uv run spotcool-world run --plant rc --speed 60
```

- SAM sees nobody in the synthetic silhouettes, so a live run needs real footage. The footage folder holds the MEVA clips and their `datasets/meva/index.yaml`. They're not in the repo: [`CREDITS.md`](CREDITS.md) lists each clip with its source file and time range, and [`scripts/footage/meva_fetch.py`](scripts/footage/meva_fetch.py) fetches them.
- With SAM, run at speed 60 or slower, so each step has time for five calls.
- Add `--world-host 127.0.0.1` and the two loopback variables above to keep both on this machine.
- **The meeting room:** add `--vrf` to the agent, and point the world at a phone camera with `SPOTCOOL_WORLD_LIVE_CAMERA_URL`. See [`packages/agent/README.md`](packages/agent/README.md), "The louvers demo".
- `cp .env.example <outside the repo>/spotcool-agent.env` makes a key file. Never put the real key in the repo.

### The scored run

The 19.94 % run used BOPTEST in a GitHub Codespace, with the world on the laptop and the BOPTEST port forwarded: [`docs/runbooks/boptest-codespace.md`](docs/runbooks/boptest-codespace.md). With the forward running, the agent started first as above, and the footage folder set:

```bash
uv run spotcool-world run --plant boptest-local --speed 60
uv run spotcool-world report <run_id>
```

### The tests

```bash
uv run pytest            # unit and integration tests (loopback only; no Meta call, no BOPTEST)
uv run pytest -m eval    # the golden evals: whole days on RC, the real agent included, a few minutes
```

CI also runs `ruff`, `pyright`, the scenario linter and the dashboard's build, lint and tests (`.github/workflows/world-ci.yml`).

## Meta models used

All five are called through the Meta AI API at `api.meta.ai/v1`.

| Model | What SpotCool uses it for |
|---|---|
| `sam-3.1` | Perception: the people in each zone's CCTV snapshot (the building loop), and the person the meeting room's louvers follow (the VRF loop) |
| `muse-spark-1.3` | The scene: what people wear and do, open doors and windows. It sets the meeting room's comfort setpoint, through Person A's PMV physics, and writes scene notes on the zones, explanations only |
| `muse-wonder-1.0` | Chat: the intent of a complaint the keywords can't settle, and the plain-words reply; a one-sentence narration of each step's decisions |
| `muse-voice-transcribe-1.0` | Voice complaints: the occupant's recording, transcribed in memory |
| `muse-image-1.0` | A privacy view: each camera redrawn as an illustration for the dashboard's `?privacy=on` (with `--render meta`), display only. The prompt asks for faceless figures; nothing verifies a render has none |

- **Models perceive, and code decides.** SAM 3.1's boxes become occupied or empty for each zone, and a fixed, tested policy turns that into BACnet writes. No language model ever writes to the BMS; muse-spark's scene reaches only the meeting room's VRF setpoint, and only through the comfort physics.
- **Every model call has a deadline, a budget and a fallback,** and each fallback returns control to the building.
- **Cameras as occupancy sensors.** SAM 3.1 finds people from the one-word prompt `humans`, with no training on our building. That turns the cameras a building already has into its occupancy sensors. Most zones don't have one.

The algorithms around each model, step by step: [`docs/META_MODELS.md`](docs/META_MODELS.md).

## Team and credits

- **Person A** built the agent MVP that SpotCool's agent was ported from: SAM perception, louver geometry, Muse Spark scene reading, PMV comfort physics, the privacy render. All of these were ported; A's plan search and supervisor weren't. The MVP stays as it was on branch `third_level_improvement`.
- **Person B** built the world, the dashboard and the video, and integrated A's MVP into `packages/agent`.
- The repo is owned by [IAMMOJAHID](https://github.com/IAMMOJAHID).

**Data and tools** (full ledger, with every package's licence: [`CREDITS.md`](CREDITS.md)):

- **MEVA:** "Multiview Extended Video with Activities" (MEVA) dataset by Kitware Inc. and the Intelligence Advanced Research Projects Activity (IARPA), licensed under CC BY 4.0 ([registry](https://registry.opendata.aws/mevadata)). Our clips are trimmed and re-encoded.
- **BOPTEST** ([IBPSA](https://github.com/ibpsa/project1-boptest)): the `multizone_office_simple_air` test case, BSD-3-Clause.
- **HyperFrames:** the video's motion graphics, Apache-2.0.

**Licence.** SpotCool's own packages (`spotcool-contracts`, `spotcool-world`, `spotcool-agent`) declare BSD-2-Clause in their `pyproject.toml`, and the dashboard declares MIT in its `package.json`. The repo has no top-level licence file. Third-party code and data keep their own licences, listed in [`CREDITS.md`](CREDITS.md).
