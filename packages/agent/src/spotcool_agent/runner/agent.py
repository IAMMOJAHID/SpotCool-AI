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
from spotcool_agent.transports.bacnet import (
    VRF_DEVICE_INSTANCE,
    VRF_DEVICE_NAME,
    BacnetClient,
    StepClock,
)
from spotcool_agent.transports.dashboard import DashboardPublisher, agent_url
from spotcool_agent.transports.server import (
    QUEUE_SIZE,
    AgentServer,
    Complaint,
    create_app,
    dashboard_origins,
)
from spotcool_agent.transports.world_http import WorldHttp

VRF_CONCURRENCY = 2
"""SAM calls the VRF loop may have in flight, on a budget of its own."""
LANGUAGE_CONCURRENCY = 2
"""Calls the transcriber and the language model may have in flight together."""
VRF_STOP_TIMEOUT_S = 5.0
"""The longest the VRF gateway's release may take at stop, after the BMS's."""


def meta_client(
    settings: AgentSettings, flag: str, *, concurrency: int, call_cap: int
) -> MetaClient:
    """One budget of Meta calls: its own limiter, call cap and ledger.

    Raises:
        ValueError: Without a key, naming ``flag``, the choice that needs it.
    """
    if settings.meta_api_key is None:
        raise ValueError(f"{flag} needs META_API_KEY: pass --env-file with the key file")
    return MetaClient(
        settings.meta_api_key,
        base_url=settings.meta_base_url,
        max_concurrency=concurrency,
        call_cap=call_cap,
    )


def create_vision(
    settings: AgentSettings,
    *,
    concurrency: int | None = None,
    call_cap: int | None = None,
    flag: str = "--vision meta",
) -> tuple[VisionService, MetaClient | None]:
    """The settings' people counter, and the Meta budget behind it if it has one.

    Args:
        settings: The settings.
        concurrency: Its calls in flight; the building's ``sam_concurrency`` by default.
        call_cap: Its call cap; the building's ``sam_call_cap`` by default.
        flag: The choice that needs the key, for the error.

    Raises:
        ValueError: If ``meta`` is chosen without a key.
    """
    if settings.vision == "blob":
        return BlobVision(), None
    if settings.vision == "scripted":
        return ScriptedVision(settings.scripted_counts), None
    client = meta_client(
        settings,
        flag,
        concurrency=concurrency or settings.sam_concurrency,
        call_cap=settings.sam_call_cap if call_cap is None else call_cap,
    )
    vision = SamVision(client, model=settings.sam_model, min_confidence=settings.sam_min_confidence)
    return vision, client


def create_language(
    settings: AgentSettings,
) -> tuple[STTService, LLMService | None, MetaClient | None]:
    """The settings' transcriber and language model, and the Meta budget they share, if any.

    Their budget is their own (``LANGUAGE_CONCURRENCY`` at once, ``llm_call_cap``), apart from
    the building's SAM calls.

    Raises:
        ValueError: If ``meta`` is chosen without a key.
    """
    wants = [f"--{name} meta" for name in ("stt", "llm") if getattr(settings, name) == "meta"]
    stt: STTService = FakeSTT()
    llm: LLMService | None = FakeLLM() if settings.llm == "fake" else None
    if not wants:
        return stt, llm, None
    key = settings.meta_api_key
    if key is None:
        raise ValueError(f"{wants[0]} needs META_API_KEY: pass --env-file with the key file")
    client = MetaClient(
        key,
        base_url=settings.meta_base_url,
        max_concurrency=LANGUAGE_CONCURRENCY,
        call_cap=settings.llm_call_cap,
    )
    if settings.stt == "meta":
        stt = MetaSTT(client, key, base_url=settings.meta_base_url, model=settings.stt_model)
    if settings.llm == "meta":
        llm = MetaLLM(client, model=settings.llm_model)
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
        stt: The transcriber; by default the settings' (``create_language``).
    """

    def __init__(
        self,
        settings: AgentSettings,
        *,
        vision: VisionService | None = None,
        stt: STTService | None = None,
    ) -> None:
        self.settings = settings
        if vision is None:
            vision, self._meta = create_vision(settings)
        else:
            self._meta = None
        self.vision = vision
        made_stt, self.llm, self._language = create_language(settings)
        self.stt = stt or made_stt
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
        self.vrf_bacnet: BacnetClient | None = None
        self.vrf_vision: VisionService | None = None
        self._vrf_meta: MetaClient | None = None
        if settings.vrf:
            self.vrf_bacnet = BacnetClient(  # a socket of its own: see transports/bacnet.py
                f"{host}:{settings.vrf_port}",
                local=f"{local}:{settings.vrf_bacnet_port}",
                instance=VRF_DEVICE_INSTANCE,
                name=VRF_DEVICE_NAME,
            )
            if self._meta is not None:  # SAM on a budget of the VRF's own
                self.vrf_vision, self._vrf_meta = create_vision(
                    settings,
                    concurrency=VRF_CONCURRENCY,
                    call_cap=settings.vrf_call_cap,
                    flag="--vrf",
                )
            else:
                self.vrf_vision = vision  # an offline counter makes no calls
            pose = CameraPose(
                mount_height_m=settings.vrf_camera_height_m,
                tilt_down_deg=settings.vrf_camera_tilt_deg,
                horizontal_fov_deg=settings.vrf_camera_hfov_deg,
            )
            self.vrf = VrfLoop(
                gateway=self.vrf_bacnet,
                world=self.world,
                vision=self.vrf_vision,
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
        if self.vrf is not None and self.vrf_bacnet is not None:
            camera = self.settings.vrf_camera
            logger.info(f"agent: VRF loop on, gateway {self.vrf_bacnet.device}, camera {camera}")
            if isinstance(self.stt, FakeSTT) and self.stt.text is None:
                logger.warning(
                    "agent: --stt fake hears nothing: voice complaints are asked to type"
                )
        loops: list[asyncio.Task[None]] = []
        try:
            await self.bacnet.open()
            if self.vrf_bacnet is not None:
                await self.vrf_bacnet.open()
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
            try:
                await self.actuator.stop()  # the BMS first: its zones, then the heartbeat last
            finally:
                try:
                    await self._stop_vrf()
                finally:
                    await self._close()

    async def _stop_vrf(self) -> None:
        """Release the VRF gateway's slots, within ``VRF_STOP_TIMEOUT_S``, after the BMS's."""
        if self.vrf is None:
            return
        try:
            async with asyncio.timeout(VRF_STOP_TIMEOUT_S):
                await self.vrf.stop()
        except TimeoutError:
            logger.warning(f"agent: the VRF release took over {VRF_STOP_TIMEOUT_S:g} s; stopped")
        except Exception as exc:  # the BMS is released already; the rest still closes
            logger.error(f"agent: the VRF release failed: {type(exc).__name__}: {exc}")

    async def _close(self) -> None:
        """Send the last events, close every connection and log the run's figures."""
        await self.publisher.stop()
        await self.server.stop()
        await self.vision.close()
        if self.vrf_vision is not None and self.vrf_vision is not self.vision:
            await self.vrf_vision.close()
        await self.stt.close()
        budgets = {"building": self._meta, "VRF": self._vrf_meta, "language": self._language}
        for client in budgets.values():
            if client is not None:
                await client.close()  # vision closes its own too; closing twice is safe
        await self.world.aclose()
        if self.vrf_bacnet is not None:
            await self.vrf_bacnet.close()
        await self.bacnet.close()
        logger.info(f"agent: {self.loop.steps} steps; latency: {self.metrics.summary()}")
        for name, client in budgets.items():
            if client is not None:
                logger.info(f"agent: {name} model calls: {ledger_summary(client.ledger)}")
