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

from spotcool_contracts.events import BmsWriteData, EventEnvelope, EventSource, EventType
from spotcool_world.bus import Subscription
from spotcool_world.processors import EVENTS_TOPIC
from spotcool_world.processors.kpi import (
    CROSS_CHECK_PCT,
    RUNS,
    energy_saved_pct,
    plant_energy_kwh,
)
from spotcool_world.runner.assembly import World
from spotcool_world.runner.report import REPORT_FILE, STUB_AGENT_NOTE, load_run, write_report
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
from spotcool_world.workers import WorkerState

pytestmark = pytest.mark.bacnet

DAY_S = 199 * 86_400.0
MakeSettings = Callable[..., WorldSettings]


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
    settings = world.settings
    for kind, port in (
        (socket.SOCK_STREAM, settings.http_port),
        (socket.SOCK_STREAM, settings.dashboard_port),
        (socket.SOCK_DGRAM, settings.bms_port),
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
    plants: dict[str, float] = {}
    for name in RUNS:
        own = plant_energy_kwh(run.manifest.final_kpis[name].boptest)
        assert own is not None
        energy = run.samples[name][-1].summary.energy_kwh
        assert energy == pytest.approx(own, rel=CROSS_CHECK_PCT / 100)  # the cross-check
        plants[name] = own
    saved = run.manifest.final_kpis["spotcool"].energy_saved_pct
    by_plants = energy_saved_pct(plants["baseline"], plants["spotcool"])
    assert saved is not None and by_plants is not None and saved > 0
    assert saved == pytest.approx(by_plants, rel=CROSS_CHECK_PCT / 100)
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
    assert manifest(world).ended == "cancelled"
    [(status, message)] = statuses(published)[1:]
    assert status == "failed" and message is not None and "the plant broke" in message
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
