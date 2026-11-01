import asyncio
import json
import wave
from email.parser import BytesParser
from pathlib import Path
from typing import Literal

import httpx
import pytest
from loguru import logger

from spotcool_contracts.events import (
    CalendarData,
    ComplaintCueData,
    EventEnvelope,
    EventSource,
    EventType,
    parse_world_payload,
)
from spotcool_contracts.zones import Zone
from spotcool_world.bus import InProcessBus
from spotcool_world.frames import CalendarFrame, ComplaintCueFrame, Frame
from spotcool_world.processors import WORLD_FRAMES_TOPIC
from spotcool_world.runner import WorkerRunner
from spotcool_world.services import WorldSettings
from spotcool_world.workers import WorkerState, occupant_worker
from spotcool_world.workers.occupant_worker import OccupantWorker

T_S = 199 * 86_400 + 14 * 3600 + 15 * 60
LINE = "It's freezing in this meeting room."
WAV_NAME = "south_complaint.wav"


def write_wav(
    path: Path, *, rate: int = 16_000, channels: int = 1, width: int = 2, frames: int = 1600
) -> bytes:
    """A sawtooth tone made with the standard library (never a real voice)."""
    size = frames * channels * width
    path.parent.mkdir(parents=True, exist_ok=True)
    with wave.open(str(path), "wb") as wav:
        wav.setnchannels(channels)
        wav.setsampwidth(width)
        wav.setframerate(rate)
        wav.writeframes((bytes(range(256)) * (size // 256 + 1))[:size])
    return path.read_bytes()


def cue(
    channel: Literal["chat", "voice"] = "voice",
    *,
    zone: Zone = Zone.SOU,
    audio: str | None = WAV_NAME,
) -> ComplaintCueFrame:
    data = ComplaintCueData(zone=zone, text=LINE, channel=channel, delivered=False)
    return ComplaintCueFrame(data=data, audio=audio if channel == "voice" else None, sim_time_s=T_S)


class Agent:
    """A mock agent: records every request and answers from a script (then 200 OK)."""

    def __init__(self, *answers: int | Exception) -> None:
        self.requests: list[httpx.Request] = []
        self._answers = list(answers)

    def __call__(self, request: httpx.Request) -> httpx.Response:
        request.read()
        self.requests.append(request)
        answer = self._answers.pop(0) if self._answers else 200
        if isinstance(answer, Exception):
            raise answer
        return httpx.Response(answer, text="ok" if answer < 400 else "nope")

    @property
    def paths(self) -> list[str]:
        return [request.url.path for request in self.requests]


async def deliver(settings: WorldSettings, agent: Agent, *messages: Frame) -> list[EventEnvelope]:
    """Run a worker, publish ``messages`` on the world topic, return the result events."""
    bus = InProcessBus()
    events = bus.subscribe("events")
    worker = OccupantWorker(settings, backoff_s=0.0, transport=httpx.MockTransport(agent))
    expected = sum(isinstance(m, ComplaintCueFrame) for m in messages)
    results: list[EventEnvelope] = []

    async def drive() -> None:
        # gather started the worker first, and run() subscribes before its first await
        assert bus.subscriber_count(WORLD_FRAMES_TOPIC) == 1
        for message in messages:
            await bus.publish(WORLD_FRAMES_TOPIC, message)
        async for envelope in events:
            results.append(envelope)
            if len(results) == expected:
                break
        await worker.stop("test done")

    WorkerRunner(bus=bus, handle_sigint=False).add(worker)  # gives the worker the bus
    await asyncio.wait_for(asyncio.gather(worker.run(), drive()), timeout=5.0)
    assert worker.state is WorkerState.FINISHED
    return results


def result(envelope: EventEnvelope) -> ComplaintCueData:
    assert (envelope.type, envelope.source) == (EventType.COMPLAINT_CUE, EventSource.WORLD)
    assert envelope.sim_time_s == T_S
    payload = parse_world_payload(envelope)
    assert isinstance(payload, ComplaintCueData)
    return payload


def form(request: httpx.Request) -> dict[str, tuple[str | None, str, bytes]]:
    """A multipart/form-data body as name -> (file name, content type, bytes)."""
    head = f"Content-Type: {request.headers['content-type']}\r\n\r\n".encode()
    message = BytesParser().parsebytes(head + request.content)
    parts: dict[str, tuple[str | None, str, bytes]] = {}
    for part in message.walk():
        if not part.is_multipart():
            name = str(part.get_param("name", header="content-disposition"))
            payload = part.get_payload(decode=True)
            assert isinstance(payload, bytes)
            parts[name] = (part.get_filename(), part.get_content_type(), payload)
    return parts


def files(root: Path) -> list[Path]:
    return sorted(root.rglob("*"))


@pytest.fixture
def footage(tmp_path: Path) -> Path:
    return tmp_path / "footage"


@pytest.fixture
def settings(footage: Path) -> WorldSettings:
    return WorldSettings(footage_dir=footage)


@pytest.mark.asyncio
async def test_a_chat_cue_posts_the_complaint_as_json(settings: WorldSettings) -> None:
    agent = Agent()
    [envelope] = await deliver(settings, agent, cue("chat"))
    [request] = agent.requests
    assert (request.method, request.url.path) == ("POST", "/chat")
    assert str(request.url).startswith(settings.agent_base_url)
    assert request.headers["content-type"] == "application/json"
    assert json.loads(request.content) == {
        "text": LINE,
        "source": "occupant-sim",
        "room_hint": "meeting-south",
        "sim_time_s": T_S,
    }
    timeout = request.extensions["timeout"]
    assert timeout == {"connect": 10.0, "read": 10.0, "write": 10.0, "pool": 10.0}
    data = result(envelope)
    assert data == ComplaintCueData(zone=Zone.SOU, text=LINE, channel="chat", delivered=True)


@pytest.mark.asyncio
async def test_a_zone_without_a_room_sends_no_room_hint(settings: WorldSettings) -> None:
    agent = Agent()
    await deliver(settings, agent, cue("chat", zone=Zone.COR))
    assert json.loads(agent.requests[0].content)["room_hint"] is None


@pytest.mark.asyncio
async def test_a_voice_cue_posts_the_recording_and_the_meta(
    settings: WorldSettings, footage: Path
) -> None:
    wav = write_wav(footage / "audio" / WAV_NAME)
    agent = Agent()
    [envelope] = await deliver(settings, agent, cue("voice"))
    [request] = agent.requests
    assert request.url.path == "/voice"
    assert request.headers["content-type"].startswith("multipart/form-data; boundary=")
    parts = form(request)
    assert set(parts) == {"audio", "meta"}
    name, kind, _ = parts["audio"]
    assert (name, kind) == (WAV_NAME, "audio/wav")
    assert wav in request.content
    assert json.loads(parts["meta"][2]) == {
        "text": LINE,
        "source": "occupant-sim",
        "room_hint": "meeting-south",
        "sim_time_s": T_S,
    }
    data = result(envelope)
    assert (data.channel, data.delivered, data.detail) == ("voice", True, None)


@pytest.mark.parametrize(
    ("prepare", "audio", "reason"),
    [
        ("nothing", WAV_NAME, "south_complaint.wav is missing"),
        ("8 kHz stereo", WAV_NAME, "is 8000 Hz, 2 channel(s), 16-bit, not 16000 Hz mono 16-bit"),
        ("8-bit", WAV_NAME, "is 16000 Hz, 1 channel(s), 8-bit, not 16000 Hz mono 16-bit"),
        ("text", WAV_NAME, "is not a WAV file"),
        ("silent header", WAV_NAME, "holds no audio"),
        ("directory", WAV_NAME, "is unreadable"),
        ("nothing", None, "the cue has no audio file"),
        ("nothing", "../south_complaint.wav", "is not a file name in"),
    ],
)
@pytest.mark.asyncio
async def test_a_recording_that_cannot_be_sent_falls_back_to_chat(
    settings: WorldSettings, footage: Path, prepare: str, audio: str | None, reason: str
) -> None:
    path = footage / "audio" / WAV_NAME
    path.parent.mkdir(parents=True)
    if prepare == "8 kHz stereo":
        write_wav(path, rate=8000, channels=2)
    elif prepare == "8-bit":
        write_wav(path, width=1)
    elif prepare == "text":
        path.write_text("not audio", encoding="utf-8")
    elif prepare == "silent header":
        write_wav(path, frames=0)
    elif prepare == "directory":
        path.mkdir()
    agent = Agent()
    [envelope] = await deliver(settings, agent, cue("voice", audio=audio))
    assert agent.paths == ["/chat"]
    data = result(envelope)
    assert (data.channel, data.delivered) == ("voice", True)
    assert data.detail is not None
    assert data.detail.startswith("voice fell back to chat: ")
    assert reason in data.detail


@pytest.mark.asyncio
async def test_without_footage_dir_a_voice_cue_falls_back_to_chat() -> None:
    agent = Agent()
    [envelope] = await deliver(WorldSettings(), agent, cue("voice"))
    assert agent.paths == ["/chat"]
    assert result(envelope).detail == "voice fell back to chat: footage_dir is not set"


@pytest.mark.asyncio
async def test_a_recording_too_large_falls_back_to_chat(
    settings: WorldSettings, footage: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    write_wav(footage / "audio" / WAV_NAME)
    monkeypatch.setattr(occupant_worker, "MAX_AUDIO_BYTES", 100)
    agent = Agent()
    [envelope] = await deliver(settings, agent, cue("voice"))
    assert agent.paths == ["/chat"]
    assert "is larger than 100 bytes" in (result(envelope).detail or "")


@pytest.mark.asyncio
async def test_connection_errors_are_retried_twice(settings: WorldSettings) -> None:
    agent = Agent(httpx.ConnectError("refused"), httpx.ConnectTimeout("slow"))
    [envelope] = await deliver(settings, agent, cue("chat"))
    assert agent.paths == ["/chat"] * 3
    assert (result(envelope).delivered, result(envelope).detail) == (True, None)


@pytest.mark.asyncio
async def test_server_errors_are_retried_twice_then_reported(settings: WorldSettings) -> None:
    agent = Agent(503, 502, 500)
    [envelope] = await deliver(settings, agent, cue("chat"))
    assert agent.paths == ["/chat"] * 3
    data = result(envelope)
    assert (data.delivered, data.detail) == (False, "POST /chat: HTTP 500 (after 3 attempts)")


@pytest.mark.asyncio
async def test_the_agent_down_is_reported_after_three_attempts(settings: WorldSettings) -> None:
    agent = Agent(*[httpx.ConnectError("refused")] * 3)
    [envelope] = await deliver(settings, agent, cue("chat"))
    assert len(agent.requests) == 3
    assert result(envelope).detail == "POST /chat: ConnectError: refused (after 3 attempts)"


@pytest.mark.parametrize(
    ("answer", "detail"),
    [
        (422, "POST /chat: HTTP 422: nope"),
        (httpx.ReadTimeout("no answer"), "POST /chat: ReadTimeout: no answer"),
    ],
)
@pytest.mark.asyncio
async def test_client_errors_and_lost_answers_are_not_retried(
    settings: WorldSettings, answer: int | Exception, detail: str
) -> None:
    # A request that may have reached the agent is not sent again: no duplicate complaints.
    agent = Agent(answer)
    [envelope] = await deliver(settings, agent, cue("chat"))
    assert len(agent.requests) == 1
    assert (result(envelope).delivered, result(envelope).detail) == (False, detail)


@pytest.mark.asyncio
async def test_a_failed_voice_post_does_not_fall_back(
    settings: WorldSettings, footage: Path
) -> None:
    write_wav(footage / "audio" / WAV_NAME)
    agent = Agent(500, 500, 500)
    [envelope] = await deliver(settings, agent, cue("voice"))
    assert agent.paths == ["/voice"] * 3
    data = result(envelope)
    assert (data.delivered, data.detail) == (False, "POST /voice: HTTP 500 (after 3 attempts)")


@pytest.mark.asyncio
async def test_a_failed_fallback_reports_both_reasons(settings: WorldSettings) -> None:
    agent = Agent(404)
    [envelope] = await deliver(settings, agent, cue("voice"))
    detail = result(envelope).detail
    assert detail is not None and detail.startswith("voice fell back to chat: ")
    assert detail.endswith("; POST /chat: HTTP 404: nope")


@pytest.mark.asyncio
async def test_a_crash_in_one_delivery_is_reported_and_the_next_cue_goes_out(
    settings: WorldSettings,
) -> None:
    agent = Agent(RuntimeError("agent exploded"))
    first, second = await deliver(settings, agent, cue("chat"), cue("chat", zone=Zone.NOR))
    assert (result(first).delivered, result(first).detail) == (
        False,
        "RuntimeError: agent exploded",
    )
    assert result(second).delivered is True


@pytest.mark.asyncio
async def test_other_frames_on_the_topic_are_ignored(settings: WorldSettings) -> None:
    agent = Agent()
    other = CalendarFrame(data=CalendarData(room="meeting-south", items=[]))
    results = await deliver(settings, agent, other, cue("chat"))
    assert len(results) == 1 and agent.paths == ["/chat"]


@pytest.mark.asyncio
async def test_audio_is_never_logged_published_or_copied(
    settings: WorldSettings, footage: Path, tmp_path: Path
) -> None:
    write_wav(footage / "audio" / WAV_NAME)
    files_before = files(tmp_path)
    logs: list[str] = []
    sink = logger.add(logs.append, level="DEBUG", format="{message}")
    try:
        [envelope] = await deliver(settings, Agent(), cue("voice"))
    finally:
        logger.remove(sink)
    assert files(tmp_path) == files_before
    assert logs and not any("RIFF" in line for line in logs)  # the WAV header never shows
    assert "RIFF" not in envelope.model_dump_json()


@pytest.mark.asyncio
async def test_a_worker_stopped_before_it_runs_returns_at_once(settings: WorldSettings) -> None:
    worker = OccupantWorker(settings)
    worker.bus = InProcessBus()
    await worker.stop()
    await asyncio.wait_for(worker.run(), timeout=1.0)
    assert worker.state is WorkerState.FINISHED
    with pytest.raises(RuntimeError, match="has already run"):
        await worker.run()


@pytest.mark.asyncio
async def test_a_worker_needs_a_bus(settings: WorldSettings) -> None:
    with pytest.raises(RuntimeError, match="needs a bus"):
        await OccupantWorker(settings).run()
