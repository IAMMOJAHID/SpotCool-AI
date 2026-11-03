"""Golden evals: the scenario library with the stub agent, judged from the saved runs.

pipecat's rule: an eval runs the world once, saves the run, and judges only what was saved, so a
BOPTEST run can be judged again without running the building. The judging goes through
``runner/report.py``: ``load_run`` (in ``conftest.py``), ``check_energy``, ``quote_blockers`` and
the manifest's final KPIs, the figures the report and the video quote. The one figure the report
does not read, the connection recoveries, comes from the same saved record.

``uv run pytest -m eval`` runs them on the RC plant (CI's ``evals`` job, about two minutes);
``uv run pytest -m "eval and live"`` runs ``hybrid_tuesday`` on BOPTEST (the lead does; CI never
does). Every band below was set around values measured at ``SPEED`` (1800) on 2026-11-03.
"""

from pathlib import Path

import pytest

from spotcool_contracts.events import Run
from spotcool_world.processors.kpi import CROSS_CHECK_PCT, RUNS
from spotcool_world.processors.plant_processor import CONNECTION_RECOVERIES_METRIC
from spotcool_world.runner.report import RecordedRun, check_energy, quote_blockers
from spotcool_world.serializers import EVENTS_FILE, EventRecord, read_records

pytestmark = pytest.mark.eval

RC_BLOCKER = (
    "The plant is rc, our own reduced model, and its cross-check is circular: both energies "
    "come from the same sub-step sums."
)
STUB_BLOCKER = (
    "A stub agent ran it, with perfect perception: quote it only as an upper bound with "
    "perfect perception, labelled so."
)
RC_RUNS = ["hybrid_tuesday", "hybrid_tuesday_again", "all_hands_day", "empty_friday"]


def saved_pct(run: RecordedRun) -> float:
    """Energy saved by the plants' own energy (%), the figure the report quotes."""
    pct = check_energy(run).saved_own
    assert pct is not None, "no saving: a run or a plant's energy is missing"
    return pct


def recoveries(run_dir: Path, run: RecordedRun) -> dict[Run, int]:
    """Each plant run's steps recovered after a dropped connection: the last
    ``plant_connection_recoveries`` metric in the saved record, 0 without one."""
    found: dict[Run, int] = dict.fromkeys(RUNS, 0)
    for record in read_records(run_dir / run.manifest.run_id / EVENTS_FILE):
        data = record.envelope.data if isinstance(record, EventRecord) else {}
        if data.get("name") == CONNECTION_RECOVERIES_METRIC:
            found[data["labels"]["run"]] = int(data["value"])
    return found


def assert_valid(run_dir: Path, run: RecordedRun, blockers: list[str]) -> None:
    """A run the evals may judge: finished, complete, the stub's, cross-checked, clean."""
    manifest = run.manifest
    assert (manifest.ended, manifest.end_reason) == ("finished", "end time reached")
    assert manifest.dropped_events == 0
    assert manifest.settings["stub_agent"] is True
    check = check_energy(run)
    for name in RUNS:
        difference = check.differences[name]
        assert difference is not None and abs(difference) <= CROSS_CHECK_PCT, (name, difference)
    assert run.fallbacks == dict.fromkeys(RUNS, 0)
    assert recoveries(run_dir, run) == dict.fromkeys(RUNS, 0)
    assert quote_blockers(run) == blockers  # paced: nothing about speed 0


@pytest.mark.parametrize("name", RC_RUNS)
def test_every_rc_run_is_valid(name: str, eval_dir: Path, request: pytest.FixtureRequest) -> None:
    """Each RC run finished, lost no events, records the stub, and its integration agrees with
    the plant's own; the quote is blocked by the RC plant and the stub, nothing else."""
    assert_valid(eval_dir, request.getfixturevalue(name), [RC_BLOCKER, STUB_BLOCKER])


def test_hybrid_tuesday_saves_and_keeps_comfort_as_measured(hybrid_tuesday: RecordedRun) -> None:
    """The demo day saves 15.45 % with 4.29 K h of discomfort where people were.

    Measured at speed 1800 on 2026-11-03: 15.4469 % saved (SpotCool 120.08 kWh against the
    baseline's 142.02); comfort where people were, summed over the zones, 4.2928 K h for
    SpotCool and 0 for the baseline. The stub writes at each step's close, so each write takes
    effect from the next step. (An earlier stub that wrote on the step's state, racing the tick,
    had some writes take effect a step sooner: 16.87 % and 4.29 K h.)

    Bands, from the same day measured with the policy changed:
    - the saving within 3 pp: the write timing moves it by up to 1.42 pp (15.45 to 16.87 %),
      while losing the standby lever (the sensor never Unoccupied) drops it to 9.97 %;
    - the comfort within 1 K h: the write timing moves it by 0.17 K h, and losing standby drops
      it to 2.90 K h. Losing the pre-cooling moves it only to 4.41 K h on RC (15.57 % saved),
      so this eval does not catch that; on BOPTEST South floated to 31 degC without it.
    """
    assert saved_pct(hybrid_tuesday) == pytest.approx(15.45, abs=3.0)
    kpis = hybrid_tuesday.manifest.final_kpis
    assert kpis["spotcool"].comfort_where_people_kh == pytest.approx(4.29, abs=1.0)
    assert kpis["baseline"].comfort_where_people_kh == pytest.approx(0.0, abs=1e-9)


@pytest.mark.xfail(
    strict=True,
    reason=(
        "finding: measured 2.34 % saved. all_hands_day leaves every zone empty 07:00-08:30 and "
        "18:00-19:00, and the stub saves there; with every zone occupied 07:00-19:00 it saves "
        "0.00 %. The scenario (WP5) or this check needs the lead's decision."
    ),
)
def test_all_hands_day_claims_no_saving(all_hands_day: RecordedRun) -> None:
    """With everyone in, SpotCool must not claim savings: |saving| < 2 % (the honesty check).

    Measured at speed 1800 on 2026-11-03: 2.3355 % saved (SpotCool 141.47 kWh against the
    baseline's 144.85), comfort 1.95 K h. The scenario is busy only from 08:30 to 18:00 while
    the run spans 07:00-19:00, so the stub empties all five zones for ten of the 48 steps. The
    same day with every zone occupied for the whole run saves 0.0000 % (145.97 kWh both).
    """
    assert abs(saved_pct(all_hands_day)) < 2.0


def test_empty_friday_saves_more_than_hybrid_tuesday(
    empty_friday: RecordedRun, hybrid_tuesday: RecordedRun
) -> None:
    """A quieter day saves more: measured 37.44 % (87.85 against 140.43 kWh) against 15.45 %."""
    assert saved_pct(empty_friday) > saved_pct(hybrid_tuesday)


def test_hybrid_tuesday_reproduces(
    hybrid_tuesday: RecordedRun, hybrid_tuesday_again: RecordedRun
) -> None:
    """Two paced runs of the same day save within 0.5 pp of each other.

    Measured at speed 1800 on 2026-11-03: both 15.4469 %, a spread of 0.0000 pp; every one of
    the 30 stub writes took effect in the step after the one it was made for. A wider spread
    is a finding (writes landing in different steps), not a band to widen.
    """
    assert saved_pct(hybrid_tuesday) == pytest.approx(saved_pct(hybrid_tuesday_again), abs=0.5)


@pytest.mark.live
def test_hybrid_tuesday_on_boptest_saves_as_measured(
    eval_dir: Path, hybrid_tuesday_boptest: RecordedRun
) -> None:
    """On BOPTEST the demo day saves 21.14 %, the policy's upper bound with perfect perception.

    Measured on 2026-11-02 (speed 60, `checkpoint-boptest-stub-s60-3`) and 2026-11-03 (speed 0
    with two recovered steps, `wp12-live-drop`): 21.14 % both times (227.0 against 287.8 kWh),
    by BOPTEST's own `ener_tot`, with the earlier stub that wrote on the step's state. The stub
    now writes at the step's close, which took RC from 16.87 to 15.45 %, so expect this a little
    lower. Not yet measured at speed 1800 with this stub: the band is 2.5 pp until it is. The
    quote is blocked by the stub alone.
    """
    assert_valid(eval_dir, hybrid_tuesday_boptest, [STUB_BLOCKER])
    assert saved_pct(hybrid_tuesday_boptest) == pytest.approx(21.14, abs=2.5)
