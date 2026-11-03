# AG6: `dev` made submission-ready: the merge, the cleanup, a README for the judges

**Branch** `release/dev-integration`, from `origin/dev` (`b938562`) with `agent/main` (`af9f50b`) already merged in (`f38a3b3`). **Worktree** `C:\Users\hussa\dev\wt\dev-integration`. **Venv** `C:\Users\hussa\dev\venvs\dev-integration`. **Tool:** Claude Code, one builder. **The user asked for this on Nov 4:** "merge the whole things from all the lanes … to dev, … the proper clean up of dev …, keep the submission ready things, … a root README for the judges".

## Goal

After this WP, `dev` is the repo the judges see. It must hold only what's true and current:
- the world, the agent, the dashboard, the video pipeline and their docs;
- no stale design material, and no superseded MVP code;
- a root README that explains SpotCool honestly in five minutes.

## Lane

The repo root files and folders named below; `docs/README.md`; `docs/agent/HANDOFF.md`, for a short "dev integration" note only.

**Don't change code** under `packages/`, `apps/`, `scripts/` or `video/`. The only exceptions are path references a deletion breaks, and you must name each one.

## 1. Delete (superseded or stale; git history keeps them)

- **Person A's pre-integration MVP, which arrived with `dev`:** `app/` (including `app/turn_details/*.json`), `rabbitmq_client.py`, `direct_test.py`, `Dockerfile`, `.dockerignore`, `compose.yaml`, `requirements.txt`, and `dev`'s `.env.example`. Its code was ported into `packages/agent`, and the original stays on branch `third_level_improvement`.
- **The old single-AC "AC Clone" design material:** `2026-10-31_DRIFT_REPORT.md`, `docs/designs/`, `docs/workflow/`, `GEMINI.md`, `.claude/rules/`.

Before deleting anything, `git grep` the tree for references to it. Fix or remove each reference (docs, `.gitignore`, `CODEOWNERS`, workflows, `pyproject.toml`), and say what you changed.

## 2. Keep

- `packages/`, `apps/`, `scripts/`, `video/`, `docs/` (except the two folders above), `CREDITS.md`, `resources/`, `evals/`, `.claude/commands/`, `.github/`, `pyproject.toml`, `uv.lock`, `.python-version`, `.gitattributes`, `.gitignore`.
- **Don't edit `.github/workflows/`,** except to remove a reference to a deleted file.

## 3. Rewrite

### `README.md`, for the judges
The front page. Plain, honest, scannable, about 200–300 lines. Every number must come from `docs/release/numbers.md`, with its wording.

1. **Title, and a one-line pitch:** "SpotCool: cooling that follows people".
2. **The problem**, using only `numbers.md`'s published rows, with their exact meaning:
   - Kastle's 56.1 % (badge swipes against Feb 2020, not a share of desks) and 66.1 % on the peak day;
   - BEE's "about 55 %" HVAC share in a typical Indian commercial building;
   - PNNL's 17.8 % against 5.9 % (a national average of whole-building energy).

   **Never say "half-empty", "every desk", "up to 60 %", or any never-quote figure.**
3. **What SpotCool does:**
   - **The building:** the agent watches each zone's existing CCTV with **Meta's SAM 3.1**, and drives the building's BACnet BMS: an empty zone goes to its occupancy-sensor Unoccupied state plus a 28 °C cooling setpoint at priority 10, so the BMS's standby shuts its airflow; occupied zones are released; booked rooms are pre-cooled 30 minutes ahead; an operator at priority 8 always wins.
   - **The meeting room:** the VRF louvers follow a person seen on a camera.
   - **Complaints**, by text or voice (Meta `muse-voice-transcribe-1.0`), adjust the meeting-room unit, answered in words by `muse-wonder-1.0`.
   - **Privacy:** frames and audio stay in memory, and only counts and decisions are recorded.
4. **The result**, from `numbers.md`, "SpotCool's own figure":
   - **19.94 % less HVAC energy than the baseline, in simulation.** That's BOPTEST's `multizone_office_simple_air`, a hybrid Tuesday, a paced run whose report says "Quotable" (`runs/ag3-boptest-agent-s60` is the record).
   - 230.4 against 287.8 kWh, BOPTEST's own energy.
   - Occupancy right on 240 of 240 zone-steps.
   - **The cost, said plainly:** comfort where people were, 2.13 against 0.33 K·h.
   - The control day with everyone in: 5.68 %, almost all from the empty hours.
   - **The caveats:** in simulation; BOPTEST's occupant heat follows its own schedule, not the cameras; the cameras played public MEVA dataset clips; only occupied or empty is scored.
   - It matches the perfect-perception upper bound (19.94 %, from the stub agent) because perception was right on every step.
5. **How it's built:**
   - An ASCII architecture diagram: the world (a BOPTEST or RC plant, the BACnet BMS, the VRF gateway, cameras, calendar, occupants, recorder, dashboard) ↔ the agent (the building loop, the VRF loop, complaints, with Meta services behind provider adapters).
   - The repo layout table (`packages/contracts`, `packages/world`, `packages/agent`, `apps/dashboard`, `scripts/footage`, `scripts/video`, `video/`, `docs/`).
   - The pipecat-inspired design, in one paragraph.
   - "Honest by construction": the report refuses a run where no agent acted, or one that was blind, partial or silent.
6. **Run it:**
   - **Offline, no key:** the world on the RC plant plus the agent with `--vision blob`, plus the dashboard.
   - **Live:** `--vision meta --env-file <a file outside the repo with META_API_KEY>`.
   - **The scored run:** a pointer to `docs/runbooks/boptest-codespace.md`, and `spotcool-world report <run>`.
   - **The tests:** `uv run pytest`, `uv run pytest -m eval`.

   Commands must match the CLIs: check `--help` and `packages/*/README.md`.
7. **The demo video:** a placeholder line, `Video: <YouTube link>`, for the user to fill in.
8. **Meta models used**, as a table: `sam-3.1` (perception, building and VRF), `muse-wonder-1.0` (chat replies), `muse-voice-transcribe-1.0` (voice complaints), all through `api.meta.ai/v1`.
9. **Team and credits:**
   - Person A (the agent MVP, on branch `third_level_improvement`) and Person B (the world, the video, and the integration). Use the GitHub handle `IAMMOJAHID` for the repo owner only; ask nothing, and invent no names.
   - The data and tools come from `CREDITS.md`: MEVA (CC BY 4.0), BOPTEST (BSD-3-Clause), HyperFrames (Apache-2.0).
   - A licence note.

### `CLAUDE.md`, for future Claude Code sessions
It's loaded into every session, so it must be accurate. Make it short, about 60–90 lines:
- what SpotCool is now;
- the trunk and branch policy (`agent/main` for work; `dev` as the integration and release branch; no PRs between our own branches, because the review workflows are paid);
- the commands (uv workspace, `UV_PROJECT_ENVIRONMENT`, the checks);
- the gotchas that matter:
  - one BACnet client per device;
  - start the agent before the world;
  - the key lives outside the repo;
  - never store frames or audio;
  - the agent never reads `/ws/events`;
  - the never-quote list and `numbers.md`;
  - SAM can't see synthetic silhouettes;
- pointers to `docs/agent/HANDOFF.md`, `packages/*/AGENTS.md` and `docs/release/USER_GUIDE.md`.

Drop the old "AC Clone", louver-slew-only, "Llama decides" and `temp/H` content.

### `AGENTS.md` (root), the rules every agent shares
Replace the old "Six Laws" and the "up to 60 %" mission with today's invariants:
- privacy (no frames or audio stored);
- honesty (`numbers.md`, `quote_blockers`, labels);
- safety (priority 10; the operator wins; release on stop; the heartbeat);
- decoupling (the agent never imports the world);
- the gate flow;
- pointers to `packages/world/AGENTS.md` and `packages/agent/AGENTS.md`.

About 60–100 lines.

### `REVIEW.md`
Keep its structure. Replace only its domain-specific "definitions of failure" that describe the old AC twin (SAM mask median depth, the louver slew-only rules, and so on) with today's, which the review workflows read:
- actuation: priority 10 only; back off from slots 1–9; never release another's slot; heartbeat; release on stop;
- perception and privacy: frames in memory; no ground truth;
- quoting: only `Quotable` runs, labelled;
- BACnet: a client per device.

Update `.github/workflows/claude-code-review.yml`'s inline domain list to match only if it duplicates those lines (lines around 39–43 mention "SAM 3 mask median depth"). That one edit to a workflow is allowed.

### `docs/README.md`
An index of `docs/`: agent, status, integration, release, runbooks, workpackages, footage.

### A new `.env.example` at the root
One placeholder, `META_API_KEY=`, with a comment: keep the real file **outside** the repo and pass `--env-file`; never commit a key. The CI hygiene job exempts `.env.example`.

## Checks

Run all of these with `export UV_PROJECT_ENVIRONMENT=C:/Users/hussa/dev/venvs/dev-integration`:
- `uv sync --all-packages --all-extras`;
- `uv run ruff check packages scripts`;
- `uv run ruff format --check packages scripts`;
- `uv run pyright packages`;
- `uv run pytest -W error::RuntimeWarning`;
- `uv run pytest -m eval`;
- `uv lock --check`;
- `uv run python scripts/video/storyboard_check.py`;
- `cd apps/dashboard && npm ci && npm run build && npm run lint && npm test`.

Then:
- `git grep -n -i -E "up to 60|AC Clone|half-empty|every desk|temp/H|GEMINI.md|feature/spotcool-dev"` must find nothing outside history and `docs/status/HANDOFF.md`'s never-quote list;
- every link in `README.md` must point at a file that exists.

## Done when

- Every check is green.
- **Commits on `release/dev-integration`,** each ending with `Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>`:
  - `chore(dev): remove the superseded MVP and the old design material`;
  - `docs: a README for the judges, and current CLAUDE.md, AGENTS.md, REVIEW.md`.
- **Don't push or merge.** The lead reviews, pushes and merges into `dev`.

## Report (under 500 words)

What you deleted and rewrote, each reference you fixed, the check results, and anything you weren't sure about.
