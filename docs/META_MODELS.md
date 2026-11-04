# How SpotCool uses Meta's models

SpotCool uses four Meta models, each with one job:

- **SAM 3.1** sees the people.
- **muse-spark-1.3** reads the scene: what the people wear and do, and whether doors are open.
- **muse-voice-transcribe-1.0** hears a spoken complaint.
- **muse-wonder-1.0** works out what a complaint means, answers it, and narrates the agent's decisions.

The decisions around them are deterministic code: **the models perceive, and the code decides.** No language model ever writes to the building's BMS.

All four are called through the Meta AI API (`api.meta.ai/v1`) by the agent in [`packages/agent`](../packages/agent/README.md). The file references below are under `packages/agent/src/spotcool_agent/`. Figures come from [`release/numbers.md`](release/numbers.md), except the latency probes: SAM 3.1's is AG0's (recorded in [`agent/HANDOFF.md`](agent/HANDOFF.md)), and Muse Spark's is the lead's of Nov 4 (recorded in [`agent/workpackages/AG8-meta-models.md`](agent/workpackages/AG8-meta-models.md)).

```text
 CCTV snapshot ──► SAM 3.1 ──► boxes ──► occupied / empty ──► policy ──► BACnet writes (BMS)
 phone camera  ──► SAM 3.1 ──► boxes ──► floor position  ──► louver aim ──► BACnet writes (meeting-room AC)
 voice message ──► muse-voice-transcribe ──► text ──┐
 typed message ─────────────────────────────────────┴► keywords, else muse-wonder ──► intent ──► meeting-room AC
                                                        muse-wonder ──► reply in plain words (grounded on live state)
 phone camera  ──► muse-spark ──► clothing, activity ──► PMV physics ──► comfort setpoint (meeting-room AC)
 zone snapshot ──► muse-spark ──► scene note (explanation only; the policy keeps SAM 3.1's count)
 step's writes ──► muse-wonder ──► one-sentence narration (explanation only)
```

## 1. SAM 3.1: the eyes

`services/meta/vision.py`

**The call:**
- **Input:** one camera snapshot as a base64 JPEG, plus the text prompt `humans`.
- **Output:** a box and a confidence for each person found. Boxes under 0.3 confidence are dropped. A rate-limited call (429) is retried once if time allows.

SAM 3.1 finds things from a text prompt, so it needed no training on our building or our cameras. That's what turns an ordinary CCTV camera into an occupancy sensor.

### The building loop, once per BMS step

`loops/building.py`, `processors/occupancy.py`, `processors/policy.py`, `actuator.py`

1. **Sync.** The loop watches the simulation clock on BACnet. One second after a new step starts, it takes the five zone cameras' snapshots in parallel. The frames are held in memory only.
2. **See.** It makes five SAM calls in parallel. Each must finish before the step's deadline: the step's length minus the larger of 2.5 s and 40 % of it. A late answer is discarded.
3. **Decide occupied or empty.** The boxes give a count per zone, and the count gives **occupied**, **empty** or **unknown**. Only occupied or empty is used, never the exact head count.
4. **Decide the command,** with a fixed policy:
   - **Empty zone:** its occupancy sensor is set to Unoccupied, and its cooling setpoint to 28 °C, at BACnet priority 10. The BMS's own occupied-standby mode then shuts the zone's airflow off.
   - **Zone with people:** its occupancy sensor is set to Occupied and its setpoint is released, so the building's schedule applies.
   - **Booked room:** treated as occupied from 30 minutes before the booking.
   - **19:00:** everything is released.
5. **Fail towards comfort:**
   - An unknown zone keeps its last command for one step, then counts as occupied.
   - If SAM is down, or the run's budget of SAM calls is used up, everything goes back to the building's own control.
6. **Write safely:**
   - Before each write, the agent reads the point's priority array. If an operator holds any slot from 1 to 9, it backs off and says so.
   - It writes only on a change, releases only its own slot, and keeps a heartbeat.
   - It releases everything it holds when it stops.

### The meeting-room loop, every 0.25 s of wall time

`loops/vrf.py`, `domain/louvers.py`

1. **See.** It keeps two SAM calls in flight on the live camera.
2. **Locate.** Each person is placed on the floor from the camera's mounting height and tilt. This is Person A's geometry:
   - **First choice:** a ray from the person's feet in the image to the floor plane.
   - **Fallback:** a pinhole estimate from the box's height, as a standing or seated person.
3. **Aim.** The aim point is the people's centre, weighted by box area. From it come the louvers' pan and tilt, from the AC's outlet to a person's torso.
4. **Map to the unit.** The angles are mapped onto the brand's real vane positions; Mitsubishi, Daikin and LG are modelled. If the people are spread wider than 1.5 beam half-angles, the louvers swing.
5. **Write and release.**
   - It writes at most once a second per point, and only on a change.
   - If nobody is seen for a while, it releases the vanes.
   - This loop has its own BACnet client and its own call budget, so it can never hold up the building loop.

## 2. muse-voice-transcribe-1.0: the ears

`services/meta/stt.py`

- **Intake.** A voice complaint arrives at the agent's `/voice` endpoint, which answers 202 at once.
- **Transcription.** In the background, the audio goes to `/asr/transcribe`, held in memory only. The request lists the comfort words to expect, and the transcription has 6 s.
- **Privacy.** The audio is never saved, and the logs keep the text's length, never its words.

## 3. muse-wonder-1.0: understanding and replying

`processors/intents.py`, `loops/complaints.py`

1. **Intent.**
   - **Keywords first.** "Freezing" means cold; "blowing on my face" means draft.
   - **Then muse-wonder.** A complaint that names no intent, or several, goes to muse-wonder with a JSON-only prompt. It must answer one of `cold`, `hot`, `draft`, `stuffy` or `none`.
   - **Fallback.** An answer that is late (over 4 s), fails or isn't one of these counts as `none`, and then nothing is changed.
2. **Action.** The action is deterministic, and on the meeting room's AC only:

   | Intent | Setpoint | Fan | Vanes |
   |---|---|---|---|
   | `cold` | up 1 °C | one step down | |
   | `hot` | down 1 °C | one step up | |
   | `stuffy` | | one step up | |
   | `draft` | | | level (Pos1); the aiming pauses |

   The setpoint stays within 16–30 °C. A point an operator holds is left as it is, and the reply says so.
3. **Reply.**
   - muse-wonder writes the answer, grounded on live facts: the last zone counts, the room's temperature, and what the agent just did. That's Person A's `state_card` idea.
   - It has 8 s, and the reply is cut to two sentences.
   - If it's late or fails, a template answers instead.

4. **Narration.** After a building step whose writes changed what the agent holds, one background call asks muse-wonder for a one-sentence summary, grounded on that step's decisions only: each zone written, its count and its reason. It has 8 s and goes out as an explanation starting "Muse Wonder:". If it fails, nothing is sent; the template explanations already cover the step. It needs `--llm meta`, shares the language models' budget, and never delays the step or writes anything.

## 4. muse-spark-1.3: the scene

`services/meta/scene.py`, `domain/scene.py`, `domain/comfort.py`

**The call:**
- **Input:** one snapshot, in memory, and a JSON-only prompt adapted from Person A's `SCENE_INSTRUCTIONS`, with SAM 3.1's count as text.
- **Output:** per person, the closest ASHRAE 55 clothing ensemble, the activity, the posture and a visible discomfort cue; for the room, the open doors and windows, the sunlight and the space type. It is validated against a strict schema: any other key, a temperature included, makes the answer invalid. An invalid or late answer (30 s) is dropped.
- **Budget:** its own, `--scene-call-cap` (300). It never takes a call from SAM 3.1's.

**The meeting room's comfort setpoint** (`loops/vrf.py`, with `--vrf`; `--no-comfort` turns it off):
1. Every 60 s while people are seen, muse-spark reads the live camera's frame.
2. Person A's ISO 7730 physics, ported near verbatim, turns each person into clo (the ensemble's, plus 0.1 seated) and met (the activity's), and finds the air temperature at PMV 0 for them, in still air at 50 % RH (the gateway has no humidity point).
3. The result goes on the setpoint's 0.5 °C grid, within 16–30 °C, and is written to `IDU-SP` at priority 10 on a change only.
4. **Precedence:** the operator first (the agent backs off from slots 1 to 9), then a complaint's override, then the comfort setpoint. With nobody seen, it's released, and the unit goes back to its default.
5. The explanation reads like "Muse Spark: 2 people, seated, typical office clothing (0.71 clo, 1.1 met) -> comfort setpoint 24.5 degC (PMV 0)".

**Scene notes for the building's zones** (`loops/scene.py`), explanations only:
- Each occupied zone is read at most every 4 steps, from that step's snapshot, after the step's writes are made, in a background task. A zone still being read is skipped.
- The note reads like "Muse Spark, West: 1 person, typical office clothing; a door is open".
- **Cross-check (Person A's rule):** when muse-spark's count differs from SAM 3.1's by more than 2, or 30 % of the larger, a second note says "SAM 3.1 saw N, Muse Spark M: the policy keeps SAM's".
- The notes never change the policy's input. A test runs the building loop with and without the scene notes and narration and requires the same BMS writes, in the same order.

**The latency probes** (the lead's, Nov 4): a short prompt on one MEVA cafe frame took 10.4 s; the full scene prompt took 16.6–18.7 s on three frames (the cafe, the meeting-room clip and the entrance), each valid JSON, with 6, 1 and 3 people, as many as the clips show. That's why muse-spark runs in the background with a 30 s timeout, and never on a building step's deadline.

## 5. muse-image-1.0: the privacy view

`services/meta/render.py`, `loops/render.py`, `transports/server.py`

**The call** (Person A's `render_privacy_snapshot`, from the MVP's `app/spotcool/media.py`):
- **Input:** one snapshot, in memory, sent to `images.edit` as `frame.jpg` with A's `PRIVACY_RENDER_PROMPT`: redraw the scene as a flat isometric illustration, keep the layout and each person's position, posture and clothing colours, make people faceless, add or remove nothing, add no text. `n=1`, a 45 s timeout, no retries.
- **Output:** one image as `b64_json`. It's checked, kept as a JPEG, and held in memory only. The trace gives the frame's and the render's sizes, never the image or its base64.
- **Budget:** its own, `--render-call-cap` (60), one call at a time. It never takes a call from another model's.

**The render loop** (`--render meta`; `off` by default, whatever `--vision` says, because each render takes about 24 s):
1. Every 180 s of wall time, it renders the five zone cameras one at a time, then the meeting room's, each from a fresh snapshot. Until the world's cameras answer, it looks again every 5 s, and sends nothing.
2. Only the latest render of each camera is kept, in memory. A failed render keeps the camera's last one.
3. The agent's server answers `GET /privacy/<camera>.jpg` with it (`Cache-Control: no-store`), or 404 before the first.
4. The dashboard's `?privacy=on` (with `?agent=`) shows the renders in the camera tiles, refreshed every 10 s, in place of the raw feeds, with SAM 3.1's boxes on top and the label "Muse Image render". Until a render exists, a tile says "rendering…".
5. At the call cap, the loop stops; the last renders stay on show. The agent goes on.

**A render is an illustration, never evidence.** It's for display only. The policy, the BMS, the VRF and every step's deadline never see it. A test runs a building day with and without the render loop and requires the same BMS writes, in the same order.

**What it doesn't do:**
- The snapshots still go to the Meta AI API, as they do for SAM 3.1.
- The prompt asks for faceless figures; nothing checks that a render has none.
- `?privacy=on` chooses what this page shows. The world's camera server still serves the raw feeds.
- It hasn't been verified live through the agent; the tests use fakes.

**The latency probe** (the lead's, Nov 4, recorded in [`agent/workpackages/AG9-muse-image.md`](agent/workpackages/AG9-muse-image.md)): `images.edit` with A's prompt on one MEVA frame took 24 s and answered with a 280 KB `b64_json`, and no URL.

## Why it's built this way

- **The models perceive, and code decides.** The building's actions come from a small, tested policy, so every write can be audited, and a model's mistake can't become an arbitrary command.
- **Every model call has a deadline, a budget and a fallback.**
  - **Budgets:** 600 SAM calls a run for the building, 3,000 for the louvers, 200 for the voice and language models, and 300 for muse-spark.
  - **Fallbacks:** each one returns control to the building or to a template.
- **Privacy:**
  - **Nothing on disk.** Frames and audio stay in memory, and the agent writes no file; an audit-hook test checks it.
  - **What the API sees.** With the Meta providers, the snapshots, recordings and complaint text go to the Meta AI API.
  - **What the dashboard sees.** It gets counts, boxes, decisions and explanations.
- **What the logs see.** Muse-spark's trace gives the people, a clothing tally and the doors; the narration's, its length.
- **Offline fakes.** Each model sits behind a provider interface with an offline fake: a colour-blob counter, and scripted speech, language and scene models. CI and the evals therefore run with no key and no network.

## What the models make possible

- **Cameras as occupancy sensors.** Occupancy-based control needs to know which zones have people, and most zones have no occupancy sensor. The usual answers are to install sensors or train a detector for each site. SAM 3.1 turns the cameras already in the building into that sensor, from a one-word prompt.
- **Fast enough for every step.** AG0's probe measured p50 1.9 s and p95 about 3.2 s with five calls in parallel.
- **Complaints in people's own words.** The muse models let occupants complain by voice or text and get a plain answer about what the system did. We didn't build speech recognition or a parser.

**The evidence,** from the scored BOPTEST run (`runs/ag3-boptest-agent-s60`; [`release/numbers.md`](release/numbers.md)):
- SAM 3.1's occupancy was right on **240 of 240** zone-steps.
- The agent made 40 accepted BMS writes, each landing one step after its decision.
- **SpotCool saved (SAM 3.1 perception, in simulation): 19.94 % less HVAC energy than the baseline.**
  - It equals the perfect-perception upper bound, because perception never missed.
  - The cost: discomfort where people were rose from 0.33 to 2.13 K·h.

**What we don't claim:**
- The louvers, complaints, comfort setpoint, scene notes and narration are a live demo, not part of the 19.94 %. With `--vision blob`, as in the evals, none of muse-spark or the narration runs.
- The comfort setpoint's effect on comfort isn't measured, and muse-spark and the narration haven't been verified live through the agent; the tests use fakes.
- The cameras played public MEVA dataset clips.
- BOPTEST's occupant heat follows its own schedule, not the cameras.
