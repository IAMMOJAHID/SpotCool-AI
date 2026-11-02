"""EventEmitterProcessor: every domain frame as a dashboard event, in the contract envelope."""

from pydantic import BaseModel

from spotcool_contracts.events import ErrorData, EventEnvelope, EventSource, EventType, TickData
from spotcool_world.clocks import SimClock
from spotcool_world.frames import (
    BmsWriteFrame,
    CalendarFrame,
    CameraSceneFrame,
    ComplaintCueFrame,
    ErrorFrame,
    EventFrame,
    Frame,
    KpiFrame,
    MetricsFrame,
    PlantMeasurementsFrame,
    RunStatusFrame,
    ScenarioStateFrame,
    TickFrame,
    VrfStateFrame,
)
from spotcool_world.processors.frame_processor import FrameDirection, FrameProcessor

EVENTS_TOPIC = "events"
"""The bus topic of the one event stream: the pipeline's events (``BusPublisher``), the occupant
worker's results and the agent's events from the dashboard server's ``/ws/agent``."""


def frame_events(frame: Frame) -> list[EventEnvelope]:
    """The world events a frame becomes, stamped with the frame's simulation time.

    - ``TickFrame``: a ``tick`` event, with the clock and day of year from ``SimClock``.
    - ``RunStatusFrame``, ``ScenarioStateFrame``, ``BmsWriteFrame``, ``KpiFrame``,
      ``VrfStateFrame``, ``CameraSceneFrame``, ``CalendarFrame`` and ``ComplaintCueFrame``:
      their payload. A complaint's ``audio`` file name is not part of it.
    - ``PlantMeasurementsFrame``: its ``summary``; nothing while it has none.
    - ``ErrorFrame``: an ``error`` event naming the processor that reported it.
    - ``MetricsFrame``: one ``metrics`` event per sample.

    Any other frame becomes nothing.

    Args:
        frame: The frame.
    """
    payloads: list[tuple[EventType, BaseModel]] = []
    t_s = frame.sim_time_s
    if isinstance(frame, TickFrame):
        assert t_s is not None  # a TickFrame always has one
        tick = TickData(
            index=frame.index,
            step_s=frame.step_s,
            clock=SimClock.clock_text(t_s),
            day_of_year=SimClock.day_of_year(t_s),
        )
        payloads.append((EventType.TICK, tick))
    elif isinstance(frame, RunStatusFrame):
        payloads.append((EventType.RUN_STATUS, frame.data))
    elif isinstance(frame, ScenarioStateFrame):
        payloads.append((EventType.SCENARIO_STATE, frame.state))
    elif isinstance(frame, PlantMeasurementsFrame):
        if frame.summary is not None:
            payloads.append((EventType.PLANT_MEASUREMENTS, frame.summary))
    elif isinstance(frame, BmsWriteFrame):
        payloads.append((EventType.BMS_WRITE, frame.data))
    elif isinstance(frame, KpiFrame):
        payloads.append((EventType.KPI, frame.data))
    elif isinstance(frame, VrfStateFrame):
        payloads.append((EventType.VRF_STATE, frame.data))
    elif isinstance(frame, CameraSceneFrame):
        payloads.append((EventType.CAMERA_SCENE, frame.data))
    elif isinstance(frame, CalendarFrame):
        payloads.append((EventType.CALENDAR, frame.data))
    elif isinstance(frame, ComplaintCueFrame):
        payloads.append((EventType.COMPLAINT_CUE, frame.data))  # never frame.audio
    elif isinstance(frame, ErrorFrame):
        error = ErrorData(
            where=frame.processor or "world", message=frame.message, fatal=frame.fatal
        )
        payloads.append((EventType.ERROR, error))
    elif isinstance(frame, MetricsFrame):
        payloads.extend((EventType.METRICS, sample) for sample in frame.samples)
    return [EventEnvelope.build(kind, EventSource.WORLD, data, t_s) for kind, data in payloads]


class EventEmitterProcessor(FrameProcessor):
    """Pushes an ``EventFrame`` for every event a frame becomes (``frame_events``), then the frame.

    The events go downstream, ahead of the frame they came from, whichever way that frame
    travels: an ``ErrorFrame`` from a processor further down passes on upstream after its
    ``error`` event has gone down. Within a tick the ``tick`` event comes last, because derived
    frames travel ahead of their tick. ``EventFrame``s pass on unchanged.

    ``ErrorFrame``s travel upstream, so errors pushed by processors above this one never reach
    it; ``frame_events`` turns them into events where they arrive (the worker's
    ``on_pipeline_error``).

    Args:
        name: Display name.
    """

    async def process_frame(self, frame: Frame, direction: FrameDirection) -> None:
        """Push the frame's events downstream, then pass the frame on."""
        await super().process_frame(frame, direction)
        for envelope in frame_events(frame):
            await self.push_frame(EventFrame(envelope=envelope, sim_time_s=frame.sim_time_s))
        await self.push_frame(frame, direction)
