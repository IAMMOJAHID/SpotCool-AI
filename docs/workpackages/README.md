# Person B work packages

Person B's half of SpotCool is the **world**: the simulated building that the agent connects to, and the demo video. It's split into work packages (WPs) so that many AI agents can build it at the same time without stepping on each other.

- **One WP = one agent = one git worktree = one branch** (`wp/<id>-<slug>`).
- The lead (Person B with the main Claude Code session) owns `packages/contracts/`, integration and merges.
- Every agent follows [`packages/world/AGENTS.md`](../../packages/world/AGENTS.md).

## Dependency graph

```
WP0 core + contracts (lands first)
 ├─► WP1 plants ─────────────┐
 ├─► WP2 BMS (BACnet) ───────┤
 ├─► WP3 VRF gateway ────────┤
 ├─► WP4 cameras ◄── WP8 ────┤
 ├─► WP5 scenarios ──────────┼─► WP11 assembly (spotcool-world run) ─► WP10 evals + release
 ├─► WP6 telemetry/recorder ─┤                                    └─► WP9 video
 └─► WP7 dashboard (mock feed first) ┘
WP8 footage: no code dependency, starts immediately (people + scripts)
```

| WP | Brief | Tool | Branch |
|---|---|---|---|
| WP0 | (in progress) core framework + contracts | Claude Code (lead) | `wp/wp0-core-contracts` |
| WP1 | [plants](WP1-plants.md) | Claude Code | `wp/wp1-plants` |
| WP2 | [BMS over BACnet](WP2-bms-bacnet.md) | Claude Code | `wp/wp2-bms-bacnet` |
| WP3 | [VRF gateway](WP3-vrf-gateway.md) | Claude Code | `wp/wp3-vrf-gateway` |
| WP4 | [cameras](WP4-cameras.md) | Antigravity | `wp/wp4-cameras` |
| WP5 | [scenarios, calendar, occupants](WP5-scenarios.md) | Antigravity | `wp/wp5-scenarios` |
| WP6 | [telemetry, KPIs, recorder, dashboard feed](WP6-telemetry.md) | Claude Code | `wp/wp6-telemetry` |
| WP7 | [dashboard app](WP7-dashboard.md) | Antigravity | `wp/wp7-dashboard` |
| WP8 | [footage pipeline](WP8-footage.md) | Claude Code + people | `wp/wp8-footage` |
| WP9 | [video production](WP9-video.md) | Antigravity + people | `wp/wp9-video` |
| WP10 | [evals, CI, docs, release](WP10-evals-release.md) | Claude Code | `wp/wp10-evals-release` |
| WP11 | [world assembly and CLI](WP11-assembly.md) | Claude Code (lead) | `wp/wp11-assembly` |

## Starting a WP (copy-paste)

Worktrees live **outside OneDrive**, so syncing doesn't lock files mid-build. Each worktree gets its own virtualenv; uv hardlinks packages from its cache, so extra venvs cost little disk.

```bash
WP=wp1-plants                                   # change per WP
git -C "C:/Users/hussa/dev/SpotCool-AI" worktree add \
    "C:/Users/hussa/dev/wt/$WP" -b "wp/$WP" wp/wp0-core-contracts
cd "C:/Users/hussa/dev/wt/$WP"
export UV_PROJECT_ENVIRONMENT="C:/Users/hussa/dev/venvs/$WP"
uv sync --all-packages              # add --extra <name> for the extras your WP needs
```

Then give the agent its brief:
- **Claude Code:** "Read `docs/workpackages/WPn-*.md` and `packages/world/AGENTS.md`, then implement the brief. Don't commit until all checks pass. Don't push."
- **Antigravity:** open the worktree folder and paste the same instruction.

## Rules for every WP

1. **Stay in your lane.** Only touch the directories your brief lists. A change to `packages/contracts/` goes to the lead as a request, never as an edit.
2. **Contract first.** Code against `spotcool_contracts` and the WP0 base classes. Mock the other WPs at their interfaces, not inside them.
3. **Definition of done:**
   - green `uv run ruff check packages`, `uv run ruff format --check packages`, `uv run pyright packages` and `uv run pytest packages`;
   - your example runs;
   - a changelog fragment in `packages/world/changelog/`;
   - the brief's own checks pass.
4. **Review:** a reviewer from the *other* tool reviews every WP before merge. Claude Code reviews Antigravity's work with `/code-review`; Antigravity reviews Claude Code's.
5. **Never store camera frames or audio on disk** (AGENTS.md Law 6). **Always stop BOPTEST tests** you start.
6. **Disk is tight.** Don't install heavy optional dependencies outside your WP's extras, and keep media out of the repo (`.gitignore` covers it).

## Facts every WP can rely on (verified Nov 1)

- **BOPTEST office test case** `multizone_office_simple_air` (BOPTEST master `a29024f`):
  - 5 zones (cor, nor, sou, eas, wes), 1,662.66 m², Chicago weather.
  - Scenario `peak_cool_day` is centred on day 199 (about 18 July).
  - The full input and output catalog is `packages/contracts/src/spotcool_contracts/data/boptest_multizone_office_simple_air.json`.
  - BOPTEST's ElectricPower KPI sums `chi_reaPChi_y`, `chi_reaPPumDis_y`, `heaPum_reaPHeaPum_y`, `heaPum_reaPPumDis_y`, `hvac_reaAhu_PFanSup_y`, `hvac_reaAhu_PPumCoo_y` and `hvac_reaAhu_PPumHea_y` (all in W).
- **Hosted BOPTEST** (`http://api.boptest.net`, plain HTTP: the HTTPS certificate expired 2026-10-26):
  - `/version` and `/testcases` answer.
  - `POST /testcases/<name>/select` queued past a 300 s timeout on Nov 1, and again past a 900 s timeout, so the hosted service can't be relied on.
  - Local Docker (`C:/Users/hussa/dev/boptest`, master branch) and GitHub Codespaces are the real paths.
- **The service rejects an empty body sent as `Content-Type: application/json`** (HTTP 500 "Unexpected end of JSON input"). Send the header only when there is a body.
