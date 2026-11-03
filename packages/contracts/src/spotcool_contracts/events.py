"""Dashboard event envelope and the world's event payloads.

Both the world and the agent publish events in the same envelope, so the dashboard renders one
time-ordered stream. The world's payloads are defined here; the agent's payloads (perception,
decision, explanation, chat) are owned by Person A and only their type names are reserved here.
"""

import time
from enum import StrEnum
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field

from spotcool_contracts.calendar import ScheduleItem
from spotcool_contracts.zones import Zone

ENVELOPE_VERSION = 1


class EventSource(StrEnum):
    """Who published an event."""

    WORLD = "world"
    AGENT = "agent"


class EventType(StrEnum):
    """Event types on the dashboard stream."""

    # Published by the world.
    TICK = "tick"
    RUN_STATUS = "run_status"
    SCENARIO_STATE = "scenario_state"
    PLANT_MEASUREMENTS = "plant_measurements"
    BMS_WRITE = "bms_write"
    KPI = "kpi"
    VRF_STATE = "vrf_state"
    CAMERA_SCENE = "camera_scene"
    CALENDAR = "calendar"
    COMPLAINT_CUE = "complaint_cue"
    ERROR = "error"
    METRICS = "metrics"
    # Published by the agent (payloads defined by Person A).
    PERCEPTION = "perception"
    DECISION = "decision"
    EXPLANATION = "explanation"
    CHAT = "chat"


Run = Literal["spotcool", "baseline"]


class EventEnvelope(BaseModel):
    """One message on the dashboard stream.

    Parameters:
        v: Envelope version.
        type: Event type.
        source: Publisher.
        t_ms: Wall-clock publish time, Unix milliseconds.
        sim_time_s: Simulation time the event refers to (seconds since 1 January 00:00 of the
            BOPTEST year), or ``None`` for events outside simulation time.
        data: Type-specific payload.
    """

    model_config = ConfigDict(extra="forbid")

    v: Literal[1] = ENVELOPE_VERSION
    type: EventType
    source: EventSource
    t_ms: int
    sim_time_s: float | None = None
    data: dict[str, Any]

    @classmethod
    def build(
        cls,
        type: EventType,
        source: EventSource,
        data: BaseModel,
        sim_time_s: float | None = None,
    ) -> "EventEnvelope":
        """Wrap a typed payload in an envelope stamped with the current wall time."""
        return cls(
            type=type,
            source=source,
            t_ms=time.time_ns() // 1_000_000,
            sim_time_s=sim_time_s,
            data=data.model_dump(mode="json"),
        )


class _Payload(BaseModel):
    model_config = ConfigDict(extra="forbid")


class TickData(_Payload):
    """One simulation step began.

    Parameters:
        index: Step number since the start of the run.
        step_s: Simulation seconds per step.
        clock: Simulated local time of day, ``HH:MM``.
        day_of_year: Simulated day of the BOPTEST year (1-365).
    """

    index: int = Field(ge=0)
    step_s: float = Field(gt=0)
    clock: str
    day_of_year: int = Field(ge=1, le=366)


class RunStatusData(_Payload):
    """Lifecycle of a world run."""

    run_id: str
    status: Literal["starting", "running", "paused", "finished", "cancelled", "failed"]
    plant: str
    scenario: str
    message: str | None = None


class ZoneScene(_Payload):
    """What the scenario says is happening in a zone.

    ``people`` is ground truth for scoring and the dashboard. Agents must not read it; they
    count people from camera snapshots.
    """

    zone: Zone
    scene: str
    people: int = Field(ge=0)
    door_open: bool | None = None
    clothing: Literal["light", "medium", "heavy"] | None = None


class ScenarioStateData(_Payload):
    """Scenario state for the current step."""

    zones: list[ZoneScene]
    active_events: list[str] = Field(default_factory=list)


class ZoneMeasurement(_Payload):
    """Measured state of one zone, in BMS units."""

    zone: Zone
    temp_c: float
    cooling_sp_c: float
    heating_sp_c: float
    co2_ppm: float
    airflow_lps: float


class PlantMeasurementsData(_Payload):
    """Plant state after a step, for one of the two runs."""

    run: Run
    zones: list[ZoneMeasurement]
    power_kw: dict[str, float]
    total_power_kw: float
    energy_kwh: float
    outdoor_temp_c: float | None = None


class BmsWriteData(_Payload):
    """A BACnet write the BMS received.

    Parameters:
        device: Device name.
        object_id: ``type,instance`` of the target object.
        object_name: Target object name.
        priority: Priority slot written (1-16).
        value: Value written; ``None`` means a NULL write (the slot was released).
        client: Address of the writer, ``ip:port``.
        accepted: Whether the write was applied.
        reason: Why a write was rejected, or a note on how it was applied.
    """

    device: str
    object_id: str
    object_name: str
    priority: int = Field(ge=1, le=16)
    value: float | str | None
    client: str
    accepted: bool
    reason: str | None = None


class KpiData(_Payload):
    """Key performance indicators for one run.

    Parameters:
        run: Which run the KPIs belong to.
        boptest: BOPTEST's own KPIs (e.g. ``ener_tot``, ``tdis_tot``); ``None`` values are
            not yet available.
        comfort_where_people_kh: Kelvin-hours outside the comfort band, counted only in
            zones and at times the scenario had people present.
        energy_saved_pct: Energy saved relative to the baseline run (SpotCool run only).
        provisional: True while the run is still in progress.
    """

    run: Run
    boptest: dict[str, float | None]
    comfort_where_people_kh: float | None = None
    energy_saved_pct: float | None = None
    provisional: bool = True


class VrfStateData(_Payload):
    """State of the meeting-room VRF indoor unit."""

    profile: str
    power_on: bool
    mode: str
    setpoint_c: float
    fan: str
    vane_v_pos: str
    vane_h_pos: str | None
    vane_v_angle_deg: float
    vane_h_angle_deg: float | None
    room_temp_c: float
    power_kw: float


class CameraSceneData(_Payload):
    """Which scene a camera is showing (for the dashboard; agents must not read it)."""

    camera_id: str
    zone: Zone | None
    scene: str


class CalendarData(_Payload):
    """Bookings for a room, published when they change."""

    room: str
    items: list[ScheduleItem]


class ComplaintCueData(_Payload):
    """A scripted occupant complaint fired."""

    zone: Zone
    text: str
    channel: Literal["chat", "voice"]
    delivered: bool
    detail: str | None = None


class ErrorData(_Payload):
    """Something went wrong in the world."""

    where: str
    message: str
    fatal: bool = False


class MetricsData(_Payload):
    """One metric sample (latency, time ratio, queue depth, ...)."""

    name: str
    value: float
    unit: str
    labels: dict[str, str] = Field(default_factory=dict)


WORLD_PAYLOADS: dict[EventType, type[BaseModel]] = {
    EventType.TICK: TickData,
    EventType.RUN_STATUS: RunStatusData,
    EventType.SCENARIO_STATE: ScenarioStateData,
    EventType.PLANT_MEASUREMENTS: PlantMeasurementsData,
    EventType.BMS_WRITE: BmsWriteData,
    EventType.KPI: KpiData,
    EventType.VRF_STATE: VrfStateData,
    EventType.CAMERA_SCENE: CameraSceneData,
    EventType.CALENDAR: CalendarData,
    EventType.COMPLAINT_CUE: ComplaintCueData,
    EventType.ERROR: ErrorData,
    EventType.METRICS: MetricsData,
}
"""Payload model for every event type the world publishes."""


def parse_world_payload(envelope: EventEnvelope) -> BaseModel:
    """Validate and return the typed payload of a world event.

    Args:
        envelope: An event published by the world.

    Raises:
        KeyError: If the event type is not a world event type.
        pydantic.ValidationError: If the payload does not match its model.
    """
    return WORLD_PAYLOADS[envelope.type].model_validate(envelope.data)
