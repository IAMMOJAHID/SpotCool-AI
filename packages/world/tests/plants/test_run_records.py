import math
from pathlib import Path

import pytest
from pydantic import ValidationError

from spotcool_contracts.events import EventEnvelope, EventSource, EventType, KpiData
from spotcool_world.serializers import EventRecord, RawRecord, RunWriter, read_records

RAW = RawRecord(
    run="baseline", index=0, sim_time_s=17_143_200.0, raw={"time": 1.5, "x_y": 0.1 + 0.2}
)
EVENT = EventRecord(
    envelope=EventEnvelope.build(
        EventType.KPI, EventSource.WORLD, KpiData(run="baseline", boptest={"ener_tot": None})
    )
)


def test_records_round_trip_in_order(tmp_path: Path) -> None:
    path = tmp_path / "runs" / "r1" / "events.jsonl"  # the directory is created
    with RunWriter(path) as writer:
        writer.write(RAW)
        writer.write(EVENT)
    assert list(read_records(path)) == [RAW, EVENT]
    assert path.read_text(encoding="utf-8").splitlines()[0].startswith('{"kind":"raw"')


def test_flush_makes_records_visible_and_close_twice_is_fine(tmp_path: Path) -> None:
    path = tmp_path / "events.jsonl"
    writer = RunWriter(path)
    writer.write(RAW)
    writer.flush()
    assert list(read_records(path)) == [RAW]
    writer.close()
    writer.close()


def test_a_writer_appends(tmp_path: Path) -> None:
    path = tmp_path / "events.jsonl"
    for _ in range(2):
        with RunWriter(path) as writer:
            writer.write(RAW)
    assert list(read_records(path)) == [RAW, RAW]


def test_a_bad_line_is_reported_with_its_number(tmp_path: Path) -> None:
    path = tmp_path / "events.jsonl"
    path.write_text(RAW.model_dump_json() + "\n\n" + '{"kind": "raw"}\n', encoding="utf-8")
    with pytest.raises(ValueError, match=r"events\.jsonl:3: not a run record"):
        list(read_records(path))


def test_a_torn_last_line_is_skipped_but_a_torn_middle_line_is_not(tmp_path: Path) -> None:
    torn = RAW.model_dump_json()[:25]  # the writer was killed mid-line
    path = tmp_path / "events.jsonl"
    path.write_text(RAW.model_dump_json() + "\n" + torn, encoding="utf-8")
    assert list(read_records(path)) == [RAW]
    path.write_text(RAW.model_dump_json() + "\n" + torn + "\n", encoding="utf-8")
    with pytest.raises(ValueError, match=r"events\.jsonl:2"):
        list(read_records(path))
    path.write_text(RAW.model_dump_json(), encoding="utf-8")  # a whole record, no newline
    assert list(read_records(path)) == [RAW]


@pytest.mark.parametrize("value", [math.nan, math.inf])
def test_non_finite_values_are_refused_before_writing(value: float) -> None:
    with pytest.raises(ValidationError):
        RawRecord(run="spotcool", index=0, sim_time_s=0.0, raw={"x_y": value})
    with pytest.raises(ValidationError):
        RawRecord(run="spotcool", index=0, sim_time_s=value, raw={})
