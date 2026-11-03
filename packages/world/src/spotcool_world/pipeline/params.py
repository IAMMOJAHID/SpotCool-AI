"""Parameters of one pipeline run."""

import math
import uuid

from pydantic import BaseModel, ConfigDict, Field

from spotcool_world.clocks import SimClock
from spotcool_world.frames import StartFrame
from spotcool_world.observers.base_observer import BaseObserver


class PipelineParams(BaseModel):
    """How a ``PipelineWorker`` runs its pipeline.

    The run, scenario, plant, speed and end time only fill the ``StartFrame`` the worker builds
    when none is queued; the start time and step come from ``clock``.

    Parameters:
        observers: Observers notified of every push.
        fatal_error_cancels: Cancel the run when a fatal ``ErrorFrame`` reaches the worker.
        clock: The run's simulation clock. Pass the same clock to the ``TickSource`` so every
            processor sees the time the ticks advance.
        run_id: Run identifier.
        scenario: Scenario name.
        plant: Plant provider name.
        speed: Simulation seconds per wall second, or ``None`` for as fast as possible.
        end_time_s: Simulation time the run ends; infinity means "until an ``EndFrame``".
    """

    model_config = ConfigDict(arbitrary_types_allowed=True)

    observers: list[BaseObserver] = Field(default_factory=list)
    fatal_error_cancels: bool = True
    clock: SimClock = Field(default_factory=lambda: SimClock(0.0, 900.0))
    run_id: str = Field(default_factory=lambda: uuid.uuid4().hex[:12])
    scenario: str = ""
    plant: str = ""
    speed: float | None = None
    end_time_s: float = math.inf

    def start_frame(self) -> StartFrame:
        """Build the ``StartFrame`` these parameters describe, starting at ``clock.now_s``."""
        return StartFrame(
            run_id=self.run_id,
            scenario=self.scenario,
            plant=self.plant,
            step_s=self.clock.step_s,
            speed=self.speed,
            start_time_s=self.clock.now_s,
            end_time_s=self.end_time_s,
            sim_time_s=self.clock.now_s,
        )
