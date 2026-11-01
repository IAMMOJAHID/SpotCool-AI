"""01: Simulation time vs. wall time.

The world runs on simulation time, BOPTEST's clock: seconds since 1 January 00:00 of a non-leap
year. The wall clock only sets how fast the ticks come. Here twelve 15-minute steps of 18 July
(day 199) take about 2.4 wall seconds, because the pacer runs at 4500 sim-seconds per second.

Run: uv run python packages/world/examples/foundational/01_clock.py
"""

import asyncio
import time

from spotcool_world.clocks import SimClock, WallPacer

DAY_S = 86_400


async def main() -> None:
    clock = SimClock(start_time_s=198 * DAY_S + 7 * 3600, step_s=900.0)  # day 199, 07:00
    pacer = WallPacer(speed=900.0 / 0.2)  # one 15-minute step every 0.2 wall seconds
    began = time.perf_counter()
    while clock.index < 12:
        now = clock.now_s
        wall = time.perf_counter() - began
        print(
            f"tick {clock.index:2d}  day {clock.day_of_year(now)}  {clock.clock_text(now)}"
            f"  (wall {wall:4.1f} s)"
        )
        await pacer.pace(clock.step_s)
        clock.advance()
    print(f"3 simulated hours took {time.perf_counter() - began:.1f} wall seconds")


if __name__ == "__main__":
    asyncio.run(main())
