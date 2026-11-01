"""Frames: the pieces of simulation news that flow through a pipeline.

Three families, as in pipecat:

- ``SystemFrame``: lifecycle and out-of-band news (start, cancel, errors, metrics).
- ``ControlFrame``: in-band control that keeps its place in the flow (ticks, end of run).
- ``DataFrame``: domain data. Payloads are the shared contract models from
  ``spotcool_contracts``, so what the dashboard and the agent see is exactly what flowed here.

Frames are ``@dataclass(kw_only=True)``; every subclass repeats the decorator.
"""

import itertools
import time
from dataclasses import dataclass, field
from typing import Any

from spotcool_contracts.events import (
    BmsWriteData,
    CalendarData,
    CameraSceneData,
    ComplaintCueData,
    EventEnvelope,
    KpiData,
    MetricsData,
    PlantMeasurementsData,
    Run,
    RunStatusData,
    ScenarioStateData,
    VrfStateData,
)

_frame_ids = itertools.count(1)


@dataclass(kw_only=True)
class Frame:
    """Base of every frame.

    Parameters:
        id: Process-wide unique id, assigned on creation.
        name: ``ClassName#id``, assigned on creation.
        created_ns: Wall-clock creation time, ``time.perf_counter_ns()`` (high resolution on
            every platform, unlike ``time.monotonic`` on Windows).
        sim_time_s: Simulation time the frame refers to (seconds since 1 January 00:00 of the
            BOPTEST year), or ``None`` when it has none.
        metadata: Free-form annotations; nothing in the core reads them.
    """

    id: int = field(init=False)
    name: str = field(init=False)
    created_ns: int = field(init=False)
    sim_time_s: float | None = None
    metadata: dict[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        self.id = next(_frame_ids)
        self.name = f"{type(self).__name__}#{self.id}"
        self.created_ns = time.perf_counter_ns()

    def __str__(self) -> str:
        return self.name


@dataclass(kw_only=True)
class SystemFrame(Frame):
    """Lifecycle and out-of-band frames."""


@dataclass(kw_only=True)
class ControlFrame(Frame):
    """In-band control frames that keep their place in the flow."""


@dataclass(kw_only=True)
class DataFrame(Frame):
    """Domain data frames."""


# System frames.


@dataclass(kw_only=True)
class StartFrame(SystemFrame):
    """First frame of a run; every processor records it before anything else arrives.

    Parameters:
        run_id: Identifier of the run (also the recorder's directory name).
        scenario: Scenario name.
        plant: Plant provider name.
        step_s: Simulation seconds per tick.
        speed: Simulation seconds per wall second, or ``None`` to run as fast as possible.
        start_time_s: Simulation time of the first tick.
        end_time_s: Simulation time at which the run ends.
    """

    run_id: str
    scenario: str
    plant: str
    step_s: float
    speed: float | None
    start_time_s: float
    end_time_s: float


@dataclass(kw_only=True)
class CancelFrame(SystemFrame):
    """Stop now: processors cancel their background work and pass the frame on.

    Parameters:
        reason: Why the run was cancelled.
    """

    reason: str = ""


@dataclass(kw_only=True)
class ErrorFrame(SystemFrame):
    """Something went wrong. Travels upstream to the worker.

    Parameters:
        message: What went wrong.
        fatal: Whether the run cannot continue; a fatal error cancels the run.
        processor: Name of the processor that reported it.
    """

    message: str
    fatal: bool = False
    processor: str | None = None


@dataclass(kw_only=True)
class MetricsFrame(SystemFrame):
    """Metric samples (call latency, BOPTEST time ratio, queue depth, ...).

    Parameters:
        samples: The samples.
    """

    samples: list[MetricsData]


# Control frames.


@dataclass(kw_only=True)
class TickFrame(ControlFrame):
    """One simulation step. ``sim_time_s`` is the step's start time and is required.

    Parameters:
        index: Step number since the start of the run.
        step_s: Simulation seconds the step covers.
    """

    index: int
    step_s: float

    def __post_init__(self) -> None:
        super().__post_init__()
        if self.sim_time_s is None:
            raise ValueError("TickFrame needs sim_time_s")


@dataclass(kw_only=True)
class EndFrame(ControlFrame):
    """The run is over. Processors finish their work; the worker stops once it leaves the end.

    Parameters:
        reason: Why the run ended.
    """

    reason: str = ""


# Domain data frames.


@dataclass(kw_only=True)
class RunStatusFrame(DataFrame):
    """Run lifecycle news for the dashboard.

    Parameters:
        data: The status payload.
    """

    data: RunStatusData


@dataclass(kw_only=True)
class ScenarioStateFrame(DataFrame):
    """What the scenario says is happening in every zone this step.

    Parameters:
        state: The scenario state payload.
    """

    state: ScenarioStateData


@dataclass(kw_only=True)
class SetpointCommandFrame(DataFrame):
    """Inputs to apply to one plant run on the next step.

    Parameters:
        run: Which run the command is for.
        overrides: BOPTEST input name to value in BOPTEST units. ``None`` releases the input
            (``_activate = 0``), handing control back to BOPTEST's own controller.
    """

    run: Run
    overrides: dict[str, float | None]


@dataclass(kw_only=True)
class PlantMeasurementsFrame(DataFrame):
    """Measurements after a plant step.

    Parameters:
        run: Which run produced them.
        raw: BOPTEST output name to value, in BOPTEST units.
        summary: The same state in BMS units for the dashboard, once a processor builds it.
    """

    run: Run
    raw: dict[str, float]
    summary: PlantMeasurementsData | None = None


@dataclass(kw_only=True)
class BmsWriteFrame(DataFrame):
    """A BACnet write the BMS received.

    Parameters:
        data: The write payload.
    """

    data: BmsWriteData


@dataclass(kw_only=True)
class KpiFrame(DataFrame):
    """Key performance indicators for one run.

    Parameters:
        data: The KPI payload.
    """

    data: KpiData


@dataclass(kw_only=True)
class VrfStateFrame(DataFrame):
    """State of the meeting-room VRF indoor unit.

    Parameters:
        data: The VRF state payload.
    """

    data: VrfStateData


@dataclass(kw_only=True)
class CameraSceneFrame(DataFrame):
    """Which scene a camera is showing (ground truth; agents never read it).

    Parameters:
        data: The camera scene payload.
    """

    data: CameraSceneData


@dataclass(kw_only=True)
class CalendarFrame(DataFrame):
    """Room bookings, published when they change.

    Parameters:
        data: The calendar payload.
    """

    data: CalendarData


@dataclass(kw_only=True)
class ComplaintCueFrame(DataFrame):
    """A scripted occupant complaint fired.

    Parameters:
        data: The complaint payload.
    """

    data: ComplaintCueData


@dataclass(kw_only=True)
class EventFrame(DataFrame):
    """A dashboard event, already wrapped in the contract envelope.

    Parameters:
        envelope: The event.
    """

    envelope: EventEnvelope
