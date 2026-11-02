# SpotCool dashboard

The judges' dashboard (WP7): one screen for a run of the SpotCool world, drawn from the world's
event stream. It has a dense operator view and a fixed 1920×1080 video mode for the demo video.

Panels: header (simulated clock and day, run status, plant, scenario, connection), floor plan,
camera wall, BACnet log, power and energy, KPI cards, timeline, the meeting-room AC unit and the
agent panel (decisions, explanations, a chat box and hold-to-talk to Person A's agent). The
operator view adds the world's errors and metrics, and the script's true counts, labelled
"script".

## Commands

Run everything in `apps/dashboard`. Node 24 and npm 11; `gen:types` also needs `uv` and the
repo's Python workspace (set `UV_PROJECT_ENVIRONMENT` as in `packages/world/AGENTS.md`).

```bash
npm ci                      # install the pinned dependencies
npm run gen:types           # contracts -> src/contracts/schemas (JSON Schema) -> src/contracts/types (TypeScript)
npm run mock                # mock feed on 127.0.0.1:8790, playing fixtures/sample-events.jsonl at speed 300
npm run dev                 # Vite on http://localhost:5173
#   open http://localhost:5173/?world=127.0.0.1:8790&cameras=127.0.0.1:8790
npm run build               # gen:types, type check, then dist/ (relative asset paths)
npm run lint                # ESLint (type-aware)
npm test                    # Vitest
npm run test:e2e            # Playwright on the installed Microsoft Edge; run `npm run build` first
```

- **Mock feed:** `node scripts/mock-feed.mjs [recording.jsonl] [--port 8790] [--host 127.0.0.1]
  [--speed 300]` replays a WP6 recording's event records (raw records are skipped) to each
  client from the start, with gaps scaled from `sim_time_s` (`--speed 0`: no gaps). Without a
  file it plays the fixture. It also serves a stand-in camera catalog and plain generated frames,
  so the camera wall is live.
- **Fixture:** `npm run fixture` rewrites `fixtures/sample-events.jsonl` from
  `scripts/make-fixture.ts`: a synthetic day with every event type, the stub-labelled agent's
  included. A Vitest test fails if the committed file is stale.
- **Playwright** never downloads browsers (`PLAYWRIGHT_SKIP_BROWSER_DOWNLOAD=1` for installs). It
  starts the mock feed on `127.0.0.1:41731` and `vite preview` on `127.0.0.1:41730`. Screenshot
  baselines (`e2e/__screenshots__`) are Edge on Windows at half device scale; update them with
  `npx playwright test --update-snapshots=all -g screenshot`.

## Where it connects

| Query parameter | Default | Meaning |
|---|---|---|
| `world=host:port` | the page's host (served by the world); `127.0.0.1:8090` under `npm run dev` | WP6's dashboard server: `ws://<world>/ws/events` |
| `cameras=host:port` | the world's host, port 8081 | WP4's camera server: `/cameras`, `/cameras/{id}/mjpeg`, `/cameras/{id}/snapshot.jpg` |
| `agent=http://host:port` | `VITE_AGENT_URL`, else none | Person A's agent: `POST /chat` and `POST /voice` (it must allow CORS from this page) |
| `mode=video` | operator view | the 1920×1080 video layout |

In production `spotcool-world run` serves `dist/` at `/` on its dashboard port (8090), so
`http://<world>:8090/` needs no parameters; `?mode=video&agent=http://<agent>:8002` for a take.
When the world runs on other ports, pass `?cameras=` (the dashboard server does not tell the app
where the camera server is).

## Behaviour worth knowing

- **One reducer** (`src/state/reducer.ts`) folds every envelope into the state. World state keeps
  the latest sim time, wall-clock state (VRF, perception) the latest `t_ms`, and lists are kept in
  `t_ms` order. A `tick` closes its step, so the clock shows the step's end. A new run id, or a
  new connection after a run ended, clears the screen.
- **Energy** is drawn as each step's mean power over that step (`stepped: "before"`), and
  cumulative energy is the world's `energy_kwh`; nothing is integrated in the browser.
- **Ground truth:** people on the floor plan and the camera tiles come from the agent's
  `perception` (`people: {zone: n}`; masks from `masks: {camera_id: [[[x, y], ...], ...]}` in the
  camera's frame pixels). The script's counts and scenes appear only in the operator view,
  labelled "script". Agent events with `stub: true` carry a "stub" tag wherever they appear.
- **KPIs:** video mode shows energy saved and comfort where people were. BOPTEST's own KPIs
  appear only in the operator view, under a caption: BOPTEST's schedule counts every zone as
  occupied all day.
- **Law 6:** camera frames are `<img>` streams (MJPEG, or snapshots once a second if a stream
  fails) and the hold-to-talk recording stays in memory until its one POST to the agent. Nothing
  uses browser storage, caches or a service worker (a Vitest test checks the sources).
- **Hold-to-talk:** hold the button, or Space or Enter while it has focus. The browser records,
  Web Audio decodes and resamples to 16 kHz, and the app sends a mono s16le WAV (`VOICE_FORMAT`)
  as multipart `audio` plus `meta` (a `ComplaintText`, `source: "occupant"`).

## Dependency licences

Runtime (bundled into `dist/`), all MIT:

| Package | Version | Licence |
|---|---|---|
| react | 19.3.1 | MIT |
| react-dom | 19.3.1 | MIT |
| scheduler (react-dom) | 0.28.0 | MIT |
| chart.js | 4.6.1 | MIT |
| @kurkle/color (chart.js) | 0.3.4 | MIT |
| react-chartjs-2 | 5.3.1 | MIT |

Development only: Vite, @vitejs/plugin-react, TypeScript (Apache-2.0), ESLint, typescript-eslint,
eslint-plugin-react-hooks, globals, Vitest, json-schema-to-typescript, ws (the mock feed) and
@playwright/test (Apache-2.0); the rest MIT.
