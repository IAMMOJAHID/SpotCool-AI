# WP10: Golden evals, CI, operator runbooks, credits

**Tool:** Claude Code. **Branch:** `wp/wp10-evals-release`, cut from `world/main`. The lead syncs the trunk in, including WP3, before the build starts.
**Depends on:** every merged WP, especially WP11 (the stub agent, `run`, `report`), WP6.1 (`quote_blockers`, `check_energy`), WP7 (the dashboard) and WP3 (the VRF).

## Why
The world is built. What's left is proving it keeps working, and making it safe to hand over:
- evals a change can't silently break;
- CI that covers the dashboard too;
- the stub agent respecting a human operator, which the video shows;
- runbooks the user follows during the takes;
- honest credits and notes.

This brief replaces the day-1 draft, which predates WP11's stub agent.

## Your lane (only these paths)
- **The stub agent:** `packages/world/src/spotcool_world/stub_agent/`.
- **Evals:** new `packages/world/evals/` (pytest, marker `eval`), and pytest's `testpaths` in the root `pyproject.toml`.
- **Report wording:** `packages/world/src/spotcool_world/runner/report.py`, for the honesty note only (decision 5).
- **The dashboard:** `apps/dashboard/src/format.ts` (and its test), for the "-0°" fix only.
- **CI:** `.github/workflows/world-ci.yml`.
- **Runbooks:** new `docs/runbooks/`.
- **Credits:** the root `CREDITS.md`.
- **Release notes:** `docs/release/`.
- **Docs:** `packages/world/AGENTS.md`, and the changelog fragments.

`packages/contracts/` is read-only.

Don't touch the root `README.md`, `CLAUDE.md`, `AGENTS.md`, `GEMINI.md` or `REVIEW.md`. They are shared with Person A (decision 6).

## Decisions already made (binding)
1. **The stub agent backs off from an operator.**
   - Before writing a point, the stub reads its `priority-array`. If a slot more urgent than 10 (1–9) holds a value, it doesn't write. It publishes one `decision` and one `explanation` (both `stub: true`) saying it backed off and who holds the point, then retries on the next tick.
   - It never releases a slot it didn't write.
   - It applies the same rule at 19:00 and at its stop.
   - **Test,** with a real BMS on loopback: an operator writes `ZN-SOU-CLG-SP` = 23.0 at priority 8 mid-run. That value is never overwritten, the stub explains it backed off, and when the operator releases, the stub resumes.
2. **Golden evals, judged from saved runs.**
   - Each eval runs the world once on the **RC** plant with the stub agent, at `--speed 1800`, so each 15-minute step gets 0.5 s of wall time and the stub's loopback writes have ample slack. It saves the run to a temp `run_dir`, then judges it only through `runner/report.py`: `load_run`, `check_energy`, `quote_blockers` and the comfort figures. That is pipecat's rule: evals re-judge saved runs.
   - Put the run in a session-scoped fixture, one run per scenario.
   - **Every run must be valid:**
     - it ended `finished`;
     - `dropped_events == 0`;
     - the manifest records the stub agent;
     - both runs' integration agrees with the plant's own within `CROSS_CHECK_PCT`;
     - no power fallbacks or connection recoveries;
     - `quote_blockers` names exactly RC, stub and nothing else (the runs are paced).
   - **Per scenario:**
     - **`hybrid_tuesday`:** the saving lies in a band around the measured value. RC measured 16.87 % at speed 0; measure it at 1800 and set the band at about ±3 pp. Comfort where people were, summed over the zones, also gets a band around the measured value. State the measured values and the reason for each band in the test's docstring.
     - **`all_hands_day`:** |saving| < 2 %. This is the honesty check: with everyone in, SpotCool must not claim savings.
     - **`empty_friday`:** its saving is greater than `hybrid_tuesday`'s.
     - **Reproducibility:** running `hybrid_tuesday` twice gives savings within 0.5 pp of each other. If it doesn't, report why: it's a finding, not a band to widen.
   - **Live variant:** `-m "eval and live"` runs `hybrid_tuesday` once on `boptest-local`, with the same validity checks and a band around the measured 21.14 %. The lead runs it; CI never does.
3. **CI** (`world-ci.yml`; the repo is public, so it's free).
   - **Add jobs:**
     - `evals`: `pytest -m eval` on RC;
     - `scenarios`: `spotcool-world lint` on the library;
     - `dashboard`: Node 24, `npm ci`, `npm run gen:types` (it needs uv and the Python workspace), `npm run build`, `npm run lint` and `npm test`, with no Playwright (its baselines are Windows Edge only).
   - Cache the uv and npm stores.
   - Add `scripts` to pytest's `testpaths`, so the footage-script tests run in CI.
   - **A hygiene check:** fail if `git ls-files` lists any `.env`, anything under `runs/`, footage or audio files (`*.mp4`, `*.mov`, `*.m4a`, `*.wav`), or `apps/dashboard/node_modules`.
4. **Runbooks the user follows** (`docs/runbooks/`, short, step by step, linking to AGENTS.md for depth, never duplicating it):
   - `boptest-codespace.md`: start the Codespace and compose (waiting for Docker), the supervised forward loop, the one-network rule with the `netsh` command, the checkpoint, and stopping the Codespace;
   - `yabe.md`: the BMS and the VRF, using Add Remote BACnet/IP node, the priority-array demo, and an operator override at 8 for the video;
   - `phone-camera.md`: IP Webcam, the hotspot, `SPOTCOOL_WORLD_LIVE_CAMERA_URL`, the firewall;
   - `windows-firewall.md`: UDP 47808 and 47810, TCP 8081 and 8090;
   - `video-takes.md`:
     - the dashboard in `?mode=video`, one tab;
     - `replay --wait-viewer` for the building segment;
     - the VRF segment filmed live with Mitsubishi, starting the world a few minutes early so the room has settled;
     - stop with Ctrl+C, never a hard kill.
5. **Honesty.**
   - **`report.py`'s honesty note** adds one sentence: "BOPTEST's own discomfort KPIs (`tdis_tot`, `idis_tot`) follow its fixed schedule, which counts every zone as occupied all day, so they include zones the scenario leaves empty." Update the report test that pins the note.
   - **`docs/release/numbers.md`** lists every figure the video may use, with its source:
     - the published sources, from the handoff's §7;
     - our BOPTEST stub run as an upper bound with perfect perception;
     - the line "SpotCool's own figure needs Person A's agent".
   - **It also lists figures never to quote:**
     - 18.2 %, 27.1 % and 32.8 %;
     - the shared README's "up to 60 %", which no run of ours supports. The user raises it with Person A.
6. **Shared files stay as they are.** Draft the root README's world section in `docs/release/README-world-section.md`: both halves, a Mermaid diagram, how to run the world, links to Person A's handoff. The user agrees it with Person A before it goes in. Release merges into `dev` and `main`, and the tag, are the user's call with Person A, and are not part of this WP.
7. **`CREDITS.md`, consolidated.**
   - Every runtime and tooling dependency of the world and the dashboard with its licence, read from the lock files, not from memory: BOPTEST BSD-3, BACpypes3 MIT, FastAPI MIT, OpenCV Apache-2.0, React MIT, Chart.js MIT, and so on.
   - Video tools as planned: OBS GPL-2, ffmpeg LGPL/GPL, HyperFrames.
   - WP8's media rows, kept.
   - Mark anything unverified as such.
8. **The dashboard's "-0°":** the formatter never shows a negative zero. One test.
9. **A security pass:** read the diff of every WP since WP0 for secrets, and confirm no test writes frames or audio. Report the results. Don't run any paid review workflow.

## Tests
As in each decision. The evals carry the `eval` marker and run in CI's `evals` job.

## Done when
- **Locally green:**
  - `uv run ruff check packages`, `uv run ruff format --check packages`, `uv run pyright packages`;
  - `uv run pytest packages scripts -W error::RuntimeWarning`;
  - `uv run pytest -m eval`;
  - `uv lock --check`;
  - the dashboard's `npm ci`, `gen:types`, `build`, `lint`, `test` and `test:e2e`.
- The CI workflow is valid, and runs green on the branch: the lead pushes.
- The runbooks, `CREDITS.md` and `docs/release/` are written, plus the changelog fragments.
