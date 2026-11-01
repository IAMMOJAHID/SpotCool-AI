# WP10: Golden evals, CI, documentation, release

**Tool:** Claude Code. **Branch:** `wp/wp10-evals-release`. **Depends on:** WP1–WP7 and WP11 (it starts with the stub agent once WP2 and WP11 exist).
**Directories:** `packages/world/evals/`, `.github/workflows/`, root `README.md`, `CREDITS.md`, `docs/runbooks/`.

## 1. Stub agent (`packages/world/evals/stub_agent.py`)
A tiny BACpypes3 client that drives the world the way Person A's agent will, so the world can be tested end to end without A.
- **Occupancy input:** it uses the scenario's **ground-truth** people count from the bus. Say so in the docstring: it tests the world's plumbing and physics, not perception.
- **Policy, per zone:**
  - occupied: release `CLG-SP` (NULL at priority 10) and write `OCC-SENSOR` = Occupied;
  - empty for 5 minutes or more: `OCC-SENSOR` = Unoccupied (the BMS applies G36 standby itself);
  - empty for 30 minutes or more: `CLG-SP` = 26.0 at priority 10;
  - empty for 60 minutes or more: `CLG-SP` = 28.0.
- **Heartbeat:** toggles every 30 s.
- **Operator rule:** never writes over an active priority-8 slot. It reads `priorityArray` first.

## 2. Golden evals (`packages/world/evals/test_golden.py`, marker `eval`)
Each eval runs the assembled world (WP11) with the stub agent on the **RC** plant in CI, and on **BOPTEST** with `-m "eval and live"`.

**Assertions:**
- **`hybrid_tuesday`:**
  - SpotCool energy saved is above 5% to start with. Recalibrate the band after the first BOPTEST run and record why.
  - Comfort where people were is ≤ 1.0 K·h per zone.
- **`all_hands_day`:** savings are below 2% in absolute value. This is the honesty check.
- **`empty_friday`:** savings are greater than `hybrid_tuesday`'s.
- **Operator override:** a scripted operator write of `ZN-SOU-CLG-SP` = 23.0 at priority 8, made mid-run, is never overwritten, and the stub logs that it backed off.
- **Replay:** replaying the recorded run reproduces the same KPIs.

## 3. CI (GitHub Actions; free for public repos)
- Extend `world-ci.yml` with:
  - `evals` (RC only);
  - `scenarios` (`spotcool-world lint` on the library);
  - `dashboard` (`npm ci`, `npm run build`, `vitest`; no Playwright in CI).
- Cache the uv and npm stores.

## 4. Documentation
- **Root `README.md`, rewritten for the new architecture:** both halves, a Mermaid diagram, how to run the world, and links to Person A's docs and the video.
- **`docs/runbooks/`:**
  - `boptest-local.md` (Docker Desktop, compose services, ports, disk needed);
  - `boptest-codespaces.md` (create, `docker compose up`, `gh codespace ports forward`, stopping it to save quota);
  - `yabe.md` (browsing, priority-array demo);
  - `phone-camera.md` (IP Webcam, hotspot, firewall);
  - `windows-firewall.md` (UDP 47808–47810, TCP 8081/8090).
- **`CREDITS.md`, consolidated:** software licences (BOPTEST BSD-3, BACpypes3 MIT, BAC0 LGPL-3, YABE MIT, FastAPI MIT, OpenCV Apache-2.0, React MIT, Chart.js MIT, HyperFrames Apache-2.0, OBS GPL-2, ffmpeg LGPL/GPL, Kokoro Apache-2.0), plus WP8's media rows and contributors.

## 5. Security and release
- Run `/security-review` on the branch and fix anything it finds.
- Confirm that `.env` files, footage, audio and runs are not tracked (`git ls-files` check in CI).
- Confirm that no frames or audio are written by any test (the privacy tests in WP4, WP5 and WP6).
- **Release (only with the user's go-ahead):**
  1. merge the WP branches into `feature/spotcool-dev`;
  2. PR to `dev`, then `main`;
  3. tag `submission-2026-11-04`.

## Done when
- CI is green on the release branch, with the evals passing on RC and once on BOPTEST.
- The README and runbooks are current.
- `CREDITS.md` is complete.
- The security review is clean.
