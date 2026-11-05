# SpotCool dashboard

The judges' dashboard (WP7): one screen for a run of the SpotCool world, drawn from the world's
event stream. It has a dense operator view and a fixed 1920×1080 video mode for the demo video.

Panels: header (simulated clock and day, run status, plant, scenario, connection), floor plan,
camera wall, BACnet log, power and energy, KPI cards, timeline, the meeting-room AC unit and the
agent panel (decisions, explanations, a chat box and hold-to-talk to SpotCool's agent). The
operator view adds the world's errors and metrics, and the script's true counts, labelled
"script".

## Commands

Run everything in `apps/dashboard`. Node 24 and npm 11; `gen:types` also needs `uv` and the
repo's Python workspace (set `UV_PROJECT_ENVIRONMENT` as in `packages/world/AGENTS.md`).

```bash
npm ci                      # install the pinned dependencies
npm run gen:types           # clears src/contracts, then contracts -> schemas (JSON Schema) -> types (TypeScript)
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
  starts the mock feed on `127.0.0.1:41731` and `vite preview` on `127.0.0.1:41730`; the agent
  tests start a stand-in agent on a free port. Screenshot baselines (`e2e/__screenshots__`) are
  Edge on Windows at half device scale, and the tests also check the key numbers as text; update
  the baselines with `npx playwright test --update-snapshots=all -g screenshot`. `e2e/video.spec.ts`
  checks that in video mode nothing is cut off and no text is under 22 px, on the fixture and on
  the longest texts the world and the agent send.

## Where it connects

| Query parameter | Default | Meaning |
|---|---|---|
| `world=host:port` | the page's host (served by the world); `127.0.0.1:8090` under `npm run dev` | WP6's dashboard server: `ws://<world>/ws/events` |
| `cameras=host:port` | The world's host, port 8081 | WP4's camera server: `/cameras`, `/cameras/{id}/mjpeg`, `/cameras/{id}/snapshot.jpg` |
| `agent=http://host:port` | `VITE_AGENT_URL`, else none | SpotCool's agent: `POST /chat` and `POST /voice` |
| `mode=video` | operator view | the 1920×1080 video layout |
| `panels=a,b,c` | every panel | only those panels, filling the window (such as a 960×540 OBS source): `header`, `floorplan`, `cameras`, `kpis`, `energy`, `timeline`, `bacnet`, `acunit`, `agent`, `errors` |
| `masks=off` | boxes drawn | the camera tiles without SAM's boxes and counts: the raw feeds |

`world`, `cameras` and `agent` (`VITE_AGENT_URL` too) must be on this machine (loopback), a
private RFC 1918 or link-local address (IPv4 or IPv6), or the page's own host; anything else is
reported on the page and not used, so a crafted link cannot send complaints or audio elsewhere.
Both POSTs carry `X-SpotCool-Client: dashboard`, so the browser asks the agent first (a CORS
preflight): the agent must allow this page's origin, `POST` and that header, and until it does
no text or audio leaves the page. Video mode shows the agent's host next to hold-to-talk.

In production `spotcool-world run` serves `dist/` at `/` on its dashboard port (8090), so
`http://<world>:8090/` needs no parameters; `?mode=video&agent=http://<agent>:8002` for a take.
When the world runs on other ports, pass `?cameras=` (the dashboard server does not tell the app
where the camera server is).

## Behaviour worth knowing

- **An RC run shows no saving.** RC is our own test model, and none of its figures is quoted
  (`docs/release/numbers.md`): on a run whose plant is `rc`, the KPI cards and the energy chart
  show "RC test plant: a model for testing, no saving is quoted" in place of the saving, the
  comfort figures and the kWh; the power lines and kW stay. BOPTEST and replayed runs are
  unchanged. The fixture is a BOPTEST run (`boptest-local`), so the tests see a saving.
- **One reducer** (`src/state/reducer.ts`) folds every envelope into the state. World state keeps
  the latest sim time, wall-clock state (VRF, perception) the latest `t_ms`, and lists are kept in
  `t_ms` order. A `tick` closes its step, so the clock shows the step's end. Every new connection
  starts from a clear screen, because the world sends each new viewer the run so far (and the
  mock feed replays from the start), and a new run id clears it too.
- **Robust to bad input:** a message over 1 MB, or a world event whose data lacks what the panels
  read (`src/state/guards.ts`), is dropped and counted in the operator view. Each panel has an
  error boundary: one that fails to draw says so, and the others carry on.
- **Energy** is drawn as each step's mean power over that step, and cumulative energy is the
  world's `energy_kwh`; nothing is integrated in the browser. A point is stamped at the end of
  its step, so the chart uses Chart.js's `stepped: "after"`, which draws a point's value over
  the interval before it.
- **Video mode** is a fixed 1920×1080 frame with the bottom 130 px clear for captions. No visible
  text is under 22 px; panel titles, the clock and energy saved are 28 px or more. Long texts end
  in an ellipsis or are clamped to their lines.
- **Load:** each panel gets only its slice of the state and is memoized, the BACnet log and the
  agent feed draw their newest 50 rows, and each panel contains its own layout and paint. The VRF
  unit's 5 Hz state, eased in at 60 fps, re-draws only the AC unit.
- **Ground truth:** people on the floor plan and the camera tiles come from the agent's
  `perception` (`people: {zone: n}`; masks from `masks: {camera_id: [[[x, y], ...], ...]}` in the
  camera's frame pixels). The script's counts and scenes appear only in the operator view,
  labelled "script". Agent events with `stub: true` carry a "stub" tag wherever they appear.
- **KPIs:** video mode shows energy saved and comfort where people were. BOPTEST's own KPIs
  appear only in the operator view, under a caption: BOPTEST's schedule counts every zone as
  occupied all day. On a stub run, the KPI cards say "Stub agent: upper bound with perfect
  perception", as the world's quote verdict does.
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
