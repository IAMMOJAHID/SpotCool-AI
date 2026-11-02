"""10: The whole world in one run, scored without Person A.

The world `spotcool-world run --stub-agent` runs, built the same way (runner/assembly.py): the
hybrid Tuesday on two RC plants, SpotCool's and the baseline's, behind the BACnet BMS, with the
cameras, the room calendar, the occupants, the recorder and the dashboard server, all on one bus.

The stub agent stands in for Person A's agent. It is a real BACnet/IP client of the BMS on its own
UDP port, but its perception is the scenario's ground truth, which A's agent never sees: empty
zones get OCC-SENSOR = Unoccupied at priority 10 (the BMS then puts them on standby), booked
rooms are pre-cooled, and its heartbeat keeps the BMS's watchdog quiet.

It runs as fast as it can, on 127.0.0.1 only (no firewall prompt), records into a temporary
folder and prints the KPIs, in about 10 s: the day itself takes 2 s, then the occupants wait for
the 14:15 complaint to fail, since no agent listens for it. Keep runs elsewhere with
SPOTCOOL_WORLD_RUN_DIR=<dir>.

Run: uv run python packages/world/examples/foundational/10_full_world.py
"""

import asyncio
import os
import sys
import tempfile
from pathlib import Path

from loguru import logger

from spotcool_world.processors.kpi import RUNS
from spotcool_world.runner.assembly import World
from spotcool_world.runner.report import load_run
from spotcool_world.services import WorldSettings


async def main() -> None:
    logger.remove()
    logger.add(sys.stderr, level="WARNING", diagnose=False)
    run_dir = Path(os.environ.get("SPOTCOOL_WORLD_RUN_DIR") or tempfile.mkdtemp(prefix="spotcool-"))
    settings = WorldSettings(
        plant="rc",
        scenario="hybrid_tuesday",
        speed=None,
        run_dir=run_dir,
        http_host="127.0.0.1",
        bms_address="127.0.0.1",
    )
    world = World(settings, stub_agent=True)
    print(f"run {world.run_id}: {world.compiled.name}, two RC plants, the stub agent")
    for label, url in world.urls().items():
        print(f"  {label:<13} {url}")
    await world.run()

    recorded = load_run(run_dir, world.run_id)
    print(
        f"{recorded.manifest.ended} ({recorded.manifest.end_reason}), in {run_dir / world.run_id}"
    )
    for run in RUNS:
        kpi = recorded.manifest.final_kpis[run]
        energy = recorded.samples[run][-1].summary.energy_kwh
        saved = "" if kpi.energy_saved_pct is None else f", saved {kpi.energy_saved_pct:.1f} %"
        comfort = kpi.comfort_where_people_kh or 0.0
        print(f"  {run:<9} {energy:6.1f} kWh, comfort where people were {comfort:.2f} K h{saved}")
    print(f"replay: SPOTCOOL_WORLD_RUN_DIR={run_dir} uv run spotcool-world replay {world.run_id}")
    print(f"report: SPOTCOOL_WORLD_RUN_DIR={run_dir} uv run spotcool-world report {world.run_id}")


if __name__ == "__main__":
    asyncio.run(main())
