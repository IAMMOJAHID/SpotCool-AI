"""04: Two plants side by side, and the same answer on every run.

The world runs two copies of the office: one driven by SpotCool, one left on its own controls
(the baseline), so the energy saving can be scored. ParallelPipeline hands each tick to both
plants at once. Here the SpotCool plant is the slower one, yet its result always comes out
first: branch outputs leave in branch order, then the tick itself, exactly once.

Run: uv run python packages/world/examples/foundational/04_parallel_pipeline.py
"""

import asyncio

from spotcool_contracts.events import Run
from spotcool_world.clocks import SimClock, WallPacer
from spotcool_world.frames import Frame, PlantMeasurementsFrame, TickFrame
from spotcool_world.pipeline import ParallelPipeline, Pipeline, PipelineParams
from spotcool_world.processors import FrameDirection, FrameProcessor, TickSource
from spotcool_world.runner import WorkerRunner
from spotcool_world.workers import PipelineWorker

START_S = 198 * 86_400 + 9 * 3600  # 18 July, 09:00


class FakePlant(FrameProcessor):
    """Stands in for a BOPTEST plant: a slow "HTTP call", then measurements."""

    def __init__(self, run: Run, power_kw: float, delay_s: float) -> None:
        super().__init__(name=f"plant-{run}")
        self._run: Run = run
        self._power_kw = power_kw
        self._delay_s = delay_s

    async def process_frame(self, frame: Frame, direction: FrameDirection) -> None:
        await super().process_frame(frame, direction)
        if isinstance(frame, TickFrame):
            await asyncio.sleep(self._delay_s)
            raw = {"res_PHvac_y": self._power_kw * 1000}  # BOPTEST reports watts
            end_s = (frame.sim_time_s or 0) + frame.step_s  # measured at the end of the step
            await self.push_frame(PlantMeasurementsFrame(run=self._run, raw=raw, sim_time_s=end_s))
        await self.push_frame(frame, direction)


class Printer(FrameProcessor):
    async def process_frame(self, frame: Frame, direction: FrameDirection) -> None:
        await super().process_frame(frame, direction)
        if isinstance(frame, PlantMeasurementsFrame):
            print(f"  {frame.run:<9} {frame.raw['res_PHvac_y'] / 1000:5.1f} kW")
        elif isinstance(frame, TickFrame):
            print(f"{self.clock.clock_text(frame.sim_time_s or 0)} tick {frame.index} done")
        await self.push_frame(frame, direction)


async def main() -> None:
    clock = SimClock(START_S, step_s=900.0)
    plants = ParallelPipeline(
        [FakePlant("spotcool", power_kw=31.0, delay_s=0.05)],  # slower, listed first
        [FakePlant("baseline", power_kw=42.0, delay_s=0.0)],
    )
    end_s = START_S + 4 * 900
    pipeline = Pipeline([TickSource(clock, WallPacer(None), end_s), plants, Printer()])
    params = PipelineParams(clock=clock, scenario="toy", plant="fake", end_time_s=end_s)
    await WorkerRunner().run(PipelineWorker(pipeline, params=params))


if __name__ == "__main__":
    asyncio.run(main())
