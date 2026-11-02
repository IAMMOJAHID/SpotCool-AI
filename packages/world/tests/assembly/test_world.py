"""Whole-world runs on loopback: the stub agent, the BOPTEST stop hook, Ctrl+C and errors.

Each run binds the BMS, the HTTP server and the dashboard on free 127.0.0.1 ports and records
into the tmp dir. After it, every port can be bound again and no task is left.
"""

import asyncio
import hashlib
import signal
import socket
from collections.abc import Callable, Mapping
from pathlib import Path
from typing import Any

import pytest

from spotcool_contracts.events import BmsWriteData, EventEnvelope, EventSource, EventType
from spotcool_world.bus import Subscription
from spotcool_world.processors import EVENTS_TOPIC
from spotcool_world.runner.assembly import World
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


def assert_all_stopped(world: World) -> None:
    """Every worker is done, no task is left, and every port the world bound is free again."""
    done = (WorkerState.FINISHED, WorkerState.CANCELLED)
    assert world.pipeline.state in done
    assert all(worker.state is WorkerState.FINISHED for worker in world.workers)
    assert {t for t in asyncio.all_tasks() if t is not asyncio.current_task()} == set()
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
    world = World(make_settings(scenario=two_hours), run_id="stub-run", stub_agent=True)
    await asyncio.wait_for(world.run(handle_sigint=False), 60)

    done = manifest(world)
    assert (done.ended, done.end_reason, done.dropped_events) == ("finished", "end time reached", 0)
    assert (done.start_time_s, done.end_time_s) == (DAY_S + 7 * 3600, DAY_S + 9 * 3600)
    assert done.scenario_sha256 == sha256(two_hours)
    kpis = done.final_kpis
    assert sorted(kpis) == ["baseline", "spotcool"]
    assert not any(kpi.provisional for kpi in kpis.values())
    saved = kpis["spotcool"].energy_saved_pct
    assert saved is not None and saved > 0

    events = recorded(world)
    writes = [BmsWriteData.model_validate(e.data) for e in events if e.type is EventType.BMS_WRITE]
    assert writes and all(write.accepted and write.priority == 10 for write in writes)
    clients = {write.client for write in writes}
    assert len(clients) == 1 and clients != {world.bms_endpoint}  # the stub's own UDP port
    assert {
        ("SPOTCOOL-HEARTBEAT", "On"),
        ("ZN-COR-OCC-SENSOR", "Unoccupied"),
        ("ZN-COR-OCC-SENSOR", "Occupied"),  # people from 08:00
        ("ZN-SOU-OCC-SENSOR", "Occupied"),  # pre-cooling the 08:30 booking from 08:00
        ("ZN-EAS-OCC-SENSOR", "Unoccupied"),
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
    assert_all_stopped(world)


@pytest.mark.parametrize("plant", ["boptest-local", "boptest-hosted"])
@pytest.mark.asyncio
async def test_both_boptest_tests_are_stopped_after_the_run(
    plant: str, make_settings: MakeSettings, two_hours: str, fake_boptest: list[Any]
) -> None:
    world = World(make_settings(plant=plant, scenario=two_hours), run_id="boptest")
    await asyncio.wait_for(world.run(handle_sigint=False), 60)
    assert manifest(world).ended == "finished"
    assert len(fake_boptest) == 2
    assert all(fake.stops >= 1 for fake in fake_boptest)
    assert [fake.starts[0].time_period for fake in fake_boptest] == ["peak_cool_day"] * 2
    final = manifest(world).final_kpis.values()
    assert all(kpi.boptest["ener_tot"] for kpi in final)  # the plants' own KPIs, merged
    assert_all_stopped(world)


@pytest.mark.asyncio
async def test_ctrl_c_stops_the_world_and_both_boptest_tests(
    make_settings: MakeSettings, fake_boptest: list[Any]
) -> None:
    settings = make_settings(plant="boptest-local", speed=18_000.0)  # a step every 0.05 s
    world = World(settings, run_id="interrupted", stub_agent=True)
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
    assert len(fake_boptest) == 2
    assert all(fake.stops >= 1 for fake in fake_boptest)
    assert signal.getsignal(signal.SIGINT) == before
    assert_all_stopped(world)


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
    errors = [e for e in await taken(events) if e.type is EventType.ERROR]
    events.close()
    assert sorted(e.data["where"] for e in errors) == [
        "PlantProcessor(baseline)",
        "PlantProcessor(spotcool)",
    ]
    assert all(e.data["fatal"] and "the plant broke" in e.data["message"] for e in errors)
    assert manifest(world).ended == "cancelled"
    assert_all_stopped(world)


@pytest.mark.asyncio
async def test_an_error_below_the_emitter_is_published_once(
    make_settings: MakeSettings, two_hours: str
) -> None:
    settings = make_settings(scenario=two_hours)
    taken_dir = settings.run_dir / "taken"
    taken_dir.mkdir(parents=True)
    (taken_dir / EVENTS_FILE).write_text("", encoding="utf-8")
    world = World(settings, run_id="taken")
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
