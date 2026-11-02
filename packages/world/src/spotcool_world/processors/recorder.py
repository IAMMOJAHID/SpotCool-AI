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
_SECRETS = re.compile(r"token|key|secret|password|auth", re.IGNORECASE)
_SECRET_PARAMS = re.compile(
    r"((?:^|&)[^=&]*(?:token|key|secret|password|auth)[^=&]*=)[^&]*", re.IGNORECASE
)


class RecorderProcessor(FrameProcessor):
    """Records the run in ``settings.run_dir/<run_id>/``: ``events.jsonl``, then ``manifest.json``.

    ``setup()`` subscribes to the events topic, before the run starts. Then, on every frame,
    the events waiting there (the pipeline's, the occupant worker's, the agent's) become event
    records, each one as it is taken, and each ``PlantMeasurementsFrame`` becomes a raw record
    (``index`` 0 is the run's initial state). Each ``TickFrame`` hands its records to a
    background task, which writes and flushes them in a worker thread, so the record follows
    pipeline order and the tick never waits on the disk.

    The first ``EndFrame`` or ``CancelFrame`` finishes the record, as one task: it takes the
    last events (lockstep: everything the pipeline published has passed ``BusPublisher`` by
    then), waits for the writes, writes any batch the writing task left, and writes
    ``manifest.json`` (``RunManifest``) with the final ``KpiFrame``s it saw, then closes the
    file. The end frame waits for that task through ``asyncio.shield``, so a cancel of the
    task carrying it (``stop()`` while it passes) cannot cut the record short, and a later end
    frame waits for the same task. This happens before the base class handles the frame,
    since a downstream ``CancelFrame`` cancels this processor's tasks. A full subscription
    drops events; the manifest counts them in ``dropped_events`` and a warning is logged.

    A run without its record is invalid, so every recording failure is fatal: an unsafe run id
    (``RUN_ID_PATTERN``), a run directory that already holds a recording or cannot be made, a
    failed write. A run whose writes failed gets no manifest, and so does a run that never
    reached an end frame: ``cleanup()`` closes its file. Records hold JSON only: no image or
    audio bytes.

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
        self._path = Path()  # the run's events.jsonl, once it is recording
        self._start: StartFrame | None = None
        self._created = datetime.now(UTC)
        self._git_sha: str | None = None
        self._scenario_sha256: str | None = None
        self._indices: dict[Run, int] = {}
        self._final_kpis: dict[Run, KpiData] = {}
        self._batch: list[Record] = []
        self._batches: asyncio.Queue[list[Record] | None] = asyncio.Queue()
        self._writing: asyncio.Task[Any] | None = None
        self._in_flight: asyncio.Future[None] | None = None
        self._finishing: asyncio.Task[Any] | None = None
        self._failed = False

    async def setup(self, setup: FrameProcessorSetup) -> None:
        """Subscribe to the events topic, so the run's first events are not missed."""
        await super().setup(setup)
        self._events = setup.bus.subscribe(self._topic, maxsize=EVENTS_QUEUE_SIZE)

    async def process_frame(self, frame: Frame, direction: FrameDirection) -> None:
        """Record the run as frames pass, and finish the record at its end; pass frames on."""
        downstream = direction is FrameDirection.DOWNSTREAM
        if downstream and isinstance(frame, EndFrame | CancelFrame):
            await self._finish(frame)  # first: on a CancelFrame the base class cancels our tasks
        await super().process_frame(frame, direction)
        if downstream and isinstance(frame, StartFrame):
            await self._open(frame)
        elif downstream and self._writer is not None and self._finishing is None:
            await self._record(frame)
        await self.push_frame(frame, direction)

    async def cleanup(self) -> None:
        """Close the subscription, and the file of a record the run never finished."""
        await super().cleanup()
        if self._events is not None:
            self._events.close()
        if self._writer is not None:
            if self._in_flight is not None:
                await asyncio.wait([self._in_flight], timeout=5)  # a write in a thread ends first
            with contextlib.suppress(OSError):  # the other processors still need their cleanup
                self._writer.close()
            self._writer = None
            logger.warning(f"{self.name}: closed {self._path} without a manifest: unfinished")

    async def _open(self, start: StartFrame) -> None:
        """Begin recording the run the ``StartFrame`` starts."""
        if not re.fullmatch(RUN_ID_PATTERN, start.run_id):
            await self.push_error(
                f"cannot record run {start.run_id!r}: a run id is letters, digits, _ and -",
                fatal=True,
            )
            return
        path = self._settings.run_dir / start.run_id / EVENTS_FILE
        try:
            writer = await asyncio.to_thread(_new_writer, path)
        except OSError as exc:
            await self.push_error(f"cannot record run {start.run_id}: {exc}", fatal=True)
            return
        self._writer, self._path, self._start = writer, path, start
        self._created = datetime.now(UTC)
        self._git_sha = await asyncio.to_thread(_git_sha)
        self._scenario_sha256 = await asyncio.to_thread(_scenario_sha256, start.scenario)
        self._indices, self._final_kpis, self._batch, self._failed = {}, {}, [], False
        self._writing = self.create_task(self._write(writer), "write")

    async def _record(self, frame: Frame) -> None:
        """Turn the waiting events and the frame into records; hand them over at each tick."""
        await self._take_events()
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
                await self._fail(
                    f"cannot record {frame.run} measurements at {frame.sim_time_s} s: {exc}"
                )
                return
            self._batch.append(raw)
        elif isinstance(frame, KpiFrame) and not frame.data.provisional:
            self._final_kpis[frame.data.run] = frame.data
        elif isinstance(frame, TickFrame):
            self._batches.put_nowait(self._batch)  # written and flushed once per tick
            self._batch = []

    async def _take_events(self) -> None:
        """Move the events waiting on the subscription into the batch, each as it is taken.

        The only wait is on an empty subscription, so a cancel never loses a taken event.
        """
        if self._events is None:
            return
        while True:
            try:
                async with asyncio.timeout(0):  # a waiting event returns before the timeout fires
                    message = await anext(self._events)
            except (TimeoutError, StopAsyncIteration):
                return
            if isinstance(message, EventEnvelope):
                self._batch.append(EventRecord(envelope=message))

    async def _write(self, writer: RunWriter) -> None:
        """Write each batch the ticks hand over, until ``None`` arrives (a background task)."""
        while (batch := await self._batches.get()) is not None:
            await self._write_batch(writer, batch)

    async def _write_batch(self, writer: RunWriter, batch: list[Record]) -> None:
        """Write and flush a batch in a worker thread; a failure fails the record and the run."""
        if self._failed or not batch:
            return
        self._in_flight = asyncio.get_running_loop().run_in_executor(
            None, _write_records, writer, batch
        )
        try:
            await asyncio.shield(self._in_flight)  # a cancel never cuts a write short
        except Exception as exc:  # any failure leaves the record incomplete
            await self._fail(f"recording to {self._path} failed: {type(exc).__name__}: {exc}")

    async def _fail(self, message: str) -> None:
        """The record is incomplete: it gets no manifest, and the run fails."""
        self._failed = True
        await self.push_error(message, fatal=True)

    async def _finish(self, frame: EndFrame | CancelFrame) -> None:
        """Finish the record once: the first end frame starts it, a later one waits for it."""
        if self._finishing is None:
            if self._writer is None:
                return
            self._finishing = self.create_task(self._finish_record(frame), "finish")
        await asyncio.shield(self._finishing)

    async def _finish_record(self, frame: EndFrame | CancelFrame) -> None:
        """Write the last records and the manifest, then close the file (a background task)."""
        writer, start = self._writer, self._start
        assert writer is not None and start is not None  # _finish starts it only while recording
        await self._take_events()
        self._batches.put_nowait(self._batch)
        self._batch = []
        if self._writing is not None and not self._writing.done():
            self._batches.put_nowait(None)
            await asyncio.wait([self._writing])
        await self._write_left(writer)
        dropped = getattr(self._events, "dropped", 0)  # InProcessSubscription counts its losses
        if dropped:
            logger.warning(f"{self.name}: the record of run {start.run_id} lacks {dropped} events")
        manifest = None if self._failed else self._manifest(frame, start, dropped)
        try:
            await asyncio.to_thread(_close, writer, manifest, self._path.parent / MANIFEST_FILE)
        except OSError as exc:
            await self._fail(f"cannot finish the record of run {start.run_id}: {exc}")
        self._writer = None  # closed, even when the last flush failed
        if self._failed:
            logger.error(f"{self.name}: run {start.run_id} has no manifest: its record failed")
        else:
            logger.info(f"{self.name}: recorded run {start.run_id} in {self._path.parent}")

    async def _write_left(self, writer: RunWriter) -> None:
        """Write the batches the writing task left: it ended early, or was cancelled."""
        if self._in_flight is not None:
            await asyncio.wait([self._in_flight])  # its last write, which a cancel did not stop
            failure = None if self._in_flight.cancelled() else self._in_flight.exception()
            if failure is not None and not self._failed:
                await self._fail(f"recording to {self._path} failed: {failure}")
        while not self._batches.empty():
            if (batch := self._batches.get_nowait()) is not None:
                await self._write_batch(writer, batch)

    def _manifest(
        self, frame: EndFrame | CancelFrame, start: StartFrame, dropped: int
    ) -> RunManifest:
        return RunManifest(
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


def _redacted(settings: WorldSettings) -> dict[str, Any]:
    """The settings as JSON data, without secrets.

    A field whose name says it holds a secret (token, key, secret, password, auth) shows
    ``REDACTED``, and so do a URL's user name and password and its query parameters named so.

    Args:
        settings: The settings.
    """
    data = settings.model_dump(mode="json")
    for key, value in data.items():
        if _SECRETS.search(key) and value is not None:
            data[key] = REDACTED
        elif isinstance(value, str):
            data[key] = _redacted_url(value)
    return data


def _redacted_url(text: str) -> str:
    """``text`` without the credentials it carries if it is a URL; otherwise unchanged."""
    parts = urlsplit(text)
    if not parts.scheme or not parts.netloc:
        return text
    netloc, query = parts.netloc, _SECRET_PARAMS.sub(rf"\1{REDACTED}", parts.query)
    if parts.username is not None or parts.password is not None:
        netloc = f"{REDACTED}@{netloc.rpartition('@')[2]}"
    if (netloc, query) == (parts.netloc, parts.query):
        return text
    return parts._replace(netloc=netloc, query=query).geturl()


def _new_writer(path: Path) -> RunWriter:
    if path.exists():
        raise FileExistsError(f"{path} already holds a recording")
    return RunWriter(path)


def _write_records(writer: RunWriter, batch: list[Record]) -> None:
    for record in batch:
        writer.write(record)
    writer.flush()


def _close(writer: RunWriter, manifest: RunManifest | None, path: Path) -> None:
    writer.close()
    if manifest is not None:
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
