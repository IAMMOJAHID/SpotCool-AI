from collections.abc import Callable

import pytest

from spotcool_contracts import Zone
from spotcool_contracts.calendar import DateTimeTimeZone, ScheduleItem
from spotcool_contracts.events import (
    WORLD_PAYLOADS,
    BmsWriteData,
    CalendarData,
    CameraSceneData,
    ComplaintCueData,
    EventEnvelope,
    EventSource,
    EventType,
    KpiData,
    MetricsData,
    PlantMeasurementsData,
    RunStatusData,
    ScenarioStateData,
    TickData,
    VrfStateData,
    parse_world_payload,
)
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
from spotcool_world.pipeline import Pipeline
from spotcool_world.processors import EventEmitterProcessor, FrameDirection, FrameProcessor
from spotcool_world.processors.event_emitter import frame_events
from spotcool_world.testing import run_test

T_S = 199 * 86_400.0 + 14 * 3600 + 15 * 60  # 14:15 on day 199


def every_kind_of_frame(
    make_summary: Callable[..., PlantMeasurementsData], make_state: Callable[..., ScenarioStateData]
) -> list[Frame]:
    item = ScheduleItem(
        status="busy",
        subject="Quarterly review",
        location="meeting-south",
        start=DateTimeTimeZone(dateTime="2026-07-19T14:00:00"),
        end=DateTimeTimeZone(dateTime="2026-07-19T15:00:00"),
    )
    vrf = VrfStateData(
        profile="mitsubishi",
        power_on=True,
        mode="cool",
        setpoint_c=24.0,
        fan="auto",
        vane_v_pos="swing",
        vane_h_pos=None,
        vane_v_angle_deg=30.0,
        vane_h_angle_deg=None,
        room_temp_c=25.0,
        power_kw=1.2,
    )
    write = BmsWriteData(
        device="SpotCool-Office-BMS",
        object_id="analog-value,203",
        object_name="ZN-SOU-CLG-SP",
        priority=10,
        value=26.0,
        client="127.0.0.1:47809",
        accepted=True,
    )
    return [
        TickFrame(index=29, step_s=900.0, sim_time_s=T_S),
        RunStatusFrame(
            data=RunStatusData(run_id="r", status="running", plant="rc", scenario="s"),
            sim_time_s=T_S,
        ),
        ScenarioStateFrame(state=make_state({Zone.SOU: 5}), sim_time_s=T_S),
        PlantMeasurementsFrame(
            run="spotcool", raw={}, summary=make_summary("spotcool"), sim_time_s=T_S
        ),
        BmsWriteFrame(data=write, sim_time_s=T_S),
        KpiFrame(data=KpiData(run="baseline", boptest={}), sim_time_s=T_S),
        VrfStateFrame(data=vrf, sim_time_s=T_S),
        CameraSceneFrame(
            data=CameraSceneData(camera_id="cam-sou", zone=Zone.SOU, scene="meeting"),
            sim_time_s=T_S,
        ),
        CalendarFrame(data=CalendarData(room="meeting-south", items=[item]), sim_time_s=T_S),
        ComplaintCueFrame(
            data=ComplaintCueData(
                zone=Zone.SOU, text="Freezing!", channel="voice", delivered=False
            ),
            audio="south_complaint.wav",
            sim_time_s=T_S,
        ),
        ErrorFrame(message="step failed", fatal=True, processor="PlantProcessor(spotcool)"),
        MetricsFrame(
            samples=[MetricsData(name="plant_step_latency", value=0.2, unit="s")], sim_time_s=T_S
        ),
    ]


def test_every_world_event_type_comes_from_a_frame(
    make_summary: Callable[..., PlantMeasurementsData],
    make_state: Callable[..., ScenarioStateData],
) -> None:
    events = [e for f in every_kind_of_frame(make_summary, make_state) for e in frame_events(f)]
    assert {e.type for e in events} == set(WORLD_PAYLOADS)
    for event in events:
        assert event.source is EventSource.WORLD
        parse_world_payload(event)  # each payload is the contract's model


def test_a_tick_has_the_clock_and_day_of_year_and_metrics_go_one_per_sample() -> None:
    (tick,) = frame_events(TickFrame(index=29, step_s=900.0, sim_time_s=T_S))
    assert tick.sim_time_s == T_S
    assert TickData.model_validate(tick.data) == TickData(
        index=29, step_s=900.0, clock="14:15", day_of_year=200
    )
    samples = [MetricsData(name=f"m{i}", value=float(i), unit="s") for i in range(3)]
    events = frame_events(MetricsFrame(samples=samples, sim_time_s=T_S))
    assert [e.type for e in events] == [EventType.METRICS] * 3
    assert [MetricsData.model_validate(e.data) for e in events] == samples


def test_the_complaint_audio_never_reaches_an_envelope() -> None:
    data = ComplaintCueData(zone=Zone.SOU, text="Freezing!", channel="voice", delivered=False)
    frame = ComplaintCueFrame(data=data, audio="south_complaint.wav", sim_time_s=T_S)
    (event,) = frame_events(frame)
    assert event.data == data.model_dump(mode="json")
    assert "south_complaint" not in event.model_dump_json()


def test_frames_without_an_event_become_nothing() -> None:
    tick = TickData(index=0, step_s=900.0, clock="07:00", day_of_year=200)
    envelope = EventEnvelope.build(EventType.TICK, EventSource.WORLD, tick)
    assert frame_events(EventFrame(envelope=envelope)) == []  # never wrapped twice
    assert frame_events(PlantMeasurementsFrame(run="baseline", raw={}, sim_time_s=T_S)) == []


@pytest.mark.asyncio
async def test_events_go_ahead_of_their_frame_and_every_frame_passes_on(
    make_state: Callable[..., ScenarioStateData],
) -> None:
    state = ScenarioStateFrame(state=make_state(), sim_time_s=T_S)
    tick = TickFrame(index=0, step_s=900.0, sim_time_s=T_S)
    down, up = await run_test(EventEmitterProcessor(), frames_to_send=[state, tick])
    assert not up
    assert [type(f).__name__ for f in down] == [
        "EventFrame",
        "ScenarioStateFrame",
        "EventFrame",
        "TickFrame",
    ]
    assert down[1] is state and down[3] is tick
    first, second = down[0], down[2]
    assert isinstance(first, EventFrame) and first.envelope.type is EventType.SCENARIO_STATE
    assert isinstance(second, EventFrame) and second.envelope.type is EventType.TICK
    assert first.sim_time_s == T_S


class FailOnTick(FrameProcessor):
    """Reports a non-fatal error on every tick, like a processor further down."""

    async def process_frame(self, frame: Frame, direction: FrameDirection) -> None:
        await super().process_frame(frame, direction)
        if isinstance(frame, TickFrame):
            await self.push_error("dashboard socket closed")
        await self.push_frame(frame, direction)


@pytest.mark.asyncio
async def test_an_error_from_further_down_goes_up_after_its_event_went_down() -> None:
    tick = TickFrame(index=0, step_s=900.0, sim_time_s=T_S)
    down, up = await run_test(
        Pipeline([EventEmitterProcessor(), FailOnTick(name="below")]), frames_to_send=[tick]
    )
    errors = [f for f in up if isinstance(f, ErrorFrame)]
    assert len(errors) == 1 and errors[0].message == "dashboard socket closed"
    events = [f.envelope for f in down if isinstance(f, EventFrame)]
    assert [e.type for e in events] == [EventType.TICK, EventType.ERROR]
    assert events[1].data == {
        "where": "below",
        "message": "dashboard socket closed",
        "fatal": False,
    }
