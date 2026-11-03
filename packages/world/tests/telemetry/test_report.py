from collections.abc import Awaitable, Callable, Mapping
from dataclasses import replace
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import pytest

from spotcool_contracts import Zone
from spotcool_contracts.events import (
    BmsWriteData,
    EventEnvelope,
    EventSource,
    EventType,
    KpiData,
    MetricsData,
    Run,
    ScenarioStateData,
)
from spotcool_world.processors.plant_processor import POWER_FALLBACKS_METRIC
from spotcool_world.runner.cli import main
from spotcool_world.runner.report import (
    NO_AGENT_WRITES,
    OCCUPANCY_CHART,
    POWER_CHART,
    REPORT_FILE,
    ZONES_CHART,
    RecordedRun,
    agent_activity,
    arrivals,
    check_energy,
    load_run,
    quote_blockers,
    verdict,
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
PACED = {"speed": 300.0, "stub_agent": False}  # the settings of a run that may be quoted
QUOTABLE = (
    "Quotable: 50.00 % saved (the plants' own energy; the world's step-mean integration agrees "
    "within 0.000 %)."
)
BRIEF_NOTE = (
    "BOPTEST's occupant heat follows its fixed schedule. Savings in zones that the cameras show "
    "empty may therefore be overstated by up to the occupants' heat. The RC plant and the "
    "`all_hands_day` scenario bracket this. BOPTEST's own discomfort KPIs (`tdis_tot`, "
    "`idis_tot`) follow its fixed schedule, which counts every zone as occupied all day, so they "
    "include zones the scenario leaves empty."
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
AGENT = "192.168.1.30:47820"
"""The agent's BACnet client, as the BMS records its writes."""


def bms_write(
    object_name: str = "ZN-SOU-OCC-SENSOR",
    value: float | str | None = "Unoccupied",
    *,
    client: str = AGENT,
    priority: int = 10,
    accepted: bool = True,
    reason: str | None = None,
    device: str = "SpotCool-Office-BMS",
) -> BmsWriteData:
    """A write as the BMS records it; by default an agent's accepted zone write."""
    return BmsWriteData(
        device=device,
        object_id="binary-value,453",
        object_name=object_name,
        priority=priority,
        value=value,
        client=client,
        accepted=accepted,
        reason=reason,
    )


HEARTBEAT_RELEASE = bms_write(
    "ZN-SOU-CLG-SP", None, client="192.168.1.20:47808", reason="heartbeat timeout"
)
"""The BMS's watchdog releasing an agent's slot."""


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
    fallbacks: Mapping[Run, int] | None = None,
    writes: tuple[BmsWriteData, ...] = (bms_write(),),
    seen: list[Mapping[Zone, int]] | None = None,
) -> Path:
    """A paced, finished BOPTEST run of an hour, without the stub agent: one that may be quoted.

    ``fallbacks`` records each run's power fallbacks as the plant processor does, one metric
    sample with the count so far per fallback. ``writes`` are recorded as the BMS's, at 07:15;
    ``seen`` is the agent's perception of each tick's people (by default the scenario's).
    """
    folder = run_dir / run_id
    kpis: dict[Run, KpiData] = {
        "spotcool": KpiData(
            run="spotcool",
            boptest={"ener_tot": ener_tot[0], "tdis_tot": None},
            comfort_where_people_kh=1.234,
            energy_saved_pct=25.0,  # never shown: the report computes both savings itself
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
                counts = {z.value: n for z, n in (seen or people)[index - 1].items()}
                perceived = {"people": {z.value: 0 for z in Zone} | counts, "masks": {}}
                envelope = EventEnvelope(
                    type=EventType.PERCEPTION,
                    source=EventSource.AGENT,
                    t_ms=0,
                    sim_time_s=t_s - STEP_S,
                    data=perceived,
                )
                writer.write(EventRecord(envelope=envelope))
            if index == 1:
                for write in writes:
                    envelope = EventEnvelope.build(
                        EventType.BMS_WRITE, EventSource.WORLD, write, t_s
                    )
                    writer.write(EventRecord(envelope=envelope))
            temps = {Zone.SOU: south[index], Zone.COR: core[index]}
            spotcool = make_raw(temps, meter_w=1000 / 7)  # 1 kW
            baseline = make_raw(meter_w=2000 / 7)  # 2 kW
            for key in missing_at_start if index == 0 else ():
                del spotcool[key], baseline[key]
            writer.write(RawRecord(run="spotcool", index=index, sim_time_s=t_s, raw=spotcool))
            writer.write(RawRecord(run="baseline", index=index, sim_time_s=t_s, raw=baseline))
        for name, count in (fallbacks or {}).items():
            for so_far in range(1, count + 1):
                sample = MetricsData(
                    name=POWER_FALLBACKS_METRIC, value=so_far, unit="1", labels={"run": name}
                )
                envelope = EventEnvelope.build(
                    EventType.METRICS, EventSource.WORLD, sample, T0 + so_far * STEP_S
                )
                writer.write(EventRecord(envelope=envelope))
        for kpi in kpis.values():
            envelope = EventEnvelope.build(EventType.KPI, EventSource.WORLD, kpi, T0 + 4 * STEP_S)
            writer.write(EventRecord(envelope=envelope))
    manifest = RunManifest(
        run_id=run_id,
        created_utc=datetime(2026, 11, 2, 9, 30, tzinfo=UTC),
        ended="finished",
        end_reason="end time reached",
        settings=dict(PACED),
        git_sha=None,
        world_version="0.1.0",
        contracts_version="0.1.0",
        scenario="hybrid_tuesday",
        scenario_sha256=None,
        plant="boptest-local",
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
    assert "# Run tiny: hybrid_tuesday on the boptest-local plant" in text
    assert "2026-07-19, 07:00 to 08:00 in 15-minute steps; finished" in text
    for row in [
        "| KPI | SpotCool | Baseline |",
        # An hour at 1 and 2 kW, from the raw records: both savings are 50 %, not the 25 % in
        # the manifest's KPIs.
        "| HVAC energy, step-mean integration (kWh) | 1.0 | 2.0 |",
        "| HVAC energy, plant's own: `ener_tot` x 1662.66 m2 (kWh) | 1.0 | 2.0 |",
        "| Difference from the plant's own (%) | 0.000 | 0.000 |",
        "| Energy saved, plants' own (%) | 50.00 | n/a |",
        "| Energy saved, step-mean integration (%) | 50.00 | n/a |",
        "| Power readings that fell back (steps) | 0 | 0 |",
        "| Comfort where people were (K h) | 1.23 | 0.00 |",
        "| BOPTEST `ener_tot` | 0.0006 | 0.0012 |",
        "| BOPTEST `tdis_tot` | n/a | n/a |",
        QUOTABLE,
        "| South | 07:15 | 5 | 30 | 0 |",
        "| Core | 07:45 | 3 | never | 0 |",
        "- SpotCool: Core at 07:45 (3 people)",
        "## Agent activity",
        f"| {AGENT} | 1 |",
        "- Heartbeat-timeout releases: 0.",
        "- Occupancy agreement: 100.00 % (20 of the 20 zone-steps the agent perceived agree with "
        "the scenario on occupied or empty; it perceived 20 of the run's 20).",
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
    assert "| HVAC energy, step-mean integration (kWh) | 1.0 | 2.0 |" in text
    assert "| South | 07:15 | 5 | 30 | 0 |" in text


@pytest.fixture
def quotable(tmp_path: Path, make_raw: Raw, make_state: State) -> RecordedRun:
    """The tiny run read back: it may be quoted."""
    write_run(tmp_path, make_raw, make_state)
    return load_run(tmp_path, "tiny")


def with_manifest(run: RecordedRun, **changes: Any) -> RecordedRun:
    return replace(run, manifest=run.manifest.model_copy(update=changes))


def with_ener_tot(run: RecordedRun, name: Run, ener_tot: float | None) -> RecordedRun:
    kpis = dict(run.manifest.final_kpis)
    kpis[name] = kpis[name].model_copy(update={"boptest": {"ener_tot": ener_tot}})
    return with_manifest(run, final_kpis=kpis)


def test_a_paced_whole_boptest_run_without_a_stub_is_quotable(quotable: RecordedRun) -> None:
    assert quote_blockers(quotable) == []
    assert verdict(quotable) == [QUOTABLE]


@pytest.mark.parametrize(
    ("change", "blocker"),
    [
        (
            lambda run: with_manifest(run, plant="rc"),
            "The plant is rc, our own reduced model, and its cross-check is circular: both "
            "energies come from the same sub-step sums.",
        ),
        (
            lambda run: with_manifest(run, plant="replay"),
            "The plant is replay, not BOPTEST (local or hosted).",
        ),
        (
            lambda run: with_manifest(run, ended="cancelled", end_reason="SIGINT"),
            "The run did not finish: it was cancelled (SIGINT).",
        ),
        (
            lambda run: with_manifest(run, ended="failed", end_reason="spotcool plant step failed"),
            "The run did not finish: it failed (spotcool plant step failed).",
        ),
        (lambda run: with_manifest(run, dropped_events=3), "The recorder dropped 3 events."),
        (
            lambda run: replace(run, samples={"spotcool": run.samples["spotcool"]}),
            "The Baseline run has no measurements, so no saving can be computed.",
        ),
        (
            lambda run: with_ener_tot(run, "baseline", None),
            "The Baseline plant reported no energy, so no saving can be computed.",
        ),
        (
            lambda run: replace(run, fallbacks={"spotcool": 2, "baseline": 0}),
            "The SpotCool run's power fell back to its end-of-step value in 2 of its steps.",
        ),
        (
            lambda run: with_ener_tot(run, "spotcool", 1.002 / AREA),
            "The SpotCool run's step-mean integration is -0.200 % off its plant's own energy, "
            "more than 0.1 %.",
        ),
        (
            lambda run: with_manifest(run, settings=PACED | {"speed": None}),
            "The run was unpaced (speed 0): the agent's writes land at random points between "
            "steps, so it does not reproduce.",
        ),
        (
            lambda run: with_manifest(run, settings=PACED | {"stub_agent": True}),
            "A stub agent ran it, with perfect perception: quote it only as an upper bound with "
            "perfect perception, labelled so.",
        ),
        (lambda run: replace(run, writes=[]), NO_AGENT_WRITES),
        (
            lambda run: replace(run, writes=[*run.writes, (T0 + 2 * STEP_S, HEARTBEAT_RELEASE)]),
            "The BMS's heartbeat watchdog released 1 priority-10 slots: the agent's heartbeat "
            "stopped, so part of the run fell back to the building's own control.",
        ),
    ],
    ids=[
        "rc",
        "not-boptest",
        "cancelled",
        "failed",
        "dropped-events",
        "a-run-missing",
        "no-plant-energy",
        "power-fell-back",
        "off-its-plant",
        "unpaced",
        "stub-agent",
        "no-agent-writes",
        "heartbeat-timeout",
    ],
)
def test_each_blocker_stops_the_quote_on_its_own(
    change: Callable[[RecordedRun], RecordedRun], blocker: str, quotable: RecordedRun
) -> None:
    run = change(quotable)
    assert quote_blockers(run) == [blocker]
    assert verdict(run) == ["Not quotable:", blocker]


def test_two_runs_each_within_2_pct_can_misstate_the_saving_so_both_are_blocked(
    quotable: RecordedRun,
) -> None:
    # Each integration is 1.9 % off its plant's own, in opposite directions.
    run = with_ener_tot(quotable, "spotcool", 1.0 / 0.981 / AREA)
    run = with_ener_tot(run, "baseline", 2.0 / 1.019 / AREA)
    check = check_energy(run)
    assert check.differences == {"spotcool": pytest.approx(-1.9), "baseline": pytest.approx(1.9)}
    assert check.saved_integrated == pytest.approx(50.0)
    assert check.saved_own == pytest.approx(48.06, abs=0.01)  # the saving is 1.9 points off
    assert quote_blockers(run) == [
        "The SpotCool run's step-mean integration is -1.900 % off its plant's own energy, "
        "more than 0.1 %.",
        "The Baseline run's step-mean integration is +1.900 % off its plant's own energy, "
        "more than 0.1 %.",
    ]


def test_power_fallbacks_in_the_recording_reach_the_report_and_block_the_quote(
    tmp_path: Path, make_raw: Raw, make_state: State
) -> None:
    write_run(tmp_path, make_raw, make_state, fallbacks={"spotcool": 2})
    assert load_run(tmp_path, "tiny").fallbacks == {"spotcool": 2, "baseline": 0}  # the last
    text = write_report(tmp_path, "tiny").read_text(encoding="utf-8")
    assert "| Power readings that fell back (steps) | 2 | 0 |" in text
    assert (
        "Not quotable:\n\n- The SpotCool run's power fell back to its end-of-step value in 2 of "
        "its steps.\n"
    ) in text


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
    for row in (
        "| HVAC energy, step-mean integration (kWh) |",
        "| Energy saved, plants' own (%) |",
        "| Energy saved, step-mean integration (%) |",
        "| Difference from the plant's own (%) | 0.000 | 0.000 |",  # the RC plants' sums
        "| Power readings that fell back (steps) | 0 | 0 |",
        "Not quotable:\n\n- The plant is rc, our own reduced model",
        "| Core | 08:30 | 4 |",
        "| North | 08:30 | 2 |",
        "| West | 08:30 | 1 |",
    ):
        assert row in report
    assert (run.folder / ZONES_CHART).exists()


@pytest.mark.parametrize(
    "write",
    [
        bms_write(accepted=False, reason="value-out-of-range"),
        bms_write(priority=8),
        bms_write("SPOTCOOL-HEARTBEAT", "On"),
        bms_write("IDU-VANE-V", "Pos1", device="VRF-Gateway-MeetingRoom"),
        HEARTBEAT_RELEASE,
    ],
    ids=["refused", "operator", "heartbeat", "vrf", "watchdog-release"],
)
def test_only_an_accepted_priority_10_zone_write_counts_as_an_agent_acting(
    write: BmsWriteData, tmp_path: Path, make_raw: Raw, make_state: State
) -> None:
    write_run(tmp_path, make_raw, make_state, writes=(write,))
    run = load_run(tmp_path, "tiny")
    assert agent_activity(run).writes == {}
    assert NO_AGENT_WRITES in quote_blockers(run)
    text = write_report(tmp_path, "tiny").read_text(encoding="utf-8")
    assert "No agent wrote a zone point at priority 10." in text


def test_agent_writes_are_counted_per_client_and_releases_count(
    tmp_path: Path, make_raw: Raw, make_state: State
) -> None:
    writes = (bms_write(), bms_write("ZN-SOU-CLG-SP", None), bms_write(client="10.0.0.9:47820"))
    write_run(tmp_path, make_raw, make_state, writes=writes)
    activity = agent_activity(load_run(tmp_path, "tiny"))
    assert activity.writes == {AGENT: 2, "10.0.0.9:47820": 1}
    assert activity.heartbeat_releases == 0


def test_occupancy_agreement_compares_occupied_or_empty_where_the_agent_counted(
    tmp_path: Path, make_raw: Raw, make_state: State
) -> None:
    # The agent sees 2 in South at 07:00 (empty: wrong), counts 3 for Core's 2 (occupied:
    # right), and leaves West out at 07:30 (no count: not compared).
    seen: list[Mapping[Zone, int]] = [{Zone.COR: 3, Zone.SOU: 2}, *PEOPLE[1:]]
    write_run(tmp_path, make_raw, make_state, seen=seen)
    run = load_run(tmp_path, "tiny")
    t_s, people = run.perceptions[2]
    run.perceptions[2] = (t_s, {zone: n for zone, n in people.items() if zone is not Zone.WES})
    activity = agent_activity(run)
    assert (activity.agreed, activity.compared, activity.zone_steps) == (18, 19, 20)
    assert activity.agreement_pct == pytest.approx(100 * 18 / 19)
