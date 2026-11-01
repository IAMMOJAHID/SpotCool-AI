import asyncio
import hashlib
import json
import math
from collections.abc import Awaitable, Callable, Iterator
from pathlib import Path
from typing import Any

import pytest
from loguru import logger

from spotcool_contracts import CONTRACTS_VERSION, Zone
from spotcool_contracts.events import (
    ComplaintCueData,
    EventEnvelope,
    EventSource,
    EventType,
)
from spotcool_world import __version__
from spotcool_world.frames import (
    CancelFrame,
    ErrorFrame,
    Frame,
    PlantMeasurementsFrame,
    TickFrame,
)
from spotcool_world.pipeline import PipelineParams
from spotcool_world.processors import FrameDirection, FrameProcessor, RecorderProcessor
from spotcool_world.processors import recorder as recorder_module
from spotcool_world.scenarios.loader import scenario_path
from spotcool_world.serializers import (
    EVENTS_FILE,
    MANIFEST_FILE,
    EventRecord,
    RawRecord,
    RunManifest,
    read_records,
)
from spotcool_world.services import PlantStartParams, WorldSettings
from spotcool_world.services.replay import ReplayPlant
from spotcool_world.testing import run_test
from spotcool_world.workers import WorkerState

RecordWorld = Callable[..., Awaitable[Any]]
STEP_S = 900.0
DAY_S = 199 * 86_400.0
START_S = DAY_S + 7 * 3600


def manifest_of(folder: Path) -> RunManifest:
    return RunManifest.model_validate_json((folder / MANIFEST_FILE).read_text("utf-8"))


def agent_event(t_s: float) -> EventEnvelope:
    return EventEnvelope(
        type=EventType.DECISION,
        source=EventSource.AGENT,
        t_ms=1,
        sim_time_s=t_s,
        data={"zone": "sou", "action": "precool"},
    )


def occupant_result(t_s: float) -> EventEnvelope:
    """A delivery result, published the way the OccupantWorker publishes it."""
    data = ComplaintCueData(zone=Zone.SOU, text="Freezing!", channel="chat", delivered=True)
    return EventEnvelope.build(EventType.COMPLAINT_CUE, EventSource.WORLD, data, t_s)


class PublishAtTick(FrameProcessor):
    """Publishes envelopes on the events topic when a tick passes, as workers beside it do."""

    def __init__(self, index: int, envelopes: list[EventEnvelope]) -> None:
        super().__init__()
        self._index = index
        self._envelopes = envelopes

    async def process_frame(self, frame: Frame, direction: FrameDirection) -> None:
        await super().process_frame(frame, direction)
        if isinstance(frame, TickFrame) and frame.index == self._index:
            for envelope in self._envelopes:
                await self.bus.publish("events", envelope)
        await self.push_frame(frame, direction)


class CancelAtTick(FrameProcessor):
    """Asks for a cancel from inside once a tick has passed."""

    def __init__(self, index: int) -> None:
        super().__init__()
        self._index = index

    async def process_frame(self, frame: Frame, direction: FrameDirection) -> None:
        await super().process_frame(frame, direction)
        await self.push_frame(frame, direction)
        if isinstance(frame, TickFrame) and frame.index == self._index:
            await self.push_frame(CancelFrame(reason="operator stop"), FrameDirection.UPSTREAM)


class BlockAtTick(FrameProcessor):
    """Holds a tick until the test is done with it (the run never reaches its end)."""

    def __init__(self, index: int, reached: asyncio.Event) -> None:
        super().__init__()
        self._index = index
        self._reached = reached

    async def process_frame(self, frame: Frame, direction: FrameDirection) -> None:
        await super().process_frame(frame, direction)
        if isinstance(frame, TickFrame) and frame.index == self._index:
            self._reached.set()
            await asyncio.Event().wait()
        await self.push_frame(frame, direction)


@pytest.mark.asyncio
async def test_a_run_round_trips_through_its_record(record_world: RecordWorld) -> None:
    run = await record_world(steps=4)
    assert run.worker.state is WorkerState.FINISHED and not run.errors
    records = list(read_records(run.folder / EVENTS_FILE))
    assert [r.envelope for r in records if isinstance(r, EventRecord)] == run.published
    expected, counts = [], {}
    for frame in run.frames:
        if isinstance(frame, PlantMeasurementsFrame):
            index = counts.get(frame.run, 0)
            counts[frame.run] = index + 1
            expected.append((frame.run, index, frame.sim_time_s, frame.raw))
    raws = [(r.run, r.index, r.sim_time_s, r.raw) for r in records if isinstance(r, RawRecord)]
    assert raws == expected
    assert [t for run_, _, t, _ in raws if run_ == "spotcool"] == [
        START_S + k * STEP_S
        for k in range(5)  # index 0: the initial state at the start time
    ]
    for before, record in zip(records, records[1:], strict=False):  # pipeline order
        if isinstance(record, RawRecord):
            assert isinstance(before, EventRecord)
            assert before.envelope.type is EventType.PLANT_MEASUREMENTS
            assert before.envelope.data["run"] == record.run
            assert before.envelope.sim_time_s == record.sim_time_s

    manifest = manifest_of(run.folder)
    assert (manifest.run_id, manifest.ended, manifest.end_reason) == (
        "run-1",
        "finished",
        "end time reached",
    )
    assert (manifest.scenario, manifest.plant, manifest.step_s) == ("hybrid_tuesday", "rc", 900.0)
    sha = hashlib.sha256(scenario_path("hybrid_tuesday").read_bytes()).hexdigest()
    assert manifest.scenario_sha256 == sha
    assert (manifest.start_time_s, manifest.end_time_s) == (START_S, START_S + 4 * STEP_S)
    assert (manifest.world_version, manifest.contracts_version) == (__version__, CONTRACTS_VERSION)
    assert manifest.git_sha is None or len(manifest.git_sha) == 40
    assert manifest.dropped_events == 0
    assert manifest.settings["run_dir"] == str(run.folder.parent)
    assert list(manifest.final_kpis) == ["spotcool", "baseline"]
    for kpi in manifest.final_kpis.values():
        assert not kpi.provisional and kpi.boptest["ener_tot"] is not None
        assert kpi.comfort_where_people_kh is not None


@pytest.mark.asyncio
async def test_the_replay_plant_replays_a_recorded_run(record_world: RecordWorld) -> None:
    run = await record_world(steps=3)
    for name in ("spotcool", "baseline"):
        measured = [
            f.raw for f in run.frames if isinstance(f, PlantMeasurementsFrame) and f.run == name
        ]
        plant = ReplayPlant(run.folder / EVENTS_FILE, name)
        assert (
            await plant.start(PlantStartParams(start_time_s=START_S, step_s=STEP_S)) == measured[0]
        )
        assert [await plant.step({}) for _ in range(3)] == measured[1:]  # float for float
        assert await plant.kpis() == manifest_of(run.folder).final_kpis[name].boptest
        await plant.stop()


@pytest.mark.asyncio
async def test_agent_events_and_occupant_results_on_the_bus_land_in_the_record(
    record_world: RecordWorld,
) -> None:
    t_s = START_S + STEP_S
    published = [agent_event(t_s), occupant_result(t_s)]
    run = await record_world(steps=3, extra=[PublishAtTick(1, published)])
    events = [
        r.envelope for r in read_records(run.folder / EVENTS_FILE) if isinstance(r, EventRecord)
    ]
    assert published[0] in events and published[1] in events
    assert events.index(published[1]) == events.index(published[0]) + 1
    assert manifest_of(run.folder).dropped_events == 0


def strings(value: Any) -> Iterator[str]:
    if isinstance(value, str):
        yield value
    elif isinstance(value, dict):
        for key, item in value.items():
            yield key
            yield from strings(item)
    elif isinstance(value, list):
        for item in value:
            yield from strings(item)


@pytest.mark.asyncio
async def test_the_record_holds_no_image_or_audio_bytes(record_world: RecordWorld) -> None:
    run = await record_world(steps=2, start_s=DAY_S + 14 * 3600)  # the 14:15 voice complaint
    cues = [e for e in run.published if e.type is EventType.COMPLAINT_CUE]
    assert len(cues) == 1 and cues[0].data["channel"] == "voice"
    data = (run.folder / EVENTS_FILE).read_bytes()
    assert b"south_complaint" not in data  # the cue's recording is never named, let alone kept
    for magic in (b"\xff\xd8\xff", b"\\xff\\xd8\\xff", b"ffd8ff", b"/9j/", b"RIFF", b"UklGR"):
        assert magic not in data  # JPEG and WAV: raw, escaped, hex or base64
    for line in data.decode("utf-8").splitlines():
        assert all(len(text) < 1000 for text in strings(json.loads(line)))  # no blob of any kind
    assert manifest_of(run.folder).dropped_events == 0


@pytest.mark.asyncio
async def test_dropped_events_are_counted_and_warned_about(
    record_world: RecordWorld, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(recorder_module, "EVENTS_QUEUE_SIZE", 3)
    warnings: list[str] = []
    sink = logger.add(lambda message: warnings.append(str(message)), level="WARNING")
    try:
        flood = [agent_event(START_S + STEP_S) for _ in range(10)]  # between two frames
        run = await record_world(steps=2, extra=[PublishAtTick(1, flood)])
    finally:
        logger.remove(sink)
    assert manifest_of(run.folder).dropped_events == 7
    assert any("lacks 7 events" in line for line in warnings)


@pytest.mark.asyncio
async def test_a_cancelled_run_still_gets_its_manifest(record_world: RecordWorld) -> None:
    run = await record_world(steps=4, extra=[CancelAtTick(1)])
    assert run.worker.state is WorkerState.CANCELLED
    manifest = manifest_of(run.folder)
    assert (manifest.ended, manifest.end_reason) == ("cancelled", "operator stop")
    assert set(manifest.final_kpis) == {"spotcool", "baseline"}  # the plants' KPIs at the cancel
    raws = [r for r in read_records(run.folder / EVENTS_FILE) if isinstance(r, RawRecord)]
    assert {r.index for r in raws} == {0, 1, 2}  # the initial states and two steps


@pytest.mark.asyncio
async def test_an_existing_record_is_never_appended_to(
    record_world: RecordWorld, tmp_path: Path
) -> None:
    folder = tmp_path / "runs" / "run-1"
    folder.mkdir(parents=True)
    (folder / EVENTS_FILE).write_text("an earlier run\n", encoding="utf-8")
    run = await record_world(steps=2)
    assert run.worker.state is WorkerState.CANCELLED
    assert any(e.fatal and "already holds a recording" in e.message for e in run.errors)
    assert (folder / EVENTS_FILE).read_text(encoding="utf-8") == "an earlier run\n"
    assert not (folder / MANIFEST_FILE).exists()


@pytest.mark.asyncio
async def test_an_unsafe_run_id_is_refused(record_world: RecordWorld, tmp_path: Path) -> None:
    run = await record_world(steps=1, run_id="../escape")
    assert run.worker.state is WorkerState.CANCELLED
    assert any(e.fatal and "a run id is" in e.message for e in run.errors)
    assert not (tmp_path / "escape").exists() and not (tmp_path / "runs").exists()


@pytest.mark.asyncio
async def test_a_failed_write_is_fatal_and_leaves_no_manifest(
    record_world: RecordWorld, monkeypatch: pytest.MonkeyPatch
) -> None:
    def full_disk(writer: object, batch: object) -> None:
        raise OSError("No space left on device")

    monkeypatch.setattr(recorder_module, "_write_batch", full_disk)
    run = await record_world(steps=3)
    assert run.worker.state is WorkerState.CANCELLED
    assert any(e.fatal and "No space left on device" in e.message for e in run.errors)
    assert not (run.folder / MANIFEST_FILE).exists()


@pytest.mark.parametrize(
    ("raw", "sim_time_s", "reason"),
    [({"x_y": math.nan}, 900.0, "finite"), ({"x_y": 1.0}, None, "no sim_time_s")],
    ids=["nan", "unstamped"],
)
@pytest.mark.asyncio
async def test_measurements_that_cannot_be_recorded_are_fatal(
    raw: dict[str, float], sim_time_s: float | None, reason: str, tmp_path: Path
) -> None:
    frame = PlantMeasurementsFrame(run="spotcool", raw=raw, sim_time_s=sim_time_s)
    recorder = RecorderProcessor(WorldSettings(run_dir=tmp_path / "runs"))
    _, up = await run_test(recorder, frames_to_send=[frame], params=PipelineParams(run_id="r"))
    errors = [f for f in up if isinstance(f, ErrorFrame)]
    assert len(errors) == 1 and errors[0].fatal and reason in errors[0].message
    assert not (tmp_path / "runs" / "r" / MANIFEST_FILE).exists()


@pytest.mark.asyncio
async def test_secrets_never_reach_the_manifest(record_world: RecordWorld, tmp_path: Path) -> None:
    settings = WorldSettings(
        run_dir=tmp_path / "runs",
        live_camera_url="http://admin:hunter2@192.168.43.1:8080/shot.jpg",
        agent_base_url="http://agent:s3cret@127.0.0.1:8002",
    )
    run = await record_world(steps=1, settings=settings)
    text = (run.folder / MANIFEST_FILE).read_text(encoding="utf-8")
    assert "hunter2" not in text and "s3cret" not in text and "admin" not in text
    shown = manifest_of(run.folder).settings
    assert shown["live_camera_url"] == "http://<redacted>@192.168.43.1:8080/shot.jpg"
    assert shown["agent_base_url"] == "http://<redacted>@127.0.0.1:8002"
    assert shown["boptest_url"] == "http://api.boptest.net"  # nothing to hide

    class WithToken(WorldSettings):
        agent_api_token: str | None = None

    redacted = recorder_module._redacted(WithToken(agent_api_token="t0ken", run_dir=tmp_path))
    assert redacted["agent_api_token"] == "<redacted>"


@pytest.mark.asyncio
async def test_a_run_that_never_ends_leaves_its_record_without_a_manifest(
    record_world: RecordWorld,
) -> None:
    reached = asyncio.Event()
    recording = asyncio.ensure_future(record_world(steps=4, extra=[BlockAtTick(1, reached)]))
    await asyncio.wait_for(reached.wait(), 10)
    path = Path.cwd() / "runs" / "run-1" / EVENTS_FILE  # the fixture's run_dir: tmp_path/runs
    for _ in range(500):  # tick 0's records, flushed while the run goes on
        if path.exists() and path.stat().st_size:
            break
        await asyncio.sleep(0.01)
    flushed = list(read_records(path))
    assert any(isinstance(r, RawRecord) and r.index == 1 for r in flushed)
    recording.cancel()  # a second Ctrl+C: no end frame ever reaches the recorder
    with pytest.raises(asyncio.CancelledError):
        await recording
    assert not (path.parent / MANIFEST_FILE).exists()
    assert len(list(read_records(path))) >= len(flushed)  # closed, still readable
