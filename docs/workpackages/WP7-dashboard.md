# WP7: Dashboard app

**Tool:** Claude Code. **Branch:** `wp/wp7-dashboard`, cut from `world/main`. The lead syncs the trunk in, including WP6 and WP11, before the build starts.
**Depends on:**
- contracts (the event payloads, generated as TypeScript types);
- WP6's recording format and its `/ws/events` stream;
- WP4's camera catalog and MJPEG.

Build against the mock feed first. The real feed comes from a WP11 run.

## Goal
One screen that tells the whole story to a judge watching a video. The same app has a dense "operator" view and a clean "video mode".

## Your lane (only these paths)
- `apps/dashboard/` (the whole app, its scripts, fixtures, tests, `.gitignore` and changelog notes).

Nothing in `packages/` changes. If the world's feed or an endpoint is missing something, stop and report it.

## Stack
All runtime dependencies are MIT-licensed. Versions are pinned exactly in `package.json` (no `^` or `~`) and locked by `package-lock.json`. Node 24 and npm 11 are installed.
- Vite, React and TypeScript (strict);
- Chart.js with react-chartjs-2;
- ESLint and Vitest;
- Playwright, using the **installed Microsoft Edge** (`channel: "msedge"`). Do not download Playwright browsers; disk is limited.

## Decisions already made (binding)
1. **Events.** Read `ws://<world>:8090/ws/events`, WP6's **read-only** broadcast of every `EventEnvelope`, from the world and from Person A's agent.
   - `<world>` is `location.host` when WP6's dashboard server serves the app (production).
   - In development, use the `?world=host:port` query parameter (default `127.0.0.1:8090`).
   - Reconnect with backoff; show the connection state in the header.
2. **Types** are generated from the contracts, never written by hand. `npm run gen:types` does two things:
   1. runs `uv run python ../../packages/contracts/scripts/export_schemas.py src/contracts/schemas`;
   2. runs `json-schema-to-typescript`.

   The generated output is git-ignored, and `npm run build` runs `gen:types` first.
3. **Cameras.**
   - The camera list comes from `GET http://<world-host>:8081/cameras`.
   - Each tile is an `<img>` on `/cameras/{id}/mjpeg`; WP4 uses the boundary `frame`, and an `<img>` needs no CORS.
   - If a stream fails, fall back to polling `snapshot.jpg` once a second.
   - The live phone tile appears only when `cam-meeting-live` is in the catalog.
   - The agent's mask polygons are drawn from `perception` events when present.
4. **Ground truth discipline.**
   - People icons on the floor plan come from the agent's **`perception`** events.
   - The script's true counts (from `scenario_state` and `camera_scene`) appear only in operator view, labelled "script". Video mode never shows them.
5. **Agent panel.**
   - Show the `decision` and `explanation` events.
   - The chat box posts `ComplaintText` to `{agent}/chat` with `source: "occupant"`.
   - Hold-to-talk records with Web Audio, resamples to 16 kHz mono s16le WAV (`VOICE_FORMAT`), and posts multipart (`audio`, `meta`) to `{agent}/voice`. The recording stays in memory and goes only to the agent, never to storage.
   - `{agent}` comes from `?agent=` or `VITE_AGENT_URL`. Person A's agent must allow CORS from the dashboard's origin; the lead has put that in the Person A handoff.
   - With no agent configured, the panel says so instead of failing.
6. **Mock feed.**
   - `scripts/mock-feed.mjs` replays a WP6 recording's `{"kind": "event", "envelope": …}` lines (raw records are skipped) over a local WebSocket, at a chosen speed, with gaps scaled from `sim_time_s`.
   - Without a file, it plays a committed synthetic fixture, `fixtures/sample-events.jsonl`. The fixture is small, made by a script in `scripts/`, and covers every event type, including agent ones.
7. **Production serving.** WP6's dashboard server serves `apps/dashboard/dist` at `/`. Build with relative asset paths.
8. **VRF panel.** The AC unit follows `vrf_state` (WP3 comes later; its decisions are in `WP3-vrf-gateway.md` and the Person A handoff's §6). The fixture and mock feed include `vrf_state` events, so the panel is built and tested now.
   - `vrf_state` arrives up to 5 times a second while the unit changes, and every 5 s otherwise. Interpolate the angles between events.
   - `vane_h_pos` and `vane_h_angle_deg` are `None` when the brand has no horizontal louver (Daikin and LG): don't draw that louver. The fixture includes a Mitsubishi state and an LG one.
   - The VRF's writes appear in the BACnet log as `bms_write` events with `device: "VRF-Gateway-MeetingRoom"`.
9. **Energy is measured per step (WP6.1).**
   - `plant_measurements.power_kw` and `total_power_kw` are each meter's **mean over the step that just ended**. Draw them as steps over that step: Chart.js `stepped: "after"`, which draws each point's value over the interval before it. Never draw a line between end-of-step samples. (This first said `"before"`, which in Chart.js holds a value over the *following* interval and drew every mean one step late; the review caught it.)
   - Cumulative energy is `plant_measurements.energy_kwh`, per run. Never integrate power in the browser.
   - The energy-saved card shows the SpotCool run's `kpi.energy_saved_pct`, tagged "provisional" while `kpi.provisional` is true.

## Panels
- **Header:** simulated clock and day, run status, plant (BOPTEST local or hosted, RC, or replay), scenario, connection state.
- **Floor plan:**
  - an SVG of the 5-zone floor (core in the centre; north, south, east and west around it);
  - fill colour from zone temperature on a cool-to-warm scale;
  - setpoint badges;
  - people icons from perception (decision 4).
- **Camera wall:** five zone tiles plus the live phone tile (decision 3).
- **BACnet log:** `bms_write` rows with time, device, object, priority, value (NULL means release), client, and accepted or rejected with the reason.
- **Energy:** live total power for SpotCool vs. baseline, and cumulative kWh.
- **KPI cards:**
  - energy saved (%);
  - comfort where people were (K·h) for both runs;
  - BOPTEST `ener_tot` and `tdis_tot` when final;
  - a "provisional" tag while the run is in progress.
- **Timeline:** door, meeting and complaint markers, with a playhead. A complaint shows as scheduled, then delivered or failed, from the two `complaint_cue` events.
- **AC unit:** an SVG wall unit whose vertical and horizontal louvers follow the `vrf_state` angles, interpolated at 60 fps, with the brand profile shown.
- **Agent panel:** decision 5.

## Amendments by the lead (binding)
- **BOPTEST's own KPIs:**
  - video mode never shows `tdis_tot` or `idis_tot`;
  - the operator view groups BOPTEST's own KPIs under "BOPTEST's own KPIs", captioned "BOPTEST's schedule counts every zone as occupied all day, so its discomfort includes zones the scenario leaves empty."
- **Stub runs:** when the run's agent events say `stub: true`, the KPI cards show "Stub agent: upper bound with perfect perception" in both modes.
- **Agent POSTs** send `X-SpotCool-Client: dashboard`. The browser then preflights them, so no audio or text reaches an agent that hasn't allowed CORS.
- **Local addresses only:** `?agent=`, `?world=`, `?cameras=` and `VITE_AGENT_URL` accept only loopback, RFC 1918, link-local and the page's own host. Video mode shows the agent's host next to hold-to-talk.
- **Reconnects:** every new connection clears the run's state. The world's `/ws/events` replays the run so far to each new client (`EventFeed`), so a late or refreshed page shows the whole run once.

## Video mode (`?mode=video`)
- A fixed 1920×1080 layout.
- **Type:** no visible text below 22 px. Panel titles and the headline numbers (energy saved, clock) are 28 px or more. (Amended after the review: 28 px everywhere doesn't fit the panels.)
- No developer panels.
- The bottom 12% kept clear for captions.
- A high-contrast palette that reads on YouTube compression.
- No scrollbars.

## Tests
- **Vitest:** event reducers (every `EventType`, out-of-order `t_ms`, the two-event complaint), the WAV encoder (header and sample format), and formatting helpers.
- **Playwright with Edge:** screenshot tests of operator and video modes fed by the mock feed, plus a smoke test that every panel populates within 5 s.
- `npm run build`, `npm run lint` and `npm test` must be clean.

## Done when
- With the mock feed, every panel is live, and video mode matches the screenshots.
- Against a real WP11 run, the floor plan, BACnet log, energy and KPIs update in real time. The lead checks this at integration.
- `apps/dashboard/README.md` gives the commands: install, gen:types, dev with the mock feed, build, test.
