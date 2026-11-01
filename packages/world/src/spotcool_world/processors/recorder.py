"""RecorderProcessor: keeps every run as ``runs/<run_id>/``, for replays, reports and evals."""

import asyncio
import contextlib
import hashlib
import re
import subprocess
from datetime import UTC, datetime
from pathlib import Path
from typing import Any
from urllib.parse import urlsplit

from loguru import logger

from spotcool_contracts import CONTRACTS_VERSION
from spotcool_contracts.events import EventEnvelope, KpiData, Run
from spotcool_world import __version__
from spotcool_world.bus import Subscription
from spotcool_world.frames import (
    CancelFrame,
    EndFrame,
    Frame,
    KpiFrame,
    PlantMeasurementsFrame,
    StartFrame,
    TickFrame,
)
from spotcool_world.processors.event_emitter import EVENTS_TOPIC
from spotcool_world.processors.frame_processor import (
    FrameDirection,
    FrameProcessor,
    FrameProcessorSetup,
)
from spotcool_world.scenarios.loader import scenario_path
from spotcool_world.serializers.jsonl import (
    EVENTS_FILE,
    MANIFEST_FILE,
    RUN_ID_PATTERN,
    EventRecord,
    RawRecord,
    Record,
    RunManifest,
    RunWriter,
)
from spotcool_world.services.settings import WorldSettings

EVENTS_QUEUE_SIZE = 100_000
"""Capacity of the recorder's subscription to the events topic: large, so nothing is dropped."""
REDACTED = "<redacted>"
"""What the manifest shows in place of a secret."""
_SECRET_NAMES = re.compile(r"password|secret|token|api_key", re.IGNORECASE)


class RecorderProcessor(FrameProcessor):
    """Records the run in ``settings.run_dir/<run_id>/``: ``events.jsonl``, then ``manifest.json``.

    ``setup()`` subscribes to the events topic, before the run starts. Then, on every frame,
    the events waiting there (the pipeline's, the occupant worker's, the agent's) become event
    records, and each ``PlantMeasurementsFrame`` becomes a raw record (``index`` 0 is the
    run's initial state). Each ``TickFrame`` hands its records to a background task, which
    writes and flushes them, so the record follows pipeline order and the tick never waits on
    the disk.

    At the ``EndFrame`` or ``CancelFrame`` it takes the last events (lockstep: everything the
    pipeline published has passed ``BusPublisher`` by then), waits for the writes, and writes
    ``manifest.json`` (``RunManifest``) with the final ``KpiFrame``s it saw. It does this
    before the base class handles the frame, since a ``CancelFrame`` cancels this processor's
    tasks. A full subscription drops events; the manifest counts them in ``dropped_events``
    and a warning is logged.

    A run without its record is invalid, so every recording failure is fatal: an unsafe run id
    (``RUN_ID_PATTERN``), a run directory that already holds a recording or cannot be made, a
    failed write. A run whose writes failed gets no manifest. Records hold JSON only: no image
    or audio bytes.

    Args:
        settings: The run's settings. ``run_dir`` is where runs go; the manifest keeps the
            settings with secrets redacted.
        topic: The events topic.
        name: Display name.
    """

    def __init__(
        self, settings: WorldSettings, *, topic: str = EVENTS_TOPIC, name: str | None = None
    ) -> None:
        super().__init__(name=name)
        self._settings = settings
        self._topic = topic
        self._events: Subscription | None = None
        self._writer: RunWriter | None = None
        self._dir = settings.run_dir
        self._start: StartFrame | None = None
        self._created = datetime.now(UTC)
        self._git_sha: str | None = None
        self._scenario_sha256: str | None = None
        self._indices: dict[Run, int] = {}
        self._final_kpis: dict[Run, KpiData] = {}
        self._batch: list[Record] = []
        self._batches: asyncio.Queue[list[Record] | None] = asyncio.Queue()
        self._writing: asyncio.Task[Any] | None = None
        self._failed = False

    async def setup(self, setup: FrameProcessorSetup) -> None:
        """Subscribe to the events topic, so the run's first events are not missed."""
        await super().setup(setup)
        self._events = setup.bus.subscribe(self._topic, maxsize=EVENTS_QUEUE_SIZE)

    async def process_frame(self, frame: Frame, direction: FrameDirection) -> None:
        """Record the run as frames pass, and finish the record at its end; pass frames on."""
        downstream = direction is FrameDirection.DOWNSTREAM
        if downstream and isinstance(frame, EndFrame | CancelFrame):
            await self._finish(frame)  # first: the base class cancels the writer on a CancelFrame
        await super().process_frame(frame, direction)
        if downstream and isinstance(frame, StartFrame):
            await self._open(frame)
        elif downstream and self._writer is not None:
            await self._record(frame)
        await self.push_frame(frame, direction)

    async def cleanup(self) -> None:
        """Close the subscription, and the record of a run that never reached its end."""
        await super().cleanup()
        if self._events is not None:
            self._events.close()
        if self._writer is not None:
            logger.warning(f"{self.name}: run ended without an end frame; its record is incomplete")
            with contextlib.suppress(OSError):  # the other processors still need their cleanup
                self._writer.close()
            self._writer = None

    async def _open(self, start: StartFrame) -> None:
        """Begin recording the run the ``StartFrame`` starts."""
        if not re.fullmatch(RUN_ID_PATTERN, start.run_id):
            await self.push_error(
                f"cannot record run {start.run_id!r}: a run id is letters, digits, _ and -",
                fatal=True,
            )
            return
        run_dir = self._settings.run_dir / start.run_id
        path = run_dir / EVENTS_FILE
        try:
            writer = await asyncio.to_thread(_new_writer, path)
        except OSError as exc:
            await self.push_error(f"cannot record run {start.run_id}: {exc}", fatal=True)
            return
        self._writer, self._dir, self._start = writer, run_dir, start
        self._created = datetime.now(UTC)
        self._git_sha = await asyncio.to_thread(_git_sha)
        self._scenario_sha256 = await asyncio.to_thread(_scenario_sha256, start.scenario)
        self._indices, self._final_kpis, self._batch, self._failed = {}, {}, [], False
        self._writing = self.create_task(self._write(writer, path), "write")

    async def _record(self, frame: Frame) -> None:
        """Turn the waiting events and the frame into records; hand them over at each tick."""
        if self._events is not None:
            for message in await _waiting(self._events):
                if isinstance(message, EventEnvelope):
                    self._batch.append(EventRecord(envelope=message))
        if isinstance(frame, PlantMeasurementsFrame):
            index = self._indices.get(frame.run, 0)
            self._indices[frame.run] = index + 1
            try:
                if frame.sim_time_s is None:
                    raise ValueError("it has no sim_time_s")
                raw = RawRecord(
                    run=frame.run, index=index, sim_time_s=frame.sim_time_s, raw=frame.raw
                )
            except ValueError as exc:  # pydantic's ValidationError included: a value not finite
                self._failed = True
                message = f"cannot record {frame.run} measurements at {frame.sim_time_s} s: {exc}"
                await self.push_error(message, fatal=True)
                return
            self._batch.append(raw)
        elif isinstance(frame, KpiFrame) and not frame.data.provisional:
            self._final_kpis[frame.data.run] = frame.data
        elif isinstance(frame, TickFrame):
            self._batches.put_nowait(self._batch)  # written and flushed once per tick
            self._batch = []

    async def _write(self, writer: RunWriter, path: Path) -> None:
        """Write and flush each batch of records until ``None`` arrives (a background task)."""
        while (batch := await self._batches.get()) is not None:
            if self._failed:
                continue
            try:
                await asyncio.to_thread(_write_batch, writer, batch)
            except Exception as exc:  # any failure leaves the record incomplete
                self._failed = True
                message = f"recording to {path} failed: {type(exc).__name__}: {exc}"
                await self.push_error(message, fatal=True)

    async def _finish(self, frame: EndFrame | CancelFrame) -> None:
        """Write the last records and the manifest; close the record."""
        writer, start = self._writer, self._start
        if writer is None or start is None:
            return
        self._writer = None
        await self._record(frame)
        self._batches.put_nowait(self._batch)
        self._batches.put_nowait(None)
        self._batch = []
        if self._writing is None or self._writing.cancelled():
            self._failed = True  # a CancelFrame from further down cancelled the writes
        else:
            await self._writing
        dropped = getattr(self._events, "dropped", 0)  # InProcessSubscription counts its losses
        if dropped:
            logger.warning(f"{self.name}: the record of run {start.run_id} lacks {dropped} events")
        if self._failed:
            logger.error(f"{self.name}: run {start.run_id} has no manifest: its record failed")
            with contextlib.suppress(OSError):
                await asyncio.to_thread(writer.close)
            return
        manifest = RunManifest(
            run_id=start.run_id,
            created_utc=self._created,
            ended="finished" if isinstance(frame, EndFrame) else "cancelled",
            end_reason=frame.reason,
            settings=_redacted(self._settings),
            git_sha=self._git_sha,
            world_version=__version__,
            contracts_version=CONTRACTS_VERSION,
            scenario=start.scenario,
            scenario_sha256=self._scenario_sha256,
            plant=start.plant,
            step_s=start.step_s,
            start_time_s=start.start_time_s,
            end_time_s=self.clock.now_s if frame.sim_time_s is None else frame.sim_time_s,
            final_kpis=self._final_kpis,
            dropped_events=dropped,
        )
        try:
            await asyncio.to_thread(_close, writer, manifest, self._dir / MANIFEST_FILE)
        except OSError as exc:
            message = f"cannot write the manifest of run {start.run_id}: {exc}"
            await self.push_error(message, fatal=True)
            return
        logger.info(f"{self.name}: recorded run {start.run_id} in {self._dir}")


def _redacted(settings: WorldSettings) -> dict[str, Any]:
    """The settings as JSON data, without secrets.

    A field whose name says it is a secret (password, secret, token, api_key) shows
    ``REDACTED``, and so does the user name and password in a URL.

    Args:
        settings: The settings.
    """
    data = settings.model_dump(mode="json")
    for key, value in data.items():
        if _SECRET_NAMES.search(key) and value is not None:
            data[key] = REDACTED
        elif isinstance(value, str) and "@" in value:
            parts = urlsplit(value)
            if parts.scheme and (parts.username is not None or parts.password is not None):
                host = parts.netloc.rpartition("@")[2]
                data[key] = parts._replace(netloc=f"{REDACTED}@{host}").geturl()
    return data


def _new_writer(path: Path) -> RunWriter:
    if path.exists():
        raise FileExistsError(f"{path} already holds a recording")
    return RunWriter(path)


def _write_batch(writer: RunWriter, batch: list[Record]) -> None:
    for record in batch:
        writer.write(record)
    writer.flush()


def _close(writer: RunWriter, manifest: RunManifest, path: Path) -> None:
    writer.close()
    path.write_text(manifest.model_dump_json(indent=2) + "\n", encoding="utf-8")


def _git_sha() -> str | None:
    """The commit the world runs from, or ``None`` outside a git checkout."""
    try:
        result = subprocess.run(
            ["git", "rev-parse", "HEAD"],
            cwd=Path(__file__).parent,
            capture_output=True,
            text=True,
            timeout=10,
            check=True,
        )
    except (OSError, subprocess.SubprocessError):
        return None
    return result.stdout.strip() or None


def _scenario_sha256(scenario: str) -> str | None:
    """The SHA-256 of a scenario's file (a library name or a path), or ``None`` without one."""
    try:
        return hashlib.sha256(scenario_path(scenario).read_bytes()).hexdigest()
    except (OSError, ValueError):
        return None


async def _waiting(subscription: Subscription) -> list[Any]:
    """The messages already waiting on a subscription, taken without waiting for more."""
    messages: list[Any] = []
    while True:
        try:
            async with asyncio.timeout(0):  # a waiting message returns before the timeout fires
                messages.append(await anext(subscription))
        except (TimeoutError, StopAsyncIteration):
            return messages
