# spotcool-world

the simulated building for SpotCool AI: everything the agent connects to when there is no
real building. It is built the way [pipecat](https://github.com/pipecat-ai/pipecat) builds voice
agents: frames flow through processors, a pipeline orders one tick of work, the two building
plants step in parallel, workers run on a bus and a runner owns their lifecycle.

WP0 provides the core framework (frames, processors, pipelines, workers, bus, runner, clock,
observers, settings and the service and transport base classes). WP1 adds the plants: BOPTEST
(hosted or local), an RC model and replay, behind one `PlantService`. BACnet devices, cameras,
scenarios and telemetry arrive in later work packages as providers under those bases.

```bash
export UV_PROJECT_ENVIRONMENT="C:/Users/hussa/dev/venvs/spotcool-ai"   # venv lives outside OneDrive
uv sync --all-packages
uv run python packages/world/examples/foundational/01_clock.py
uv run spotcool-world run --dry-run                                     # print resolved settings
uv run pytest packages/world
```

Start with `examples/foundational/` (one concept per file) and read [AGENTS.md](AGENTS.md)
before changing code.
