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

The agent learns the brand from the gateway's `Model_Name` (`--vrf-brand` if a gateway does not
say), watches `cam-meeting-live` (`--vrf-camera`), and takes the phone's pose from
`--vrf-camera-height-m` (2.8), `--vrf-camera-tilt-deg` (30) and `--vrf-camera-hfov-deg` (90),
with the unit right under it; pass `--vrf-mirror` when the phone faces the unit. The vanes are
released after `--vrf-hold-s` (10) with nobody seen; a draft complaint levels the vertical vane
for `--draft-hold-s` (600); a setpoint or fan change from a complaint lasts `--complaint-hold-s`
(900). Every VRF write is at priority 10, at most once a second per point, and Ctrl+C releases
them all. Keep `--vrf` off for a scored run; with it on, raise `--sam-call-cap` for a demo longer
than about ten minutes.

## Probe

Snapshot every zone camera once, count people, then transcribe a second of tone and ask the
language model for one word; print the counts, the answers' lengths and the latencies:

```bash
uv run spotcool-agent probe --vision meta --env-file C:/Users/hussa/dev/secrets/spotcool-agent.env
```

`uv run spotcool-agent version` prints the agent's and the contracts' versions.
