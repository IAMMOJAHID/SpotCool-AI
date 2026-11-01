# WP7: Dashboard app

**Tool:** Antigravity (its browser agent can check the UI). **Branch:** `wp/wp7-dashboard`. **Depends on:** contracts. Build against a mock feed first; the real one comes from WP6.
**Directories:** `apps/dashboard/` only.

## Goal
One screen that tells the whole story to a judge watching a video. The same app has a dense "operator" view and a clean "video mode".

## Stack
All MIT-licensed, with versions pinned in `package.json` and locked by `package-lock.json`. Node 24 is installed.
- Vite + React + TypeScript;
- Chart.js with react-chartjs-2;
- ESLint and Vitest;
- Playwright, using the **installed Microsoft Edge** (`channel: "msedge"`). Do not download Playwright browsers; disk is tight.

## Data
- **Live:** `ws://127.0.0.1:8090/ws/events`, a stream of `EventEnvelope` JSON from both the world and the agent.
- **Cameras:**
  - images from `http://127.0.0.1:8081/cameras/{id}/snapshot.jpg` (refresh about 1/s), or `/mjpeg` if WP4 ships it;
  - the camera list from `GET /cameras`.
- **Agent:** `POST {agent}/chat` (JSON `ComplaintText`) and `POST {agent}/voice` (16 kHz mono WAV recorded with Web Audio). The agent's base URL comes from a query parameter or `.env`.
- **Types:** generated from the contracts, never written by hand. The `npm run gen:types` script:
  1. runs `uv run python ../../packages/contracts/scripts/export_schemas.py src/contracts/schemas`;
  2. then runs `json-schema-to-typescript`.

  The generated output is git-ignored.
- **Development:** `scripts/mock-feed.mjs` replays a sample `events.jsonl` (from WP6, or one the script synthesizes) over a local WebSocket at a chosen speed.

## Panels
- **Header:** simulated clock and day, run status, plant (BOPTEST, local or hosted, or RC), scenario.
- **Floor plan:**
  - an SVG of the DOE medium-office floor (core in the centre; north, south, east and west around it);
  - fill colour from zone temperature on a cool-to-warm scale;
  - setpoint badges;
  - people icons from the **agent's perception** events. Ground-truth counts appear only in operator view, labelled "script".
- **Camera wall:** five zone tiles plus the live phone tile. The agent's mask polygons are drawn from `perception` events when present.
- **BACnet log:** `bms_write` rows: time, device, object, priority, value (NULL means release), client, and accepted or rejected with the reason.
- **Energy:** live total power for SpotCool vs. baseline, and cumulative kWh.
- **KPI cards:**
  - energy saved (%);
  - comfort where people were (K·h) for both runs;
  - BOPTEST `ener_tot` and `tdis_tot` when final;
  - a "provisional" tag while the run is in progress.
- **Timeline:** door, meeting and complaint markers, with a playhead.
- **AC unit:** an SVG wall unit whose vertical and horizontal louvers follow `vrf_state` angles, interpolated at 60 fps. It shows the brand profile.
- **Agent panel:** `decision` and `explanation` events, the chat box and a hold-to-talk mic.

## Video mode (`?mode=video`)
- A fixed 1920×1080 layout.
- 28 px base type.
- No developer panels.
- The bottom 12% kept clear for captions.
- A high-contrast palette that reads on YouTube compression.
- No scrollbars.

## Tests
- **Vitest:** event reducers (every `EventType`, out-of-order `t_ms`) and formatting helpers.
- **Playwright with Edge:** screenshot tests of operator and video modes fed by the mock feed, plus a smoke test that panels populate within 5 s.
- `npm run build` and `npm run lint` must be clean.

## Done when
- With the mock feed, every panel is live.
- Video mode matches the screenshots.
- Against a real world run (WP11), the floor plan, BACnet log, energy and KPIs update in real time.
