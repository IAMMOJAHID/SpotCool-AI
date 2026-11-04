# AG8: Muse Spark and Muse Wonder narration, every Meta model where it honestly fits

**Branch:** `ag/ag8-meta-models`, cut from `agent/main` after AG7 is merged. That way the META trace (AG7) is already there.

**Worktree:** `C:\Users\hussa\dev\wt\ag8-meta-models`. **Venv:** `C:\Users\hussa\dev\venvs\ag8-meta-models`.

**The user asked on Nov 4:** "add spark, and all other meta models, where ever possible."

## The lead's live probes (Nov 4)

- `GET /v1/models` lists `sam-3.1`, `muse-spark-1.3` (also 1.1, 1.2 and the `-contributor` variants), `muse-wonder-1.0`, `muse-voice-transcribe-1.0` and `muse-image-1.0`.
- **`muse-spark-1.3`.** We sent `responses.create` one MEVA cafe frame with a JSON-only prompt. It took **10.4 s** and returned valid JSON: 6 people with clothing and activity, open doors 0, open windows 0, sunlight "some", space "lobby". The dataset index says 6 people for that clip. The usage was 1,295 tokens in and 1,287 out.
- **`muse-image-1.0`.** `images.edit` with A's privacy prompt took **24 s** and returned a 280 KB `b64_json`. That's for AG9, not this WP.

## The rule above everything

**The BMS policy and its writes don't change.** Spark and the narration never write a BMS point, and never delay a building step. They run in background tasks with their own call caps, and their output is explanation events only.

The 19.94 % stays valid because the BMS actuation path is untouched. A test asserts it: the RC eval's agent writes are identical with and without the scene features.

Only the meeting room's VRF setpoint gets a comfort-driven value. It's outside the measured result.

## 1. A scene service, provider-based like vision

Files: `services/scene.py` (the base class), `services/meta/scene.py` (Muse Spark) and a fake.

- **Model:** `muse-spark-1.3`.
- **The prompt** is adapted from A's `SCENE_INSTRUCTIONS` (`origin/third_level_improvement:app/spotcool/perception.py`). It sends the frame in memory, plus SAM 3.1's count as text.
- **The answer** is JSON only, validated with pydantic. It holds:
  - per person: the ASHRAE 55 clothing ensemble (A's enum), the activity and posture, and a visible discomfort cue;
  - for the room: open doors and windows, sunlight, and the space type.

  An invalid or late answer raises `SceneError`.
- **Never** ask for or accept temperatures.
- Timeout 20 s, and a separate cap (`--scene-call-cap`, default 300).

## 2. Comfort physics, ported from Person A

File: `domain/comfort.py`.

- Port these, near verbatim and pure: `pmv_ppd` (ISO 7730), `relative_air_speed`, `dynamic_clo`, `occupant_pmv` and `comfort_temperature`. They're in `origin/third_level_improvement:app/spotcool/physics.py`.
- Also port A's ensemble → clo and activity → met tables, from its perception or config.
- **Characterization tests:** about 8 cases, with reference values computed by running A's own functions (extract the file with `git show`, then import it from a temp dir).
- No new dependency.

## 3. The meeting room's comfort setpoint, with `--vrf` only

File: `loops/vrf.py`.

- Every `comfort_every_s` (60 s) while people are seen, Spark reads the latest live frame and returns the occupants.
- `comfort_temperature` then gives a target, at PMV 0, with the gateway's room RH or 50 % if there's none. The result is clamped to 16–30 °C on the 0.5 grid.
- It's written to `IDU-SP` through the existing actuator: priority 10, on a change only, backing off from slots 1–9.
- **Precedence:** the operator, then an active complaint override, then the comfort setpoint. When it's released, the unit goes back to its default.
- **The explanation event:** "Muse Spark: 2 people, seated, typical office clothing (0.61 clo, 1.0 met) → comfort setpoint 24.5 degC (PMV 0)".
- The flag `--no-comfort` turns it off.

## 4. Scene notes for the building zones: explanation only

File: `loops/scene.py` (or a task the building loop starts).

- Per occupied zone, at most once every `scene_every_steps` (4) steps. It uses that step's snapshot, already in memory; don't keep the frame after the call.
- Each runs in a fire-and-forget task with a 20 s timeout. A zone whose last call is still running is skipped.
- **Explanation:** "Muse Spark, West: 1 person, typical office clothing; a door is open".
- **Cross-check (A's rule):** if |Spark − SAM| > max(2, 0.3·max), add the explanation "SAM 3.1 saw N, Muse Spark M: the policy keeps SAM's".
- It never changes the policy's input.

## 5. Muse Wonder narrates decisions

When a building step makes writes, one background call (8 s timeout, the LLM's budget) writes a one-sentence, plain-language summary. It's grounded on the step's decisions: the zones, the reasons and the counts. It goes out as an explanation event, marked as written by muse-wonder. If the call fails, nothing is sent; the template explanations already cover it.

## 6. The trace and the docs

- Every new call logs AG7's `META ->` and `META <-` lines.
  - For Spark, `out:` gives the people, the clothing tally and the doors. Never anything identifying, and no frame.
  - For the narration, `out:` gives its length only, since the reply text is published as an event anyway.
- **Docs to update:**
  - `docs/META_MODELS.md`: add the Spark and narration sections, change the "what we don't claim" line, and add the latency probe;
  - the README's models table, now 4 models;
  - `packages/agent/README.md`, with the flags;
  - `packages/agent/AGENTS.md`.

## Tests

- Fakes for everything.
- The BMS writes are byte-identical with and without the scene and narration features: the RC eval, or a loop-level test.
- Precedence on the VRF setpoint.
- Clamping.
- An invalid Spark JSON → `SceneError`, and no write.
- No frame stored (the existing audit hook).

## Checks, commits, report

- **Checks:** the full set from CLAUDE.md, with the dashboard set if you touched it.
- **Commits:**
  - `feat(agent): Muse Spark scene service and ported comfort physics`;
  - `feat(agent): the meeting room's comfort setpoint and the zones' scene notes`;
  - `feat(agent): Muse Wonder narrates decisions`;
  - `docs: ...`.
- No push. Report in under 400 words.
