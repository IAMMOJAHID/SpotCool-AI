# Agent lane handoff: SpotCool's real agent (from Person A's MVP)

**Last updated:** 2026-11-04, about 02:30 IST, by the lead Claude Code session (`441270a4-d753-4379-ba6d-7751619bd543`). Written so a `/compact` or a fresh session can resume safely.

**Audience:** the next Claude Code session (or person) continuing the agent lane.
- The world's own status is `docs/status/HANDOFF.md`.
- The approved plan is `C:\Users\hussa\.claude\plans\purrfect-wibbling-reef.md`.
- The world's engineering guide is `packages/world/AGENTS.md`. The agent's will be `packages/agent/AGENTS.md`, from AG1.

## State in one breath

- **The trunk is `agent/main`,** cut from `world/main` `98ba04b`.
  - All new work lands there, world-side fixes included, through `ag/<id>-<slug>` or `fix/<slug>` branches merged `--no-ff`. No PRs.
  - `world/main` is frozen as the record of the world.
- **Person A isn't working on the agent.** We build, review and run it end to end from A's MVP, `origin/third_level_improvement` `b653cbf`, which stays frozen.
- **Done: SpotCool's own quotable figure, 19.94 % saved** (`runs/ag3-boptest-agent-s60`), and every P0 engineering WP is merged into `agent/main` (`c20ae9a`, CI green). **What's left is AG5:** the user's takes and narration, the live louvers take (until Nov 4, 16:00 IST), the final cut, the upload and the form.
- **Deadline:** Nov 4, 21:00 IST.

## Resume here (a fresh session or after `/compact`)

1. Read this file, then the plan. Don't re-plan.
2. **Check what's running:**
   - `git -C C:/Users/hussa/dev/SpotCool-AI worktree list`, plus `git status` in any `C:\Users\hussa\dev\wt\ag*` worktree;
   - `gh codespace list`: shut down anything idle, but never delete `spotcool-boptest-*`.
3. **Nothing is building.** Builder agents can't be messaged from a fresh session anyway. For new work, start a `general-purpose` builder from a brief in `docs/agent/workpackages/`, saying what already exists. **The louvers demo:** `packages/agent/README.md` ("The louvers demo") and `docs/runbooks/phone-camera.md`. Start the agent first with `--vrf --vision meta --stt meta --llm meta`, then the world with `SPOTCOOL_WORLD_LIVE_CAMERA_URL` and `SPOTCOOL_WORLD_VRF_PROFILE=mitsubishi`. Never run `--vrf` during a scored run.
4. **The gate per WP** is the world's (§ Workflow below). One agent at a time; a reviewer counts.
5. **The key** is `C:\Users\hussa\dev\secrets\spotcool-agent.env` (`META_API_KEY`), outside the repo. Never print it, commit it, or copy it into the repo. The user rotates it after the end-to-end test.

## Status board

| WP | What | Status |
|---|---|---|
| AG0 | Pre-flight: trunk, key, probe, D1 start, AG1 brief | **Done** (see "Measured" below) |
| D1 | Real CCTV footage for the cameras | **Done.** WiseNET's host is down everywhere, so it's MEVA (CC BY 4.0): 11 segments in `C:/Users/hussa/dev/footage/datasets/meva/` (`index.yaml`, `provenance.txt`; built by the scratchpad's `d1_build.py`). Each zone has its own room, and every occupied segment shows people in every 1 s frame (SAM scans). `CREDITS.md` has a row per file |
| AG1 | Agent core: cameras → SAM 3.1 → BACnet BMS; dashboard events; `/chat` and `/voice` acknowledgements; the report's agent activity and blockers; the end-to-end eval | **Done** on `ag/ag1-agent-core` (build `b9e42ac` `d4fed74` `706a0a4`, review fixes `d491c2c` `2de469e`). The eval on RC matches the stub: 15.4469 % against 15.4469 %, 30/30 writes one step late, 240/240 agreement. Builder `ab9df4e1b4b50ea81`, reviewer `a7e31493b0cfa9325` |
| AG2 | Perception rehearsal on RC with SAM on real footage; choose the speed | **Done:** `runs/ag2-rc-meva-s60`. Agreement 239/239, all writes one step late, 15.45 % (= the stub's RC; RC is never quoted). **Speed 60 chosen** |
| AG3 | The scored BOPTEST run → "Quotable" | **Done:** `runs/ag3-boptest-agent-s60` says **Quotable: 19.94 % saved**, with 40 agent writes, agreement 240/240, every write on time, comfort 2.13 against 0.33 K h. The control `runs/ag3-boptest-allhands-s60` saves 5.68 % (one miss: East 13:15). Both are in `docs/release/numbers.md`; the card label is "SpotCool saved (SAM 3.1 perception, in simulation)" |
| AG4 | Meeting room (VRF louvers from the phone camera), voice and chat with Meta models | **Merged** `c20ae9a` (build `e0d36bc` `b1018bf`, review fixes `12d9040` `f4f0bac`). One BACnet client and socket per device (VRF: device 1911 on UDP 47821), because a shared one went deaf to both when they started late (the reviewer's probe). Separate budgets for the VRF and the language models; the BMS is always released first; replies never claim a change an operator blocked. The live phone, STT and LLM path is not yet run end to end. Builder `a0a4fdccde6d41e26`, reviewer `af197050fb64dc5ab` |
| AG5 | Video and release: takes, narration after AG3, final cut | **In progress.** The video shows the agent's run (`4de4264`): the results card ("SpotCool saved (SAM 3.1 perception, in simulation)", 19.94 %), the narration, the dashboard replay and the MEVA credit. The draft passes `qa.py`, pending the narration and the three takes |

## Measured (AG0, Nov 3)

- **SAM 3.1** (`api.meta.ai/v1`, `openai` 3.42.1, A's `responses.create` call), with the existing key:
  - p50 1.9 s and p95 about 3.2 s, with 5 calls running at once; no failures in 15 calls;
  - output: `<0f>0<|box;x1=..;y1=..;x2=..;y2=..;w=..;h=..;c=..|><|mask;...|>`;
  - a real-person frame (MEVA cafe) gave confidence 0.84.
- **SAM sees nobody in the world's synthetic silhouettes.** So live runs need real footage (D1), and CI uses the blob counter instead.
- **muse-wonder-1.0:** about 2.2 s for a short reply.
- **WiseNET:** `data.4tu.nl:443` refused or timed out from the laptop, from a GitHub Codespace and from Anthropic's fetcher. The Codespace was deleted.

## AG1 decisions and deviations (Nov 3)

- **The report refuses an agent run** that has:
  - no accepted priority-10 zone write;
  - a heartbeat-timeout release;
  - no perception events;
  - coverage below `MIN_COVERAGE` (0.95);
  - agreement below `MIN_AGREEMENT` (0.90).

  It prints the agent's writes per client, coverage, agreement and the decision-to-write lags. That proves an agent acted and saw the people, and catches blind, partial and silent runs.
- **BACnet requests go one at a time.** Several requests at once to a BMS that wasn't up yet left bacpypes3 0.0.110 deaf to it.
- **The agent is BACnet device 1910, on UDP 47820.** It reaches the world at the machine's primary IPv4 by default, like the world binds its BMS. A `SIM-CLOCK` that stays silent for 10 s is logged.
- **Releases skip the operator back-off.** A NULL write at priority 10 never overrides an operator's slot 8, so the agent always releases its own slot, unlike the stub. That avoids a watchdog release, which would block the quote.
- **A step's deadline margin is `max(2.5, 0.4 × step_wall_s)`.** One failing step is logged and skipped, never fatal.
- **On a new run** (`SIM-CLOCK` going back, or an initial state appearing after steps), the agent releases its old slots and resets its state.
- **Start the agent before the world.** The run's initial state (`HVAC-ENERGY` 0) isn't treated as a step.

## Binding decisions (the plan's summary)

1. **The policy is the stub's** (empty → Unoccupied plus 28 °C at priority 10; occupied → release; pre-cool 30 min; 19:00 release; back off from slots 1–9), fed by SAM counts instead of ground truth. Perception is then the only difference from the 19.94 % run.
2. **The agent never imports the world** at runtime, and its tests may use it.
3. **Loops, not a frame pipeline:** a building loop per step, a wall-time VRF loop (AG4, off during the scored run), and complaints.
4. **Privacy:** frames and audio stay in memory only. The agent never opens `/ws/events`.
5. **No CO₂ in decisions,** because BOPTEST's follows its own schedule.
6. **Complaints act on the VRF only,** never on `CLG-SP`, because the comfort KPI's band is the baseline's setpoints.
7. **The scored run:**
   - speed 30 by default, or 60 if the rehearsal shows at least 98 % of writes on time;
   - the agent starts first;
   - the report must say "Quotable", with agent activity and occupancy agreement.
8. **Not ported from A:** RabbitMQ, the 60 s windows, legacy mode, the overlay, render and turn-JSON writes, muse-image, and A's model-based "energy saved", which is never quotable.

## Workflow (the gate, as for the world)

1. The lead's kickoff brief with binding decisions.
2. One builder agent.
3. The lead's checks and scope.
4. An independent reviewer, for actuation, privacy or quoting.
5. One fix round (at most 3).
6. Trunk sync and docs.
7. Push, branch CI, `--no-ff` merge, a tree-identity check (`git diff --quiet <branch> HEAD`), trunk CI.
8. This handoff and the memory notes.

**Checks:**
- `uv run ruff check packages scripts`;
- `uv run ruff format --check packages scripts`;
- `uv run pyright packages`;
- `uv run pytest -W error::RuntimeWarning`;
- `uv run pytest -m eval`;
- `uv lock --check`.

Always run them with `UV_PROJECT_ENVIRONMENT=C:/Users/hussa/dev/venvs/<wp>`.

## User actions

- **Through the night:** keep the laptop on one Wi-Fi network; switch accounts and say "continue" when a limit hits.
- **Nov 4:**
  - stand in front of the phone for the louvers take (until 16:00);
  - record the narration after AG3's verdict;
  - upload the video and fill in the form.
- **After the end-to-end test:** rotate the Meta and Baseten keys.
