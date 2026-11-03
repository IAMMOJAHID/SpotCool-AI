"""Agent: the whole agent assembled from its settings, run until stopped.

``Agent(settings).run()`` opens the BACnet client, starts the dashboard publisher, the
complaint server, the heartbeat, the step clock, the building loop, the complaint loop and,
with ``--vrf``, the meeting room's VRF loop, and runs until ``stop()``, a cancel (Ctrl+C) or a
crash of any of them. Whatever ends it, the ``finally`` stops the loops first, so nothing
writes after the release, then releases every slot the agent holds on the VRF gateway and on
the BMS while they still answer (``Actuator.stop``), the BMS's heartbeat last, then sends the
last events and closes the rest. The run log goes to stderr; nothing is written to a file.
"""

import asyncio
import socket
from collections.abc import Coroutine
from typing import Any

from loguru import logger

from spotcool_agent.actuator import Actuator
from spotcool_agent.domain.louvers import CameraPose
from spotcool_agent.loops.building import BuildingLoop
from spotcool_agent.loops.complaints import ComplaintLoop
from spotcool_agent.loops.vrf import VrfLoop
from spotcool_agent.observers.metrics import StageMetrics, ledger_summary
from spotcool_agent.processors.policy import AgentPolicy
from spotcool_agent.services.fake.blob import BlobVision
from spotcool_agent.services.fake.llm import FakeLLM
from spotcool_agent.services.fake.scripted import ScriptedVision
from spotcool_agent.services.fake.stt import FakeSTT
from spotcool_agent.services.llm import LLMService
from spotcool_agent.services.meta.client import MetaClient
from spotcool_agent.services.meta.llm import MetaLLM
from spotcool_agent.services.meta.stt import MetaSTT
from spotcool_agent.services.meta.vision import SamVision
from spotcool_agent.services.stt import STTService
from spotcool_agent.services.vision import VisionService
from spotcool_agent.settings import AgentSettings
from spotcool_agent.transports.bacnet import BacnetClient, BacnetPeer, StepClock
from spotcool_agent.transports.dashboard import DashboardPublisher, agent_url
from spotcool_agent.transports.server import (
    QUEUE_SIZE,
    AgentServer,
    Complaint,
    create_app,
    dashboard_origins,
)
from spotcool_agent.transports.world_http import WorldHttp
from spotcool_contracts import load_point_map

VRF_DEVICE = load_point_map("vrf_gateway").device_instance
"""The VRF gateway's device instance (2001)."""


def meta_client(settings: AgentSettings, flag: str) -> MetaClient:
    """The process's one Meta client: its limiter, ledger and call cap serve every model.

    Raises:
        ValueError: Without a key, naming ``flag``, the choice that needs it.
    """
    if settings.meta_api_key is None:
        raise ValueError(f"{flag} needs META_API_KEY: pass --env-file with the key file")
    return MetaClient(
        settings.meta_api_key,
        base_url=settings.meta_base_url,
        max_concurrency=settings.sam_concurrency,
        call_cap=settings.sam_call_cap,
    )


def create_vision(
    settings: AgentSettings, client: MetaClient | None = None
) -> tuple[VisionService, MetaClient | None]:
    """The settings' people counter, and the Meta client behind it if it has one.

    Args:
        settings: The settings.
        client: The Meta client to share, if one is made already.

    Raises:
        ValueError: If ``meta`` is chosen without a key.
    """
    if settings.vision == "blob":
        return BlobVision(), client
    if settings.vision == "scripted":
        return ScriptedVision(settings.scripted_counts), client
    client = client or meta_client(settings, "--vision meta")
    vision = SamVision(client, model=settings.sam_model, min_confidence=settings.sam_min_confidence)
    return vision, client


def create_language(
    settings: AgentSettings, client: MetaClient | None = None
) -> tuple[STTService, LLMService | None, MetaClient | None]:
    """The settings' transcriber and language model, and the Meta client they share, if any.

    Args:
        settings: The settings.
        client: The Meta client to share, if one is made already (``create_vision``'s).

    Raises:
        ValueError: If ``meta`` is chosen without a key.
    """
    key = settings.meta_api_key
    if settings.stt == "meta":
        client = client or meta_client(settings, "--stt meta")
        if key is None:  # a client given by the caller was made with another key
            raise ValueError("--stt meta needs META_API_KEY: pass --env-file with the key file")
        stt: STTService = MetaSTT(
            key,
            base_url=settings.meta_base_url,
            model=settings.stt_model,
            ledger=client.ledger,
        )
    else:
        stt = FakeSTT()
    llm: LLMService | None = None
    if settings.llm == "meta":
        client = client or meta_client(settings, "--llm meta")
        llm = MetaLLM(client, model=settings.llm_model)
    elif settings.llm == "fake":
        llm = FakeLLM()
    return stt, llm, client


def local_address(world_host: str) -> str:
    """This machine's IPv4 address on the route to ``world_host`` (no packet is sent)."""
    with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as probe:
        probe.connect((world_host, 47808))
        return probe.getsockname()[0]


class Agent:
    """The agent (see the module docstring).

    Args:
        settings: The settings.
        vision: The people counter; by default the settings' (``create_vision``).
    """

    def __init__(self, settings: AgentSettings, *, vision: VisionService | None = None) -> None:
        self.settings = settings
        if vision is None:
            vision, self._meta = create_vision(settings)
        else:
            self._meta = None
        self.vision = vision
        self.stt, self.llm, self._meta = create_language(settings, self._meta)
        host = settings.world_host
        local = settings.bacnet_address or local_address(host)
        self.bacnet = BacnetClient(
            f"{host}:{settings.bms_port}", local=f"{local}:{settings.bacnet_port}"
        )
        self.publisher = DashboardPublisher(agent_url(host, settings.dashboard_port))
        self.world = WorldHttp(f"http://{host}:{settings.http_port}")
        self.complaints: asyncio.Queue[Complaint] = asyncio.Queue(QUEUE_SIZE)
        origins = dashboard_origins(host, settings.dashboard_port)
        self.server = AgentServer(
            create_app(self.complaints, origins=origins), settings.listen_host, settings.listen_port
        )
        self.metrics = StageMetrics()
        self.actuator = Actuator(
            self.bacnet, self.publisher.publish, heartbeat_s=settings.heartbeat_s
        )
        self.clock = StepClock(self.bacnet.clock)
        self.loop = BuildingLoop(
            clock=self.clock,
            world=self.world,
            vision=vision,
            policy=AgentPolicy(),
            actuator=self.actuator,
            publish=self.publisher.publish,
            metrics=self.metrics,
            settle_s=settings.settle_s,
            on_new_run=self._meta.new_run if self._meta is not None else None,
        )
        self.vrf: VrfLoop | None = None
        if settings.vrf:
            gateway = BacnetPeer(self.bacnet, f"{host}:{settings.vrf_port}", VRF_DEVICE)
            pose = CameraPose(
                mount_height_m=settings.vrf_camera_height_m,
                tilt_down_deg=settings.vrf_camera_tilt_deg,
                horizontal_fov_deg=settings.vrf_camera_hfov_deg,
            )
            self.vrf = VrfLoop(
                gateway=gateway,
                world=self.world,
                vision=vision,
                publish=self.publisher.publish,
                camera_id=settings.vrf_camera,
                pose=pose,
                brand=settings.vrf_brand,
                mirror=settings.vrf_mirror,
                hold_s=settings.vrf_hold_s,
                draft_hold_s=settings.draft_hold_s,
                complaint_hold_s=settings.complaint_hold_s,
            )
        self.complaint_loop = ComplaintLoop(
            self.complaints,
            self.publisher.publish,
            stt=self.stt,
            llm=self.llm,
            vrf=self.vrf,
            counts=lambda: self.loop.counts,
        )
        self._stop = asyncio.Event()

    def stop(self) -> None:
        """Ask ``run()`` to release everything and return."""
        self._stop.set()

    async def run(self) -> None:
        """Run until ``stop()``, a cancel or a crash; release everything on the way out.

        Raises:
            OSError: If the BACnet or HTTP port cannot be bound.
            Exception: Whatever crashed a loop, after the release.
        """
        llm = self.llm.name if self.llm else "off"
        logger.info(
            f"agent: vision {self.vision.name}, STT {self.stt.name}, LLM {llm}, BMS "
            f"{self.bacnet.device}, cameras {self.world.base_url}, dashboard {self.publisher.url}"
        )
        if self.vrf is not None:
            port, camera = self.settings.vrf_port, self.settings.vrf_camera
            logger.info(f"agent: VRF loop on, gateway port {port}, camera {camera}")
        loops: list[asyncio.Task[None]] = []
        try:
            await self.bacnet.open()
            await self.server.start()
            self.publisher.start()
            self.actuator.start()
            jobs: list[Coroutine[Any, Any, None]] = [
                self.clock.run(),
                self.loop.run(),
                self.complaint_loop.run(),
            ]
            if self.vrf is not None:
                jobs.append(self.vrf.run())
            loops = [asyncio.create_task(job) for job in jobs]
            stopped = asyncio.create_task(self._stop.wait())
            try:
                done, _ = await asyncio.wait([stopped, *loops], return_when=asyncio.FIRST_COMPLETED)
            finally:
                stopped.cancel()
            for task in done:
                if task is not stopped and task.exception() is not None:
                    error = task.exception()
                    logger.error(f"agent: a loop failed: {type(error).__name__}: {error}")
                    raise error  # type: ignore[misc]
        finally:
            for task in loops:  # the loops stop before the release, so nothing writes after it
                task.cancel()
            await asyncio.gather(*loops, return_exceptions=True)
            if self.vrf is not None:
                await self.vrf.stop()
            await self.actuator.stop()  # the BMS's heartbeat is released last
            await self.publisher.stop()
            await self.server.stop()
            await self.vision.close()
            await self.stt.close()
            if self._meta is not None:
                await self._meta.close()  # vision closes it too; closing twice is safe
            await self.world.aclose()
            await self.bacnet.close()
            logger.info(f"agent: {self.loop.steps} steps; latency: {self.metrics.summary()}")
            if self._meta is not None:
                logger.info(f"agent: model calls: {ledger_summary(self._meta.ledger)}")
