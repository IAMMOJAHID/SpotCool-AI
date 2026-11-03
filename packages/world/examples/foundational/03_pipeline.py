"""03: A pipeline is the order of work in one tick.

TickSource pushes one TickFrame per simulation step, and each tick flows through every
processor before the next one starts (lockstep). Here a toy scenario says how many people sit
in the Core zone, and a printer shows it. PipelineWorker runs the pipeline from StartFrame to
EndFrame; WorkerRunner runs the worker (Ctrl+C stops it cleanly); StepTimingObserver measures
how long each tick took. Three simulated hours take about 3 wall seconds.

Run: uv run python packages/world/examples/foundational/03_pipeline.py
"""

import asyncio

from spotcool_contracts import Zone
from spotcool_contracts.events import ScenarioStateData, ZoneScene
from spotcool_world.clocks import SimClock, WallPacer
from spotcool_world.frames import Frame, ScenarioStateFrame, TickFrame
from spotcool_world.observers import StepTimingObserver
from spotcool_world.pipeline import Pipeline, PipelineParams
from spotcool_world.processors import FrameDirection, FrameProcessor, TickSource
from spotcool_world.runner import WorkerRunner
from spotcool_world.workers import PipelineWorker

DAY_S = 86_400
START_S = 198 * DAY_S + 7 * 3600  # 18 July, 07:00
ARRIVAL_S = 198 * DAY_S + 8.5 * 3600  # people arrive at 08:30


class ToyScenario(FrameProcessor):
    """On every tick, says who is in the Core zone (the real scenario engine is WP5).

    The state goes out before the tick, so processors further down already know who is where
    when the tick reaches them.
    """

    async def process_frame(self, frame: Frame, direction: FrameDirection) -> None:
        await super().process_frame(frame, direction)
        if isinstance(frame, TickFrame):
            people = 4 if (frame.sim_time_s or 0) >= ARRIVAL_S else 0
            scene = ZoneScene(
                zone=Zone.COR, scene="core_many" if people else "core_empty", people=people
            )
            state = ScenarioStateData(zones=[scene])
            await self.push_frame(ScenarioStateFrame(state=state, sim_time_s=frame.sim_time_s))
        await self.push_frame(frame, direction)


class Printer(FrameProcessor):
    async def process_frame(self, frame: Frame, direction: FrameDirection) -> None:
        await super().process_frame(frame, direction)
        if isinstance(frame, ScenarioStateFrame):
            core = frame.state.zones[0]
            print(f"{self.clock.clock_text(frame.sim_time_s or 0)}  Core: {core.people} people")
        await self.push_frame(frame, direction)


async def main() -> None:
    clock = SimClock(START_S, step_s=900.0)
    end_s = START_S + 3 * 3600
    pacer = WallPacer(speed=900.0 / 0.25)  # a 15-minute step every 0.25 s
    pipeline = Pipeline([TickSource(clock, pacer, end_s), ToyScenario(), Printer()])
    timing = StepTimingObserver()
    params = PipelineParams(
        clock=clock, observers=[timing], scenario="toy", plant="none", end_time_s=end_s
    )
    await WorkerRunner().run(PipelineWorker(pipeline, params=params))
    mean_us, max_us = (timing.mean_s or 0) * 1e6, (timing.max_s or 0) * 1e6
    print(f"{timing.count} ticks; work per tick: mean {mean_us:.0f} us, max {max_us:.0f} us")


if __name__ == "__main__":
    asyncio.run(main())
