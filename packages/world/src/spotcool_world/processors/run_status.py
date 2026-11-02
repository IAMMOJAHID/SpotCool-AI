"""RunStatusProcessor: the run's lifecycle for the dashboard: running, then finished or failed."""

from typing import Literal

from spotcool_contracts.events import RunStatusData
from spotcool_world.frames import (
    CancelFrame,
    EndFrame,
    ErrorFrame,
    Frame,
    RunStatusFrame,
    StartFrame,
)
from spotcool_world.processors.frame_processor import FrameDirection, FrameProcessor


class RunStatusProcessor(FrameProcessor):
    """Pushes a ``RunStatusFrame`` as the run starts and as it ends; every frame passes on.

    - After passing a ``StartFrame`` on: ``running``, unless a fatal error came up meanwhile.
    - Before passing an ``EndFrame`` on: ``finished``.
    - Before passing a downstream ``CancelFrame`` on: ``failed`` with the message of the first
      fatal ``ErrorFrame`` it saw pass upstream, or else ``cancelled`` with the cancel's reason
      (a stop or Ctrl+C is not a failure). A run that did not reach its end is never
      ``finished``.

    Only a run's first end frame is reported. Place it right after the ``TickSource``: every
    other processor's errors then pass it on their way up, and the emitter turns its frames into
    ``run_status`` events.

    Args:
        run_id: The run.
        plant: The plant provider.
        scenario: The scenario's name.
        name: Display name.
    """

    def __init__(self, run_id: str, plant: str, scenario: str, *, name: str | None = None) -> None:
        super().__init__(name=name)
        self._run_id = run_id
        self._plant = plant
        self._scenario = scenario
        self._fatal: str | None = None
        self._ended = False

    async def process_frame(self, frame: Frame, direction: FrameDirection) -> None:
        """Report the run's start after its ``StartFrame`` and its end before its end frame."""
        await super().process_frame(frame, direction)
        if direction is FrameDirection.UPSTREAM:
            if isinstance(frame, ErrorFrame) and frame.fatal and self._fatal is None:
                self._fatal = frame.message
        elif isinstance(frame, StartFrame):
            self._fatal, self._ended = None, False
            await self.push_frame(frame, direction)
            if self._fatal is None:
                await self._report("running", None, frame.start_time_s)
            return
        elif isinstance(frame, EndFrame | CancelFrame) and not self._ended:
            self._ended = True
            t_s = self.clock.now_s if frame.sim_time_s is None else frame.sim_time_s
            if isinstance(frame, EndFrame):
                await self._report("finished", None, t_s)
            elif self._fatal is not None:
                await self._report("failed", self._fatal, t_s)
            else:
                await self._report("cancelled", frame.reason or None, t_s)
        await self.push_frame(frame, direction)

    async def _report(
        self,
        status: Literal["running", "finished", "cancelled", "failed"],
        message: str | None,
        t_s: float,
    ) -> None:
        data = RunStatusData(
            run_id=self._run_id,
            status=status,
            plant=self._plant,
            scenario=self._scenario,
            message=message,
        )
        await self.push_frame(RunStatusFrame(data=data, sim_time_s=t_s))
