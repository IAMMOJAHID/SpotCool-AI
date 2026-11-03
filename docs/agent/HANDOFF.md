# Agent lane handoff: SpotCool's real agent (from Person A's MVP)

**Last updated:** 2026-11-04, about 05:15 IST (AG6: `dev` integrated), by the lead Claude Code session (`441270a4-d753-4379-ba6d-7751619bd543`). It's written so that a `/compact` or a fresh session can resume safely.

**Audience:** the next Claude Code session, or a person, continuing SpotCool. **This file is the project's live status.**
- `docs/status/HANDOFF.md` (Person B's world) and `docs/integration/PERSON_A_HANDOFF.md` (Person A) defer to it.
- **The user's own step-by-step guide** to the submission is `docs/release/USER_GUIDE.md`.
- **The approved plan** is `C:\Users\hussa\.claude\plans\purrfect-wibbling-reef.md`.
- **The engineering guides** are `packages/agent/AGENTS.md` and `packages/world/AGENTS.md`.

## State in one breath

- **The trunk is `agent/main`.** Everything is merged there: the world, the agent and the video, with CI green. Work lands through `ag/*`, `fix/*` or `docs/*` branches merged `--no-ff`, never through PRs. `world/main` is frozen as the record of the world.
- **`dev` is integrated and submission-ready** (AG6, below). After AG6, `agent/main` and `dev` are the same commit. **The judges see `main`, the default branch, which is still "Initial commit"; updating it is the user's call** (USER_GUIDE step H5).
- **SpotCool's own figure is done and quotable: 19.94 % HVAC energy saved** (`runs/ag3-boptest-agent-s60`).
  - The setup: SpotCool's agent, perceiving with Meta's SAM 3.1 on MEVA CCTV clips, drove the world's BACnet BMS on a paced BOPTEST run.
  - The proof: agreement 240/240 zone-steps, all 30 writes one step after their decision, 40 accepted agent writes, no heartbeat-timeout release.
  - The cost: comfort where people were, 2.13 against 0.33 K·h.
  - The control day `all_hands_day` saves 5.68 % (`runs/ag3-boptest-allhands-s60`).
- **The video shows that run.** The draft passes `qa.py`, pending the user's narration and three takes.
- **What's left is the user's AG5 material,** by Nov 4, 21:00 IST: the narration, the takes, the final cut, the upload and the form (`docs/release/USER_GUIDE.md`). There's also an optional engineering backlog ("Remaining", below).
- **Person A isn't working on it.** We built, reviewed and ran the agent end to end from A's MVP (`origin/third_level_improvement` `b653cbf`, frozen).

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
  - the README's `Video: <YouTube link>` line, after the upload (USER_GUIDE step H4);
  - updating `main` from `dev` (step H5, the user's call).

## Resume here (a fresh session or after `/compact`)

1. **Read this file. Don't re-plan.** The plan's P0 is done; what's left is below.
2. **Check what's running:**
   - `git -C C:/Users/hussa/dev/SpotCool-AI status -sb`. The main checkout is on `agent/main`.
   - `git worktree list`. Clean, merged worktrees can stay.
   - `gh codespace list`: `spotcool-boptest-qxxjjj94x672rp` should be `Shutdown`. **Never delete it.**
   - No agent or world process should be running: `curl -s -m 2 http://127.0.0.1:8081/cameras` should fail.
3. **To help the user,** follow `docs/release/USER_GUIDE.md`, step by step.
   - The likely asks are: debug a take, "drop the louvers beat", add the music's `CREDITS.md` row, rebuild the final cut, and the upload text.
   - **Dropping the louvers beat** means:
     1. remove the `louvers` beat from `video/storyboard.yaml`, and shift the later beats' `start` and `end` by its 17 s;
     2. remove the `## louvers` section from `video/narration.md`;
     3. update `video/README.md`'s table;
     4. run `storyboard_check.py`, `render_motion.py`, `assemble.py` and `qa.py`.
   - Do it **before** the user records the narration.
4. **For new engineering work** (the backlog below), follow the gate in "Workflow": a brief in `docs/agent/workpackages/`, one builder, the lead's checks, a reviewer for anything that actuates or quotes, one fix round, then merge and CI. Builder ids from this session can't be messaged from a fresh session.
5. **The key** is `C:\Users\hussa\dev\secrets\spotcool-agent.env` (`META_API_KEY`), outside the repo. A folder under `packages/` would break the uv workspace.
   - Never print it, commit it, or copy it into the repo.
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

### P0, the user's (see `docs/release/USER_GUIDE.md`)

| Step | What | Note |
|---|---|---|
| A | Live louvers take | Needs the phone and the user. Finish by 16:00 IST, or drop the beat (Resume step 3) |
| B | Narration recording | After A, so the script is final |
| C | YABE take | The real agent on loopback. Hold Core at 23 °C at priority 8 between 08:30 and 17:45; the back-off shows at 18:15 |
| D | Dashboard replay take | Hands off |
| E | Music | Optional; one `CREDITS.md` row |
| F, G | Final cut, `qa.py --final`, the watch-through | |
| H | Upload (unlisted) and the form | Before 21:00 IST. The description text is in the guide |
| I | Rotate the Meta and Baseten keys | After submitting |

**Open user decisions:**
- confirm the closing slide's repo, `github.com/IAMMOJAHID/SpotCool-AI`;
- merge `agent/main` into `dev`/`main`, which replaces the repo front page's old single-AC design;
- the root README for judges (backlog 0).

### The optional engineering backlog, in value order

Each item is a gated WP. Builders run one at a time; nothing needs BOPTEST except item 1's re-run.

| # | What | Why it helps win | Size |
|---|---|---|---|
| 0 | **A root README for the judges.** What SpotCool is, the architecture (world plus agent), Meta's models used (SAM 3.1, muse-wonder, muse-voice), the 19.94 % result with its label and caveats, how to run it, the credits. **Done in AG6** ("Dev integration", above): it replaces the stale single-AC README, with the user's OK (Nov 4) | Judges read the repo first | Done |
| 1 | **LLM-written explanations** (muse-wonder) for each decision, beside the templates, then **re-run the scored BOPTEST run** so the dashboard replay shows them | Shows Meta's LLM reasoning live in the video. The figure stays 19.94 %, because the policy is unchanged | ~2 h build plus a 25 min BOPTEST run, the storyboard's `run_id`, and a re-render |
| 2 | **VRF comfort grounding:** muse-spark clothing and activity → clo and met → A's PMV `comfort_setpoint` → `IDU-SP`, every 60 s, for the meeting room only | Uses A's physics and Meta's vision model on screen in the louvers take | ~2–3 h |
| 3 | **A's supervisor for the VRF:** muse-wonder proposes a VRF plan through A's `simulate_plan`, governed by A's `govern` | Shows A's "agentic" core | ~3 h |
| 4 | **Agent evals in CI** for `all_hands_day` and `empty_friday` (blob fake) | Robustness | ~1 h |
| 5 | **Open-door anomaly:** SAM's door concept on West's camera, at 10:00–10:45, gives an explanation event | A visible extra in the replay, if 1 is re-run | ~1 h |
| 6 | Docker and compose for agent plus world; RabbitMQ scale-out; `packages/core`; Haystack | Production story | P2 |

**Recommendation:** 0 (with the user's OK), then 1, then 2, as time allows. Stop engineering by about 15:00 IST, so the user's final cut isn't rushed.

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
