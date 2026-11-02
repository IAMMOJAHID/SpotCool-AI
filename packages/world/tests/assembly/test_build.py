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
from spotcool_world.runner.assembly import World, default_run_id, scenario_occupancy
from spotcool_world.scenarios import compile_scenario, load_scenario
from spotcool_world.serializers import RUN_ID_PATTERN
from spotcool_world.services import PlantStartParams, WorldSettings, create_plant
from spotcool_world.services.boptest.catalog import bms_signal
from spotcool_world.services.rc import RcPlant
from spotcool_world.services.replay import ReplayPlant
from spotcool_world.stub_agent.worker import StubAgentWorker
from spotcool_world.workers.http_server import HttpServerWorker
from spotcool_world.workers.occupant_worker import OccupantWorker

DAY_S = 199 * 86_400.0
MakeSettings = Callable[..., WorldSettings]


def test_the_pipeline_is_the_ticks_order_of_work(make_settings: MakeSettings) -> None:
    world = World(make_settings(), run_id="built")
    pipeline = world.pipeline.pipeline
    assert isinstance(pipeline, Pipeline)
    processors = pipeline.processors
    kinds = [
        TickSource,
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
    assert processors[3] is world.bms.input()
    assert processors[5] is world.bms.output()


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
    plant: str, make_settings: MakeSettings, fake_boptest: list[Any]
) -> None:
    replay_run = "recorded" if plant == "replay" else None
    world = World(make_settings(plant=plant, replay_run=replay_run), stub_agent=True)
    if plant.startswith("boptest"):
        assert [world.plants[run] for run in RUNS] == fake_boptest
        url = {"boptest-local": "http://127.0.0.1:8000", "boptest-hosted": "http://api.boptest.net"}
        assert [fake.base_url for fake in fake_boptest] == [url[plant]] * 2
    else:
        kind = RcPlant if plant == "rc" else ReplayPlant
        assert all(type(world.plants[run]) is kind for run in RUNS)
    assert [(type(worker), worker.name) for worker in world.workers] == [
        (OccupantWorker, "occupants"),
        (HttpServerWorker, "http"),
        (HttpServerWorker, "dashboard"),
        (StubAgentWorker, "stub-agent"),
    ]


def test_the_stub_agent_is_off_by_default(make_settings: MakeSettings) -> None:
    world = World(make_settings())
    assert not any(isinstance(worker, StubAgentWorker) for worker in world.workers)
    assert re.fullmatch(RUN_ID_PATTERN, world.run_id)
    assert world.run_id.endswith("-hybrid_tuesday")


def test_urls_name_every_server(make_settings: MakeSettings) -> None:
    settings = make_settings(http_host="0.0.0.0")
    urls = World(settings, run_id="urls").urls()
    http, dashboard = settings.http_port, settings.dashboard_port
    assert urls["cameras"] == f"http://127.0.0.1:{http}/cameras"
    assert urls["calendar"] == f"http://127.0.0.1:{http}/calendar/rooms/meeting-south/schedule"
    assert urls["event stream"] == f"ws://127.0.0.1:{dashboard}/ws/events"
    assert urls["dashboard"].startswith(f"http://127.0.0.1:{dashboard}/")
    assert urls["BMS"] == f"127.0.0.1:{settings.bms_port} (BACnet/IP)"


def test_a_bad_scenario_or_run_id_fails_the_build(make_settings: MakeSettings) -> None:
    with pytest.raises(ValueError, match="unknown scenario"):
        World(make_settings(scenario="no_such_day"))
    with pytest.raises(ValueError, match="not a run id"):
        World(make_settings(), run_id="../escape")


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
    assert re.fullmatch(RUN_ID_PATTERN, default_run_id("x" * 200))
