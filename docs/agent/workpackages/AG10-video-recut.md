# AG10: the video re-cut, built around the live four-pane take

**Branch:** `ag/ag10-video-recut`, from `agent/main` `2f84cfc`. **Worktree:** `C:\Users\hussa\dev\wt\ag10-video-recut`. **Venv:** `C:\Users\hussa\dev\venvs\ag10-video-recut`.

**The user, Nov 4–5:** the demo has 3 minutes. Part 1 shows four panes at once: the dashboard, the camera dataset videos, SAM's boxes, and a terminal with every Meta call. The video must explain how we use Meta's models, their algorithm and logic. No YABE, no live phone.

## What exists now

- **The live take.** On Nov 5 the lead ran SpotCool's agent live on BOPTEST at speed 60, with the four Meta models (SAM 3.1, Muse Spark, Muse Wonder, Muse Voice), MEVA cameras, and a recorded walking clip as the meeting room's camera.
  - Run `live-quad-boptest-s60`, `runs/` outside the repo. Its report says **Quotable: 19.94 % saved**. Energy 230.4 against 287.8 kWh, comfort 2.13 against 0.33 K·h, no heartbeat-timeout releases, occupancy agreement 100 % (239 of the 239 zone-steps it perceived; it perceived 239 of 240), and all 30 writes one step after their decision.
  - The user is recording **take 2** in OBS now: the `Quad` scene, plus about 30 s on the `Dashboard` scene around 14:00 for the louvers.
  - The file will be `video/takes/live_quad.mkv`. The lead sets the `in_s` offsets after the recording, so leave the take's `in_s` as placeholders the lead will fill.
- `docs/release/numbers.md` has the scored run `ag3-boptest-agent-s60` (also 19.94 %). The OBS `Quad` scene exists (AG7).
- `scripts/video/`: `storyboard_check`, `results_card`, `render_motion`, `assemble`, `qa`, `captions`.

## Binding decisions

### 1. `video/storyboard.yaml`: the new beats (176 s, within `max_s` 178)

| id | s | Type and source | Content |
|---|---|---|---|
| `title` | 0–4 | `title` | Title; no narration |
| `hook` | 4–26 | `hook_stats`, re-timed | Kastle's 56.1 % and 66.1 %, and BEE's about 55 %. Keep PNNL on screen only if it fits; otherwise drop it from the composition's variables |
| `plugs_in` | 26–40 | `three_connections`, re-timed | The body text "see who is in each zone" (not "count") |
| `world` | 40–52 | `architecture`, re-timed | One BOPTEST office run twice; the BACnet BMS |
| `live` | 52–112 | **take `live_quad`**, scene `Quad` | The label: "Live: SpotCool's agent on BOPTEST. Every Meta call is in the terminal. The cameras play MEVA dataset clips" |
| `louvers` | 112–124 | **take `live_quad`**, a later `in_s`, scene `Dashboard` | The label: "The meeting room's louvers follow a person (the camera plays a recorded clip)". If one take file can't feed two beats, make the smallest change in `storyboard.py` and `assemble.py`, and test it |
| `meta_models` | 124–144 | **a new composition, `meta_models`** | The four models as four cards (below), plus the footer "Models perceive. Code decides. No language model writes to the BMS." |
| `results` | 144–164 | `results_card`, `run_id: live-quad-boptest-s60` | As before, re-timed |
| `rollout` | 164–172 | `rollout_privacy`, re-timed | Fix the text: drop "Swap our world for a real building, and the agent's code does not change". Use "A real BMS needs the same BACnet points and occupied-standby" and "SpotCool never stores a frame" |
| `close` | 172–176 | `close` | The repo `github.com/IAMMOJAHID/SpotCool-AI` |

Remove the `bms_yabe` and `dashboard` beats, and their takes' cues. Delete nothing else, so the old OBS scenes stay importable.

### 2. The `meta_models` composition

Copy `three_connections` or `architecture`'s structure (HyperFrames 0.8.6) and keep the house style. It has four cards:

| Model | Card text |
|---|---|
| **SAM 3.1** | Sees the people in every camera, from a one-word prompt. The building's occupied or empty, and the person the meeting room's louvers follow |
| **Muse Spark** | Reads the scene: clothing, activity, open doors. The meeting room's comfort setpoint (PMV), and notes for each zone |
| **Muse Voice** | Turns a spoken complaint into text |
| **Muse Wonder** | Works out what a complaint means and answers it. One plain sentence for each step's decisions |

Use the exact model ids in small type: `sam-3.1`, `muse-spark-1.3`, `muse-voice-transcribe-1.0`, `muse-wonder-1.0`.

### 3. `video/narration.md`: one section per beat, at most 150 wpm

Write it from this draft, and keep its facts:
- **hook:** "Hi. Offices are emptier than they used to be. Kastle's badge data puts US attendance at 56.1 % of its pre-pandemic level. Yet the cooling still runs on a fixed schedule, and in a typical Indian commercial building HVAC is about 55 % of the energy, says the Bureau of Energy Efficiency."
- **plugs_in:** "Most zones have no occupancy sensor, so the BMS cools an empty room like a full one. SpotCool sits on what's already there: the cameras, the BMS, and people's complaints. No new sensors."
- **world:** "To test it, we built a building: a BACnet BMS, cameras and occupants. Behind it, BOPTEST, an open research simulator, runs one office twice, with SpotCool and without."
- **live**, about 60 s: "This is SpotCool running live. Top right, the building's cameras, here public dataset clips. Every fifteen minutes of building time, the agent sends each camera's snapshot to Meta's SAM 3.1 with one word: humans. Bottom right, every call: the model, the endpoint, Meta's request ID, what went in, and the boxes that came back with their confidence. Bottom left, those boxes on the frames. Then our code decides. An empty zone is set to unoccupied at twenty-eight degrees, at BACnet priority ten, and the building's own standby shuts its airflow off. A zone with people is left to the building, and an operator always wins. Top left, the dashboard shows each decision, and the power against the baseline." Trim it to fit.
- **louvers:** "In the meeting room, SAM finds the person in the camera, here a recorded clip, and the agent turns the AC's louvers to follow them."
- **meta_models:** "Meta's models do the seeing, listening and explaining. SAM 3.1 turns a camera into an occupancy sensor. Muse Spark reads what people wear and do, and our comfort physics turns that into the meeting room's setpoint. Muse Voice hears complaints, and Muse Wonder answers them, and narrates each decision. No language model ever writes to the building."
- **results:** "On BOPTEST's office model, SpotCool's agent used 19.94 % less HVAC energy than the baseline, and it was right about which zones had people at every step it saw. The cost: discomfort where people were rose from 0.33 to 2.13 kelvin hours. It's a simulation, and BOPTEST's occupant heat follows its own schedule, not the cameras."
- **rollout:** "It runs on the BMS and cameras a building already has. SpotCool never stores a frame, and an operator can always take over."
- **close:** "SpotCool. Cooling that follows people."

Every number must be backed by its beat's `sources`. Add a `video/sources.yaml` entry for the models, with the figures the checker needs (such as "3.1" and "1.0"), citing `docs/META_MODELS.md`.

### 4. `docs/release/numbers.md`: add the live run

Add `live-quad-boptest-s60` to "SpotCool's own figure", with the facts above. Label it the same, "SpotCool saved (SAM 3.1 perception, in simulation)", and say it's the run the video shows live. Every figure equals the scored run's, with all four models on, and the BMS policy unchanged.

### 5. The results card

Run `results_card.py` on `live-quad-boptest-s60`. The run directory is `C:/Users/hussa/dev/SpotCool-AI/runs`, so set `SPOTCOOL_WORLD_RUN_DIR`; it's read-only, outside your worktree.

### 6. Docs

- `video/README.md`'s storyboard table.
- `docs/release/USER_GUIDE.md`, Steps B–H, in simple words:
  - the narration sections;
  - rename the OBS file to `video/takes/live_quad.mkv`;
  - `assemble.py --final` and `qa.py --final`;
  - the upload text, now with four models.
- `docs/runbooks/video-takes.md`: the take is done this way.

## Checks

- `uv run python scripts/video/storyboard_check.py`
- `uv run pytest scripts -W error::RuntimeWarning`
- `uv run ruff check packages scripts`, and `ruff format --check`
- `render_motion.py --only meta_models`, plus `--check` (HyperFrames' lint, layout and contrast)
- `render_motion.py` for every re-timed composition
- `assemble.py` without `--final` (the draft with slates) builds
- `qa.py` on the draft

## Done when

- The commits are on `ag/ag10-video-recut`, each ending with `Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>`. Nothing is pushed.
- The report, under 400 words, has the beat table as built, each beat's narration wpm, and what the lead must fill in (the `in_s` values).
