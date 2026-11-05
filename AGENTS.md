# AGENTS.md: the rules every agent shares

Every contributor and coding agent working anywhere in this repo follows these rules. Each package's own guide adds to them: [`packages/world/AGENTS.md`](packages/world/AGENTS.md) for the simulated building, the dashboard and the video tools, and [`packages/agent/AGENTS.md`](packages/agent/AGENTS.md) for SpotCool's agent.

**SpotCool** watches each zone of an office through its existing CCTV with Meta's SAM 3.1 and drives the building's BACnet BMS: empty zones ease off, occupied zones are released to the building, booked rooms are pre-cooled. In the meeting room, the VRF louvers follow a person on a live camera, and complaints adjust that unit. In simulation it saved 19.94 % HVAC energy against the baseline (`docs/release/numbers.md`).

## 1. Privacy (Law 6)

- **Frames and audio live in memory only.** Nothing in `spotcool_agent` writes a file; the world serves snapshots from memory. An audit-hook test proves it for the agent. The Meta providers send them to the Meta AI API and nowhere else; the offline providers send nothing.
- **Logs carry lengths, types and decisions, never content:** no JPEG bytes, no audio, no complaint's words, no traceback locals (`diagnose=False`).
- **Footage, audio, recorded runs and dataset archives stay outside the repo.** `.gitignore` lists them, and CI's hygiene job fails on any that is tracked.
- **Keys live outside the repo** and reach the agent through `--env-file`. Never open, print, copy or commit a key or any `.env` file; `.env.example` is the only template.

## 2. Honesty

- **Every figure** in the README, the video, a slide or a doc comes from [`docs/release/numbers.md`](docs/release/numbers.md), with its wording and its label. A figure not there is not shown. Nothing on its never-quote list is ever quoted.
- **SpotCool's own figure** comes only from a run whose report says `Quotable`. The report's `quote_blockers` say why any other run isn't, and nobody works around them.
- **Labels:** a stub-agent run is "upper bound with perfect perception (stub agent, in simulation)"; the agent's run is "SpotCool saved (SAM 3.1 perception, in simulation)". RC figures are never quoted: RC is our own reduced model, for testing the world.
- **Say the caveats with the figure:** in simulation; BOPTEST's occupant heat follows its own schedule; the cameras played MEVA dataset clips; only occupied or empty is scored, never exact counts.
- **The comfort cost is shown,** not hidden.

## 3. Safety (actuation)

- **The agent writes at BACnet priority 10 only,** and only the zones' occupancy sensors and cooling setpoints and its heartbeat on the BMS, and the meeting room's VRF points. It never writes plant points.
- **The operator always wins.** Before a write, read the point's priority array; if any slot 1 to 9 holds a value, back off and say so. Operators write at 8.
- **Never release a slot that isn't the agent's.** Releases (NULL at 10) don't back off, because they can't override a more urgent slot.
- **The heartbeat:** toggle `SPOTCOOL-HEARTBEAT` every 30 s. If it stops for 120 s, the BMS releases everything the agent wrote, and the run can't be quoted.
- **Release on stop:** at 19:00, on a normal stop, on Ctrl+C and on a crash. The BMS's zones and heartbeat are released first, then the VRF's slots, within 5 s.
- **Model calls stay out of the BMS actuation path.** SAM's counts feed a fixed policy; no language model decides a BMS write. Complaints act on the meeting room's VRF only.
- **One BACnet client and socket per device,** one request at a time.

## 4. Decoupling

- **The agent never imports `spotcool_world`** at runtime; a subprocess test checks it. Its tests and evals may use the world to run on loopback.
- **Only [`packages/contracts`](packages/contracts/README.md) crosses the line** between the agent and the world. Changing a contract bumps its version and needs both sides' agreement.
- **The agent perceives through the cameras like any building's agent.** It connects to `/ws/agent` only, never `/ws/events`, and never reads ground-truth fields (`ZoneScene.people`, `scenario_state`, `camera_scene`).
- **Swap the world for real hardware, and the agent's code doesn't change.** Anything that breaks that is a bug.

## 5. The gate: how work lands

1. **A brief** with the binding decisions, written before the code.
2. **One builder,** one agent at a time, on its own branch and worktree, with its own venv outside OneDrive.
3. **The lead's checks** and a scope check (the commands are in [`docs/DEVELOPMENT.md`](docs/DEVELOPMENT.md) and the package guides).
4. **An independent reviewer** for anything that actuates, touches privacy or quotes a figure.
5. **One fix round;** never more than 3. A third failed round means stop and rethink the design.
6. **Merge:** sync with the trunk (`agent/main`), update the docs, push, branch CI, a `--no-ff` merge, a tree-identity check (`git diff --quiet <branch> HEAD`), then trunk CI. No PRs between our own branches. `dev` is the integration and release branch.
7. **Record it:** the commit message and the package guides say what changed and why.

## 6. Git

- Commit only on the work item's branch, and only when asked. Never change the global git config, and never skip hooks.
- Write text files with LF line endings (`.gitattributes` normalizes them).
