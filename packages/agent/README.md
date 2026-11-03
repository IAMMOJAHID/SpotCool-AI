# spotcool-agent

SpotCool's agent. Each BMS step it snapshots the five zone cameras, counts people with SAM 3.1
(or, offline, a colour-blob counter), and drives each zone's occupancy sensor and cooling
setpoint over BACnet/IP at priority 10. It publishes its perception, decisions and explanations
to the dashboard, and acknowledges complaints on `/chat` and `/voice`. The engineering guide is
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

## Probe

Snapshot every zone camera once, count people, print the counts and the latency:

```bash
uv run spotcool-agent probe --vision meta --env-file C:/Users/hussa/dev/secrets/spotcool-agent.env
```

`uv run spotcool-agent version` prints the agent's and the contracts' versions.
