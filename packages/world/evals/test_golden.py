"""Golden evals: the scenario library with the stub agent, judged from the saved runs.

pipecat's rule: an eval runs the world once, saves the run, and judges only what was saved, so a
BOPTEST run can be judged again without running the building. The judging goes through
``runner/report.py``: ``load_run`` (in ``conftest.py``), ``check_energy``, ``quote_blockers`` and
the manifest's final KPIs, the figures the report and the video quote. Two things the report
does not read come from the same saved record: the connection recoveries, and when the BMS took
each stub write (``write_lags``).

``uv run pytest -m eval`` runs them on the RC plant (CI's ``evals`` job, about two minutes);
``uv run pytest -m "eval and live"`` runs ``hybrid_tuesday`` on BOPTEST (the lead does; CI never
does). Every band below was set around values measured at ``SPEED`` (1800) on 2026-11-03.
"""

from collections import Counter
from pathlib import Path

import pytest

from spotcool_contracts.events import EventSource, EventType, Run, TickData
from spotcool_contracts.priorities import AGENT_PRIORITY
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
RC_RUNS = [
    "hybrid_tuesday",
    "hybrid_tuesday_again",
    "all_hands_day",
    "empty_friday",
    "all_day_occupied",
]
NOT_TAKEN = None
"""``write_lags``'s key for a decision whose write the BMS never took."""


def saved_pct(run: RecordedRun) -> float:
    """Energy saved by the plants' own energy (%), the figure the report quotes."""
    pct = check_energy(run).saved_own
    assert pct is not None, "no saving: a run or a plant's energy is missing"
    return pct


def run_dir_of(run: RecordedRun) -> Path:
    """Where the run was recorded, as its manifest's settings say."""
    return Path(run.manifest.settings["run_dir"])


def recoveries(run: RecordedRun) -> dict[Run, int]:
    """Each plant run's steps recovered after a dropped connection: the last
    ``plant_connection_recoveries`` metric in the saved record, 0 without one."""
    found: dict[Run, int] = dict.fromkeys(RUNS, 0)
    for record in read_records(run_dir_of(run) / run.manifest.run_id / EVENTS_FILE):
        data = record.envelope.data if isinstance(record, EventRecord) else {}
        if data.get("name") == CONNECTION_RECOVERIES_METRIC:
            found[data["labels"]["run"]] = int(data["value"])
    return found


def write_lags(run: RecordedRun) -> Counter[int | None]:
    """How many steps after its decision the BMS took each stub write, from the saved record.

    A decision is stamped with the state of the step whose close made it. The BMS takes a write
    at a tick and records it as a ``bms_write`` before that tick's own ``tick`` event, so the
    step under way is the count of ``tick`` events seen. Each point's writes pair with its
    decisions in order. A decision made at the last step's close is never taken, since no step
    follows; any other without a write counts under ``NOT_TAKEN``. Back-offs write nothing, and
    the releases at ``stop()`` come after the record has closed.

    Args:
        run: The recorded run.
    """
    decided: dict[str, list[float]] = {}
    taken: dict[str, list[int]] = {}
    under_way = 0
    for record in read_records(run_dir_of(run) / run.manifest.run_id / EVENTS_FILE):
        if not isinstance(record, EventRecord):
            continue
        event = record.envelope
        if event.type is EventType.TICK and event.source is EventSource.WORLD:
            under_way = TickData.model_validate(event.data).index + 1
        elif event.type is EventType.DECISION and event.source is EventSource.AGENT:
            if not event.data["reason"].startswith("backed off") and event.sim_time_s is not None:
                decided.setdefault(event.data["point"], []).append(event.sim_time_s)
        elif event.type is EventType.BMS_WRITE and event.data["priority"] == AGENT_PRIORITY:
            taken.setdefault(event.data["object_name"], []).append(under_way)
    start_s, step_s, last_s = run.manifest.start_time_s, run.manifest.step_s, run.states[-1][0]
    lags: Counter[int | None] = Counter()
    for point, times in decided.items():
        steps = taken.get(point, [])
        for index, t_s in enumerate(t for t in times if t < last_s):
            made = round((t_s - start_s) / step_s)
            lags[steps[index] - made if index < len(steps) else NOT_TAKEN] += 1
    return lags


def assert_valid(run: RecordedRun, blockers: list[str]) -> None:
    """A run the evals may judge: finished, complete, the stub's, cross-checked, clean, and
    every stub write taken by the BMS in the step right after its decision."""
    manifest = run.manifest
    assert (manifest.ended, manifest.end_reason) == ("finished", "end time reached")
    assert manifest.dropped_events == 0
    assert manifest.settings["stub_agent"] is True
    check = check_energy(run)
    for name in RUNS:
        difference = check.differences[name]
        assert difference is not None and abs(difference) <= CROSS_CHECK_PCT, (name, difference)
    assert run.fallbacks == dict.fromkeys(RUNS, 0)
    assert recoveries(run) == dict.fromkeys(RUNS, 0)
    assert quote_blockers(run) == blockers  # paced: nothing about speed 0
    lags = write_lags(run)
    assert lags and set(lags) == {1}, f"stub writes by steps late: {dict(lags)}"


@pytest.mark.parametrize("name", RC_RUNS)
def test_every_rc_run_is_valid(name: str, request: pytest.FixtureRequest) -> None:
    """Each RC run finished, lost no events, records the stub, its integration agrees with the
    plant's own, every stub write took effect from the next step, and the quote is blocked by
    the RC plant and the stub, nothing else."""
    assert_valid(request.getfixturevalue(name), [RC_BLOCKER, STUB_BLOCKER])


def test_hybrid_tuesday_saves_and_keeps_comfort_as_measured(hybrid_tuesday: RecordedRun) -> None:
    """The demo day saves 15.45 % with 4.29 K h of discomfort where people were.

    Measured at speed 1800 on 2026-11-03: 15.4469 % saved (SpotCool 120.08 kWh against the
    baseline's 142.02); comfort where people were, summed over the zones, 4.2928 K h for
    SpotCool and 0 for the baseline. All 30 stub writes were taken a step after their decision.

    Bands, from the same day measured with the policy changed:
    - the saving within 3 pp: write timing moves it by up to 1.42 pp (15.45 % with every write
      a step late, 16.87 % with 11 of 30 taken in their own step), while losing the standby
      lever (the sensor never Unoccupied) drops it to 9.97 %;
    - the comfort within 1 K h: write timing moves it by 0.17 K h, and losing standby drops it
      to 2.90 K h. Losing the pre-cooling moves it only to 4.41 K h on RC (15.57 % saved), so
      this eval does not catch that; on BOPTEST South floated to 31 degC without it.
    """
    assert saved_pct(hybrid_tuesday) == pytest.approx(15.45, abs=3.0)
    kpis = hybrid_tuesday.manifest.final_kpis
    assert kpis["spotcool"].comfort_where_people_kh == pytest.approx(4.29, abs=1.0)
    assert kpis["baseline"].comfort_where_people_kh == pytest.approx(0.0, abs=1e-9)


def test_with_every_zone_occupied_spotcool_claims_no_saving(
    all_day_occupied: RecordedRun,
) -> None:
    """The honesty check: with every zone occupied for the whole run, |saving| < 0.5 %.

    The control scenario (``evals/scenarios/all_day_occupied.yaml``) has people in every zone
    from 07:00 to 19:00, so the stub has nothing to switch off. Measured at speed 1800 on
    2026-11-03: 0.0000 % (145.97 kWh for both runs), comfort 0 for both. The band leaves room
    for a retuned RC model, never for a policy that cools less where people are.
    """
    assert abs(saved_pct(all_day_occupied)) < 0.5


def test_all_hands_day_saves_only_its_empty_hours(all_hands_day: RecordedRun) -> None:
    """Everyone is in from 08:30 to 18:00; the saving comes from the empty hours around it.

    Measured at speed 1800 on 2026-11-03: 2.3355 % saved (SpotCool 141.47 kWh against the
    baseline's 144.85), comfort 1.95 K h. Nobody is in before 08:30 or after 18:00, ten of the
    48 steps, while the baseline cools from 06:00 to 19:00; with every zone occupied all day
    the saving is 0 (the control). The band is 1 pp.
    """
    assert saved_pct(all_hands_day) == pytest.approx(2.34, abs=1.0)


def test_the_emptier_the_day_the_more_it_saves(
    all_day_occupied: RecordedRun,
    all_hands_day: RecordedRun,
    hybrid_tuesday: RecordedRun,
    empty_friday: RecordedRun,
) -> None:
    """The fewer people, the more it saves: control < all hands < hybrid Tuesday < empty Friday.

    Measured at speed 1800 on 2026-11-03: 0.0000 < 2.3355 < 15.4469 < 37.4430 % (empty Friday:
    87.85 against 140.43 kWh).
    """
    days = [all_day_occupied, all_hands_day, hybrid_tuesday, empty_friday]
    saved = [saved_pct(day) for day in days]
    assert saved == sorted(saved) and len(set(saved)) == len(saved), saved


def test_hybrid_tuesday_reproduces(
    hybrid_tuesday: RecordedRun, hybrid_tuesday_again: RecordedRun
) -> None:
    """Two paced runs of the same day save within 0.5 pp of each other.

    Measured at speed 1800 on 2026-11-03: both 15.4469 %, a spread of 0.0000 pp. A wider
    spread is a finding (writes taken in different steps), not a band to widen.
    """
    assert saved_pct(hybrid_tuesday) == pytest.approx(saved_pct(hybrid_tuesday_again), abs=0.5)


@pytest.mark.live
def test_hybrid_tuesday_on_boptest_saves_as_measured(
    hybrid_tuesday_boptest: RecordedRun, capsys: pytest.CaptureFixture[str]
) -> None:
    """On BOPTEST the demo day saves about 21 %, the policy's upper bound with perfect perception.

    It runs at speed 60, the paced checkpoint's, so a BOPTEST step and the stub's writes both
    fit in a step's 15 s, and every write must be taken in the step after its decision.
    Measured on 2026-11-02 (speed 60, `checkpoint-boptest-stub-s60-3`) and 2026-11-03 (speed 0
    with two recovered steps, `wp12-live-drop`): 21.14 % both times (227.0 against 287.8 kWh),
    by BOPTEST's own `ener_tot`, with an earlier stub that wrote on the step's state. The stub
    now writes at the step's close, which took RC from 16.87 to 15.45 %, so expect this a little
    lower. Not yet measured with this stub: the band is 2.5 pp until it is. The quote is
    blocked by the stub alone. The run's figures print before the checks.
    """
    run, check = hybrid_tuesday_boptest, check_energy(hybrid_tuesday_boptest)
    run_dir, run_id = run_dir_of(run), run.manifest.run_id
    with capsys.disabled():  # the run's figures, before any assertion can stop the test
        print(
            f"\nlive eval run: {run_dir / run_id}\n"
            f"  saved {check.saved_own} % by the plants' own energy: SpotCool "
            f"{check.own['spotcool']} kWh, baseline {check.own['baseline']} kWh\n"
            f"  stub writes by steps late: {dict(write_lags(run))}\n"
            f"  report: SPOTCOOL_WORLD_RUN_DIR={run_dir} uv run spotcool-world report {run_id}"
        )
    assert_valid(run, [STUB_BLOCKER])
    assert saved_pct(run) == pytest.approx(21.14, abs=2.5)
