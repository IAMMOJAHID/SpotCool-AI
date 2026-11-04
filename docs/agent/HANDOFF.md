# Agent lane handoff: SpotCool's real agent (from Person A's MVP)

**Last updated:** 2026-11-05, about 02:40 IST, by the lead Claude Code session (`441270a4-d753-4379-ba6d-7751619bd543`). It's written so that a `/compact` or a fresh session can resume safely.

**Audience:** the next Claude Code session, or a person, continuing SpotCool. **This file is the project's live status.**
- `docs/status/HANDOFF.md` (Person B's world) and `docs/integration/PERSON_A_HANDOFF.md` (Person A) defer to it.
- **The user's own step-by-step guide** to the submission is `docs/release/USER_GUIDE.md`.
- **The approved plan** is `C:\Users\hussa\.claude\plans\purrfect-wibbling-reef.md`.
- **The engineering guides** are `packages/agent/AGENTS.md` and `packages/world/AGENTS.md`.

## State in one breath

**Nov 5, about 02:40 IST. The deadline moved: the user says "till morning, as per IST", with no exact time yet (ask). Check the real time with `curl -sI https://api.github.com | grep -i '^date'`, then add 5:30. The lead's own time estimates were wrong once already.**

- **The trunk is `agent/main`, and `dev` is the same commit** (`git merge --ff-only agent/main` on `dev` after each merge). Work lands through `ag/*`, `fix/*` or `docs/*` branches merged `--no-ff`, never through PRs. `world/main` is frozen. **The judges see `main`, the default branch, still "Initial commit"; updating it is the user's call** (USER_GUIDE Step 10; it's a fast-forward).
- **Merged tonight:**
  - AG7: the `META` trace, the dashboard's `?panels=` and `?masks=off`, the OBS `Quad` scene, no RC figures on screen;
  - AG8: Muse Spark (the meeting room's comfort setpoint, the zones' scene notes), Person A's PMV physics, and the Muse Wonder narration;
  - `scripts/footage/clip_camera.py`;
  - the comfort band of 23-26 degC;
  - AG10: the video re-cut;
  - AG9: Muse Image.
- **Quotable: 19.94 % HVAC energy saved,** twice:
  - `runs/ag3-boptest-agent-s60`, the scored run: 240/240, with SAM 3.1;
  - `runs/live-quad-boptest-s60`, live on Nov 5 with all four models and the louvers: 239/239 of what it perceived, 239 of 240 perceived, all 30 writes one step later, no heartbeat-timeout release.

  Both are 230.4 against 287.8 kWh, comfort 2.13 against 0.33 K h. The control day `all_hands_day` saves 5.68 %.
- **The video** (`video/storyboard.yaml`, 176 s) now has 10 beats built around one hand-recorded OBS take, `video/takes/live_quad.mkv`. YABE and the live phone are dropped. The draft assembles and passes `qa.py`, with slates where material is missing.
- **What's left, in order:**
  1. the user's take 3;
  2. the lead sets the `in_s` values;
  3. the user records the narration;
  4. the final cut and QA;
  5. the upload, the README's video link, `main` (the user's OK) and the form;
  6. stop the Codespace; the user rotates the keys.
- **AG9, the Muse Image privacy view, is merged:** `--render meta` and the dashboard's `?privacy=on`. The service call ran live (25 s); the render loop and the view ran on fakes. The video doesn't use it, and its slide names four models.
- **Person A isn't working on it.** A's MVP `origin/third_level_improvement` `b653cbf` is frozen.

## Dev integration (AG6, Nov 4: done)

- **The user asked for it on Nov 4:** "merge the whole things from all the lanes … to dev first, also do the proper clean up of dev …, keep the submission ready things …, a root README for the judges", with the README approved.
- **How it landed:**
  - `release/dev-integration` is `origin/dev` with `agent/main` merged in (`f38a3b3`), then AG6's two commits and this docs commit. The brief is `docs/agent/workpackages/AG6-dev-integration.md`.
  - It was merged into `dev` with `--no-ff`. Then `agent/main` was fast-forwarded to `dev`, so the two are one commit.
  - **From now on,** work still lands on `agent/main` through branches. After each merge, `dev` follows with `git merge --ff-only agent/main`, and so does `main` once the user has updated it.
- **The gate:**
  - the builder's checks: 1263 tests, 16 evals (the agent eval at 15.4469 %, agreement 240/240), pyright 0, and the dashboard's 81 tests;
  - branch CI green;
  - the lead's line-by-line honesty read of the README against `numbers.md` and the code. It made four wording fixes, all along the lines of "the agent decides occupied or empty; it never counts people";
  - an independent reviewer's read of the README. The lead fixed all of its findings, which were:
    - **P0:** the privacy line said only counts leave the agent, but the Meta providers send snapshots, audio and complaint text to the Meta AI API;
    - **P1:** the baseline and the scripted `hybrid_tuesday` weren't defined, so the figure could be set against PNNL's;
    - **P1:** "swap the world for real hardware" overclaimed, because standby and the watchdog are the world's BMS emulation;
    - **P1:** occupied zones hold their sensor at Occupied rather than being fully released;
    - **P1:** the louvers and complaints sit outside the measured result;
    - **P1:** the occupant-heat caveat now says which way it errs;
    - **P1:** A's comfort physics wasn't ported.
- **Deleted** (git history keeps them): A's pre-integration MVP that came with `dev` (`app/`, the RabbitMQ client, the Docker files, `requirements.txt`, its `.env.example`), which stays on `third_level_improvement`; and the single-AC design material (`docs/designs/`, `docs/workflow/`, `.claude/rules/`, the Antigravity config, the drift report).
- **Rewritten:** the root `README.md` for the judges (every figure from `docs/release/numbers.md`; its `Video: <YouTube link>` line is the user's to fill in), `CLAUDE.md`, `AGENTS.md`, `REVIEW.md`'s definitions of failure (and the review workflow's copy of them), `docs/README.md`, and a key-free root `.env.example`.
- **Left stale on purpose,** outside AG6's lane. These only matter to PR reviews, which we don't run:
  - `.github/CODEOWNERS` names old `/src/` paths;
  - `claude-security-review.yml` and `evals/code_review_evals.json` still describe the old single-AC twin;
  - `docs/release/README-world-section.md`, a superseded draft, has root-relative links.
- **Still to do:**
  - the README's `Video: <YouTube link>` line, after the upload (USER_GUIDE Step 10);
  - updating `main` from `dev` (USER_GUIDE Step 10, the user's call).

## Resume here (a fresh session or after `/compact`)

1. **Read this block. Don't re-plan.** **Take 3 is recorded and cut in:** `video/takes/live_quad.mkv` (git-ignored), run `live-quad-boptest-s60-c`, Quotable, 19.94 %, occupancy 240 of 240, 417 louver writes following the user's clip. Dashboard segments at 104.5-175 and 390.5-427 s; `live` starts at 44 s, `louvers` at 405 s, which covers the Results panel's mid-day figures and "No agent configured". The Codespace is stopped. The next step is the user's narration, then the final cut.
   - **Why not take 2:** takes 1 and 2 show the wrong clip on the meeting-room tile. A test clip camera from 00:18 (`meva_admin2_one.mp4`, a stairwell) was still running when the real one started at 00:54. Windows let both bind port 8099 (`SO_REUSEADDR`), and the old one kept answering. The tile said "Meeting room" over a MEVA stairwell, and the person stays at the edge of that clip, so the louvers barely followed. Both runs are still valid for energy (`live-quad-boptest-s60-b` is Quotable at 19.94 %), but neither shows the user's clip. The clip camera now refuses a shared port. **Always look at a frame from `/shot.jpg` before a take; a 200 proves nothing.**
2. **Check what's running.** The forward and the clip camera ran as the old session's background shells, so a fresh session must check them and restart any that's down:
   ```bash
   git -C C:/Users/hussa/dev/SpotCool-AI status -sb      # the main checkout: agent/main
   gh codespace list                                     # spotcool-boptest-qxxjjj94x672rp: Available while we work. NEVER delete it
   curl -s -m 5 http://127.0.0.1:8000/version            # BOPTEST through the forward: 1.0.0-dev
   curl -s -m 3 -o /dev/null -w "%{http_code}\n" http://127.0.0.1:8099/shot.jpg   # the clip camera: 200
   curl -s -m 2 http://127.0.0.1:8081/cameras            # the world: should FAIL between takes
   ```
   - **The Codespace stops on idle.** To restart its stack, run this until `web running` shows (a first "permission denied" or "Cannot connect" just means Docker is still starting):
     ```bash
     gh codespace ssh -c spotcool-boptest-qxxjjj94x672rp -- 'cd /workspaces/project1-boptest && docker compose up -d --scale worker=2 web worker provision; docker compose ps --format "{{.Service}} {{.State}}"'
     ```
   - **The forward, in the background:** `while :; do gh codespace ports forward 8000:8000 -c spotcool-boptest-qxxjjj94x672rp; sleep 3; done`
   - **The clip camera, in the background:** `uv run --no-sync python scripts/footage/clip_camera.py C:/Users/hussa/dev/footage/raw/meeting_walk_30.mp4 --port 8099`. The clip is the user's own 24 s walk across a room, with the audio and metadata stripped, outside the repo; SAM 3.1 found the person in all 7 frames checked (Nov 5, confidence 0.95-0.98), and their box centre moves from 374 to 1006 of 1280 pixels.
3. **Take 3** (about 20 minutes; the user is panicked and wants simple words, one step at a time):
   1. **OBS checks** (the user has the `SpotCool video` collection imported, recording to `video/takes`, mkv):
      - `Quad dashboard`'s URL is `http://127.0.0.1:8090/?mode=video&panels=floorplan,energy,agent&agent=http://127.0.0.1:8002`;
      - the `Dashboard` scene's browser source has "Shutdown source when not visible" and "Refresh browser when scene becomes active" ticked. Otherwise it takes the browser's 6 connections per host, and the SAM pane's tiles go black, as they did in take 1;
      - the `Agent terminal` source captures `[WindowsTerminal.exe]`, method Modern (WGC), Multi-adapter Compatibility on, match "title, otherwise same executable".
   2. **The user** pastes this in their Windows Terminal tab (font 20, a smaller window):
      ```bash
      cd /c/Users/hussa/dev/SpotCool-AI && export UV_PROJECT_ENVIRONMENT=C:/Users/hussa/dev/venvs/spotcool-ai && /c/Users/hussa/.local/bin/uv run --no-sync spotcool-agent run --world-host 127.0.0.1 --vision meta --stt meta --llm meta --scene meta --vrf --env-file C:/Users/hussa/dev/secrets/spotcool-agent.env 2>&1 | grep --line-buffered "META"
      ```
   3. **The user** says "agent running".
   4. **The lead** starts the world in the background:
      ```bash
      cd /c/Users/hussa/dev/SpotCool-AI
      export UV_PROJECT_ENVIRONMENT=C:/Users/hussa/dev/venvs/spotcool-ai SPOTCOOL_WORLD_RUN_DIR=C:/Users/hussa/dev/SpotCool-AI/runs SPOTCOOL_WORLD_FOOTAGE_DIR=C:/Users/hussa/dev/footage
      export SPOTCOOL_WORLD_BMS_ADDRESS=127.0.0.1 SPOTCOOL_WORLD_HTTP_HOST=127.0.0.1 SPOTCOOL_WORLD_LIVE_CAMERA_URL=http://127.0.0.1:8099/shot.jpg SPOTCOOL_WORLD_VRF_PROFILE=mitsubishi
      uv run --no-sync spotcool-world run --plant boptest-local --speed 60 --run-id live-quad-boptest-s60-c
      ```
      About 30 s later the user refreshes `Quad dashboard`, `Quad raw feeds` and `Quad SAM boxes`, and confirms their own room is on the meeting-room tiles. The lead checks the louver events, says "record", and the user clicks `Quad`, then Start Recording. BOPTEST takes about 100 s to reach 07:00, so nothing is missed, and nobody refreshes a pane mid-take.
   5. **At 14:00 on the dashboard's clock,** the user clicks the `Dashboard` scene for about 30 s (the louvers, the KPIs), then `Quad` again.
   6. **When the world prints its verdict** (about 13 minutes), the user clicks Stop Recording, then presses Ctrl+C once in the terminal.
   - **The fallback:** take 1 is `video/takes/2026-11-05_00-58-54.mkv`, about 9 minutes of the first live run. Its terminal text is small and scrolls fast, and its SAM pane goes black partway.
4. **After take 3:**
   1. Run `spotcool-world report live-quad-boptest-s60-c`.
   2. **If it's `Quotable`,** point the `results` beat's `run_id` and `video/sources.yaml`'s `run-agent` at it, add its row to `docs/release/numbers.md`, and re-run `scripts/video/results_card.py`. **If it isn't,** keep `live-quad-boptest-s60`.
   3. Rename the recording to `video/takes/live_quad.mkv`.
   4. Set the `in_s` of the `live` beat (08:15 on the sim clock, about 75 s after the world's first step, is the best minute: the 08:30 arrivals) and the `louvers` beat (just after the switch to `Dashboard`). Both are `0` placeholders.
   5. Run `storyboard_check.py`.
   6. **Stop the Codespace:** `gh codespace stop -c spotcool-boptest-qxxjjj94x672rp`.
5. **The rest,** in `docs/release/USER_GUIDE.md`:
   1. the user reads `video/narration.md` into `video/raw/` (any format; the lead converts it with ffmpeg to `narration.wav`);
   2. `render_motion.py` (the `meta_models` slide's text changed after its last render), `assemble.py --final --output video/renders/spotcool.mp4`, `qa.py --final`;
   3. the watch-through and the YouTube upload (unlisted);
   4. the README's `Video: <YouTube link>` line, on a `docs/*` branch;
   5. **ask the user** before updating `main` from `dev`;
   6. the form;
   7. the user rotates the keys.
6. **The main checkout may lag.** AG9 and the last docs were pushed from a worktree while the user's agent was running: after the take, run `git -C C:/Users/hussa/dev/SpotCool-AI pull --ff-only`, rebuild the dashboard (`npm run build` in `apps/dashboard`), and `uv sync --all-packages --all-extras`.
7. **For new engineering work,** follow the gate in "Workflow". The user asked for development to stay in background subagents, and not to stop.
8. **The key** is `C:\Users\hussa\dev\secrets\spotcool-agent.env` (`META_API_KEY`), outside the repo.
   - Never print it, commit it, or copy it into the repo.
   - A folder under `packages/` would break the uv workspace.
   - It was copied from Person A's leaked commit without ever being displayed.
   - The user rotates it after submitting.

## What's done

### Status board

| WP | What | Result |
|---|---|---|
| AG0 | Pre-flight: the trunk, the key, live probes | SAM 3.1 works through A's call (`openai` 3.42.1, `responses.create`): p50 1.9 s, p95 about 3.2 s with 5 in parallel. muse-wonder replies in about 2.2 s. muse-voice-transcribe in 4.4 s for 5 s of audio. **SAM sees nobody in synthetic silhouettes,** so live runs need real footage, and CI uses the blob counter |
| D1 | Real CCTV for the cameras | WiseNET's host `data.4tu.nl` is down from every network, so it's **MEVA (CC BY 4.0)**: 11 segments in `C:/Users/hussa/dev/footage/datasets/meva/` (`index.yaml`, `provenance.txt`; built by the scratchpad's `d1_build.py`; raw clips in `footage/raw/meva/`). Every zone has its own room. Every occupied segment shows people in every 1 s frame, checked with SAM scans. `CREDITS.md` has a row per file |
| AG1 | The agent core (`packages/agent`) | Cameras → SAM 3.1 → the stub's policy → BACnet BMS writes at priority 10. Dashboard events. `/chat` and `/voice` (202 at once, CORS). The report's **agent activity and quote blockers.** The RC eval matches the stub: 15.4469 % against 15.4469 %, 30/30 writes one step late, 240/240. Merged `1689f76`. Builder `ab9df4e1b4b50ea81`, reviewer `a7e31493b0cfa9325` |
| AG2 | Rehearsal on RC with SAM on MEVA | `runs/ag2-rc-meva-s60`: agreement 239/239, all writes one step late, so **speed 60** was chosen |
| AG3 | The scored BOPTEST run | `runs/ag3-boptest-agent-s60`: **Quotable, 19.94 %**, recorded Nov 3 18:43 UTC, commit `1689f76`. The control is `runs/ag3-boptest-allhands-s60`: 5.68 %, with one miss (East at 13:15). The docs (`numbers.md`, the card label, the MEVA credits) are merged as `842f442` |
| AG4 | Meeting room, voice and chat | The VRF louvers follow a person on the phone camera (A's `locate`, `louver_angles_to` and `louver_aim`, brand-aware Pos1–5). Complaints go text or muse-voice → intent → **the VRF only**, answered by muse-wonder with a template fallback. One BACnet client and socket per device (VRF: 1911 on UDP 47821). Separate budgets; the BMS is released first; honest replies. Merged `c20ae9a`. Builder `a0a4fdccde6d41e26`, reviewer `af197050fb64dc5ab` |
| AG5 | The video | The results card, narration, dashboard replay and sources point at the agent's run (`4de4264`). The YABE take is re-planned for the real agent, and that flow was rehearsed (the Nov 4 docs commit). The draft passes `qa.py` |
| AG6 | `dev` integrated | Cleanup, the judges' README, `dev` = `agent/main` (see "Dev integration") |
| AG7 | The live demo screen | **`META` call trace** in the agent's log: the model, the endpoint, the `x-request-id`, the response id, the rate limit, and what went in and came out as sizes, boxes and labels, never content (a live call was checked). The dashboard's `?panels=` and `?masks=off`, for OBS's new **`Quad`** scene. **RC runs show no saving, kWh or comfort figure.** Reviewer: no P0 or P1. Left open as P2s: the mock fixture now reads as BOPTEST (20.6 %, never record it), and a `--plant replay` of an RC run shows its figures |
| AG8 | Muse Spark and Muse Wonder narration | `muse-spark-1.3` reads a frame as strict JSON: clothing, activity, doors. Person A's PMV physics, ported, sets the meeting room's setpoint every 60 s, within 23-26 degC (the operator, then a complaint, then comfort). Zones get scene notes, explanations only, with A's SAM/Spark cross-check. Muse Wonder writes one sentence per step that wrote; a sentence naming a figure the step doesn't hold is dropped, capped at 60 calls. A test proves the BMS writes are identical with and without them, and the live run confirmed it. Merged `526ca65`. Reviewer's P1 fixed |
| AG9 | Muse Image privacy view | `muse-image-1.0` redraws a camera as an illustration (25 s a call, live-checked), held in memory, served at the agent's `/privacy/<id>.jpg`, and shown by the dashboard's `?privacy=on`, with `--render meta` (off by default). Display only; it can't reach an actuation path, and a test proves the BMS writes are identical. The prompt asks for faceless figures, and nothing verifies a render has none. Reviewer's P0 (tiles never refreshed live) fixed |
| AG10 | The video re-cut | 10 beats, 176 s, around the one take `live_quad`, with a new `meta_models` slide. The results card comes from `live-quad-boptest-s60`. Reviewer's P1s fixed (Meta's calls are sampled, not "every call"; "no language model decides a BMS write"; "in simulation" is spoken with the figure). Merged `c8d7641` |

### Key decisions, with the reasons

1. **The policy is the stub's proven policy, fed by SAM counts:**
   - empty → `OCC-SENSOR` Unoccupied plus `CLG-SP` 28 °C at priority 10;
   - occupied → release;
   - pre-cool booked rooms 30 min ahead;
   - release at 19:00;
   - back off from slots 1–9 when writing.

   Perception is then the only difference from the 19.94 % perfect-perception run. The comfort KPI's band is the baseline's own setpoints, so PMV-driven zone setpoints would count as discomfort.
2. **The agent never imports `spotcool_world`** at runtime (a subprocess test checks this). It deploys to a real building unchanged; its tests may use the world.
3. **Loops, not a frame pipeline,** a recorded deviation from pipecat: each step is one bounded request and response. Pipecat's patterns are kept: service base classes plus providers (Meta, fake), transports, pure processors, settings, observers.
4. **The report refuses agent runs that are silent, blind, partial or cut off:**
   - no accepted priority-10 zone write;
   - a heartbeat-timeout release;
   - no perception events;
   - coverage below 0.95;
   - agreement below 0.90.

   It prints writes per client, coverage, agreement, and the decision-to-write lags.
5. **BACnet:** each device gets its own client and socket, with one request at a time. A shared socket went deaf to bacpypes3 devices that started late. Start the agent before the world. The agent is device 1910 on 47820, and the VRF client is 1911 on 47821.
6. **Releases skip the operator back-off.** A NULL write at priority 10 never overrides slot 8, so the agent always releases its own slot. That avoids a watchdog release, which would block the quote. So with the real agent, **the YABE back-off shows when the agent wants to write** (Core at 18:15), not at 08:30 as with the stub.
7. **Complaints act on the VRF only,** never on the BMS zones.
8. **No CO₂ in decisions,** because BOPTEST's follows its own schedule.
9. **Privacy:**
   - frames and audio stay in memory only, and an audit-hook test proves it;
   - logs hold lengths, never text;
   - the agent never opens `/ws/events`, which carries ground truth.

### What was cut, from the plan and from A's MVP, and why

| Cut | Where it came from | Why | Could it come back? |
|---|---|---|---|
| A's LLM supervisor (`agent.supervise`, muse-wonder with a `simulate_plan` tool) deciding the HVAC | A's MVP | Model calls stay out of the actuation path. A's 13–16 calls per decision, at up to 220 s, can't meet a 15 s step | Yes, for the VRF only, governed by A's `govern` interlocks (backlog 3) |
| A's PMV / plan search for the BMS zones | A's MVP | The comfort KPI's band is the baseline's setpoints, so moving occupied setpoints counts as discomfort and confounds the result | VRF setpoint only (backlog 2) |
| muse-spark scene semantics (clothing → clo, activity → met) | A's MVP | Not needed for occupied/empty, and it would slow the loop | With backlog 2 |
| muse-image privacy render; overlay JPEGs; turn JSON on disk | A's MVP | The project rule: never store frames | No |
| RabbitMQ jobs and 60 s video windows | A's MVP | The world serves live snapshots; there are no clips to batch | As a scale-out transport (P2) |
| A's model-based "energy saved" | A's MVP | An estimate, never a measurement | No (never quote it) |
| Legacy mode, which bypasses the governor | A's MVP | Unsafe | No |
| LLM-written explanations | Plan P1 | Templates were enough for P0 | Backlog 1 |
| More agent evals in CI (`all_hands_day`, `empty_friday`) | Plan P1 | Time | Backlog 4 |
| Open-door anomaly (SAM's door concept) | Plan P1 | Time | Backlog 5 |
| Docker and compose, `packages/core`, Haystack tags | Plan P2 | Time | Backlog 6 |
| The filmed footage (14 shots) | The world's plan | The user can't film, so MEVA is used | Not needed |

## Remaining

### P0, the user's (see `docs/release/USER_GUIDE.md`, and "Resume here" above)

| Step | What | Note |
|---|---|---|
| 1 | Take 3 | About 20 min. YABE and the live phone were dropped on Nov 5 (the lead's decision): the terminal and dashboard show the writes and back-offs, and the override is tested in code |
| 2 | Narration recording | `video/narration.md`, after take 3. Any format in `video/raw/` |
| 3 | Music | Optional; one `CREDITS.md` row |
| 4 | Final cut, `qa.py --final`, the watch-through | The lead runs the build; the user watches |
| 5 | Upload (unlisted), the README's link, the form | The description text is in the guide |
| 6 | Rotate the Meta and Baseten keys | After submitting |

**Open user decisions:**
- the exact morning cut-off;
- updating `main` from `dev`, or making `dev` the default branch; the judges see the default branch;
- confirm the closing slide's repo, `github.com/IAMMOJAHID/SpotCool-AI`.

**Open small points:**
- the mock fixture reads as BOPTEST (20.6 %), so never record `npm run mock`;
- `--plant replay` of an RC recording shows its figures;
- the stale files only PR reviews use, listed in "Dev integration".

### The optional engineering backlog, in value order

Each item is a gated WP. Builders run one at a time; nothing needs BOPTEST except item 1's re-run.

| # | What | Why it helps win | Size |
|---|---|---|---|
| 0 | **A root README for the judges.** What SpotCool is, the architecture (world plus agent), Meta's models used (SAM 3.1, muse-wonder, muse-voice), the 19.94 % result with its label and caveats, how to run it, the credits. **Done in AG6** ("Dev integration", above): it replaces the stale single-AC README, with the user's OK (Nov 4) | Judges read the repo first | Done |
| 1 | **Done in AG8 (the narration).** **LLM-written explanations** (muse-wonder) for each decision, beside the templates, then **re-run the scored BOPTEST run** so the dashboard replay shows them | Shows Meta's LLM reasoning live in the video. The figure stays 19.94 %, because the policy is unchanged | ~2 h build plus a 25 min BOPTEST run, the storyboard's `run_id`, and a re-render |
| 2 | **Done in AG8.** **VRF comfort grounding:** muse-spark clothing and activity → clo and met → A's PMV `comfort_setpoint` → `IDU-SP`, every 60 s, for the meeting room only | Uses A's physics and Meta's vision model on screen in the louvers take | ~2–3 h |
| 3 | **A's supervisor for the VRF:** muse-wonder proposes a VRF plan through A's `simulate_plan`, governed by A's `govern` | Shows A's "agentic" core | ~3 h |
| 4 | **Agent evals in CI** for `all_hands_day` and `empty_friday` (blob fake) | Robustness | ~1 h |
| 5 | **Partly done in AG8 (Muse Spark's door notes).** **Open-door anomaly:** SAM's door concept on West's camera, at 10:00–10:45, gives an explanation event | A visible extra in the replay, if 1 is re-run | ~1 h |
| 6 | Docker and compose for agent plus world; RabbitMQ scale-out; `packages/core`; Haystack | Production story | P2 |

**Recommendation:** nothing here before the submission. Finish take 3 and the final cut first.

## Where things are

| What | Where |
|---|---|
| Trunk | `agent/main`, in the main checkout `C:\Users\hussa\dev\SpotCool-AI`. Its venv is `C:\Users\hussa\dev\venvs\spotcool-ai` |
| Worktrees and venvs | `C:\Users\hussa\dev\wt\<wp>` and `C:\Users\hussa\dev\venvs\<wp>` (ag1-agent-core, ag3-docs, ag4-meeting-room, ag5-video). All merged |
| Runs | `C:\Users\hussa\dev\SpotCool-AI\runs\`: `ag3-boptest-agent-s60` (the figure), `ag3-boptest-allhands-s60` (the control), `ag2-rc-meva-s60` (the rehearsal), `eval-live-hybrid-20261103-122941` (the stub's upper bound) |
| Footage | `C:\Users\hussa\dev\footage\datasets\meva\` (served) and `footage\raw\meva\` (the source clips) |
| Key | `C:\Users\hussa\dev\secrets\spotcool-agent.env` |
| Scratchpad (this session) | `C:\Users\hussa\AppData\Local\Temp\claude\c--Users-hussa-dev-SpotCool-AI\441270a4-d753-4379-ba6d-7751619bd543\scratchpad\`: the `AG*_build_prompt.md` and `AG4-meeting-room.md` briefs, `ag0_probe.py`, `d1_scan.py`, `d1_window.py`, `d1_screen.py`, `d1_build.py` (rebuilds the MEVA index), `ag4_asr_probe.py`, `ag5_operator.py` (a scripted YABE operator), and the run logs `ag*-agent.log` and `ag*-world.log` |
| BOPTEST | Codespace `spotcool-boptest-qxxjjj94x672rp` (shut down); runbook `docs/runbooks/boptest-codespace.md` |
| Ports | BMS 1001 on UDP 47808 (47809 on loopback for YABE); VRF 2001 on 47810; the agent's BMS client 1910 on 47820; its VRF client 1911 on 47821; world HTTP 8081; dashboard 8090; agent HTTP 8002 |

## Workflow (the gate, as for the world)

1. The lead's kickoff brief with binding decisions.
2. One builder agent; one agent at a time, and a reviewer counts.
3. The lead's checks and scope.
4. An independent reviewer, for actuation, privacy or quoting.
5. One fix round (at most 3).
6. Trunk sync and docs.
7. Push, branch CI, a `--no-ff` merge, a tree-identity check (`git diff --quiet <branch> HEAD`), trunk CI.
8. This handoff and the memory notes.

**Checks**, with `UV_PROJECT_ENVIRONMENT=C:/Users/hussa/dev/venvs/<wp>`:
- `uv run ruff check packages scripts`;
- `uv run ruff format --check packages scripts`;
- `uv run pyright packages`;
- `uv run pytest -W error::RuntimeWarning`;
- `uv run pytest -m eval`;
- `uv lock --check`.

A venv borrowed from another worktree makes pyright see two copies of the packages, which shows up as fake errors. Give each worktree its own venv, or trust CI.
