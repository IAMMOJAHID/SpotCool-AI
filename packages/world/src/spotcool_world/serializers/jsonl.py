"""Run records: the JSONL file a recorded world run is kept in.

A run directory (``runs/<run_id>/``) holds ``events.jsonl``: one JSON object per line, in
pipeline order. Two kinds of record share the file:

- ``{"kind": "event", "envelope": {...}}``: a dashboard event, exactly as published.
- ``{"kind": "raw", "run": "spotcool", "index": 3, "sim_time_s": ..., "raw": {...}}``: one
  plant run's BOPTEST outputs after ``index`` steps (0: its initial state), for the replay
  plant to feed back.

The recorder writes the file with ``RunWriter``; the replay plant and reports read it back with
``read_records``. Records hold JSON only: never image or audio bytes. A process killed while
writing leaves a torn last line; ``read_records`` skips it with a warning.
"""

from collections.abc import Iterator
from pathlib import Path
from types import TracebackType
from typing import Annotated, Literal

from loguru import logger
from pydantic import BaseModel, ConfigDict, Field, TypeAdapter, ValidationError

from spotcool_contracts.events import EventEnvelope, Run

EVENTS_FILE = "events.jsonl"
"""Name of the record file inside a run directory."""


class RawRecord(BaseModel):
    """One plant run's raw measurements: its initial state, or its state after a step.

    Parameters:
        kind: Record discriminator, always ``raw``.
        run: Which run produced the measurements.
        index: Steps completed since the start of the run; 0 is the state at the start time.
        sim_time_s: Simulation time the measurements describe, ``start + index * step_s``, as
            on the ``PlantMeasurementsFrame``.
        raw: BOPTEST output name to value, in BOPTEST units. Values must be finite: JSON has
            no NaN, so one would come back as an unreadable null.
    """

    model_config = ConfigDict(extra="forbid", allow_inf_nan=False)

    kind: Literal["raw"] = "raw"
    run: Run
    index: int = Field(ge=0)
    sim_time_s: float
    raw: dict[str, float]


class EventRecord(BaseModel):
    """One dashboard event.

    Parameters:
        kind: Record discriminator, always ``event``.
        envelope: The event as it was published.
    """

    model_config = ConfigDict(extra="forbid")

    kind: Literal["event"] = "event"
    envelope: EventEnvelope


Record = RawRecord | EventRecord
"""Any line of a run file."""

_RECORD: TypeAdapter[Record] = TypeAdapter(Annotated[Record, Field(discriminator="kind")])


class RunWriter:
    """Appends records to a run file, one JSON line each.

    Writes are buffered; ``flush()`` hands them to the operating system, so a reader (or a
    crash) sees every record written before it. The recorder flushes once per tick. Use it as a
    context manager, or call ``close()``.

    Args:
        path: The file to append to. Its directory is created if it does not exist.
    """

    def __init__(self, path: Path) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        self._file = path.open("a", encoding="utf-8", newline="\n")

    def write(self, record: Record) -> None:
        """Append one record.

        Args:
            record: The record.
        """
        self._file.write(record.model_dump_json() + "\n")

    def flush(self) -> None:
        """Hand the records written so far to the operating system."""
        self._file.flush()

    def close(self) -> None:
        """Flush and close the file. Safe to call twice."""
        self._file.close()

    def __enter__(self) -> "RunWriter":
        return self

    def __exit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        traceback: TracebackType | None,
    ) -> None:
        self.close()


def read_records(path: Path) -> Iterator[Record]:
    """Read a run file record by record, in the order it was written.

    Args:
        path: The run file.

    Yields:
        Each record; blank lines are skipped, and so, with a warning, is a torn last line
        (no newline, not a record: the writer was killed mid-line).

    Raises:
        ValueError: If any other line is not a valid record (the message names the line).
    """
    with path.open(encoding="utf-8") as file:
        for number, line in enumerate(file, start=1):
            if not line.strip():
                continue
            try:
                yield _RECORD.validate_json(line)
            except ValidationError as exc:
                if not line.endswith("\n"):  # only the last line can lack its newline
                    logger.warning(f"{path}:{number}: skipping a torn last record")
                    return
                raise ValueError(f"{path}:{number}: not a run record: {exc}") from exc
