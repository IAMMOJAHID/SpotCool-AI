from collections.abc import Awaitable, Callable, Mapping
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import pytest

from spotcool_contracts import Zone
from spotcool_contracts.events import (
    EventEnvelope,
    EventSource,
    EventType,
    KpiData,
    Run,
    ScenarioStateData,
)
from spotcool_world.runner.cli import main
from spotcool_world.runner.report import (
    OCCUPANCY_CHART,
    POWER_CHART,
    REPORT_FILE,
    ZONES_CHART,
    arrivals,
    load_run,
    write_report,
)
from spotcool_world.serializers import (
    EVENTS_FILE,
    MANIFEST_FILE,
    EventRecord,
    RawRecord,
    RunManifest,
    RunWriter,
)
from spotcool_world.services.boptest.catalog import bms_signal, load_catalog

Raw = Callable[..., dict[str, float]]
State = Callable[..., ScenarioStateData]
STEP_S = 900.0
T0 = 199 * 86_400.0 + 7 * 3600  # 07:00 on 2026-07-19
AREA = load_catalog().area_m2
# The plants' own ener_tot (kWh/m2) of the tiny run: the hour at 1 kW and at 2 kW.
ENER_TOT: tuple[float | None, float | None] = (1.0 / AREA, 2.0 / AREA)
BASIS = (
    "Energy saved compares the HVAC energy: each step's mean power times the step, added up from "
    "the first step."
)
BRIEF_NOTE = (
    "BOPTEST's occupant heat follows its fixed schedule. Savings in zones that the cameras show "
    "empty may therefore be overstated by up to the occupants' heat. The RC plant and the "
    "`all_hands_day` scenario bracket this."
)
# People per tick (07:00, 07:15, 07:30, 07:45): South arrives at 07:15, Core is there from the
# start (no arrival), leaves at 07:30 and comes back at 07:45.
PEOPLE: list[Mapping[Zone, int]] = [
    {Zone.COR: 2},
    {Zone.COR: 2, Zone.SOU: 5},
    {Zone.SOU: 5},
    {Zone.COR: 3, Zone.SOU: 5},
]
# SpotCool's temperatures at the stamps 07:00 ... 08:00; the baseline holds 24 degC.
SOUTH = [27.0, 27.0, 25.5, 24.4, 24.0]  # within 0.5 K of 24 degC at 07:45: 30 min after 07:15
CORE = [26.0] * 5  # never within 0.5 K


def write_run(
    run_dir: Path,
    make_raw: Raw,
    make_state: State,
    *,
    people: list[Mapping[Zone, int]] = PEOPLE,
    south: list[float] = SOUTH,
    core: list[float] = CORE,
    run_id: str = "tiny",
    missing_at_start: tuple[str, ...] = (),
    ener_tot: tuple[float | None, float | None] = ENER_TOT,
) -> Path:
    folder = run_dir / run_id
    kpis: dict[Run, KpiData] = {
        "spotcool": KpiData(
            run="spotcool",
            boptest={"ener_tot": ener_tot[0], "tdis_tot": None},
            comfort_where_people_kh=1.234,
            energy_saved_pct=25.0,
            provisional=False,
        ),
        "baseline": KpiData(
            run="baseline",
            boptest={"ener_tot": ener_tot[1], "tdis_tot": None},
            comfort_where_people_kh=0.0,
            provisional=False,
        ),
    }
    with RunWriter(folder / EVENTS_FILE) as writer:
        for index in range(len(people) + 1):
            t_s = T0 + index * STEP_S
            if index:
                state = make_state(people[index - 1])
                envelope = EventEnvelope.build(
                    EventType.SCENARIO_STATE, EventSource.WORLD, state, t_s - STEP_S
                )
                writer.write(EventRecord(envelope=envelope))
            temps = {Zone.SOU: south[index], Zone.COR: core[index]}
            spotcool = make_raw(temps, meter_w=1000 / 7)  # 1 kW
            baseline = make_raw(meter_w=2000 / 7)  # 2 kW
            for key in missing_at_start if index == 0 else ():
                del spotcool[key], baseline[key]
            writer.write(RawRecord(run="spotcool", index=index, sim_time_s=t_s, raw=spotcool))
            writer.write(RawRecord(run="baseline", index=index, sim_time_s=t_s, raw=baseline))
        for kpi in kpis.values():
            envelope = EventEnvelope.build(EventType.KPI, EventSource.WORLD, kpi, T0 + 4 * STEP_S)
            writer.write(EventRecord(envelope=envelope))
    manifest = RunManifest(
        run_id=run_id,
        created_utc=datetime(2026, 11, 2, 9, 30, tzinfo=UTC),
        ended="finished",
        end_reason="end time reached",
        settings={},
        git_sha=None,
        world_version="0.1.0",
        contracts_version="0.1.0",
        scenario="hybrid_tuesday",
        scenario_sha256=None,
        plant="rc",
        step_s=STEP_S,
        start_time_s=T0,
        end_time_s=T0 + len(people) * STEP_S,
        final_kpis=kpis,
        dropped_events=0,
    )
    (folder / MANIFEST_FILE).write_text(manifest.model_dump_json(), encoding="utf-8")
    return folder


def test_a_tiny_run_renders_with_its_kpis_recovery_times_and_charts(
    tmp_path: Path, make_raw: Raw, make_state: State
) -> None:
    folder = write_run(tmp_path, make_raw, make_state)
    path = write_report(tmp_path, "tiny")
    assert path == folder / REPORT_FILE
    text = path.read_text(encoding="utf-8")
    assert "# Run tiny: hybrid_tuesday on the rc plant" in text
    assert "2026-07-19, 07:00 to 08:00 in 15-minute steps; finished" in text
    for row in [
        "| KPI | SpotCool | Baseline |",
        "| HVAC energy (kWh) | 1.0 | 2.0 |",  # an hour at 1 and 2 kW, from the raw records
        "| Plant's own energy, `ener_tot` x 1662.66 m2 (kWh) | 1.0 | 2.0 |",
        "| Difference from the plant's own (%) | 0.0 | 0.0 |",
        "| Energy saved (%) | 25.0 | n/a |",
        "| Comfort where people were (K h) | 1.23 | 0.00 |",
        "| BOPTEST `ener_tot` | 0.0006 | 0.0012 |",
        "| BOPTEST `tdis_tot` | n/a | n/a |",
        f"{BASIS} Each run's HVAC energy is within 2 % of its plant's own, so the saving is "
        "cross-checked: it is the figure to quote.",
        "| South | 07:15 | 5 | 30 | 0 |",
        "| Core | 07:45 | 3 | never | 0 |",
        "- SpotCool: Core at 07:45 (3 people)",
    ]:
        assert row in text
    assert "| Core | 07:00" not in text  # occupied from the start: not an arrival
    assert BRIEF_NOTE in text
    for chart in (POWER_CHART, ZONES_CHART, OCCUPANCY_CHART):
        assert f"]({chart})" in text
        assert (folder / chart).read_bytes().startswith(b"\x89PNG\r\n\x1a\n")


def test_a_state_without_every_value_is_not_drawn_but_its_power_counts(
    tmp_path: Path, make_raw: Raw, make_state: State
) -> None:
    setpoints = tuple(
        bms_signal(f"ZN-{zone.value.upper()}-{point}-SP")
        for zone in Zone
        for point in ("CLG", "HTG")
    )
    write_run(tmp_path, make_raw, make_state, missing_at_start=setpoints)  # as BOPTEST may
    run = load_run(tmp_path, "tiny")
    assert [s.t_s for s in run.samples["spotcool"]] == [T0 + k * STEP_S for k in range(1, 5)]
    assert run.samples["spotcool"][-1].summary.energy_kwh == pytest.approx(1.0)  # from 07:00
    text = write_report(tmp_path, "tiny").read_text(encoding="utf-8")
    assert "| HVAC energy (kWh) | 1.0 | 2.0 |" in text and "| South | 07:15 | 5 | 30 | 0 |" in text


@pytest.mark.parametrize(
    ("ener_tot", "lines"),
    [
        (
            (1.05 / AREA, 2.0 / AREA),  # SpotCool's plant used 1.05 kWh: -4.8 %
            [
                "| Difference from the plant's own (%) | -4.8 | 0.0 |",
                f"**Warning:** {BASIS} The HVAC energy differs from the plant's own by more "
                "than 2 % (SpotCool -4.8 %), so the saving is not cross-checked: do not quote it.",
            ],
        ),
        (
            (1.0 / AREA, None),
            [
                "| Difference from the plant's own (%) | 0.0 | n/a |",
                f"{BASIS} A plant reported no energy, so the saving is not cross-checked.",
            ],
        ),
    ],
    ids=["off-by-more-than-2-pct", "no-ener-tot"],
)
def test_a_saving_that_fails_the_cross_check_is_flagged(
    ener_tot: tuple[float | None, float | None],
    lines: list[str],
    tmp_path: Path,
    make_raw: Raw,
    make_state: State,
) -> None:
    write_run(tmp_path, make_raw, make_state, ener_tot=ener_tot)
    text = write_report(tmp_path, "tiny").read_text(encoding="utf-8")
    for line in lines:
        assert line in text
    assert "the figure to quote" not in text


def test_recovery_reads_temperatures_from_the_arrival_on(
    tmp_path: Path, make_raw: Raw, make_state: State
) -> None:
    write_run(tmp_path, make_raw, make_state, south=[27.0, 24.5, 30.0, 30.0, 30.0])
    found = arrivals(load_run(tmp_path, "tiny"))
    south = next(a for a in found if a.zone is Zone.SOU)
    assert south.minutes == {"spotcool": 0.0, "baseline": 0.0}  # at the setpoint + 0.5 K: done


ALL_RECOVERED = "Every arrival recovered to the baseline's cooling setpoint plus 0.5 K."


@pytest.mark.parametrize(
    ("people", "core", "line"),
    [
        ([{}] * 4, CORE, "No zone went from empty to occupied during the run."),
        (PEOPLE, [24.0] * 5, ALL_RECOVERED),
    ],
    ids=["no-arrivals", "all-recovered"],
)
def test_the_recovery_section_says_when_there_is_nothing_to_list(
    people: list[Mapping[Zone, int]],
    core: list[float],
    line: str,
    tmp_path: Path,
    make_raw: Raw,
    make_state: State,
) -> None:
    write_run(tmp_path, make_raw, make_state, people=people, core=core)
    assert line in write_report(tmp_path, "tiny").read_text(encoding="utf-8")


def test_the_report_command_writes_the_report_or_says_why_not(
    tmp_path: Path,
    make_raw: Raw,
    make_state: State,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    monkeypatch.setenv("SPOTCOOL_WORLD_RUN_DIR", str(tmp_path / "runs"))
    folder = write_run(tmp_path / "runs", make_raw, make_state)
    assert main(["report", "tiny"]) == 0
    assert str(folder / REPORT_FILE) in capsys.readouterr().out
    assert main(["report", "missing"]) == 1
    assert "no recorded run missing" in capsys.readouterr().out
    assert main(["report", "../tiny"]) == 1
    assert "not a run id" in capsys.readouterr().out
    (folder / MANIFEST_FILE).unlink()
    assert main(["report", "tiny"]) == 1
    assert "its record is incomplete" in capsys.readouterr().out


@pytest.mark.asyncio
async def test_a_recorded_world_run_gets_its_report(
    record_world: Callable[..., Awaitable[Any]],
) -> None:
    run = await record_world(steps=7)  # 07:00 to 08:45: the staff arrive at 08:30
    report = write_report(run.folder.parent, run.folder.name).read_text(encoding="utf-8")
    assert "| HVAC energy (kWh) |" in report and "| Energy saved (%) |" in report
    assert "| Difference from the plant's own (%) | 0.0 | 0.0 |" in report  # the RC plants'
    assert "the saving is cross-checked: it is the figure to quote." in report
    for row in ("| Core | 08:30 | 4 |", "| North | 08:30 | 2 |", "| West | 08:30 | 1 |"):
        assert row in report
    assert (run.folder / ZONES_CHART).exists()
