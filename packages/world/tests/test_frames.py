import pytest

from spotcool_contracts.events import (
    EventEnvelope,
    EventSource,
    EventType,
    MetricsData,
    TickData,
)
from spotcool_world.frames import (
    CancelFrame,
    ControlFrame,
    DataFrame,
    EndFrame,
    ErrorFrame,
    EventFrame,
    MetricsFrame,
    SetpointCommandFrame,
    StartFrame,
    SystemFrame,
    TickFrame,
)


def test_ids_are_unique_and_increasing() -> None:
    frames = [TickFrame(index=i, step_s=900.0, sim_time_s=i * 900.0) for i in range(100)]
    ids = [f.id for f in frames]
    assert len(set(ids)) == len(ids)
    assert ids == sorted(ids)


def test_name_is_class_name_and_id() -> None:
    frame = EndFrame(reason="done")
    assert frame.name == f"EndFrame#{frame.id}"
    assert str(frame) == frame.name


def test_families() -> None:
    start = StartFrame(
        run_id="r",
        scenario="s",
        plant="rc",
        step_s=900.0,
        speed=None,
        start_time_s=0.0,
        end_time_s=3600.0,
    )
    assert isinstance(start, SystemFrame)
    assert isinstance(CancelFrame(), SystemFrame)
    assert isinstance(ErrorFrame(message="x"), SystemFrame)
    assert isinstance(TickFrame(index=0, step_s=900.0, sim_time_s=0.0), ControlFrame)
    assert isinstance(EndFrame(), ControlFrame)
    assert isinstance(SetpointCommandFrame(run="spotcool", overrides={}), DataFrame)


def test_frames_are_keyword_only() -> None:
    with pytest.raises(TypeError):
        EndFrame("positional")  # pyright: ignore[reportCallIssue]


def test_tick_needs_sim_time() -> None:
    with pytest.raises(ValueError, match="sim_time_s"):
        TickFrame(index=0, step_s=900.0)


def test_metadata_is_per_frame() -> None:
    first, second = EndFrame(), EndFrame()
    first.metadata["note"] = 1
    assert second.metadata == {}


def test_error_defaults() -> None:
    error = ErrorFrame(message="boom")
    assert error.fatal is False
    assert error.processor is None


def test_domain_frames_carry_contract_payloads() -> None:
    metrics = MetricsFrame(samples=[MetricsData(name="step_latency", value=0.2, unit="s")])
    assert metrics.samples[0].unit == "s"
    command = SetpointCommandFrame(
        run="spotcool", overrides={"hvac_oveZonSupCor_TZonCooSet_u": 297.15, "released": None}
    )
    assert command.overrides["released"] is None
    tick = TickData(index=0, step_s=900.0, clock="07:00", day_of_year=199)
    event = EventFrame(envelope=EventEnvelope.build(EventType.TICK, EventSource.WORLD, tick))
    assert event.envelope.data["clock"] == "07:00"
