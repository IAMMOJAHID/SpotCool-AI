# Changelog

## Unreleased

- `?panels=a,b,c` shows only those panels, filling the window, and `?masks=off` the camera tiles
  without SAM's boxes and counts: the live quad take's 960×540 OBS sources.
- An RC run shows no saving, comfort figure or kWh, only the RC note; its power stays.
- The fixture's run is a BOPTEST run (`boptest-local`), so the tests still see a saving.

## 0.2.0

- The power chart draws each step's mean over its own step (`stepped: "after"`), not the next.
- Video mode fits 1920×1080 with no text under 22 px, titles and headline numbers at 28 px or
  more, and long texts cut short on purpose; camera counts sit on their frames.
- Every new connection starts from a clear screen (the world now sends each new viewer the run
  so far), so a reconnect shows no row twice.
- The chat box and hold-to-talk send `X-SpotCool-Client: dashboard`, so nothing reaches an agent
  that does not allow this page by CORS.
- `?world=`, `?cameras=`, `?agent=` and `VITE_AGENT_URL` take only addresses on this machine or
  network, or the page's own host; messages over 1 MB are dropped.
- Malformed world events are dropped and counted; each panel has an error boundary.
- The KPI cards label a stub run's results as an upper bound with perfect perception.
- The operator view draws the newest 50 BACnet rows and agent items, memoizes the panels and
  contains each panel's layout and paint.
- `npm run gen:types` clears its output first.

## 0.1.0

- The dashboard app (WP7): operator view and 1920×1080 video mode over WP6's `/ws/events`,
  with types generated from the contracts, a mock feed with a synthetic fixture, Vitest and
  Playwright (Edge) tests.
