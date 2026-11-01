"""ReplayPlant: feeds back the raw measurements a recorded run produced, step by step.

The physics comes from the recording; everything after the plant (BMS, telemetry, dashboard)
runs live on the same values, so a run can be reproduced exactly without BOPTEST.
"""

import asyncio
import itertools
import math
from collections.abc import Mapping
from pathlib import Path

from spotcool_contracts.events import EventType, Run
from spotcool_world.serializers.jsonl import EventRecord, RawRecord, Record, read_records
from spotcool_world.services.boptest.catalog import load_catalog
from spotcool_world.services.plant_service import PlantService, PlantStartParams


class ReplayPlant(PlantService):
    """Replays one run of a recording (``runs/<run_id>/events.jsonl``).

    ``start`` reads the run's raw records from the start time on (a record is stamped with the
    end of its step); each ``step`` returns the next one, whatever the overrides (they are still
    validated, like on every plant). ``kpis`` returns the run's final recorded KPIs. The records
    must be one run: no index twice, one step apart (``RunWriter`` appends, so a reused run
    directory would mix two runs).

    Args:
        path: The recording.
        run: Which run's measurements to feed back.
    """

    def __init__(self, path: Path, run: Run) -> None:
        self._path = path
        self._run: Run = run
        self._raws: list[dict[str, float]] = []
        self._next = 0
        self._kpis: dict[str, float | None] = {}
        self._step_s: float | None = None

    @property
    def step_s(self) -> float:
        """Simulation seconds each ``step()`` advances (set by ``start``)."""
        if self._step_s is None:
            raise RuntimeError("the plant has not been started, or has been stopped")
        return self._step_s

    async def start(self, params: PlantStartParams) -> dict[str, float]:
        """Load the run's records from ``params.start_time_s`` on.

        Args:
            params: Start time and step; the recording must have a step that starts then.

        Returns:
            Nothing: a recording holds measurements after steps only.

        Raises:
            ValueError: If the recording has no step of this run starting at
                ``params.start_time_s``, repeats an index, or is not one ``params.step_s``
                per step.
        """
        records = await asyncio.to_thread(lambda: list(read_records(self._path)))
        raws = sorted(
            (r for r in records if isinstance(r, RawRecord) and r.run == self._run),
            key=lambda r: r.index,
        )
        if len({r.index for r in raws}) != len(raws):
            raise ValueError(
                f"{self._path} repeats {self._run} step indices: two runs in one file?"
            )
        first_end_s = params.start_time_s + params.step_s
        raws = [r for r in raws if r.sim_time_s >= first_end_s - 1e-6]
        if not raws or not math.isclose(raws[0].sim_time_s, first_end_s, abs_tol=1e-6):
            raise ValueError(
                f"{self._path} has no {self._run} step from {params.start_time_s} s to replay"
            )
        for before, after in itertools.pairwise(raws):
            if not math.isclose(after.sim_time_s - before.sim_time_s, params.step_s, abs_tol=1e-6):
                raise ValueError(
                    f"{self._path}: {self._run} step {after.index} is not {params.step_s} s "
                    f"after step {before.index}"
                )
        self._raws = [r.raw for r in raws]
        self._next = 0
        self._kpis = _final_kpis(records, self._run)
        self._step_s = params.step_s
        return {}

    async def step(self, overrides: Mapping[str, float | None]) -> dict[str, float]:
        """Return the next recorded measurements.

        Args:
            overrides: Validated against the catalog, then ignored.

        Raises:
            ValueError: If an override is not an input of the test case.
            RuntimeError: If the recording has no more steps.
        """
        if self._step_s is None:
            raise RuntimeError("the plant has not been started, or has been stopped")
        load_catalog().check_overrides(overrides)
        if self._next >= len(self._raws):
            raise RuntimeError(f"{self._path} has no more {self._run} steps")
        raw = self._raws[self._next]
        self._next += 1
        return dict(raw)

    async def kpis(self) -> dict[str, float | None]:
        """The run's final KPIs from the recording; empty if it has none."""
        return dict(self._kpis)

    async def stop(self) -> None:
        """Stop replaying. Safe to call twice."""
        self._step_s = None


def _final_kpis(records: list[Record], run: Run) -> dict[str, float | None]:
    final: dict[str, float | None] = {}
    for record in records:
        if isinstance(record, EventRecord) and record.envelope.type is EventType.KPI:
            data = record.envelope.data
            if data.get("run") == run and not data.get("provisional", True):
                final = dict(data.get("boptest", {}))
    return final
