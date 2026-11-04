# spotcool-agent

SpotCool's agent. Each BMS step it snapshots the five zone cameras, counts people with SAM 3.1
(or, offline, a colour-blob counter), and drives each zone's occupancy sensor and cooling
setpoint over BACnet/IP at priority 10. It publishes its perception, decisions and explanations
to the dashboard. With `--vrf` the meeting room's VRF louvers follow the person on the live
phone camera, and complaints on `/chat` and `/voice` are transcribed, understood and acted on
in the meeting room, then answered in plain words. The engineering guide is
[`AGENTS.md`](AGENTS.md).

## Install

From the repo root, with the venv outside OneDrive:

```bash
export UV_PROJECT_ENVIRONMENT="C:/Users/hussa/dev/venvs/spotcool-ai"
uv sync --all-packages --all-extras
```

## Run

Start the agent first, then the world without its stub (`spotcool-world run --plant rc`):

```bash
uv run spotcool-agent run --vision blob --dry-run      # print the resolved settings, key masked
uv run spotcool-agent run --vision blob                # synthetic frames, no Meta calls
uv run spotcool-agent run --vision meta --env-file C:/Users/hussa/dev/secrets/spotcool-agent.env
```

`--world-host` (this machine's primary IPv4 address, where the world's BMS binds by default;
`127.0.0.1` for a world on loopback), `--bms-port` (47808), `--http-port` (8081),
`--dashboard-port` (8090), `--listen` (127.0.0.1:8002), `--sam-call-cap` and `--settle-s`
override the settings; every setting also reads `SPOTCOOL_AGENT_<NAME>`. Ctrl+C releases every
slot the agent holds.

`--stt meta|fake` and `--llm meta|fake|off` choose the transcriber and the language model;
both default to Meta's with `--vision meta`, and to the offline `fake` and `off` otherwise.
They share a budget of their own, `--llm-call-cap` (200), apart from the building's
`--sam-call-cap`. `--stt fake` hears nothing: a voice message is asked to be typed. With
`--llm meta`, muse-wonder also narrates each building step that wrote, in one sentence, as an
explanation.

`--scene meta|fake|off` chooses the scene reader (`muse-spark-1.3`): Meta's with
`--vision meta`, `off` otherwise. It notes each occupied zone's people and open doors every 4
steps, as explanations only, and with `--vrf` sets the meeting room's comfort setpoint (Person
A's PMV 0, on `IDU-SP`) every 60 s while people are seen; `--no-comfort` turns that off. Its
budget is its own, `--scene-call-cap` (300). None of it changes a BMS write.

`--render meta|fake|off` chooses the privacy renderer (`muse-image-1.0`). It is `off` by
default, whatever `--vision` says: each render takes about 24 s. With it on, every 180 s the
agent redraws the five zone cameras and the meeting room's, one at a time, as illustrations
(Person A's prompt asks for faceless figures; nothing verifies a render has none), keeps the
latest of each in memory and serves it at `GET /privacy/<camera>.jpg` on its own server (404
before the first; a new run clears them). The dashboard shows them
with `?privacy=on&agent=http://127.0.0.1:8002`. Its budget is its own, `--render-call-cap`
(60); at the cap it stops and the last renders stay. A render is an illustration, display
only: nothing the agent decides reads it, and it never changes a BMS or VRF write. It can be
up to about 180 s old, under live boxes and counts.

Every Meta request logs a `META ->` line as it is sent and a `META <-` (answered) or `META x`
(failed) line as it ends, for the demo's terminal: the model, the endpoint, the request id, the
rate limit left, and sizes, boxes and labels, never an image, audio or words. `--no-meta-trace`
(`SPOTCOOL_AGENT_META_TRACE=false`) turns them off; the offline providers log none.

```text
META -> sam-3.1 | POST https://api.meta.ai/v1/responses | count humans on cam-cor | in: JPEG 1280x720, 52 KB + text "humans" (include_confidence)
META <- sam-3.1 | 200 in 1.87 s | request-id 2bf0123a-... | response resp_6aeb... | out: 2 boxes [(785,376)-(905,691) c=0.93, ...] -> kept 2 (c >= 0.30) | rate limit 58/60 left
META x  sam-3.1 | timeout after 3.5 s | count humans on cam-sou
META -> muse-spark-1.3 | POST https://api.meta.ai/v1/responses | read the scene on cam-meeting-live | in: JPEG 1280x720, 52 KB + prompt 1350 characters
META <- muse-spark-1.3 | 200 in 10.40 s | request-id ... | out: 2 people (trousers_long_sleeve 2), doors open 1, windows open 0, sunlight none, office
META -> muse-image-1.0 | POST https://api.meta.ai/v1/images/edits | privacy render of cam-sou | in: JPEG 1280x720, 52 KB + prompt 324 characters
META <- muse-image-1.0 | 200 in 24.00 s | request-id ... | out: <format> <width>x<height>, <size> KB
```

## The louvers demo

The meeting room's VRF unit, its louvers aimed at whoever the phone camera sees, and voice and
chat complaints acting on it. Start the agent first:

```bash
uv run spotcool-agent run --vrf --vision meta --stt meta --llm meta --env-file C:/Users/hussa/dev/secrets/spotcool-agent.env
```

then the world, with the phone as the live camera and the Mitsubishi profile (both louver
axes):

```bash
export SPOTCOOL_WORLD_LIVE_CAMERA_URL="http://<phone-ip>:8080/shot.jpg"
export SPOTCOOL_WORLD_VRF_PROFILE=mitsubishi
uv run spotcool-world run --plant rc
```

The agent talks to the gateway through a BACnet client of its own (UDP `--vrf-bacnet-port`,
47821; the BMS's is 47820), learns the brand from the gateway's `Model_Name`, asking every 5 s
until it answers (`--vrf-brand` only if a gateway names a brand it does not know), watches
`cam-meeting-live` (`--vrf-camera`), and takes the phone's pose from
`--vrf-camera-height-m` (2.8), `--vrf-camera-tilt-deg` (30) and `--vrf-camera-hfov-deg` (90),
with the unit right under it; pass `--vrf-mirror` when the phone faces the unit. The vanes are
released after `--vrf-hold-s` (10) with nobody seen; a draft complaint levels the vertical vane
for `--draft-hold-s` (600); a setpoint or fan change from a complaint lasts `--complaint-hold-s`
(900). Every VRF write is at priority 10, at most once a second per point, and Ctrl+C releases
them all, after the BMS's. Its SAM calls have a budget of their own (`--vrf-call-cap`, 3000,
about 50 minutes); when it runs out the vanes are released and the building carries on. Keep
`--vrf` off for a scored run.

## Probe

Snapshot every zone camera once, count people, then transcribe a second of tone and ask the
language model for one word; print the counts, the answers' lengths and the latencies:

```bash
uv run spotcool-agent probe --vision meta --env-file C:/Users/hussa/dev/secrets/spotcool-agent.env
```

`uv run spotcool-agent version` prints the agent's and the contracts' versions.
