# AG4: the meeting room (VRF louvers that follow a person), voice and chat

**Branch** `ag/ag4-meeting-room`, from `agent/main` (after AG1). **Worktree** `C:\Users\hussa\dev\wt\ag4-meeting-room`. **Venv** `C:\Users\hussa\dev\venvs\ag4-meeting-room`. **Tool:** Claude Code, one builder. The lane's status is in `docs/agent/HANDOFF.md`.

## Goal

Two things the demo video and the judges see live, both built on AG1's agent (`packages/agent`, read its `AGENTS.md` first):
1. **The louvers follow a person.** The live phone camera in the South meeting room (`cam-meeting-live`) is watched by SAM 3.1. The person's floor position (A's geometry) aims the VRF's vertical and horizontal vanes, over BACnet at priority 10, and the angle points slew to follow.
2. **Complaints are heard and answered.**
   - `/voice` audio is transcribed with Meta's `muse-voice-transcribe-1.0`.
   - A text or voice complaint becomes an intent, which **acts on the VRF only**.
   - The agent answers in plain words with `muse-wonder-1.0`, grounded on the live state, with a template fallback.
   - Answers and explanations go to the dashboard.

None of this touches the BMS zones, so it never changes the scored figure. The VRF loop is **off by default** and stays off during the scored run.

## Your lane

- `packages/agent/**`.
- `uv.lock` and `CREDITS.md`, only for a new dependency (avoid new ones; `httpx` and `openai` are already there).

**Not:** the world, contracts, `scripts/`, `video/`, `apps/`, or `docs/` outside `packages/agent`. If a contract is missing, stop and say so.

## Read first

1. `packages/agent/AGENTS.md`, plus the code it describes: settings, the CLI, `transports/bacnet.py`, `actuator.py`, `services/`, `loops/complaints.py`, `transports/server.py`, `transports/dashboard.py`.
2. `docs/integration/PERSON_A_HANDOFF.md` §6: the VRF gateway, device 2001 on UDP 47810, its points, brand refusals, slew, wall time, LG's quirk, and the rule that `IDU-PWR` is illustrative. §9: `/chat`, `/voice`, events.
3. `packages/contracts/src/spotcool_contracts/points/vrf_gateway.yaml` and `vrf_profiles.yaml`:
   - Mitsubishi: vertical angles `[0, -15, -30, -45, -60]` (Pos1–5), horizontal `[-40, -20, 0, 20, 40]` (Pos1 far left to Pos5 far right, seen from the unit), Swing = 6;
   - Daikin: no horizontal axis;
   - LG: swing only.

   Find out how a client learns the active brand from the gateway (a point, a property, or the description). If it can't, take `--vrf-brand`.
4. **Person A's MVP**, read with `MSYS_NO_PATHCONV=1 git show origin/third_level_improvement:<path>`, frozen at `b653cbf`:
   - `app/spotcool/perception.py:431-480`: `_intrinsics`, `locate`, `project_floor_point`;
   - `app/spotcool/physics.py:358-366`: `louver_angles_to`;
   - `app/spotcool/control.py:309-336`: `louver_aim` (a weighted centroid; swing when the pan spread exceeds 1.5 × the beam half-angle);
   - `app/spotcool/config.py:187-197`: the `CAMERA_PARAMETERS` defaults (mount 2.8 m, tilt 30°, HFOV 90°; the unit under the camera at 2.6 m, aiming at 1.1 m);
   - `app/spotcool/media.py:66-100`: `_post_transcription`;
   - `app/spotcool/agent.py:108-186`: `state_card`.

   Port what you use near-verbatim into `spotcool_agent/domain/`, credit A in the docstring, and test it.

## Facts measured by the lead

- **`muse-voice-transcribe-1.0`:** a multipart POST to `https://api.meta.ai/v1/asr/transcribe`. The parts are `request` (JSON: `{"model": "muse-voice-transcribe-1.0", "mode": "DIARIZATION", "audioEncoding": "WAV", "keywords": [...]}`) and `audio` (`clip.wav`, `audio/wav`), with `Authorization: Bearer <key>`.
  - **Speed and accuracy:** HTTP 200 in 4.4 s for a 5 s, 16 kHz mono WAV. The transcript was verbatim.
  - **The reply shape:** `{sessionId: str, transcript: str, audioDurationMs: int, turns: [{turnId, startMs, endMs, transcript, speaker}]}`. A's code reads other field names, so use this shape.
  - **A fixture of the reply** (text only, synthetic speech): `C:\Users\hussa\AppData\Local\Temp\claude\c--Users-hussa-dev-SpotCool-AI\441270a4-d753-4379-ba6d-7751619bd543\scratchpad\ag0-sam-fixtures-real\asr_reply.json`. Copy it to `packages/agent/tests/fixtures/`.
- **`muse-wonder-1.0`:** `responses.create(model=..., input=...)` answers a short prompt in about 2.2 s.
- **SAM 3.1** is as in AG1: p50 1.9 s, p95 3.2 s.
- **The key** lives only in settings (`SecretStr`). **Tests never call Meta;** use fakes.

## Decisions (binding)

1. **Services, alongside `VisionService`:**
   - an `STTService` base, with `MetaSTT` (httpx async, the format above, a 6 s timeout, no retries, and audio kept in memory only) and a fake;
   - an `LLMService` base, with `MetaLLM` (`muse-wonder-1.0` through AG1's `MetaClient`, so it shares the limiter, ledger and cap; an 8 s timeout) and a fake.

   Neither ever logs content beyond its length.
2. **`loops/vrf.py`, the spot-cooling loop** (wall time, enabled by `--vrf`, off by default):
   - **Snapshots:** `cam-meeting-live` from the camera catalog, kept in memory only.
   - **SAM:** `detect(jpeg, "humans")`, with at most 2 calls in flight. A result older than a newer one that has already been applied is dropped.
   - **Target:** every person seen, weighted by box area, as A does. `locate` uses the phone's camera parameters (`--vrf-camera-height-m`, `--vrf-camera-tilt-deg`, `--vrf-camera-hfov-deg`; A's defaults), then `louver_angles_to`, then A's centroid and spread rule.
   - **Mapping to the profile:**
     - vertical = -tilt, clamped to the profile's range and snapped to the nearest position (Pos1-5);
     - horizontal = pan (positive is the unit's right), times -1 when `--vrf-mirror`, snapped to the nearest position;
     - swing (6) on an axis that supports it, when A's spread rule says so;
     - an axis the brand lacks is never written (Daikin and LG horizontal); on LG, only swing on or off.
   - **Writes:** at priority 10, only on a change, at most one per point a second, through AG1's actuator semantics (back off from slots 1-9, release our own slot only).
   - **Nobody seen for `--vrf-hold-s`** (10 s): release the vanes, so the unit goes back to its relinquish default.
   - **On stop:** release every VRF slot we hold.
   - **Events:**
     - `perception`, with `masks: {"cam-meeting-live": [...]}` and no zone people, since the building loop owns those;
     - `decision` for each vane write, zone `sou`, the point, the value, and a reason such as "aim at 1 person at 2.1 m, 0.4 m right";
     - `explanation`, on aim changes, at most one every 10 s.
3. **Complaints** (`loops/complaints.py`, extending AG1's):
   - **Every complaint:** `/chat` and `/voice` still answer 202 at once. In the background:
     1. `/voice` is transcribed by `STTService`, with a timeout;
     2. the intent is classified by keywords first (cold, hot, draft, stuffy, with simple synonyms), then by `LLMService`, with a JSON-only prompt and a 4 s timeout, for anything the keywords don't settle.
   - **What each intent does to the VRF:**
     - cold → `IDU-SP` +1 degC (16-30, 0.5 grid), fan one step down;
     - hot → `IDU-SP` -1 degC, fan one step up;
     - draft → vertical vane to Pos1 (horizontal throw), aiming at the person paused for `--draft-hold-s` (600);
     - stuffy → fan one step up.

     Overrides decay after `--complaint-hold-s` (900 wall seconds).
   - **Complaints about another zone** (`room_hint`), or no intent: acknowledge in words and change nothing. **Never write the BMS zones.**
   - **The reply:** `LLMService` with A's `state_card` idea: zone counts from the last perception, the meeting room's `IDU-ROOM-T`, `IDU-SP` and the vanes, and the action just taken. At most 2 sentences, within 8 s; otherwise a template ("Thanks, I've raised the meeting room's setpoint to 25.0 degC."). Publish it as `chat` and the action as `decision` plus `explanation`.
4. **Settings and CLI:** `--vrf`, `--vrf-port 47810`, `--vrf-camera cam-meeting-live`, `--vrf-brand` (when the gateway doesn't say), the camera parameters, `--vrf-mirror`, and `--stt meta|fake`, `--llm meta|fake|off`. Add an STT and LLM line to `probe`.
5. **The VRF and building loops share the BACnet client,** the actuator's rules and the dashboard publisher, but neither can block the other. The heartbeat stays the BMS's alone.

## Tests

- **Unit:**
  - geometry, with A's numbers: a person straight ahead at 3 m gives pan 0 and the expected tilt, mirrored and not;
  - snapping per brand (Mitsubishi both axes, Daikin vertical only, LG swing only);
  - the spread-to-swing rule;
  - the hold and release;
  - the write rate limit;
  - intents (keywords and the LLM fallback, faked);
  - each override and its decay;
  - other-zone complaints changing nothing;
  - the reply's template fallback when the LLM times out;
  - STT reply parsing (the fixture), its timeout, and no audio written.
- **Integration (loopback):** the world's real VRF gateway and camera server in process. Check the world's own VRF tests for how to start them.
  - A scripted person moves from left to right in fake snapshots. On Mitsubishi, `IDU-VANE-H` goes Pos1 to Pos5 and `IDU-VANE-H-ANGLE` follows. On Daikin, H is never written.
  - A `/voice` POST of a generated WAV (a sine tone; the transcript comes from the fake STT) leads to an `IDU-SP` +1 write and a `chat` event.
  - Stop releases every VRF slot.
- **Keep everything green:**
  - `uv run ruff check packages scripts`;
  - `uv run ruff format --check packages scripts`;
  - `uv run pyright packages`;
  - `uv run pytest -W error::RuntimeWarning`;
  - `uv run pytest -m eval`;
  - `uv lock --check`.

## Docs

Update `packages/agent/AGENTS.md` (the VRF loop, complaints, services) and the README (how to run the louvers demo: the world with `SPOTCOOL_WORLD_LIVE_CAMERA_URL` and `SPOTCOOL_WORLD_VRF_PROFILE=mitsubishi`, then `spotcool-agent run --vrf --vision meta --stt meta --llm meta`).

## Done when

- Every check is green.
- **Commits on `ag/ag4-meeting-room`:** `feat(agent): ...` and `docs(agent): ...`, with `Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>`. Don't push or merge.

## Report (under 600 words)

Files, test results, every deviation, what you couldn't verify (live Meta, the real phone, a real VRF brand switch), and the check results.
