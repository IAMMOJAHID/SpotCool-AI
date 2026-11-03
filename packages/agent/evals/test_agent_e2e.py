"""The end-to-end eval: the real agent drives the RC world for a day, judged from the record.

``hybrid_tuesday`` on RC, paced at speed 300 (a step every 3 s), without the stub. The agent
counts people with the blob counter on the world's synthetic frames, which is exact there, so
the run measures everything but SAM: the clock, the timing, the policy, the BACnet writes, the
heartbeat, the events and the release. The comparison is a stub run of the same day in the
same session (speed 1800, the golden evals'), whose writes also all land one step after their
decision, so both runs have the same write timing.

Measured on 2026-11-03: the agent saved 15.4469 % (the stub 15.4469 %, a difference of
0.0000 pp), all 30 of its paired writes (40 zone writes with the 19:00 releases) landed one
step later, its occupancy agreed on all 240 zone-steps, and both runs took 175 s of wall time.
A step without any count (every zone unknown) releases everything and moves the saving by
about 0.2 pp, which is why the coverage of the agreement is checked too.
"""

from collections import Counter
from pathlib import Path
from typing import Any

import pytest

from spotcool_contracts.events import EventSource, EventType, TickData
from spotcool_contracts.priorities import AGENT_PRIORITY
from spotcool_world.runner.report import (
    RecordedRun,
    agent_activity,
    check_energy,
    quote_blockers,
    write_report,
)
from spotcool_world.serializers import EVENTS_FILE, EventRecord, read_records

pytestmark = pytest.mark.eval

RC_BLOCKER = (
    "The plant is rc, our own reduced model, and its cross-check is circular: both energies "
    "come from the same sub-step sums."
)
AgentRun = Any
"""``conftest.AgentRun``: a test module cannot import its conftest (pytest's importlib mode)."""
RELEASED_BY_S = 199 * 86_400.0 + 19.25 * 3600
"""The 19:15 stamp: every slot the agent holds is released by then."""


def saved_pct(run: RecordedRun) -> float:
    """Energy saved by the plants' own energy (%), the figure the report quotes."""
    pct = check_energy(run).saved_own
    assert pct is not None, "no saving: a run or a plant's energy is missing"
    return pct


def write_lags(run: RecordedRun) -> Counter[int | None]:
    """How many steps after its decision the BMS took each agent write (the world's golden
    evals' ``write_lags``, copied): ``None`` counts a decision whose write it never took."""
    folder = run.manifest.settings["run_dir"]
    decided: dict[str, list[float]] = {}
    taken: dict[str, list[int]] = {}
    under_way = 0
    for record in read_records(Path(folder) / run.manifest.run_id / EVENTS_FILE):
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
            lags[steps[index] - made if index < len(steps) else None] += 1
    return lags


def test_the_agents_day_is_valid_and_blocked_only_by_the_rc_plant(agent_day: AgentRun) -> None:
    """A finished, paced, complete run that an agent drove: only the RC plant blocks the quote."""
    manifest = agent_day.run.manifest
    assert (manifest.ended, manifest.end_reason) == ("finished", "end time reached")
    assert manifest.dropped_events == 0
    assert manifest.settings["stub_agent"] is False
    assert quote_blockers(agent_day.run) == [RC_BLOCKER]
    assert agent_day.answered == 1  # the 14:15 complaint, by voice or its chat fallback
    assert set(agent_day.dialled) == {
        f"ws://127.0.0.1:{manifest.settings['dashboard_port']}/ws/agent"
    }  # never /ws/events


def test_the_saving_is_the_stubs_under_the_same_write_timing(
    agent_day: AgentRun, stub_day: RecordedRun, capsys: pytest.CaptureFixture[str]
) -> None:
    """(a) Within 0.5 pp of the stub's saving on the same day; both write a step late."""
    agent, stub = saved_pct(agent_day.run), saved_pct(stub_day)
    activity = agent_activity(agent_day.run)
    with capsys.disabled():  # the eval's numbers, before any assertion can stop the test
        print(
            f"\nagent eval: saved {agent:.4f} % (stub {stub:.4f} %), agent lags "
            f"{dict(write_lags(agent_day.run))}, stub lags {dict(write_lags(stub_day))}, "
            f"agreement {activity.agreed}/{activity.compared} of {activity.zone_steps}, "
            f"writes {activity.writes}"
        )
    assert set(write_lags(stub_day)) == {1}
    assert agent == pytest.approx(stub, abs=0.5)


def test_the_agents_writes_land_one_step_after_their_decision(agent_day: AgentRun) -> None:
    """(b) At least 98 % of the agent's writes take effect from the step after its decision."""
    lags = write_lags(agent_day.run)
    assert sum(lags.values()) >= 30
    assert lags[1] / sum(lags.values()) >= 0.98, dict(lags)


def test_the_report_shows_the_agents_writes_and_its_occupancy_agreement(
    agent_day: AgentRun,
) -> None:
    """(c) The agent-activity section lists the agent's writes; agreement is at least 98 %."""
    activity = agent_activity(agent_day.run)
    assert set(activity.writes) == {agent_day.client}
    assert activity.writes[agent_day.client] >= 30
    pct = activity.agreement_pct
    assert pct is not None and pct >= 98.0
    assert activity.compared / activity.zone_steps >= 0.98  # no step without counts
    folder = agent_day.run.manifest.settings["run_dir"]
    text = write_report(Path(folder), agent_day.run.manifest.run_id).read_text(encoding="utf-8")
    assert "## Agent activity" in text and f"| {agent_day.client} |" in text


def test_the_heartbeat_never_lapsed(agent_day: AgentRun) -> None:
    """(d) The BMS's heartbeat watchdog released nothing."""
    assert agent_activity(agent_day.run).heartbeat_releases == 0


def test_everything_is_released_by_the_19_15_stamp(agent_day: AgentRun) -> None:
    """(e) Each zone point's last accepted agent write is a release, stamped by 19:15."""
    last: dict[str, tuple[float | None, object]] = {}
    for t_s, write in agent_day.run.writes:
        if (
            write.client == agent_day.client
            and write.accepted
            and write.object_name.startswith("ZN-")
        ):
            last[write.object_name] = (t_s, write.value)
    assert len(last) == 10
    for name, (t_s, value) in last.items():
        assert value is None, name
        assert t_s is not None and t_s <= RELEASED_BY_S, name
