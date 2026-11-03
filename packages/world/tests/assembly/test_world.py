"""Whole-world runs on loopback: the stub agent, the BOPTEST stop hook, Ctrl+C and errors.

Each run binds the BMS, the HTTP server and the dashboard on free 127.0.0.1 ports and records
into the tmp dir. After it, every port can be bound again and no task is left.
"""

import asyncio
import hashlib
import signal
import socket
import time
from collections.abc import Callable, Mapping
from pathlib import Path
from typing import Any

import pytest
from bacpypes3.ipv4.app import NormalApplication
from bacpypes3.local.device import DeviceObject
from bacpypes3.pdu import Address, IPv4Address
from bacpypes3.primitivedata import Null

from spotcool_contracts import Zone
from spotcool_contracts.events import (
    BmsWriteData,
    EventEnvelope,
    EventSource,
    EventType,
    VrfStateData,
)
from spotcool_world.bus import Subscription
from spotcool_world.clocks import SimClock
from spotcool_world.processors import EVENTS_TOPIC
from spotcool_world.processors.kpi import RUNS
from spotcool_world.processors.plant_processor import CONNECTION_RECOVERIES_METRIC
from spotcool_world.runner.assembly import World
from spotcool_world.runner.report import (
    REPORT_FILE,
    STUB_AGENT_NOTE,
    check_energy,
    load_run,
    quote_blockers,
    write_report,
)
from spotcool_world.serializers import (
    EVENTS_FILE,
    MANIFEST_FILE,
    EventRecord,
    RunManifest,
    read_records,
)
from spotcool_world.services import WorldSettings
from spotcool_world.services import rc as rc_module
from spotcool_world.services.rc import RcPlant
from spotcool_world.transports.bacnet.vrf import BacnetVrfGateway
from spotcool_world.workers import WorkerState

pytestmark = pytest.mark.bacnet

DAY_S = 199 * 86_400.0
MakeSettings = Callable[..., WorldSettings]

OPERATOR_HOLD = """\
name: operator_hold
description: South's meeting ends at 07:45; an operator holds its setpoint from 07:15 to 08:15.
day: 199
start: "07:00"
end: "09:00"
zones:
  cor:
    - {at: "07:00", scene: empty, people: 0}
  nor:
    - {at: "07:00", scene: empty, people: 0}
  sou:
    - {at: "07:00", scene: meeting, people: 3}
    - {at: "07:45", scene: empty, people: 0}
  eas:
    - {at: "07:00", scene: empty, people: 0}
  wes:
    - {at: "07:00", scene: empty, people: 0}
events: []
"""
SOUTH_SP = "analog-value,203"  # ZN-SOU-CLG-SP


def manifest(world: World) -> RunManifest:
    path = world.settings.run_dir / world.run_id / MANIFEST_FILE
    return RunManifest.model_validate_json(path.read_text("utf-8"))


def recorded(world: World) -> list[EventEnvelope]:
    path = world.settings.run_dir / world.run_id / EVENTS_FILE
    return [r.envelope for r in read_records(path) if isinstance(r, EventRecord)]


def statuses(events: list[EventEnvelope]) -> list[tuple[str, str | None]]:
    return [(e.data["status"], e.data["message"]) for e in events if e.type is EventType.RUN_STATUS]


def report_top(world: World) -> str:
    """The first lines of the run's report, written now."""
    write_report(world.settings.run_dir, world.run_id)
    lines = (world.settings.run_dir / world.run_id / REPORT_FILE).read_text("utf-8").splitlines()
    return "\n".join(lines[:4])


def sha256(path: str) -> str:
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


async def taken(subscription: Subscription) -> list[Any]:
    """The messages waiting on a subscription, without waiting for more."""
    messages: list[Any] = []
    while True:
        try:
            async with asyncio.timeout(0):
                messages.append(await anext(subscription))
        except (TimeoutError, StopAsyncIteration):
            return messages


async def until(condition: Callable[[], bool], timeout_s: float = 10.0) -> None:
    deadline = time.perf_counter() + timeout_s
    for _ in range(100_000):
        if condition():
            return
        if time.perf_counter() > deadline:
            break
        await asyncio.sleep(0.01)
    raise AssertionError("timed out")


def leftover_tasks() -> set[asyncio.Task[Any]]:
    return {t for t in asyncio.all_tasks() if t is not asyncio.current_task()}


def assert_all_stopped(world: World, *, forced: bool = False) -> None:
    """Every worker is done, every port the world bound is free again and, unless the run was
    cancelled by force (a second Ctrl+C), no task is left."""
    done = (WorkerState.FINISHED, WorkerState.CANCELLED)
    assert world.pipeline.state in done
    assert all(worker.state in done for worker in world.workers)
    assert forced or leftover_tasks() == set()
    assert world.bms.address is None
    assert world.vrf.address is None
    settings = world.settings
    for kind, port in (
        (socket.SOCK_STREAM, settings.http_port),
        (socket.SOCK_STREAM, settings.dashboard_port),
        (socket.SOCK_DGRAM, settings.bms_port),
        (socket.SOCK_DGRAM, settings.vrf_port),
    ):
        with socket.socket(socket.AF_INET, kind) as probe:
            probe.bind(("127.0.0.1", port))


@pytest.mark.asyncio
async def test_two_hours_with_the_stub_agent(make_settings: MakeSettings, two_hours: str) -> None:
    world = World(make_settings(scenario=two_hours, stub_agent=True), run_id="stub-run")
    await asyncio.wait_for(world.run(handle_sigint=False), 60)

    done = manifest(world)
    assert (done.ended, done.end_reason, done.dropped_events) == ("finished", "end time reached", 0)
    assert (done.start_time_s, done.end_time_s) == (DAY_S + 7 * 3600, DAY_S + 9 * 3600)
    assert done.scenario_sha256 == sha256(two_hours)
    assert done.settings["stub_agent"] is True
    kpis = done.final_kpis
    assert sorted(kpis) == ["baseline", "spotcool"]
    assert not any(kpi.provisional for kpi in kpis.values())
    saved = kpis["spotcool"].energy_saved_pct
    assert saved is not None and saved > 0

    events = recorded(world)
    assert statuses(events) == [("running", None), ("finished", None)]
    writes = [BmsWriteData.model_validate(e.data) for e in events if e.type is EventType.BMS_WRITE]
    assert writes and all(write.accepted and write.priority == 10 for write in writes)
    clients = {write.client for write in writes}
    assert len(clients) == 1 and clients != {world.bms_endpoint}  # the stub's own UDP port
    assert {
        ("SPOTCOOL-HEARTBEAT", "On"),
        ("ZN-COR-OCC-SENSOR", "Unoccupied"),
        ("ZN-COR-CLG-SP", 28.0),
        ("ZN-COR-OCC-SENSOR", "Occupied"),  # people from 08:00
        ("ZN-COR-CLG-SP", None),  # ...and the schedule's setpoint again
        ("ZN-SOU-OCC-SENSOR", "Occupied"),  # pre-cooling the 08:30 booking from 08:00
        ("ZN-EAS-OCC-SENSOR", "Unoccupied"),
        ("ZN-EAS-CLG-SP", 28.0),
        ("ZN-WES-OCC-SENSOR", "Occupied"),
    } <= {(write.object_name, write.value) for write in writes}
    agent = [e for e in events if e.source is EventSource.AGENT]
    assert {e.type for e in agent} == {
        EventType.PERCEPTION,
        EventType.DECISION,
        EventType.EXPLANATION,
    }
    assert sum(e.type is EventType.PERCEPTION for e in agent) == 8  # one per step
    assert all(e.data["stub"] is True for e in agent)
    assert STUB_AGENT_NOTE in report_top(world)
    assert_all_stopped(world)


@pytest.mark.asyncio
async def test_the_stub_backs_off_from_an_operator_and_resumes_after_the_release(
    make_settings: MakeSettings, tmp_path: Path
) -> None:
    path = tmp_path / "operator_hold.yaml"
    path.write_text(OPERATOR_HOLD, encoding="utf-8")
    settings = make_settings(scenario=str(path), stub_agent=True, speed=1800.0)  # 0.5 s a step
    world = World(settings, run_id="operator")
    events = world.bus.subscribe(EVENTS_TOPIC, maxsize=100_000)
    slots: list[Any] = []  # South's setpoint's priority array just before the release

    async def operator() -> None:
        """An operator's BACnet client, as YABE would be: holds South at 23 degC at priority 8."""
        device = DeviceObject(
            objectIdentifier=("device", 1998), objectName="operator", vendorIdentifier=999
        )
        app = NormalApplication(device, IPv4Address("127.0.0.1:0"))
        bms = Address(world.bms_endpoint)
        try:
            async for envelope in events:
                if envelope.type is not EventType.SCENARIO_STATE or envelope.sim_time_s is None:
                    continue
                clock = SimClock.clock_text(envelope.sim_time_s)
                if clock == "07:15":
                    await app.write_property(bms, SOUTH_SP, "present-value", 23.0, priority=8)
                elif clock == "08:15":
                    slots.extend(await app.read_property(bms, SOUTH_SP, "priority-array"))
                    await app.write_property(bms, SOUTH_SP, "present-value", Null(()), priority=8)
                    return
        finally:
            app.close()

    await asyncio.wait_for(asyncio.gather(world.run(handle_sigint=False), operator()), 60)
    events.close()

    assert manifest(world).ended == "finished"
    held = [(index + 1, slot.real) for index, slot in enumerate(slots) if slot.null is None]
    assert held == [(8, 23.0)]  # the stub never wrote South's setpoint during the hold
    events_ = recorded(world)
    writes = [BmsWriteData.model_validate(e.data) for e in events_ if e.type is EventType.BMS_WRITE]
    south = [(w.priority, w.value, w.accepted) for w in writes if w.object_name == "ZN-SOU-CLG-SP"]
    assert south == [(8, 23.0, True), (8, None, True), (10, 28.0, True)]  # resumed after it
    run = load_run(settings.run_dir, world.run_id)
    setpoint = {
        SimClock.clock_text(sample.t_s): next(
            z.cooling_sp_c for z in sample.summary.zones if z.zone is Zone.SOU
        )
        for sample in run.samples["spotcool"]
    }
    assert (setpoint["08:00"], setpoint["08:15"]) == (23.0, 23.0)  # the operator's, applied
    assert setpoint["09:00"] == 28.0  # the stub's, once the operator let go
    said = [e for e in events_ if e.source is EventSource.AGENT and "backed off" in str(e.data)]
    assert [(e.type, e.sim_time_s) for e in said] == [
        (EventType.DECISION, DAY_S + 7.75 * 3600),  # South empties at 07:45: 28 wanted
        (EventType.EXPLANATION, DAY_S + 7.75 * 3600),
    ]  # said once, though it tried again at each step of the hold
    assert said[1].data["text"] == (
        "South: ZN-SOU-CLG-SP is held at 23.0 by an operator at priority 8 -> backed off from "
        "writing 28.0; trying again each step"
    )
    assert_all_stopped(world)


@pytest.mark.asyncio
async def test_a_full_day_with_the_stub_agent_matches_the_plants_own_energy(
    make_settings: MakeSettings,
) -> None:
    settings = make_settings(scenario="hybrid_tuesday", stub_agent=True)
    # The world's own HTTP server answers the 14:15 complaint with a 404 at once, where a
    # closed port takes three refused connections of about 2 s each on Windows.
    agent = f"http://127.0.0.1:{settings.http_port}"
    world = World(settings.model_copy(update={"agent_base_url": agent}), run_id="full-day")
    await asyncio.wait_for(world.run(handle_sigint=False), 120)
    run = load_run(settings.run_dir, world.run_id)
    assert (run.manifest.ended, run.manifest.end_time_s) == ("finished", DAY_S + 19 * 3600)
    check = check_energy(run)
    for name in RUNS:  # the RC plant sums the same sub-steps as the world's integration
        own = check.own[name]
        assert own is not None and check.integrated[name] == pytest.approx(own, rel=1e-9)
    saved = check.saved_own
    assert saved is not None and saved > 0
    assert check.saved_integrated == pytest.approx(saved, rel=1e-9)
    assert run.manifest.final_kpis["spotcool"].energy_saved_pct == pytest.approx(saved, rel=1e-9)
    assert quote_blockers(run) == [
        "The plant is rc, our own reduced model, and its cross-check is circular: both "
        "energies come from the same sub-step sums.",
        "The run was unpaced (speed 0): the agent's writes land at random points between "
        "steps, so it does not reproduce.",
        "A stub agent ran it, with perfect perception: quote it only as an upper bound with "
        "perfect perception, labelled so.",
    ]
    assert_all_stopped(world)


@pytest.mark.asyncio
async def test_a_power_fallback_is_recorded_reported_and_blocks_the_quote(
    make_settings: MakeSettings, two_hours: str, fake_boptest: Any
) -> None:
    world = World(make_settings(plant="boptest-local", scenario=two_hours), run_id="fell-back")
    fake_boptest.clients[0].results_failures = 2  # SpotCool's first two steps
    await asyncio.wait_for(world.run(handle_sigint=False), 60)
    run = load_run(world.settings.run_dir, world.run_id)
    assert run.manifest.ended == "finished"  # never fatal
    assert run.fallbacks == {"spotcool": 2, "baseline": 0}
    errors = [e.data for e in recorded(world) if e.type is EventType.ERROR]
    assert [(e["where"], e["fatal"]) for e in errors] == [("PlantProcessor(spotcool)", False)] * 2
    assert "step 0 (07:00-07:15) reports its end-of-step power" in errors[0]["message"]
    fell_back = "The SpotCool run's power fell back to its end-of-step value in 2 of its steps."
    assert fell_back in quote_blockers(run)
    write_report(world.settings.run_dir, world.run_id)
    report = (world.settings.run_dir / world.run_id / REPORT_FILE).read_text("utf-8")
    assert "| Power readings that fell back (steps) | 2 | 0 |" in report
    assert f"- {fell_back}" in report
    assert_all_stopped(world)


@pytest.mark.asyncio
async def test_a_lost_advance_answer_is_recovered_recorded_and_still_quotable(
    make_settings: MakeSettings, two_hours: str, fake_boptest: Any
) -> None:
    clean = World(make_settings(plant="boptest-local", scenario=two_hours), run_id="clean")
    await asyncio.wait_for(clean.run(handle_sigint=False), 60)
    world = World(make_settings(plant="boptest-local", scenario=two_hours), run_id="dropped")
    fake_boptest.clients[2].lost_answers = 1  # SpotCool's first step happens; its answer is lost
    await asyncio.wait_for(world.run(handle_sigint=False), 60)
    run = load_run(world.settings.run_dir, world.run_id)
    assert run.manifest.ended == "finished"
    events = recorded(world)
    errors = [e.data for e in events if e.type is EventType.ERROR]
    assert [(e["where"], e["fatal"]) for e in errors] == [("PlantProcessor(spotcool)", False)]
    assert "step 0 (07:00-07:15) was recovered after its connection" in errors[0]["message"]
    recoveries = [
        (e.data["value"], e.data["labels"])
        for e in events
        if e.type is EventType.METRICS and e.data["name"] == CONNECTION_RECOVERIES_METRIC
    ]
    assert recoveries == [(1, {"run": "spotcool"})]
    expected = load_run(clean.settings.run_dir, clean.run_id)
    assert run.fallbacks == {"spotcool": 0, "baseline": 0}
    assert quote_blockers(run) == quote_blockers(expected)  # the report is unaffected
    assert check_energy(run) == check_energy(expected)  # the recovered state is the plant's own
    assert_all_stopped(world)


@pytest.mark.parametrize("plant", ["boptest-local", "boptest-hosted"])
@pytest.mark.asyncio
async def test_both_boptest_tests_are_stopped_after_the_run(
    plant: str, make_settings: MakeSettings, two_hours: str, fake_boptest: Any
) -> None:
    world = World(make_settings(plant=plant, scenario=two_hours), run_id="boptest")
    await asyncio.wait_for(world.run(handle_sigint=False), 60)
    assert manifest(world).ended == "finished"
    assert [client.events for client in fake_boptest.clients] == [
        ["select", "stop sent", "stop done", "closed", "closed"]  # closed again: idempotent
    ] * 2
    final = manifest(world).final_kpis.values()
    assert all(kpi.boptest["ener_tot"] for kpi in final)  # the plants' own KPIs, merged
    assert STUB_AGENT_NOTE not in report_top(world)  # no stub: no label
    assert_all_stopped(world)


@pytest.mark.asyncio
async def test_ctrl_c_stops_the_world_and_both_boptest_tests(
    make_settings: MakeSettings, fake_boptest: Any
) -> None:
    settings = make_settings(plant="boptest-local", speed=18_000.0, stub_agent=True)
    world = World(settings, run_id="interrupted")  # a step every 0.05 s
    events = world.bus.subscribe(EVENTS_TOPIC, maxsize=100_000)
    before = signal.getsignal(signal.SIGINT)

    async def press_ctrl_c() -> None:
        async for envelope in events:
            if envelope.type is EventType.TICK and envelope.data["index"] == 2:
                signal.raise_signal(signal.SIGINT)
                return

    await asyncio.wait_for(asyncio.gather(world.run(), press_ctrl_c()), 30)
    events.close()
    done = manifest(world)
    assert (done.ended, done.end_reason) == ("cancelled", "SIGINT")
    assert done.end_time_s < world.compiled.end_s
    assert sorted(done.final_kpis) == ["baseline", "spotcool"]
    assert statuses(recorded(world)) == [("running", None), ("cancelled", "SIGINT")]
    for client in fake_boptest.clients:
        assert client.events[:4] == ["select", "stop sent", "stop done", "closed"]
    assert signal.getsignal(signal.SIGINT) == before
    assert all(worker.state is WorkerState.FINISHED for worker in world.workers)
    assert_all_stopped(world)


@pytest.mark.asyncio
async def test_a_second_ctrl_c_never_cuts_a_boptest_stop_request(
    make_settings: MakeSettings, fake_boptest: Any
) -> None:
    fake_boptest.stop_s = 1.0  # a slow BOPTEST: each stop request takes a second
    world = World(make_settings(plant="boptest-local", speed=18_000.0), run_id="cut")
    events = world.bus.subscribe(EVENTS_TOPIC, maxsize=100_000)

    async def press_ctrl_c_twice() -> None:
        async for envelope in events:
            if envelope.type is EventType.TICK and envelope.data["index"] == 2:
                break
        signal.raise_signal(signal.SIGINT)  # stops the run: both stop requests go out...
        await until(lambda: all(c.stop_sent.is_set() for c in fake_boptest.clients))
        signal.raise_signal(signal.SIGINT)  # ...and this cancels everything meanwhile

    started = time.perf_counter()
    await asyncio.wait_for(asyncio.gather(world.run(), press_ctrl_c_twice()), 30)
    events.close()
    assert time.perf_counter() - started >= 1.0  # run() waited for the requests
    for client in fake_boptest.clients:  # each request finished before its client closed
        assert client.events[:4] == ["select", "stop sent", "stop done", "closed"]
    assert world.pipeline.state is WorkerState.CANCELLED
    assert_all_stopped(world, forced=True)
    # Cancelled mid-shutdown, uvicorn leaves its lifespan tasks (HttpServerWorker's servers).
    leftovers = leftover_tasks()
    for task in leftovers:
        task.cancel()
    await asyncio.gather(*leftovers, return_exceptions=True)


def sent_together(fake_boptest: Any) -> bool:
    """Whether both plants' stop requests went out at once, not one after the other (1 s)."""
    first, second = (client.stop_sent_s for client in fake_boptest.clients)
    return first is not None and second is not None and abs(first - second) < 0.5


@pytest.mark.asyncio
async def test_a_ctrl_c_stops_both_slow_boptest_tests_at_once(
    make_settings: MakeSettings, fake_boptest: Any
) -> None:
    fake_boptest.stop_s = 1.0
    world = World(make_settings(plant="boptest-local", speed=18_000.0), run_id="graceful")
    events = world.bus.subscribe(EVENTS_TOPIC, maxsize=100_000)

    async def press_ctrl_c() -> None:
        async for envelope in events:
            if envelope.type is EventType.TICK and envelope.data["index"] == 2:
                signal.raise_signal(signal.SIGINT)
                return

    await asyncio.wait_for(asyncio.gather(world.run(), press_ctrl_c()), 30)
    events.close()
    assert manifest(world).ended == "cancelled"
    assert sent_together(fake_boptest)
    assert_all_stopped(world)


@pytest.mark.asyncio
async def test_a_second_ctrl_c_stops_both_slow_boptest_tests_at_once(
    make_settings: MakeSettings, fake_boptest: Any
) -> None:
    fake_boptest.stop_s, fake_boptest.kpis_s = 1.0, 30.0  # the final KPIs hang: no stop sent yet
    world = World(make_settings(plant="boptest-local", speed=18_000.0), run_id="forced")
    events = world.bus.subscribe(EVENTS_TOPIC, maxsize=100_000)

    async def press_ctrl_c_twice() -> None:
        async for envelope in events:
            if envelope.type is EventType.TICK and envelope.data["index"] == 2:
                break
        signal.raise_signal(signal.SIGINT)
        await until(lambda: all(c.kpis_asked.is_set() for c in fake_boptest.clients))
        signal.raise_signal(signal.SIGINT)  # cancels the KPIs: cleanup() stops the plants

    started = time.perf_counter()
    await asyncio.wait_for(asyncio.gather(world.run(), press_ctrl_c_twice()), 30)
    events.close()
    assert time.perf_counter() - started < 20.0  # never waited for the KPIs
    assert sent_together(fake_boptest)
    for client in fake_boptest.clients:
        assert client.events[:4] == ["select", "stop sent", "stop done", "closed"]
    assert_all_stopped(world, forced=True)
    leftovers = leftover_tasks()  # uvicorn's lifespan tasks, as above
    for task in leftovers:
        task.cancel()
    await asyncio.gather(*leftovers, return_exceptions=True)


class BrokenPlant(RcPlant):
    """An RC plant whose every step fails."""

    async def step(self, overrides: Mapping[str, float | None]) -> dict[str, float]:
        raise RuntimeError("the plant broke")


@pytest.mark.asyncio
async def test_an_error_above_the_emitter_is_published_once(
    make_settings: MakeSettings, two_hours: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(rc_module, "RcPlant", BrokenPlant)
    world = World(make_settings(scenario=two_hours), run_id="broken")
    events = world.bus.subscribe(EVENTS_TOPIC, maxsize=100_000)
    await asyncio.wait_for(world.run(handle_sigint=False), 60)
    published = await taken(events)
    events.close()
    errors = [e for e in published if e.type is EventType.ERROR]
    assert sorted(e.data["where"] for e in errors) == [
        "PlantProcessor(baseline)",
        "PlantProcessor(spotcool)",
    ]
    assert all(e.data["fatal"] and "the plant broke" in e.data["message"] for e in errors)
    [(status, message)] = statuses(published)[1:]
    assert status == "failed" and message is not None and "the plant broke" in message
    done = manifest(world)
    assert done.ended == "failed" and "the plant broke" in done.end_reason  # as the status says
    assert_all_stopped(world)


@pytest.mark.asyncio
async def test_an_error_below_the_emitter_is_published_once(
    make_settings: MakeSettings, two_hours: str
) -> None:
    settings = make_settings(scenario=two_hours)
    world = World(settings, run_id="taken")
    taken_dir = settings.run_dir / "taken"  # a record appears after the world was built
    taken_dir.mkdir(parents=True)
    (taken_dir / EVENTS_FILE).write_text("", encoding="utf-8")
    events = world.bus.subscribe(EVENTS_TOPIC, maxsize=100_000)
    await asyncio.wait_for(world.run(handle_sigint=False), 60)
    errors = [e for e in await taken(events) if e.type is EventType.ERROR]
    events.close()
    assert len(errors) == 1  # the emitter made it an event; the world did not again
    assert errors[0].data["where"].startswith("RecorderProcessor")
    assert "already holds a recording" in errors[0].data["message"]
    assert (taken_dir / EVENTS_FILE).read_text(encoding="utf-8") == ""  # never appended to
    assert not (taken_dir / MANIFEST_FILE).exists()
    assert_all_stopped(world)


@pytest.mark.asyncio
async def test_a_taken_bms_port_names_the_endpoint(make_settings: MakeSettings) -> None:
    settings = make_settings()
    with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as holder:
        holder.bind(("127.0.0.1", settings.bms_port))
        world = World(settings, run_id="no-bms")
        with pytest.raises(OSError, match=f"BMS UDP 127.0.0.1:{settings.bms_port} is in use"):
            await world.run(handle_sigint=False)
    assert world.pipeline.state is WorkerState.IDLE  # nothing ran
    assert not (settings.run_dir / "no-bms").exists()


@pytest.mark.asyncio
async def test_a_run_serves_the_vrf_and_records_its_state(
    make_settings: MakeSettings, two_hours: str
) -> None:
    settings = make_settings(scenario=two_hours, vrf_profile="lg")
    world = World(settings, run_id="vrf")
    served: list[str | None] = []
    world.pipeline.add_event_handler(
        "on_pipeline_started", lambda *_: served.append(world.vrf.address)
    )
    await asyncio.wait_for(world.run(handle_sigint=False), 60)
    assert served == [world.vrf_endpoint] == [f"127.0.0.1:{settings.vrf_port}"]
    states = [
        VrfStateData.model_validate(e.data)
        for e in recorded(world)
        if e.type is EventType.VRF_STATE
    ]
    assert states and all(state.profile == "lg" for state in states)
    assert (states[0].vane_h_pos, states[0].vane_h_angle_deg) == (None, None)
    assert manifest(world).ended == "finished"
    assert_all_stopped(world)


@pytest.mark.asyncio
async def test_a_taken_vrf_port_names_the_endpoint(make_settings: MakeSettings) -> None:
    settings = make_settings()
    with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as holder:
        holder.bind(("127.0.0.1", settings.vrf_port))
        world = World(settings, run_id="no-vrf")
        with pytest.raises(OSError, match=f"VRF UDP 127.0.0.1:{settings.vrf_port} is in use"):
            await world.run(handle_sigint=False)
    assert world.pipeline.state is WorkerState.IDLE  # nothing ran
    assert world.bms.address is None  # the BMS, started first, is stopped again
    assert not (settings.run_dir / "no-vrf").exists()


@pytest.mark.asyncio
async def test_a_failing_vrf_tick_never_ends_the_run(
    make_settings: MakeSettings, two_hours: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    def broken(self: BacnetVrfGateway, dt_s: float) -> None:
        raise RuntimeError("the unit broke")

    monkeypatch.setattr(BacnetVrfGateway, "advance", broken)
    world = World(make_settings(scenario=two_hours), run_id="vrf-broken")
    await asyncio.wait_for(world.run(handle_sigint=False), 60)
    assert manifest(world).ended == "finished"
    assert_all_stopped(world)  # the VRF worker finished too, and every port is free
