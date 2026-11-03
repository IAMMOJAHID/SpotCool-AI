"""02: Push a frame, transform it, pass it on.

A FrameProcessor receives frames in process_frame() and passes frames on with push_frame().
CelsiusToKelvin turns a setpoint command written in BMS units (degC) into BOPTEST units (K);
every other frame passes through untouched. run_test() runs the processor inside a real
pipeline and returns the frames that came out of it.

Run: uv run python packages/world/examples/foundational/02_frames_and_processors.py
"""

import asyncio

from spotcool_contracts.points import Conversion
from spotcool_world.adapters.units import to_boptest
from spotcool_world.frames import DataFrame, Frame, SetpointCommandFrame
from spotcool_world.processors import FrameDirection, FrameProcessor
from spotcool_world.testing import run_test


class CelsiusToKelvin(FrameProcessor):
    async def process_frame(self, frame: Frame, direction: FrameDirection) -> None:
        await super().process_frame(frame, direction)  # lifecycle first (StartFrame, CancelFrame)
        if isinstance(frame, SetpointCommandFrame):
            kelvin = {
                name: None if value is None else to_boptest(value, Conversion.KELVIN_TO_CELSIUS)
                for name, value in frame.overrides.items()
            }
            frame = SetpointCommandFrame(
                run=frame.run, overrides=kelvin, sim_time_s=frame.sim_time_s
            )
        await self.push_frame(frame, direction)  # pass it on, in the direction it came


async def main() -> None:
    command = SetpointCommandFrame(
        run="spotcool",
        overrides={
            "hvac_oveZonSupCor_TZonCooSet_u": 24.0,  # agent wrote 24 degC
            "hvac_oveZonSupNor_TZonCooSet_u": None,  # agent released the point
        },
        sim_time_s=198 * 86_400 + 9 * 3600,
    )
    other = DataFrame()
    down, up = await run_test(CelsiusToKelvin(), frames_to_send=[command, other])
    print(f"in : {command.name} {command.overrides}")
    for frame in down:
        detail = frame.overrides if isinstance(frame, SetpointCommandFrame) else "(untouched)"
        print(f"out: {frame.name} {detail}")
    print(f"upstream frames: {len(up)}")


if __name__ == "__main__":
    asyncio.run(main())
