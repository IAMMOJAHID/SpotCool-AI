"""06: The building's BMS on BACnet/IP, as the agent sees it.

The world's BMS is a real BACnet/IP device, SpotCool-Office-BMS (instance 1001), built from
the office_bms point map. This example serves it against the RC plant and, after every
15-minute step, reads a few points with a BACnet client, as SpotCool's agent would. After the
first step the client marks the South zone empty: it writes ZN-SOU-OCC-SENSOR = Unoccupied at
priority 10, and the BMS stamps the write 07:15. The 07:15 tick comes too soon after it, so at
the 07:30 tick, 15 minutes on, the BMS puts South in occupied-standby on its own: it raises
South's cooling setpoint by 0.5 K and shuts the zone's airflow off while it sits within its
setpoints, letting it back on 0.5 K beyond them.

By default it runs the whole occupied day (07:00 to 19:00, a step every 3 s), so you can
browse the device with YABE meanwhile (packages/world/AGENTS.md has the steps); Ctrl+C stops
it. The device binds this machine's primary IPv4 address, UDP 47808, unless told otherwise.

Run: uv run python packages/world/examples/foundational/06_bacnet_device.py --ticks 4
"""

import argparse
import asyncio
import sys

from bacpypes3.ipv4.app import NormalApplication
from bacpypes3.local.device import DeviceObject
from bacpypes3.pdu import Address, IPv4Address
from loguru import logger

from spotcool_contracts import load_point_map
from spotcool_contracts.priorities import AGENT_PRIORITY
from spotcool_world.clocks import SimClock, WallPacer
from spotcool_world.frames import Frame, TickFrame
from spotcool_world.pipeline import ParallelPipeline, Pipeline, PipelineParams
from spotcool_world.processors import FrameDirection, FrameProcessor, PlantProcessor, TickSource
from spotcool_world.runner import WorkerRunner
from spotcool_world.services.rc import RcPlant
from spotcool_world.transports.bacnet.bms import BacnetBmsTransport
from spotcool_world.workers import PipelineWorker

START_S = 198 * 86_400 + 7 * 3600  # 18 July, 07:00
STEP_S = 900.0
MAP = load_point_map("office_bms")


def object_id(name: str) -> str:
    return MAP.by_name(name).object_id


class AgentLike(FrameProcessor):
    """After every step, reads the BMS over BACnet; after the first, marks South empty."""

    def __init__(self, client: NormalApplication, device: Address) -> None:
        super().__init__(name="agent-like")
        self._client = client
        self._device = device

    async def process_frame(self, frame: Frame, direction: FrameDirection) -> None:
        await super().process_frame(frame, direction)
        if isinstance(frame, TickFrame):
            await self._report()
            if frame.index == 0:
                await self._client.write_property(
                    self._device,
                    object_id("ZN-SOU-OCC-SENSOR"),
                    "present-value",
                    "inactive",  # Unoccupied
                    priority=AGENT_PRIORITY,
                )
                print("       the client writes ZN-SOU-OCC-SENSOR = Unoccupied at priority 10")
        await self.push_frame(frame, direction)

    async def _report(self) -> None:
        names = ("SIM-CLOCK", "ZN-COR-T", "ZN-SOU-T", "ZN-SOU-OCC-MODE", "ZN-SOU-CLG-SP")
        names += ("ZN-SOU-SA-FLOW", "HVAC-TOTAL-PWR")
        values = [
            await self._client.read_property(self._device, object_id(name), "present-value")
            for name in names
        ]
        sim_time_s, core_c, south_c, mode, setpoint_c, airflow_lps, hvac_kw = values
        south_mode = (MAP.by_name("ZN-SOU-OCC-MODE").states or [])[mode - 1]
        print(
            f"{self.clock.clock_text(sim_time_s)}  core {core_c:5.2f} degC   "
            f"south {south_c:5.2f} degC, {south_mode:<10} setpoint {setpoint_c:4.1f} degC, "
            f"airflow {airflow_lps:5.1f} L/s   HVAC {hvac_kw:5.2f} kW"
        )


async def main(args: argparse.Namespace) -> int:
    logger.remove()
    logger.add(sys.stderr, level="WARNING")  # keep the table readable
    bms = BacnetBmsTransport(address=args.address, port=args.port)
    try:
        await bms.start()
    except OSError as exc:
        print(f"cannot bind the BMS: {exc}. Another BACnet app on this port? Try --port 47809.")
        return 1
    assert bms.address is not None
    host = bms.address.split(":")[0]
    client = NormalApplication(
        DeviceObject(
            objectIdentifier=("device", 1999), objectName="06-client", vendorIdentifier=999
        ),
        IPv4Address(f"{host}:0"),  # any free port
    )
    print(
        f"{MAP.device_name} ({MAP.device_instance}) serves {len(MAP.points)} points on {bms.address}"
    )
    try:
        clock = SimClock(START_S, STEP_S)
        end_s = START_S + args.ticks * STEP_S
        plants = ParallelPipeline(
            [PlantProcessor(RcPlant(), "spotcool")], [PlantProcessor(RcPlant(), "baseline")]
        )
        pipeline = Pipeline(
            [
                TickSource(clock, WallPacer(args.speed or None), end_s),
                bms.input(),
                plants,
                bms.output(),
                AgentLike(client, Address(bms.address)),
            ]
        )
        params = PipelineParams(clock=clock, plant="rc", end_time_s=end_s)
        await WorkerRunner().run(PipelineWorker(pipeline, params=params))
    finally:
        client.close()
        await bms.stop()
    return 0


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="The building's BMS on BACnet/IP.")
    parser.add_argument("--ticks", type=int, default=48, help="15-minute steps to run (48: a day)")
    parser.add_argument(
        "--speed", type=float, default=300.0, help="sim seconds per wall second; 0: unpaced"
    )
    parser.add_argument("--address", help="IPv4 address to bind (default: the primary one)")
    parser.add_argument("--port", type=int, default=47808, help="UDP port (default 47808)")
    sys.exit(asyncio.run(main(parser.parse_args())))
