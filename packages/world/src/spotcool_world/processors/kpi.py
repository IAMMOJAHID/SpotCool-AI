"""KpiProcessor: energy saved and comfort where people were, for the SpotCool and baseline runs."""

import math

from spotcool_contracts import Zone
from spotcool_contracts.events import KpiData, PlantMeasurementsData, Run, ScenarioStateData
from spotcool_world.frames import (
    CancelFrame,
    EndFrame,
    Frame,
    KpiFrame,
    PlantMeasurementsFrame,
    ScenarioStateFrame,
    StartFrame,
    TickFrame,
)
from spotcool_world.processors.frame_processor import FrameDirection, FrameProcessor

PROVISIONAL_EVERY_STEPS = 4
"""Steps between two provisional ``KpiFrame``s."""
RUNS: tuple[Run, Run] = ("spotcool", "baseline")
"""The plant runs, in the order their KPI frames are pushed."""


def comfort_kh(
    state: ScenarioStateData,
    run: PlantMeasurementsData,
    baseline: PlantMeasurementsData,
    step_s: float,
) -> dict[Zone, float]:
    """Kelvin-hours outside the comfort band over one step, in each zone that had people.

    Args:
        state: The scenario state of the step (who was where).
        run: The scored run's measurements at the end of the step.
        baseline: The baseline run's measurements at the same stamp. Its applied heating and
            cooling setpoints, which describe the step that just ended, are the band.
        step_s: The step's length.

    Returns:
        ``max(0, T - cooling) + max(0, heating - T)`` times the step in hours, for every zone
        with people; zones without people are left out.
    """
    temps = {zone.zone: zone.temp_c for zone in run.zones}
    bands = {zone.zone: (zone.heating_sp_c, zone.cooling_sp_c) for zone in baseline.zones}
    scores: dict[Zone, float] = {}
    for scene in state.zones:
        if scene.people > 0 and scene.zone in temps and scene.zone in bands:
            low, high = bands[scene.zone]
            temp = temps[scene.zone]
            scores[scene.zone] = (max(0.0, temp - high) + max(0.0, low - temp)) * step_s / 3600
    return scores


def energy_saved_pct(baseline_kwh: float, spotcool_kwh: float) -> float | None:
    """``(baseline - spotcool) / baseline * 100``, or ``None`` before the baseline used any.

    Args:
        baseline_kwh: The baseline run's energy.
        spotcool_kwh: The SpotCool run's energy at the same stamp.
    """
    if baseline_kwh <= 0:
        return None
    return (baseline_kwh - spotcool_kwh) / baseline_kwh * 100


class KpiProcessor(FrameProcessor):
    """Scores both plant runs and pushes their ``KpiFrame``s.

    It needs each ``PlantMeasurementsFrame``'s summary, so it goes after the
    ``TelemetryProcessor``.

    - **Comfort where people were** (K h), scored at each ``TickFrame``, after both runs'
      measurements of the step (stamped ``t``, the end of the step) have passed. The people come
      from the latest ``ScenarioStateFrame``, which arrived before the plants stepped: the state
      of the step that just ended. The band is the baseline run's applied setpoints at ``t``
      (``comfort_kh``). Both runs are scored with the same people and band.
    - **Energy saved** (%), the SpotCool run only, from both runs' cumulative energy at the same
      stamp (``energy_saved_pct``).
    - Every ``PROVISIONAL_EVERY_STEPS`` steps it pushes a provisional ``KpiFrame`` per run,
      stamped ``t``, ahead of the tick; BOPTEST's KPIs are empty until the end.
    - The plants' own final ``KpiFrame``s (BOPTEST's KPIs) are kept, not passed on. At the
      first ``EndFrame`` or ``CancelFrame`` it pushes one final ``KpiFrame`` per run
      (``provisional=False``) that merges them with the scores, then passes the frame on.

    Measurements without a summary are skipped: a step is scored only when both runs have one
    at its stamp. A run's comfort is ``None`` until a step of it could be scored. A
    ``StartFrame`` starts afresh. Every other frame passes on.

    Args:
        name: Display name.
    """

    def __init__(self, *, name: str | None = None) -> None:
        super().__init__(name=name)
        self._state: ScenarioStateData | None = None
        self._latest: dict[Run, tuple[float | None, PlantMeasurementsData]] = {}  # stamp, summary
        self._comfort: dict[Run, float] = {}
        self._boptest: dict[Run, dict[str, float | None]] = {}
        self._ended = False

    async def process_frame(self, frame: Frame, direction: FrameDirection) -> None:
        """Track the runs, push KPI frames ahead of ticks and at the end; pass frames on."""
        await super().process_frame(frame, direction)
        if direction is FrameDirection.DOWNSTREAM:
            if isinstance(frame, StartFrame):
                self._state, self._latest, self._comfort, self._boptest = None, {}, {}, {}
                self._ended = False
            elif isinstance(frame, ScenarioStateFrame):
                self._state = frame.state
            elif isinstance(frame, PlantMeasurementsFrame) and frame.summary is not None:
                self._latest[frame.run] = (frame.sim_time_s, frame.summary)
            elif isinstance(frame, KpiFrame) and not frame.data.provisional:
                self._boptest[frame.data.run] = dict(frame.data.boptest)
                return  # merged into this processor's final KPI frame
            elif isinstance(frame, TickFrame):
                for kpi in self._step(frame):
                    await self.push_frame(kpi)
            elif isinstance(frame, EndFrame | CancelFrame) and not self._ended:
                self._ended = True  # a CancelFrame after the EndFrame (stop() at the end) adds none
                for kpi in self._kpis(provisional=False, sim_time_s=frame.sim_time_s):
                    await self.push_frame(kpi)
        await self.push_frame(frame, direction)

    def _step(self, tick: TickFrame) -> list[KpiFrame]:
        """Score the step the tick ended; the provisional KPI frames if they are due."""
        assert tick.sim_time_s is not None  # a TickFrame always has one
        t_s = tick.sim_time_s + tick.step_s
        baseline = self._summary_at("baseline", t_s)
        if self._state is not None and baseline is not None:
            for run in RUNS:
                summary = self._summary_at(run, t_s)
                if summary is not None:
                    scores = comfort_kh(self._state, summary, baseline, tick.step_s)
                    self._comfort[run] = self._comfort.get(run, 0.0) + sum(scores.values())
        if (tick.index + 1) % PROVISIONAL_EVERY_STEPS:
            return []
        return self._kpis(provisional=True, sim_time_s=t_s)

    def _summary_at(self, run: Run, t_s: float | None) -> PlantMeasurementsData | None:
        stamp, summary = self._latest.get(run, (None, None))
        if stamp is None or t_s is None or not math.isclose(stamp, t_s, abs_tol=1e-6):
            return None
        return summary

    def _kpis(self, *, provisional: bool, sim_time_s: float | None) -> list[KpiFrame]:
        saved = None
        stamp, spotcool = self._latest.get("spotcool", (None, None))
        baseline = self._summary_at("baseline", stamp)
        if spotcool is not None and baseline is not None:
            saved = energy_saved_pct(baseline.energy_kwh, spotcool.energy_kwh)
        return [
            KpiFrame(
                data=KpiData(
                    run=run,
                    boptest={} if provisional else self._boptest.get(run, {}),
                    comfort_where_people_kh=self._comfort.get(run),
                    energy_saved_pct=saved if run == "spotcool" else None,
                    provisional=provisional,
                ),
                sim_time_s=sim_time_s,
            )
            for run in RUNS
            if run in self._latest or run in self._boptest
        ]
