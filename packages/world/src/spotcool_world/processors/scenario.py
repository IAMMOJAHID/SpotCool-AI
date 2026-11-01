"""ScenarioProcessor: what the scenario says happens in each step, pushed ahead of the tick."""

from spotcool_contracts.calendar import ScheduleItem
from spotcool_contracts.events import CalendarData, ComplaintCueData
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

    Args:
        compiled: The scenario.
        name: Display name.
    """

    def __init__(self, compiled: CompiledScenario, *, name: str | None = None) -> None:
        super().__init__(name=name or f"ScenarioProcessor({compiled.name})")
        self._compiled = compiled
        self._previous_s: float | None = None
        self._calendars: dict[str, list[ScheduleItem]] = {}

    async def process_frame(self, frame: Frame, direction: FrameDirection) -> None:
        """Push the scenario's frames ahead of each tick; pass every frame on."""
        await super().process_frame(frame, direction)
        if direction is FrameDirection.DOWNSTREAM:
            if isinstance(frame, StartFrame):
                self._previous_s = None
                self._calendars = {}
            elif isinstance(frame, TickFrame):
                for derived in self._frames_for(frame):
                    await self.push_frame(derived)
        await self.push_frame(frame, direction)

    def _frames_for(self, tick: TickFrame) -> list[Frame]:
        t_s = tick.sim_time_s
        assert t_s is not None  # a TickFrame always has one
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
