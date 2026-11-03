"""World assembly: what a run is built from, for every plant provider. Nothing runs or binds."""

import re
from collections.abc import Callable
from typing import Any

import pytest

from spotcool_contracts import Zone
from spotcool_world.pipeline import ParallelPipeline, Pipeline
from spotcool_world.processors import (
    BusPublisher,
    CameraSceneProcessor,
    EventEmitterProcessor,
    FramePublisher,
    KpiProcessor,
    RecorderProcessor,
    ScenarioProcessor,
    TelemetryProcessor,
    TickSource,
)
from spotcool_world.processors.kpi import RUNS
from spotcool_world.processors.run_status import RunStatusProcessor
from spotcool_world.runner import assembly
from spotcool_world.runner.assembly import World, default_run_id, scenario_occupancy
from spotcool_world.scenarios import compile_scenario, load_scenario
from spotcool_world.serializers import EVENTS_FILE, RUN_ID_PATTERN
from spotcool_world.services import PlantStartParams, WorldSettings, create_plant
from spotcool_world.services.boptest.catalog import bms_signal
from spotcool_world.services.boptest.plant import BoptestPlant
from spotcool_world.services.rc import RcPlant
from spotcool_world.services.replay import ReplayPlant
from spotcool_world.stub_agent.worker import StubAgentWorker
from spotcool_world.workers.http_server import HttpServerWorker
from spotcool_world.workers.occupant_worker import OccupantWorker
from spotcool_world.workers.vrf_worker import VrfWorker

DAY_S = 199 * 86_400.0
LAN = "192.0.2.7"  # a documentation address standing in for this PC's
MakeSettings = Callable[..., WorldSettings]


def test_the_pipeline_is_the_ticks_order_of_work(make_settings: MakeSettings) -> None:
    world = World(make_settings(), run_id="built")
    pipeline = world.pipeline.pipeline
    assert isinstance(pipeline, Pipeline)
    processors = pipeline.processors
    kinds = [
        TickSource,
        RunStatusProcessor,
        ScenarioProcessor,
        FramePublisher,
        None,  # bms.input()
        ParallelPipeline,
        None,  # bms.output()
        CameraSceneProcessor,
        TelemetryProcessor,
        KpiProcessor,
        EventEmitterProcessor,
        RecorderProcessor,
        BusPublisher,
    ]
    assert len(processors) == len(kinds)
    for processor, kind in zip(processors, kinds, strict=True):
        assert kind is None or isinstance(processor, kind)
    assert processors[4] is world.bms.input()
    assert processors[6] is world.bms.output()


def test_the_run_window_is_the_scenarios(make_settings: MakeSettings) -> None:
    settings = make_settings(scenario="empty_friday", speed=60.0)
    start = World(settings, run_id="window").pipeline.params.start_frame()
    assert (start.start_time_s, start.end_time_s) == (DAY_S + 7 * 3600, DAY_S + 19 * 3600)
    assert (start.run_id, start.scenario, start.plant, start.speed, start.step_s) == (
        "window",
        "empty_friday",  # the manifest hashes the scenario's file from this
        "rc",
        60.0,
        900.0,
    )


@pytest.mark.parametrize("plant", ["rc", "boptest-local", "boptest-hosted", "replay"])
def test_the_world_builds_with_every_plant(
    plant: str, make_settings: MakeSettings, fake_boptest: Any
) -> None:
    replay_run = "recorded" if plant == "replay" else None
    settings = make_settings(plant=plant, replay_run=replay_run, stub_agent=True)
    world = World(settings)
    if plant.startswith("boptest"):
        assert all(isinstance(world.plants[run], BoptestPlant) for run in RUNS)
        url = {"boptest-local": "http://127.0.0.1:8000", "boptest-hosted": "http://api.boptest.net"}
        assert [client.base_url for client in fake_boptest.clients] == [url[plant]] * 2
    else:
        kind = RcPlant if plant == "rc" else ReplayPlant
        assert all(type(world.plants[run]) is kind for run in RUNS)
    assert [(type(worker), worker.name) for worker in world.workers] == [
        (OccupantWorker, "occupants"),
        (HttpServerWorker, "http"),
        (HttpServerWorker, "dashboard"),
        (VrfWorker, "vrf"),
        (StubAgentWorker, "stub-agent"),
    ]


def test_the_stub_agent_comes_from_the_settings_and_marks_the_run_id(
    make_settings: MakeSettings,
) -> None:
    plain = World(make_settings())
    assert not any(isinstance(worker, StubAgentWorker) for worker in plain.workers)
    assert re.fullmatch(RUN_ID_PATTERN, plain.run_id)
    assert plain.run_id.endswith("-hybrid_tuesday")
    stub = World(make_settings(stub_agent=True))
    assert isinstance(stub.workers[-1], StubAgentWorker)
    assert re.fullmatch(RUN_ID_PATTERN, stub.run_id)
    assert stub.run_id.endswith("-hybrid_tuesday-stub")


def test_urls_name_every_server(make_settings: MakeSettings) -> None:
    settings = make_settings()
    urls = World(settings, run_id="urls").urls()
    http, dashboard = settings.http_port, settings.dashboard_port
    assert urls["cameras"] == f"http://127.0.0.1:{http}/cameras"
    assert urls["calendar"] == f"http://127.0.0.1:{http}/calendar/rooms/meeting-south/schedule"
    assert urls["event stream"] == f"ws://127.0.0.1:{dashboard}/ws/events"
    assert urls["dashboard"].startswith(f"http://127.0.0.1:{dashboard}/")
    assert urls["BMS"] == f"127.0.0.1:{settings.bms_port} (BACnet/IP)"
    assert urls["VRF"] == f"127.0.0.1:{settings.vrf_port} (BACnet/IP)"


def test_a_wildcard_host_shows_the_lan_and_the_loopback_address(
    make_settings: MakeSettings, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(assembly, "primary_ipv4", lambda: LAN)
    settings = make_settings(http_host="0.0.0.0")
    urls = World(settings, run_id="lan").urls()
    port = settings.http_port
    assert urls["cameras"] == (
        f"http://{LAN}:{port}/cameras (this PC: http://127.0.0.1:{port}/cameras)"
    )
    stream = f"ws://{LAN}:{settings.dashboard_port}/ws/events"
    assert urls["event stream"].startswith(f"{stream} (this PC: ws://127.0.0.1:")
    wildcard6 = make_settings(http_host="::")
    port = wildcard6.http_port
    assert World(wildcard6, run_id="lan6").urls()["cameras"] == (
        f"http://{LAN}:{port}/cameras (this PC: http://[::1]:{port}/cameras)"
    )
    loopback6 = make_settings(http_host="::1")
    assert World(loopback6, run_id="ip6").urls()["cameras"] == (
        f"http://[::1]:{loopback6.http_port}/cameras"
    )


def test_a_bad_scenario_run_id_or_bms_address_fails_the_build(
    make_settings: MakeSettings,
) -> None:
    with pytest.raises(ValueError, match="unknown scenario"):
        World(make_settings(scenario="no_such_day"))
    with pytest.raises(ValueError, match="not a run id"):
        World(make_settings(), run_id="../escape")
    with pytest.raises(ValueError, match="bms_address 'localhost' is not an IPv4 address"):
        World(make_settings(bms_address="localhost"))
    assert World(make_settings(bms_address="127.0.0.1/8")).bms_endpoint.startswith("127.0.0.1:")


def test_a_run_id_that_holds_a_recording_is_refused(make_settings: MakeSettings) -> None:
    settings = make_settings()
    record = settings.run_dir / "taken" / EVENTS_FILE
    record.parent.mkdir(parents=True)
    record.write_text("", encoding="utf-8")
    with pytest.raises(ValueError, match="already holds a recording") as refused:
        World(settings, run_id="taken")
    assert str(record) in str(refused.value)


def test_rc_occupancy_follows_the_scenario() -> None:
    compiled = compile_scenario(load_scenario("hybrid_tuesday").scenario, step_s=900.0)
    occupancy = scenario_occupancy(compiled)
    assert occupancy(compiled.start_s - 1.0) == {}  # the warm-up: nobody yet
    assert occupancy(compiled.start_s)[Zone.COR] == 0
    in_step = occupancy(DAY_S + 8.5 * 3600 + 30.0)  # 08:30:30, inside the 08:30 step
    assert in_step == {Zone.COR: 4, Zone.NOR: 2, Zone.SOU: 0, Zone.EAS: 0, Zone.WES: 1}


@pytest.mark.asyncio
async def test_create_plant_gives_the_rc_plant_its_occupancy() -> None:
    settings = WorldSettings()
    co2 = bms_signal("ZN-COR-CO2")
    params = PlantStartParams(start_time_s=DAY_S + 10 * 3600, step_s=900.0)
    measured: list[float] = []
    for people in ({Zone.COR: 60}, {}):
        plant = create_plant(settings, "spotcool", occupancy=lambda t_s, people=people: people)
        await plant.start(params)
        measured.append((await plant.step({}))[co2])
        await plant.stop()
    crowded, empty = measured
    assert empty == pytest.approx(400.0)  # outdoor air: nobody breathes
    assert crowded > empty + 50


def test_a_default_run_id_is_safe_for_any_scenario_name() -> None:
    run_id = default_run_id("my day/v2: draft!")
    assert re.fullmatch(RUN_ID_PATTERN, run_id)
    assert run_id.endswith("-my-day-v2-draft")
    assert re.fullmatch(RUN_ID_PATTERN, default_run_id("x" * 200, stub=True))
