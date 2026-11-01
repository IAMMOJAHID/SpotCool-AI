"""ScenarioProcessor: what the scenario says happens in each step, pushed ahead of the tick."""

from loguru import logger

from spotcool_contracts.calendar import ScheduleItem
from spotcool_contracts.events import CalendarData, ComplaintCueData
from spotcool_world.clocks import SimClock
from spotcool_world.frames import (
    CalendarFrame,
    ComplaintCueFrame,
    Frame,
    ScenarioStateFrame,
    StartFrame,
    TickFrame,
)
from spotcool_world.processors.frame_processor import FrameDirection, FrameProcessor
from spotcool_world.scenarios.compiler import CompiledScenario


class ScenarioProcessor(FrameProcessor):
    """Plays a compiled scenario, tick by tick.

    On each ``TickFrame`` it pushes, in this order and each stamped with the tick's
    ``sim_time_s``:

    1. a ``ScenarioStateFrame`` with all five zones;
    2. a ``CalendarFrame`` per room, on the first tick and whenever the room's visible bookings
       change;
    3. a ``ComplaintCueFrame`` (``delivered=False``) for each complaint in the tick's window,
       ``(previous tick, this tick]`` or ``[t, t]`` on the first tick, with ``audio`` set for
       voice cues;
    4. then the tick itself.

    Every other frame passes through unchanged. A ``StartFrame`` begins a new run, so the next
    tick is a first tick again. Deterministic: the same ticks always give the same frames.

    The scenario has no state before its start. A run that starts earlier is a fatal error, and
    a tick before the start passes on with a fatal error and no scenario frames. A run that
    starts later only logs a warning: complaints before its first tick never fire.

    Args:
        compiled: The scenario.
        name: Display name.
    """

    def __init__(self, compiled: CompiledScenario, *, name: str | None = None) -> None:
        super().__init__(name=name or f"ScenarioProcessor({compiled.name})")
        self._compiled = compiled
        self._start_text = SimClock.clock_text(compiled.start_s)
        self._previous_s: float | None = None
        self._calendars: dict[str, list[ScheduleItem]] = {}

    async def process_frame(self, frame: Frame, direction: FrameDirection) -> None:
        """Push the scenario's frames ahead of each tick; pass every frame on."""
        await super().process_frame(frame, direction)
        if direction is FrameDirection.DOWNSTREAM:
            if isinstance(frame, StartFrame):
                await self._start(frame)
            elif isinstance(frame, TickFrame):
                await self._tick(frame)
        await self.push_frame(frame, direction)

    async def _start(self, frame: StartFrame) -> None:
        self._previous_s = None
        self._calendars = {}
        run = SimClock.clock_text(frame.start_time_s)
        scenario = f"scenario {self._compiled.name} starts at {self._start_text}"
        if frame.start_time_s < self._compiled.start_s:
            await self.push_error(f"the run starts at {run}, before {scenario}", fatal=True)
        elif frame.start_time_s > self._compiled.start_s:
            logger.warning(
                f"{self.name}: the run starts at {run}, after {scenario}; "
                f"complaints before {run} will not fire"
            )

    async def _tick(self, tick: TickFrame) -> None:
        t_s = tick.sim_time_s
        assert t_s is not None  # a TickFrame always has one
        if t_s < self._compiled.start_s:
            at = SimClock.clock_text(t_s)
            message = f"tick at {at} is before scenario {self._compiled.name} starts at {self._start_text}"
            await self.push_error(message, fatal=True)
            return
        for derived in self._frames_for(t_s):
            await self.push_frame(derived)

    def _frames_for(self, t_s: float) -> list[Frame]:
        frames: list[Frame] = [
            ScenarioStateFrame(state=self._compiled.state_at(t_s), sim_time_s=t_s)
        ]
        for room, items in self._compiled.bookings_at(t_s).items():
            if self._calendars.get(room) != items:
                self._calendars[room] = items
                frames.append(
                    CalendarFrame(data=CalendarData(room=room, items=items), sim_time_s=t_s)
                )
        for cue in self._compiled.cues_between(self._previous_s, t_s):
            data = ComplaintCueData(
                zone=cue.zone, text=cue.text, channel=cue.channel, delivered=False
            )
            audio = cue.audio if cue.channel == "voice" else None
            frames.append(ComplaintCueFrame(data=data, audio=audio, sim_time_s=t_s))
        self._previous_s = t_s
        return frames
