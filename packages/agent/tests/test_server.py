import asyncio
import io
import socket
import time
import wave

import httpx
import pytest
from pydantic import BaseModel

from spotcool_agent.loops.complaints import NOT_ON, UNHEARD, ComplaintLoop
from spotcool_agent.transports.server import (
    MAX_VOICE_BYTES,
    AgentServer,
    Complaint,
    create_app,
    dashboard_origins,
)
from spotcool_contracts.events import EventType

ORIGINS = dashboard_origins("192.168.1.20", 8090)
COMPLAINT = {"text": "It's freezing in this meeting room.", "room_hint": "meeting-south"}


def wav(seconds: float = 1.5, rate: int = 16_000, channels: int = 1) -> bytes:
    buffer = io.BytesIO()
    with wave.open(buffer, "wb") as out:
        out.setnchannels(channels)
        out.setsampwidth(2)
        out.setframerate(rate)
        out.writeframes(b"\x00\x00" * channels * int(seconds * rate))
    return buffer.getvalue()


def client(queue: "asyncio.Queue[Complaint]") -> httpx.AsyncClient:
    app = create_app(queue, origins=ORIGINS)
    return httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://agent")


@pytest.mark.asyncio
async def test_a_chat_complaint_is_queued_and_answered_202_at_once() -> None:
    queue: asyncio.Queue[Complaint] = asyncio.Queue()
    async with client(queue) as http:
        began = time.perf_counter()
        response = await http.post("/chat", json=COMPLAINT)
        assert time.perf_counter() - began < 1.0  # the world waits 10 s at most
    assert (response.status_code, response.json()) == (202, {"accepted": True})
    queued = queue.get_nowait()
    assert queued.channel == "chat" and queued.complaint is not None
    assert queued.complaint.text == COMPLAINT["text"]


@pytest.mark.asyncio
async def test_a_voice_complaint_keeps_its_audio_in_memory() -> None:
    queue: asyncio.Queue[Complaint] = asyncio.Queue()
    async with client(queue) as http:
        response = await http.post(
            "/voice",
            files={"audio": ("complaint.wav", wav(), "audio/wav")},
            data={"meta": '{"text": "It is freezing.", "source": "occupant-sim"}'},
        )
    assert response.status_code == 202
    queued = queue.get_nowait()
    assert (queued.channel, queued.audio_s, queued.audio) == ("voice", 1.5, wav())
    assert queued.complaint is not None and queued.complaint.text == "It is freezing."


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("post", "status"),
    [
        ({"url": "/chat", "json": {"text": ""}}, 422),
        ({"url": "/chat", "json": {"text": "x" * 281}}, 422),
        ({"url": "/chat", "json": {"text": "hi", "colour": "red"}}, 422),
        ({"url": "/voice", "files": {"audio": ("a.wav", b"RIFF....", "audio/wav")}}, 422),
        ({"url": "/voice", "files": {"audio": ("a.wav", wav(rate=8000), "audio/wav")}}, 422),
        ({"url": "/voice", "files": {"audio": ("a.wav", wav(channels=2), "audio/wav")}}, 422),
        ({"url": "/voice", "data": {"meta": '{"text": "hi"}'}}, 422),
        (
            {
                "url": "/voice",
                "files": {"audio": ("a.wav", wav(), "audio/wav")},
                "data": {"meta": "not json"},
            },
            422,
        ),
        ({"url": "/voice", "files": {"audio": ("a.wav", wav(seconds=40), "audio/wav")}}, 413),
    ],
    ids=[
        "empty",
        "too-long",
        "extra-field",
        "not-a-wav",
        "8-khz",
        "stereo",
        "no-audio",
        "bad-meta",
        "too-big",
    ],
)
async def test_an_invalid_complaint_is_refused_and_not_queued(post: dict, status: int) -> None:
    queue: asyncio.Queue[Complaint] = asyncio.Queue()
    async with client(queue) as http:
        response = await http.post(**post)
    assert response.status_code == status
    assert queue.empty()
    assert len(wav(seconds=40)) > MAX_VOICE_BYTES


@pytest.mark.asyncio
async def test_a_full_queue_answers_503_which_the_world_retries() -> None:
    queue: asyncio.Queue[Complaint] = asyncio.Queue(maxsize=1)
    async with client(queue) as http:
        assert (await http.post("/chat", json=COMPLAINT)).status_code == 202
        assert (await http.post("/chat", json=COMPLAINT)).status_code == 503


@pytest.mark.asyncio
async def test_the_dashboard_page_may_post_with_its_headers_and_others_may_not() -> None:
    queue: asyncio.Queue[Complaint] = asyncio.Queue()
    preflight = {
        "Access-Control-Request-Method": "POST",
        "Access-Control-Request-Headers": "content-type,x-spotcool-client",
    }
    async with client(queue) as http:
        for origin in ("http://192.168.1.20:8090", "http://localhost:5173"):
            response = await http.options("/voice", headers={"Origin": origin, **preflight})
            assert response.status_code == 200
            assert response.headers["access-control-allow-origin"] == origin
            allowed = response.headers["access-control-allow-headers"].lower()
            assert "content-type" in allowed and "x-spotcool-client" in allowed
        stranger = await http.options(
            "/chat", headers={"Origin": "http://evil.example", **preflight}
        )
        assert stranger.status_code == 400
        assert (await http.get("/healthz")).json() == {"ok": True}


@pytest.mark.asyncio
async def test_each_complaint_gets_one_chat_reply_even_without_models_or_the_vrf() -> None:
    queue: asyncio.Queue[Complaint] = asyncio.Queue()
    published: list[tuple[EventType, str, float | None]] = []

    def publish(kind: EventType, data: BaseModel, t_s: float | None) -> None:
        if kind is EventType.CHAT:
            published.append((kind, data.model_dump()["text"], t_s))

    loop = ComplaintLoop(queue, publish)  # no transcriber, no model, no VRF loop
    running = asyncio.create_task(loop.run())
    async with client(queue) as http:
        await http.post("/chat", json=COMPLAINT | {"sim_time_s": 17_270_100.0})
        await http.post("/voice", files={"audio": ("a.wav", wav(), "audio/wav")})
    deadline = time.perf_counter() + 2.0
    while loop.answered < 2:
        assert time.perf_counter() < deadline, "the complaints were not answered"
        await asyncio.sleep(0.01)
    running.cancel()
    await asyncio.gather(running, return_exceptions=True)
    assert published == [
        (EventType.CHAT, f"Thanks, I've {NOT_ON}.", 17_270_100.0),
        (EventType.CHAT, UNHEARD, None),
    ]


@pytest.mark.asyncio
async def test_the_server_serves_on_its_port_and_stops() -> None:
    with socket.socket() as probe:
        probe.bind(("127.0.0.1", 0))
        port = probe.getsockname()[1]
    queue: asyncio.Queue[Complaint] = asyncio.Queue()
    server = AgentServer(create_app(queue, origins=ORIGINS), "127.0.0.1", port)
    await server.start()
    try:
        async with httpx.AsyncClient() as http:
            response = await http.post(f"http://127.0.0.1:{port}/chat", json=COMPLAINT)
        assert response.status_code == 202
        with pytest.raises(OSError):  # the port is the agent's alone
            await AgentServer(create_app(queue, origins=ORIGINS), "127.0.0.1", port).start()
    finally:
        await server.stop()
    await server.stop()
