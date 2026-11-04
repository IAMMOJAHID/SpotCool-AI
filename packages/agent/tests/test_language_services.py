"""MetaSTT on a mock transport (the measured reply), MetaLLM on a fake client; no Meta call."""

import asyncio
import json
from pathlib import Path
from types import SimpleNamespace
from typing import Any, cast

import httpx
import pytest
from loguru import logger
from openai import AsyncOpenAI
from pydantic import SecretStr

from spotcool_agent.runner.cli import tone_wav
from spotcool_agent.services.llm import LLMError
from spotcool_agent.services.meta.client import MetaClient
from spotcool_agent.services.meta.llm import MetaLLM
from spotcool_agent.services.meta.stt import MetaSTT, describe_wav, parse_transcript
from spotcool_agent.services.stt import STTError

FIXTURE = Path(__file__).parent / "fixtures" / "asr_reply.json"
SAID = "It is too cold in the meeting room, can you turn the air conditioning down please? please?"
KEY = "sk-not-a-real-key-0123456789"
WAV = b"RIFF\x24\x00\x00\x00WAVEfmt " + b"\x00" * 64  # the bytes are never decoded here


def test_the_measured_reply_parses_to_its_transcript() -> None:
    assert parse_transcript(FIXTURE.read_bytes()) == SAID
    turns_only = {"sessionId": "s", "turns": [{"transcript": " It is "}, {"transcript": "cold."}]}
    assert parse_transcript(json.dumps(turns_only).encode()) == "It is cold."
    for bad in (b"not json", b"[1, 2]", b'{"sessionId": "s"}'):
        with pytest.raises(STTError):
            parse_transcript(bad)


@pytest.mark.asyncio
async def test_meta_stt_sends_as_request_keeps_audio_in_memory_and_logs_no_content(
    writes: list[str], capfd: pytest.CaptureFixture[str]
) -> None:
    seen: list[httpx.Request] = []

    def phone_meta(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        return httpx.Response(
            200, content=FIXTURE.read_bytes(), headers={"x-request-id": "rid-asr"}
        )

    logged: list[str] = []
    sink = logger.add(lambda message: logged.append(str(message)), level=0)
    client = MetaClient(SecretStr(KEY), base_url="https://api.meta.ai/v1")
    stt = MetaSTT(
        client,
        SecretStr(KEY),
        base_url="https://api.meta.ai/v1/",
        transport=httpx.MockTransport(phone_meta),
    )
    try:
        assert await stt.transcribe(WAV, 6.0) == SAID
    finally:
        await stt.close()
        logger.remove(sink)
    (request,) = seen
    assert str(request.url) == "https://api.meta.ai/v1/asr/transcribe"
    assert request.headers["authorization"] == f"Bearer {KEY}"
    body = request.content
    assert b'name="request"' in body and b"Content-Type: application/json" in body
    assert b'name="audio"; filename="clip.wav"' in body and b"Content-Type: audio/wav" in body
    assert WAV in body
    start = body.index(b'{"model"')
    settings = json.loads(body[start : body.index(b"}", start) + 1])
    assert settings["model"] == "muse-voice-transcribe-1.0"
    assert (settings["mode"], settings["audioEncoding"]) == ("DIARIZATION", "WAV")
    assert "draft" in settings["keywords"]
    (call,) = client.ledger
    assert (call.model, call.ok, call.request_id) == ("muse-voice-transcribe-1.0", True, "rid-asr")
    assert writes == []  # nothing written to disk, the audio included
    out, err = capfd.readouterr()
    for shown in (*logged, out, err):
        assert KEY not in shown and "meeting room" not in shown
    sent, answered = [line for line in logged if "META" in line]
    assert (
        "META -> muse-voice-transcribe-1.0 | POST https://api.meta.ai/v1/asr/transcribe"
        " | transcribe a voice complaint | in: WAV 0 KB (comfort keywords)"
    ) in sent  # these bytes are no real WAV, so no length
    assert f"| request-id rid-asr | out: {len(SAID)} characters" in answered


def test_the_trace_gives_a_voice_message_as_its_length_and_size() -> None:
    assert describe_wav(tone_wav(5.0)) == "WAV 5.0 s, 156 KB (comfort keywords)"
    assert describe_wav(WAV) == "WAV 0 KB (comfort keywords)"


@pytest.mark.asyncio
async def test_meta_stt_gives_up_at_its_timeout_and_on_a_refusal() -> None:
    async def slow(request: httpx.Request) -> httpx.Response:
        await asyncio.sleep(2.0)
        return httpx.Response(200, content=FIXTURE.read_bytes())

    budget = MetaClient(SecretStr(KEY), base_url="x", call_cap=2)
    stt = MetaSTT(budget, SecretStr(KEY), base_url="https://x", transport=httpx.MockTransport(slow))
    try:
        with pytest.raises(STTError, match="no answer within 0.1 s"):
            await stt.transcribe(WAV, 0.1)
    finally:
        await stt.close()
    refused = MetaSTT(
        budget,
        SecretStr(KEY),
        base_url="https://x",
        transport=httpx.MockTransport(lambda request: httpx.Response(500, text="oops")),
    )
    try:
        with pytest.raises(STTError, match="HTTP 500"):
            await refused.transcribe(WAV, 1.0)
        with pytest.raises(STTError, match="no time left"):
            await refused.transcribe(WAV, 0.0)
        with pytest.raises(STTError, match="cap of 2"):  # the language services' own budget
            await refused.transcribe(WAV, 1.0)
    finally:
        await refused.close()


class FakeResponses:
    def __init__(self, delay_s: float = 0.0) -> None:
        self.delay_s = delay_s
        self.requests: list[dict[str, Any]] = []

    async def create(self, **kwargs: Any) -> Any:
        self.requests.append(kwargs)
        await asyncio.sleep(self.delay_s)
        answer = SimpleNamespace(id="resp_llm", output_text='{"intent": "cold"}')
        headers = {"x-request-id": "rid-llm"}
        return SimpleNamespace(headers=headers, status_code=200, parse=lambda: answer)

    @property
    def with_raw_response(self) -> "FakeResponses":
        return self


def meta(responses: FakeResponses, cap: int | None = None) -> MetaClient:
    fake = SimpleNamespace(responses=responses, close=_none)
    return MetaClient(
        SecretStr(KEY), base_url="x", call_cap=cap, make_openai=lambda: cast(AsyncOpenAI, fake)
    )


async def _none() -> None:
    return None


@pytest.mark.asyncio
async def test_meta_llm_asks_muse_wonder_through_the_shared_client() -> None:
    responses = FakeResponses()
    client = meta(responses, cap=1)
    llm = MetaLLM(client)
    logged: list[str] = []
    sink = logger.add(lambda message: logged.append(message.record["message"]), level="INFO")
    try:
        answer = await llm.complete("Classify this.", 4.0, purpose="classify")
    finally:
        logger.remove(sink)
    assert answer == '{"intent": "cold"}'
    assert logged[0] == (
        "META -> muse-wonder-1.0 | POST x/responses | classify | in: prompt 14 characters"
    )
    assert logged[1].endswith(' s | request-id rid-llm | response resp_llm | out: intent "cold"')
    assert not any("Classify this" in line for line in logged)  # never the prompt's words
    (request,) = responses.requests
    assert request["model"] == "muse-wonder-1.0"
    assert request["input"][0]["content"] == [{"type": "input_text", "text": "Classify this."}]
    assert [(c.model, c.purpose, c.ok, c.request_id) for c in client.ledger] == [
        ("muse-wonder-1.0", "classify", True, "rid-llm")
    ]
    with pytest.raises(LLMError, match="cap of 1"):  # the run's cap, shared with SAM
        await llm.complete("Again.", 4.0, purpose="reply")


@pytest.mark.asyncio
async def test_meta_llm_gives_up_at_its_timeout() -> None:
    llm = MetaLLM(meta(FakeResponses(delay_s=2.0)))
    with pytest.raises(LLMError, match="no answer within 0.1 s"):
        await llm.complete("Reply.", 0.1, purpose="reply")
