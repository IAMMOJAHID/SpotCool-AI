# CLAUDE.md: SpotCool

@AGENTS.md

## What SpotCool is

An agent for an office building's existing CCTV and BACnet BMS, built for the Meta Global AI Developer Hackathon (due the morning of Nov 5, 2026, IST). It sees which zones have people in them with Meta's SAM 3.1 and drives the BMS at priority 10: empty zones go to Unoccupied at a 28 °C cooling setpoint, occupied zones are released, booked rooms are pre-cooled. With `--vrf`, the meeting room's VRF louvers follow a person on a camera, Muse Spark and Person A's PMV physics set that unit's comfort setpoint (`muse-spark-1.3`), and text or voice complaints adjust it (`muse-voice-transcribe-1.0`, `muse-wonder-1.0`). `docs/META_MODELS.md` explains every model's use.

- `packages/agent`: the agent. `packages/world`: the simulated building it runs against (BOPTEST or RC plant, BMS, VRF gateway, cameras, calendar, occupants, recorder, report, stub agent). `packages/contracts`: the only code both share.
- `apps/dashboard`: the judges' dashboard. `scripts/video` and `video/`: the demo video, built from code.
- The quotable result is 19.94 % HVAC energy saved, in simulation (`docs/release/numbers.md`).

## Read first

- `docs/agent/HANDOFF.md`: the live status, the decisions and why, what's left. Don't re-plan what it settles.
- `packages/world/AGENTS.md` and `packages/agent/AGENTS.md`: the engineering guides (code style, architecture, tests, known limitations).
- `docs/release/USER_GUIDE.md`: the user's step-by-step guide to the submission.

## Branches

- **`agent/main` is the trunk for work.** Each work item gets its own `ag/*`, `fix/*` or `docs/*` branch, merged back `--no-ff` after the checks and, for anything that actuates, stores or quotes, an independent review.
- **`dev` is the integration and release branch;** `main` follows it for release. `world/main` is frozen as the record of the world.
- **No PRs between our own branches:** the review workflows on PRs are paid. Merge locally, then push.
- Commit or push only when asked. Never change the git config.

## Commands

The venv lives outside OneDrive, one per worktree (a borrowed one makes pyright see two copies of the packages). Set it in every shell:

```bash
export UV_PROJECT_ENVIRONMENT="C:/Users/hussa/dev/venvs/<worktree>"
uv sync --all-packages --all-extras

# the checks (CI runs the same: .github/workflows/world-ci.yml)
uv run ruff check packages scripts
uv run ruff format --check packages scripts
uv run pyright packages
uv run pytest -W error::RuntimeWarning
uv run pytest -m eval                          # golden evals on RC, a few minutes
uv lock --check
uv run python scripts/video/storyboard_check.py
cd apps/dashboard && npm ci && npm run build && npm run lint && npm test

# running it (details in packages/agent/README.md and packages/world/AGENTS.md)
uv run spotcool-agent run --vision blob --dry-run
uv run spotcool-world run --plant rc
uv run spotcool-world report <run_id>
```

`/code-review`, `/security-review` and `/explain <module>` are in `.claude/commands/`. Cap automated fix rounds at 3; then stop and rethink.

## Gotchas

- **One BACnet client and socket per device.** With BACpypes3 0.0.110, requests in flight to a device that doesn't answer leave a shared socket deaf for good. The agent's BMS client is 1910 on UDP 47820, its VRF client 1911 on 47821; one request at a time each.
- **Start the agent before the world.** The world never waits for the agent.
- **The Meta key lives outside the repo** (`C:\Users\hussa\dev\secrets\spotcool-agent.env`, passed with `--env-file`). Never open, print, copy or commit it, or any `.env` file. A folder under `packages/` would also break the uv workspace.
- **Never store frames or audio.** They stay in memory; logs hold lengths, never words. An audit-hook test enforces it.
- **The agent never reads `/ws/events`** or any ground-truth field; it connects to `/ws/agent` only.
- **Numbers:** quote only what `docs/release/numbers.md` lists, with its wording, and nothing from its never-quote list. Only a run whose report says `Quotable` is SpotCool's own; a stub run is labelled an upper bound with perfect perception; RC figures are never quoted.
- **SAM sees nobody in the world's synthetic silhouettes.** Live runs need real footage (`SPOTCOOL_WORLD_FOOTAGE_DIR`, the MEVA clips); CI and the evals use the blob counter (`--vision blob`).
- **Speed:** with SAM, run the world at speed 60 or slower. At speed 300 the blob counter needs `--settle-s 0.3`.
- **Keep the PC on one network for a run:** the BMS binds its IPv4 address, and a Codespace port forward dies with the network.
- **Stop programs with Ctrl+C,** never by closing the window: that's how the agent releases its points and the world stops BOPTEST's tests.
