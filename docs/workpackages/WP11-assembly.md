# WP11: World assembly and CLI (`spotcool-world run`)

**Tool:** Claude Code (lead-supervised). **Branch:** `wp/wp11-assembly`. **Depends on:** WP1–WP6, merged in as they land. Stub any WP that's late at its interface, so the assembly never blocks.
**Directories:** `packages/world/src/spotcool_world/runner/{assembly.py,cli.py}`, `examples/foundational/09_full_world.py`, `tests/assembly/`, `world.example.yaml`.

## Goal
One command brings up the whole stand-in building from `WorldSettings` / `world.yaml`.

## Pipeline (plan §2.2), in this exact order
```
TickSource
 → ScenarioProcessor                       (WP5)
 → bms.input()                             (WP2: agent writes → SetpointCommandFrame)
 → ParallelPipeline[PlantProcessor(spotcool) ║ PlantProcessor(baseline)]   (WP1)
 → bms.output()                            (WP2: measurements → BACnet points)
 → CameraSceneProcessor                    (WP4)
 → TelemetryProcessor → KpiProcessor       (WP6)
 → EventEmitterProcessor → RecorderProcessor → BusPublisher   (WP6, WP0)
```

**Started alongside it under `WorkerRunner`:**
- the pipeline worker;
- `VrfWorker` (WP3);
- `OccupantWorker` (WP5);
- the HTTP server (cameras and calendar, WP4/WP5);
- `DashboardServer` (WP6).

**Shutdown:** Ctrl+C stops everything gracefully, and **stops both BOPTEST tests**.

## CLI
- **`spotcool-world run`.** Flags: `[--config world.yaml] [--plant rc|boptest-local|boptest-hosted] [--scenario hybrid_tuesday] [--speed 300] [--run-id auto]`.
  - It prints the URLs at start-up: dashboard, camera catalog, calendar, BMS address and VRF address.
- **`spotcool-world replay <run_id> [--speed]`.** Re-publishes a recorded run's events to the bus and dashboard at the given speed. There's no physics and no BACnet; it exists for video takes.
- **Also wired here:** `probe` (WP1), `lint` (WP5) and `report` (WP6).

## Tests
- **Build:** assembly builds with every plant provider, using fakes for the network-bound ones.
- **End to end on RC:** 2 simulated hours at maximum speed with the WP10 stub agent (or a fake writer) finish cleanly. The recorder writes a manifest, the KPIs are emitted, and the BOPTEST stop hook is called for the BOPTEST plants (with a fake).
- **Ctrl+C:** a simulated SIGINT mid-run stops cleanly with no orphaned tasks.

## Done when
- `uv run spotcool-world run --plant rc --speed 0` completes `hybrid_tuesday` and prints the KPIs.
- `09_full_world.py` does the same in under a minute.
- With `--plant boptest-local`, the same run completes against local Docker BOPTEST.
