import os
from pathlib import Path
from types import SimpleNamespace

import pytest
import results_card
from results_card import QUOTABLE_LABEL, STUB_LABEL, CardRefused, ResultsCard, build_card

from spotcool_contracts import Zone
from spotcool_contracts.events import BmsWriteData, ScenarioStateData, ZoneScene
from spotcool_world.runner.report import RecordedRun, Sample, load_run
from spotcool_world.serializers import RunManifest
from spotcool_world.services.boptest.catalog import load_catalog

AREA = load_catalog().area_m2
LIVE_RUN = "eval-live-hybrid-20261103-122941"


def recorded(
    *, stub: bool = True, plant: str = "boptest-local", speed: float = 60.0, ended: str = "finished"
) -> RecordedRun:
    """A finished, paced BOPTEST run with the live eval's energies and comfort."""
    energy = {"spotcool": 230.4, "baseline": 287.8}
    comfort = {"spotcool": 2.13436946791073, "baseline": 0.3335328942999922}
    manifest = RunManifest.model_validate(
        {
            "run_id": "eval-live-hybrid-test",
            "created_utc": "2026-11-03T07:01:19Z",
            "ended": ended,
            "end_reason": "end time reached",
            "settings": {
                "stub_agent": stub,
                "speed": speed,
                "testcase": "multizone_office_simple_air",
            },
            "git_sha": None,
            "world_version": "0.1.0",
            "contracts_version": "0.1.1",
            "scenario": "hybrid_tuesday",
            "scenario_sha256": None,
            "plant": plant,
            "step_s": 900.0,
            "start_time_s": 0.0,
            "end_time_s": 43200.0,
            "final_kpis": {
                run: {
                    "run": run,
                    "boptest": {"ener_tot": energy[run] / AREA},
                    "comfort_where_people_kh": comfort[run],
                    "provisional": False,
                }
                for run in energy
            },
            "dropped_events": 0,
        }
    )
    # check_energy reads only each run's last cumulative energy.
    samples = {
        run: [Sample(43200.0, SimpleNamespace(energy_kwh=kwh))] for run, kwh in energy.items()
    }
    # Four steps the agent saw as the scenario had them (South full from the second), and its
    # accepted priority-10 writes: what the report needs to call an agent's run acted on.
    people = [{}, {Zone.SOU: 5}, {Zone.SOU: 5}, {Zone.SOU: 5}]
    states = [
        (
            k * 900.0,
            ScenarioStateData(
                zones=[
                    ZoneScene(zone=zone, scene="empty", people=seen.get(zone, 0)) for zone in Zone
                ]
            ),
        )
        for k, seen in enumerate(people)
    ]
    perceptions = [
        (k * 900.0, {zone: seen.get(zone, 0) for zone in Zone}) for k, seen in enumerate(people)
    ]
    writes = [
        (
            900.0,
            BmsWriteData(
                device="SpotCool-Office-BMS",
                object_id=object_id,
                object_name=name,
                priority=10,
                value=value,
                client="127.0.0.1:47820",
                accepted=True,
            ),
        )
        for object_id, name, value in [
            ("binary-value,453", "ZN-SOU-OCC-SENSOR", "Unoccupied"),
            ("analog-value,203", "ZN-SOU-CLG-SP", 28.0),
        ]
    ]
    return RecordedRun(manifest, samples, states, writes=writes, perceptions=perceptions)  # type: ignore[arg-type]


def test_a_stub_run_is_labelled_an_upper_bound():
    card = build_card(recorded())
    assert card.stub and card.label == STUB_LABEL
    assert card.figures == ("19.94 %", "230.4", "287.8", "2.13", "0.33")
    assert card.variables["saved"] == "19.94 %"
    assert card.variables["saved_caption"] == "less HVAC energy than the baseline"
    assert (card.variables["spotcool_kwh"], card.variables["baseline_kwh"]) == (230.4, 287.8)
    assert card.variables["comfort_spotcool"] == "2.13 K h"
    assert card.variables["caveat"].startswith(
        "BOPTEST's occupant heat follows its fixed schedule."
    )
    assert set(card.variables) == set(results_card.CARD_KEYS)
    assert len(card.verdict) == 1 and "stub agent" in card.verdict[0]


def test_only_a_quotable_run_says_spotcool_saved():
    card = build_card(recorded(stub=False))
    assert not card.stub and card.label == QUOTABLE_LABEL and card.verdict == ()


@pytest.mark.parametrize(
    "run, reason",
    [
        (dict(plant="rc"), "reduced model"),
        (dict(speed=0.0), "unpaced"),
        (dict(ended="cancelled"), "did not finish"),
        (dict(stub=False, plant="rc"), "reduced model"),
    ],
)
def test_any_other_blocker_refuses_the_card(run, reason):
    with pytest.raises(CardRefused, match=reason):
        build_card(recorded(**run))


def test_the_card_round_trips_as_json():
    card = build_card(recorded())
    assert ResultsCard.from_json(card.to_json()) == card
    with pytest.raises(ValueError):
        ResultsCard.from_json("{}")


def test_the_cli_needs_the_run_dir(monkeypatch, capsys):
    monkeypatch.delenv(results_card.RUN_DIR_ENV, raising=False)
    assert results_card.main([LIVE_RUN]) == 1
    assert "SPOTCOOL_WORLD_RUN_DIR" in capsys.readouterr().err


def test_the_live_eval_run_gives_the_numbers_md_figures(tmp_path):
    run_dir = Path(os.environ.get(results_card.RUN_DIR_ENV, "C:/Users/hussa/dev/SpotCool-AI/runs"))
    if not (run_dir / LIVE_RUN / "manifest.json").is_file():
        pytest.skip(f"the saved run {LIVE_RUN} is not on this machine")
    out = tmp_path / "card.json"
    assert results_card.main([LIVE_RUN, "--run-dir", str(run_dir), "--out", str(out)]) == 0
    card = ResultsCard.from_json(out.read_text("utf-8"))
    assert card.label == STUB_LABEL and card.figures == (
        "19.94 %",
        "230.4",
        "287.8",
        "2.13",
        "0.33",
    )
    assert card == build_card(load_run(run_dir, LIVE_RUN))
