"""Assembly: the whole world in one run, and the replay of a recorded run.

``World(settings)`` builds what one ``spotcool-world run`` needs: the pipeline below, the BACnet
BMS, the cameras, the room calendar, the occupants, the HTTP and dashboard servers on one bus
and, with ``settings.stub_agent``, the stub agent. ``World.run()`` owns their lifecycle.
``Replay(settings, run_id)`` plays a recorded run back for a video take: its events, the
dashboard and the cameras, with no physics and no BACnet.

One tick of the world::

    TickSource -> RunStatusProcessor -> ScenarioProcessor
     -> FramePublisher(calendar, complaint cues) -> bms.input()
     -> ParallelPipeline[PlantProcessor(spotcool) || PlantProcessor(baseline)] -> bms.output()
     -> CameraSceneProcessor -> TelemetryProcessor -> KpiProcessor
     -> EventEmitterProcessor -> RecorderProcessor -> BusPublisher(events)
"""

import asyncio
import contextlib
import ipaddress
import math
import re
from collections.abc import Callable, Sequence
from datetime import datetime
from pathlib import Path

from fastapi import FastAPI
from loguru import logger

from spotcool_contracts import Zone
from spotcool_contracts.calendar import ROOMS, SCHEDULE_PATH
from spotcool_contracts.cameras import CATALOG_PATH
from spotcool_contracts.events import EventEnvelope, EventType, ScenarioStateData, TickData
from spotcool_world.bus import InProcessBus
from spotcool_world.clocks import SimClock, WallPacer
from spotcool_world.frames import (
    CalendarFrame,
    ComplaintCueFrame,
    EndFrame,
    ErrorFrame,
    EventFrame,
    Frame,
    ScenarioStateFrame,
    StartFrame,
)
from spotcool_world.pipeline import ParallelPipeline, Pipeline, PipelineParams
from spotcool_world.processors import (
    EVENTS_TOPIC,
    WORLD_FRAMES_TOPIC,
    BusPublisher,
    CameraSceneProcessor,
    EventEmitterProcessor,
    FrameDirection,
    FrameProcessor,
    FramePublisher,
    KpiProcessor,
    PlantProcessor,
    RecorderProcessor,
    ScenarioProcessor,
    TelemetryProcessor,
    TickSource,
)
from spotcool_world.processors.event_emitter import frame_events
from spotcool_world.processors.kpi import RUNS
from spotcool_world.processors.run_status import RunStatusProcessor
from spotcool_world.runner.runner import WorkerRunner
from spotcool_world.scenarios import CompiledScenario, compile_scenario, load_scenario
from spotcool_world.serializers import EVENTS_FILE, RUN_ID_PATTERN, EventRecord, read_records
from spotcool_world.services.camera_service import create_cameras
from spotcool_world.services.plant_factory import create_plant
from spotcool_world.services.settings import WorldSettings
from spotcool_world.stub_agent.worker import StubAgentWorker
from spotcool_world.transports.bacnet.bms import BacnetBmsTransport, primary_ipv4
from spotcool_world.transports.http.calendar import CalendarStore, calendar_router
from spotcool_world.transports.http.cameras import cameras_router
from spotcool_world.transports.http.server import create_http_app
from spotcool_world.transports.websocket.dashboard import create_dashboard_app
from spotcool_world.utils.base_object import BaseObject
from spotcool_world.workers import BaseWorker, PipelineWorker
from spotcool_world.workers.http_server import HttpServerWorker
from spotcool_world.workers.occupant_worker import OccupantWorker

DASHBOARD_DIST = Path(__file__).resolve().parents[5] / "apps" / "dashboard" / "dist"
"""The built dashboard in this checkout, which the dashboard server serves at ``/``."""
STOPPED = "the run ended"
"""Why the workers beside the pipeline are stopped when the run ends."""
VIEWER_POLL_S = 0.1
"""How often a replay that waits for a dashboard checks for one."""


def scenario_occupancy(compiled: CompiledScenario) -> Callable[[float], dict[Zone, int]]:
    """The RC plant's occupancy: the scenario's people per zone in the step under way at ``t``.

    The scenario has nobody before its start, so the plant's warm-up runs on an empty building.

    Args:
        compiled: The scenario.
    """

    def occupancy(t_s: float) -> dict[Zone, int]:
        if t_s < compiled.start_s:
            return {}
        return {scene.zone: scene.people for scene in compiled.state_at(t_s).zones}

    return occupancy


def default_run_id(scenario: str, *, stub: bool = False) -> str:
    """A run id from the time now and a scenario's name, such as ``20261102-101500-hybrid_tuesday``.

    Args:
        scenario: The scenario's name; characters a run id cannot hold become ``-``.
        stub: The stub agent drives the run: the id ends in ``-stub``.
    """
    safe = re.sub(r"[^A-Za-z0-9_-]+", "-", scenario).strip("-")[:40]
    return f"{datetime.now():%Y%m%d-%H%M%S}-{safe}".rstrip("-") + ("-stub" if stub else "")


class World(BaseObject):
    """One run of the whole world, built from its settings; ``run()`` runs it once.

    The pipeline is the module docstring's, from the scenario's start to its end, paced at
    ``settings.speed``. Both plants come from ``create_plant``, and the RC plant's occupant heat
    follows the scenario (``scenario_occupancy``). One ``InProcessBus`` serves the pipeline, the
    workers and the dashboard server. Beside the pipeline run ``workers``: the occupants, the
    HTTP server (cameras and calendar, ``http_host:http_port``), the dashboard server
    (``http_host:dashboard_port``) and, with ``settings.stub_agent``, the stub agent. Nothing
    starts before ``run()``.

    Args:
        settings: The run's settings.
        run_id: The run's directory under ``settings.run_dir`` (``RUN_ID_PATTERN``), which must
            not hold a recording yet; by default ``default_run_id`` of the scenario's name.
        name: Display name.

    Raises:
        ValueError: If the run id is unsafe or already holds a recording, ``bms_address`` is not
            an IPv4 address, the scenario names no file or has errors, or a URL in the settings
            is invalid.
        OSError: If the scenario file cannot be read.
    """

    def __init__(
        self, settings: WorldSettings, *, run_id: str | None = None, name: str | None = None
    ) -> None:
        super().__init__(name=name or "world")
        if run_id is not None and not re.fullmatch(RUN_ID_PATTERN, run_id):
            raise ValueError(f"{run_id!r} is not a run id: letters, digits, _ and -")
        bms_host = _bms_host(settings.bms_address)
        compiled = compile_scenario(
            load_scenario(settings.scenario).scenario, step_s=settings.step_s
        )
        self.run_id = run_id or default_run_id(compiled.name, stub=settings.stub_agent)
        """The run's id, its directory under ``settings.run_dir``."""
        record = settings.run_dir / self.run_id / EVENTS_FILE
        if record.exists():
            raise ValueError(f"{record} already holds a recording: give the run another id")
        self.settings = settings
        """The run's settings."""
        self.compiled = compiled
        """The scenario; it sets the run's start and end."""
        self.bus = InProcessBus()
        """The bus the pipeline, the workers and the dashboard server share."""
        self.bms = BacnetBmsTransport(address=settings.bms_address, port=settings.bms_port)
        """The BACnet BMS."""
        self.bms_endpoint = f"{bms_host}:{settings.bms_port}"
        """The ``ip:port`` the BMS serves on."""
        self.cameras = create_cameras(settings)
        """The camera system, shared by the pipeline and the HTTP server."""
        self.store = CalendarStore()
        """The room calendar the HTTP server serves."""
        occupancy = scenario_occupancy(compiled)
        self.plants = {run: create_plant(settings, run, occupancy=occupancy) for run in RUNS}
        """Both runs' plants, by run."""
        clock = SimClock(compiled.start_s, settings.step_s)
        recorder, publisher = RecorderProcessor(settings), BusPublisher(self.bus, EVENTS_TOPIC)
        self._below_emitter = {recorder.name, publisher.name}
        pipeline = Pipeline(
            [
                TickSource(clock, WallPacer(settings.speed), compiled.end_s),
                RunStatusProcessor(self.run_id, settings.plant, compiled.name),
                ScenarioProcessor(compiled),
                FramePublisher(self.bus, (CalendarFrame, ComplaintCueFrame)),
                self.bms.input(),
                # WP3: the VRF gateway's input() goes here, so its writes join the tick's frames.
                ParallelPipeline(
                    *(
                        [PlantProcessor(self.plants[run], run, time_period=settings.time_period)]
                        for run in RUNS
                    )
                ),
                self.bms.output(),
                CameraSceneProcessor(self.cameras),
                TelemetryProcessor(),
                KpiProcessor(),
                EventEmitterProcessor(),
                recorder,
                publisher,
            ]
        )
        params = PipelineParams(
            clock=clock,
            run_id=self.run_id,
            scenario=settings.scenario,
            plant=settings.plant,
            speed=settings.speed,
            end_time_s=compiled.end_s,
        )
        self.pipeline = PipelineWorker(pipeline, params=params, name="pipeline")
        """The pipeline's worker."""
        http = create_http_app([cameras_router(self.cameras), calendar_router(self.store)])
        dashboard = create_dashboard_app(self.bus, settings.run_dir, dist_dir=DASHBOARD_DIST)
        self.workers: list[BaseWorker] = [
            OccupantWorker(settings, name="occupants"),
            *_servers(settings, http, dashboard),
            # WP3: the VRF gateway's worker joins these.
        ]
        """The workers beside the pipeline, stopped when the run ends."""
        if settings.stub_agent:
            self.workers.append(StubAgentWorker(self.bms_endpoint, name="stub-agent"))
        self.pipeline.add_event_handler("on_pipeline_finished", self._stop_workers)
        self.pipeline.add_event_handler("on_pipeline_error", self._publish_error)
        self._ran = False

    def urls(self) -> dict[str, str]:
        """Where the world serves the dashboard, the cameras, the calendar and the BMS."""
        host, port = self.settings.http_host, self.settings.http_port
        room = next(iter(ROOMS))
        return {
            **_dashboard_urls(self.settings),
            "cameras": _url("http", host, port, CATALOG_PATH),
            "calendar": _url("http", host, port, SCHEDULE_PATH.format(room=room)),
            "BMS": f"{self.bms_endpoint} (BACnet/IP)",
        }

    async def run(self, *, handle_sigint: bool = True) -> None:
        """Run the world once, to the scenario's end or until it is stopped.

        Before the workers: the BMS starts on the settings' address and port, the cameras start,
        and the calendar store follows ``WORLD_FRAMES_TOPIC``. Then one ``WorkerRunner`` runs
        the pipeline and the workers: the first Ctrl+C stops the run gracefully, a second
        cancels it. The run's end (its ``EndFrame`` or ``CancelFrame``) stops the workers. After
        them, everything started is stopped again, in reverse order, the plants last: a plant's
        ``stop()`` waits, bounded, for a stop request still on its way (``STOP_WAIT_S`` for
        BOPTEST), so ``run()`` never returns while one is.

        Args:
            handle_sigint: Let the runner handle Ctrl+C (off in tests).

        Raises:
            OSError: If the BMS or a server cannot bind its port.
            RuntimeError: If the world has run before.
        """
        if self._ran:
            raise RuntimeError(f"{self.name} has already run")
        self._ran = True
        async with contextlib.AsyncExitStack() as stack:
            for plant in self.plants.values():
                stack.push_async_callback(plant.stop)
            try:
                await self.bms.start()
            except OSError as exc:
                raise OSError(
                    f"BMS UDP {self.bms_endpoint} is in use, or not an address of this PC: {exc}"
                ) from exc
            stack.push_async_callback(self.bms.stop)
            await self.cameras.start()
            stack.push_async_callback(self.cameras.stop)
            calendar = self.bus.subscribe(WORLD_FRAMES_TOPIC)
            self.create_task(self.store.follow(calendar), "calendar")
            stack.push_async_callback(self.cancel_tasks)
            stack.callback(calendar.close)
            runner = WorkerRunner(bus=self.bus, handle_sigint=handle_sigint)
            # The pipeline first: Ctrl+C stops it first, and its end stops the rest. The workers
            # still subscribe before the first tick, whose task starts after theirs.
            await runner.run(self.pipeline, *self.workers)

    async def _stop_workers(self, worker: PipelineWorker, frame: Frame) -> None:
        """Stop every worker beside the pipeline: the run has ended."""
        await asyncio.gather(*(each.stop(STOPPED) for each in self.workers))

    async def _publish_error(self, worker: PipelineWorker, error: ErrorFrame) -> None:
        """Put an error on the events topic, unless it passed the emitter on its way up."""
        if error.processor in self._below_emitter:
            return  # the emitter has already made it an event
        for envelope in frame_events(error):
            await self.bus.publish(EVENTS_TOPIC, envelope)


class Replay(BaseObject):
    """A recorded run played back for a video take: no physics, no BACnet.

    The record's events go on a new bus's events topic in their recorded order, and each step
    lasts ``step_s / speed`` wall seconds after its ``tick`` event, as in the live run. The
    dashboard server serves them (``http_host:dashboard_port``); with ``wait_viewer_s``, the
    replay waits up to that long for a first ``/ws/events`` client before it starts. The camera
    system shows the recorded scenes and people (``http_host:http_port``): each recorded
    scenario state goes through a ``CameraSceneProcessor``, the processor that made the
    record's ``camera_scene`` events. The run's end stops the servers.

    Args:
        settings: ``run_dir`` (where the run is), and the servers' and cameras' settings.
        run_id: The recorded run.
        speed: Simulation seconds per wall second, or ``None`` for as fast as possible.
        wait_viewer_s: Longest wait for a dashboard to connect before the first event; 0 starts
            at once.
        name: Display name.

    Raises:
        ValueError: If the run id is unsafe, the run has no record, or a record is invalid.
        OSError: If the record cannot be read.
    """

    def __init__(
        self,
        settings: WorldSettings,
        run_id: str,
        *,
        speed: float | None,
        wait_viewer_s: float = 0.0,
        name: str | None = None,
    ) -> None:
        super().__init__(name=name or "replay")
        if not re.fullmatch(RUN_ID_PATTERN, run_id):
            raise ValueError(f"{run_id!r} is not a run id: letters, digits, _ and -")
        path = settings.run_dir / run_id / EVENTS_FILE
        if not path.is_file():
            raise ValueError(f"no recorded run {run_id} in {settings.run_dir}")
        self.settings = settings
        """The settings."""
        self.envelopes = [r.envelope for r in read_records(path) if isinstance(r, EventRecord)]
        """The recorded events, in their recorded order."""
        self.bus = InProcessBus()
        """The bus the replay publishes on and the dashboard server reads."""
        self.cameras = create_cameras(settings)
        """The camera system."""
        self.dashboard = create_dashboard_app(self.bus, settings.run_dir, dist_dir=DASHBOARD_DIST)
        """The dashboard server's app; ``state.viewers`` counts its ``/ws/events`` clients."""
        source = _RecordSource(
            self.envelopes,
            WallPacer(speed),
            viewers=lambda: self.dashboard.state.viewers,
            wait_viewer_s=wait_viewer_s,
        )
        pipeline = Pipeline(
            [source, CameraSceneProcessor(self.cameras), BusPublisher(self.bus, EVENTS_TOPIC)]
        )
        params = PipelineParams(run_id=run_id, speed=speed)
        self.pipeline = PipelineWorker(pipeline, params=params, name="replay")
        """The replay's worker."""
        http = create_http_app([cameras_router(self.cameras)])
        self.workers: list[BaseWorker] = _servers(settings, http, self.dashboard)
        """The servers, stopped when the replay ends."""
        self.pipeline.add_event_handler("on_pipeline_finished", self._stop_workers)
        self._ran = False

    def urls(self) -> dict[str, str]:
        """Where the replay serves the dashboard and the cameras."""
        host, port = self.settings.http_host, self.settings.http_port
        return {**_dashboard_urls(self.settings), "cameras": _url("http", host, port, CATALOG_PATH)}

    async def run(self, *, handle_sigint: bool = True) -> None:
        """Play the record once, to its end or until stopped; the cameras run meanwhile.

        Args:
            handle_sigint: Let the runner handle Ctrl+C (off in tests): the first stops the
                replay, a second cancels it.

        Raises:
            OSError: If a server cannot bind its port.
            RuntimeError: If the replay has run before.
        """
        if self._ran:
            raise RuntimeError(f"{self.name} has already run")
        self._ran = True
        async with contextlib.AsyncExitStack() as stack:
            await self.cameras.start()
            stack.push_async_callback(self.cameras.stop)
            runner = WorkerRunner(bus=self.bus, handle_sigint=handle_sigint)
            await runner.run(self.pipeline, *self.workers)  # Ctrl+C stops the replay first

    async def _stop_workers(self, worker: PipelineWorker, frame: Frame) -> None:
        """Stop the servers: the replay has ended."""
        await asyncio.gather(*(each.stop("the replay ended") for each in self.workers))


class _RecordSource(FrameProcessor):
    """Pushes recorded events as ``EventFrame``s, paced after each ``tick``, then an ``EndFrame``.

    On ``StartFrame`` it starts a background task, like ``TickSource``. The task first waits up
    to ``wait_viewer_s`` for a viewer. A scenario state also goes as a ``ScenarioStateFrame``,
    just ahead of its event, for the ``CameraSceneProcessor``.

    Args:
        envelopes: The recorded events, in order.
        pacer: Paces each step: ``step_s / speed`` wall seconds after its ``tick`` event.
        viewers: How many dashboards watch now.
        wait_viewer_s: Longest wait for the first one.
        name: Display name.
    """

    def __init__(
        self,
        envelopes: Sequence[EventEnvelope],
        pacer: WallPacer,
        *,
        viewers: Callable[[], int],
        wait_viewer_s: float,
        name: str | None = None,
    ) -> None:
        super().__init__(name=name)
        self._envelopes = envelopes
        self._pacer = pacer
        self._viewers = viewers
        self._wait_viewer_s = wait_viewer_s
        self._task: asyncio.Task[None] | None = None

    async def process_frame(self, frame: Frame, direction: FrameDirection) -> None:
        """Start the replay on ``StartFrame``; pass every frame on."""
        await super().process_frame(frame, direction)
        await self.push_frame(frame, direction)
        if isinstance(frame, StartFrame) and self._task is None:
            self._task = self.create_task(self._replay(), "replay")

    async def _replay(self) -> None:
        await self._wait_for_viewer()
        for envelope in self._envelopes:
            t_s = envelope.sim_time_s
            if envelope.type is EventType.SCENARIO_STATE:
                state = ScenarioStateData.model_validate(envelope.data)
                await self.push_frame(ScenarioStateFrame(state=state, sim_time_s=t_s))
            await self.push_frame(EventFrame(envelope=envelope, sim_time_s=t_s))
            if envelope.type is EventType.TICK:
                await self._pacer.pace(TickData.model_validate(envelope.data).step_s)
                await asyncio.sleep(0)  # yield every step, so stop() and cancels get a turn
        await self.push_frame(EndFrame(reason="the record ended"))

    async def _wait_for_viewer(self) -> None:
        if self._wait_viewer_s <= 0 or self._viewers():
            return
        logger.info(f"{self.name}: waiting up to {self._wait_viewer_s:g} s for a dashboard")
        for _ in range(math.ceil(self._wait_viewer_s / VIEWER_POLL_S)):
            await asyncio.sleep(VIEWER_POLL_S)
            if self._viewers():
                return
        logger.warning(f"{self.name}: no dashboard after {self._wait_viewer_s:g} s; replaying")


def _bms_host(address: str | None) -> str:
    """The IPv4 address the BMS binds: ``address`` without its prefix length, or the primary one.

    Raises:
        ValueError: If ``address`` is not an IPv4 address, with or without a prefix length.
    """
    if address is None:
        return primary_ipv4()
    try:
        return str(ipaddress.IPv4Interface(address).ip)
    except ValueError:
        raise ValueError(
            f"bms_address {address!r} is not an IPv4 address such as 192.168.1.20 or "
            "192.168.1.20/24"
        ) from None


def _servers(settings: WorldSettings, http: FastAPI, dashboard: FastAPI) -> list[BaseWorker]:
    """The HTTP server on ``http_host:http_port`` and the dashboard on ``dashboard_port``."""
    host = settings.http_host
    return [
        HttpServerWorker(http, host, settings.http_port, name="http"),
        HttpServerWorker(dashboard, host, settings.dashboard_port, name="dashboard"),
    ]


def _dashboard_urls(settings: WorldSettings) -> dict[str, str]:
    """The dashboard's page and its event stream."""
    host, port = settings.http_host, settings.dashboard_port
    page = _url("http", host, port, "/")
    if not DASHBOARD_DIST.is_dir():
        page += f"  (not built: no {DASHBOARD_DIST})"
    return {"dashboard": page, "event stream": _url("ws", host, port, "/ws/events")}


def _url(scheme: str, host: str, port: int, path: str) -> str:
    """Where a server bound to ``host:port`` serves ``path``.

    A wildcard host (``0.0.0.0``, ``::``) gives this PC's LAN address, then its loopback one.
    """
    if host in ("0.0.0.0", "::"):
        loopback = "127.0.0.1" if host == "0.0.0.0" else "::1"
        lan, here = _url(scheme, primary_ipv4(), port, path), _url(scheme, loopback, port, path)
        return f"{lan} (this PC: {here})"
    return f"{scheme}://{f'[{host}]' if ':' in host else host}:{port}{path}"
