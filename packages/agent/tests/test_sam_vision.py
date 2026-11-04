import asyncio
import io
from types import SimpleNamespace
from typing import Any, cast

import httpx2
import openai
import pytest
from loguru import logger
from openai import AsyncOpenAI
from PIL import Image
from pydantic import SecretStr

from spotcool_agent.domain.geometry import Detection
from spotcool_agent.services.meta.client import MetaClient
from spotcool_agent.services.meta.vision import SamVision, describe_boxes
from spotcool_agent.services.vision import CallCapReached, VisionError

ANSWER = (
    "<0f>0<|box;x1=10;y1=10;x2=50;y2=90;w=100;h=100;c=0.9|>"
    ",1<|box;x1=60;y1=10;x2=90;y2=90;w=100;h=100;c=0.2|>"
)
"""Two people, the second below the 0.3 confidence floor."""


def jpeg(size: tuple[int, int] = (200, 100)) -> bytes:
    buffer = io.BytesIO()
    Image.new("RGB", size, (128, 128, 128)).save(buffer, format="JPEG")
    return buffer.getvalue()


def rate_limited() -> openai.RateLimitError:
    request = httpx2.Request("POST", "https://api.meta.ai/v1/responses")
    return openai.RateLimitError(
        "slow down", response=httpx2.Response(429, request=request), body=None
    )


NO_TEXT = object()
"""An answer without ``output_text``."""


class FakeResponses:
    """``responses.create``: each call pops the next outcome (text, exception or delay)."""

    def __init__(self, *outcomes: Any, headers: dict[str, str] | None = None) -> None:
        self.outcomes = list(outcomes)
        self.requests: list[dict[str, Any]] = []
        self.headers = headers or {}

    @property
    def with_raw_response(self) -> Any:
        """``responses.with_raw_response``: the answer with its status and headers."""

        async def create(**kwargs: Any) -> Any:
            answer = await self.create(**kwargs)
            return SimpleNamespace(headers=self.headers, status_code=200, parse=lambda: answer)

        return SimpleNamespace(create=create)

    async def create(self, **kwargs: Any) -> Any:
        self.requests.append(kwargs)
        outcome = self.outcomes.pop(0) if self.outcomes else ANSWER
        if isinstance(outcome, BaseException):
            raise outcome
        if isinstance(outcome, float):
            await asyncio.sleep(outcome)
            outcome = ANSWER
        if outcome is NO_TEXT:
            return SimpleNamespace()
        return SimpleNamespace(id="resp_6aeb", output_text=outcome)


def client(responses: FakeResponses, **kwargs: Any) -> MetaClient:
    fake = SimpleNamespace(responses=responses, close=lambda: asyncio.sleep(0))
    return MetaClient(
        SecretStr("sk-test"),
        base_url="https://api.meta.ai/v1",
        make_openai=lambda: cast(AsyncOpenAI, fake),
        **kwargs,
    )


@pytest.mark.asyncio
async def test_a_request_is_as_in_the_mvp_and_low_confidence_boxes_are_dropped() -> None:
    responses = FakeResponses(ANSWER)
    meta = client(responses)
    found = await SamVision(meta).detect(jpeg(), "humans", 5.0, camera_id="cam-sou")
    assert [d.object_id for d in found.boxes] == [0]
    assert (found.width, found.height) == (200, 100)
    (request,) = responses.requests
    assert request["model"] == "sam-3.1"
    assert request["metadata"] == {"include_confidence": "true"}
    (message,) = request["input"]
    text, image = message["content"]
    assert text == {"type": "input_text", "text": "humans"}
    assert image["image_url"].startswith("data:image/jpeg;base64,")
    (call,) = meta.ledger
    assert (call.model, call.purpose, call.ok) == ("sam-3.1", "count humans on cam-sou", True)
    assert "base64" not in repr(meta.ledger)  # the ledger never holds pixels


@pytest.mark.asyncio
async def test_a_429_is_retried_once() -> None:
    responses = FakeResponses(rate_limited(), ANSWER)
    meta = client(responses)
    found = await SamVision(meta, retry_429_s=0.01).detect(jpeg(), "humans", 5.0)
    assert len(found.boxes) == 1
    assert [call.ok for call in meta.ledger] == [False, True]


@pytest.mark.asyncio
async def test_a_second_429_and_any_other_failure_give_no_answer() -> None:
    meta = client(FakeResponses(rate_limited(), rate_limited()))
    with pytest.raises(VisionError, match="429"):
        await SamVision(meta, retry_429_s=0.01).detect(jpeg(), "humans", 5.0)
    assert meta.calls == 2
    request = httpx2.Request("POST", "https://api.meta.ai/v1/responses")
    failure = openai.InternalServerError(
        "boom", response=httpx2.Response(500, request=request), body=None
    )
    meta = client(FakeResponses(failure))
    with pytest.raises(VisionError, match="HTTP 500"):
        await SamVision(meta).detect(jpeg(), "humans", 5.0)
    assert meta.calls == 1  # never retried


@pytest.mark.asyncio
async def test_a_call_that_outlasts_its_timeout_gives_no_answer() -> None:
    meta = client(FakeResponses(5.0))  # answers after 5 s
    with pytest.raises(VisionError, match="no answer within 0.1 s"):
        await SamVision(meta).detect(jpeg(), "humans", 0.1)
    assert [call.ok for call in meta.ledger] == [False]
    with pytest.raises(VisionError, match="no time left"):
        await SamVision(meta).detect(jpeg(), "humans", 0.0)


@pytest.mark.asyncio
async def test_the_call_cap_stops_every_call_after_it() -> None:
    responses = FakeResponses()
    meta = client(responses, call_cap=2)
    vision = SamVision(meta)
    for _ in range(2):
        await vision.detect(jpeg(), "humans", 5.0)
    assert meta.cap_reached
    with pytest.raises(CallCapReached):
        await vision.detect(jpeg(), "humans", 5.0)
    assert len(responses.requests) == 2  # nothing sent past the cap


@pytest.mark.asyncio
async def test_at_most_five_calls_are_in_flight() -> None:
    in_flight = peak = 0

    class Counting(FakeResponses):
        async def create(self, **kwargs: Any) -> Any:
            nonlocal in_flight, peak
            in_flight += 1
            peak = max(peak, in_flight)
            await asyncio.sleep(0.02)
            in_flight -= 1
            return SimpleNamespace(output_text="")

    vision = SamVision(client(Counting()))
    results = await asyncio.gather(*(vision.detect(jpeg(), "humans", 5.0) for _ in range(12)))
    assert peak == 5 and all(not found.boxes for found in results)


@pytest.mark.asyncio
async def test_a_snapshot_that_is_not_an_image_is_refused_before_any_call() -> None:
    responses = FakeResponses()
    with pytest.raises(VisionError, match="not an image"):
        await SamVision(client(responses)).detect(b"<html>", "humans", 5.0)
    assert responses.requests == []


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "answer",
    [
        "<0f>0<|box;x1=10;y1=10;x2=50;y2=90;w=100;h=100;c=0.5.1|>",
        "<0f>0<|box;x1=10;y1=10;x2=50;y2=90;w=100;h=100;c=e|>",
        NO_TEXT,
        42,
    ],
    ids=["two-dots", "bare-e", "no-output-text", "not-text"],
)
async def test_an_unreadable_answer_is_no_answer(answer: Any) -> None:
    meta = client(FakeResponses(answer))
    with pytest.raises(VisionError, match="unreadable answer"):
        await SamVision(meta).detect(jpeg(), "humans", 5.0)
    if answer is NO_TEXT:
        assert [call.ok for call in meta.ledger] == [False]


def test_a_new_run_starts_the_call_count_afresh() -> None:
    meta = client(FakeResponses(), call_cap=1)
    meta._calls = 1
    assert meta.cap_reached
    meta.new_run()
    assert not meta.cap_reached


def meta_lines() -> tuple[list[str], int]:
    """A sink that keeps every ``META`` line's message, and its id."""
    lines: list[str] = []
    sink = logger.add(
        lambda message: lines.append(message.record["message"]),
        level="INFO",
        filter=lambda record: record["message"].startswith("META"),
    )
    return lines, sink


@pytest.mark.asyncio
async def test_each_call_logs_a_meta_line_when_sent_and_when_answered() -> None:
    headers = {
        "x-request-id": "2bf0123a-9c1d-4e8f-a1b2-c3d4e5f60718",
        "x-ratelimit-limit-requests": "60",
        "x-ratelimit-remaining-requests": "58",
    }
    meta = client(FakeResponses(ANSWER, headers=headers))
    lines, sink = meta_lines()
    try:
        await SamVision(meta).detect(jpeg((1280, 720)), "humans", 5.0, camera_id="cam-cor")
    finally:
        logger.remove(sink)
    sent, answered = lines
    assert sent.startswith(
        "META -> sam-3.1 | POST https://api.meta.ai/v1/responses | count humans on cam-cor"
        " | in: JPEG 1280x720, "
    )
    assert sent.endswith(' KB + text "humans" (include_confidence)')
    assert answered.startswith("META <- sam-3.1 | 200 in ")
    assert answered.endswith(
        " s | request-id 2bf0123a-9c1d-4e8f-a1b2-c3d4e5f60718 | response resp_6aeb"
        " | out: 2 boxes [(128,72)-(640,648) c=0.90, (768,72)-(1152,648) c=0.20]"
        " -> kept 1 (c >= 0.30) | rate limit 58/60 left"
    )
    assert all(line.isascii() for line in lines)
    assert "base64" not in "".join(lines) and "sk-test" not in "".join(lines)
    (call,) = meta.ledger
    assert call.request_id == "2bf0123a-9c1d-4e8f-a1b2-c3d4e5f60718"


@pytest.mark.asyncio
async def test_a_timeout_a_failure_and_no_trace_each_log_as_they_should() -> None:
    lines, sink = meta_lines()
    try:
        with pytest.raises(VisionError):
            await SamVision(client(FakeResponses(5.0))).detect(
                jpeg(), "humans", 0.2, camera_id="cam-sou"
            )
        request = httpx2.Request("POST", "https://api.meta.ai/v1/responses")
        failure = openai.InternalServerError(
            "boom", response=httpx2.Response(500, request=request), body=None
        )
        with pytest.raises(VisionError):
            await SamVision(client(FakeResponses(failure))).detect(jpeg(), "humans", 5.0)
        quiet = client(FakeResponses(), trace=False)
        await SamVision(quiet).detect(jpeg(), "humans", 5.0)
    finally:
        logger.remove(sink)
    assert [line[:7] for line in lines] == ["META ->", "META x ", "META ->", "META x "]
    assert lines[1] == "META x  sam-3.1 | timeout after 0.2 s | count humans on cam-sou"
    assert lines[3].startswith("META x  sam-3.1 | InternalServerError (HTTP 500) after ")
    assert quiet.ledger[0].request_id is None  # no header, no id


def test_the_trace_lists_at_most_six_boxes() -> None:
    boxes = [Detection(i, 0.0, 0.0, 0.5, 0.5, 0.25 if i else None) for i in range(8)]
    assert describe_boxes(boxes, 100, 200, 0.3) == (
        "8 boxes [(0,0)-(50,100), "
        + ", ".join(["(0,0)-(50,100) c=0.25"] * 5)
        + ", +2 more] -> kept 1 (c >= 0.30)"
    )
    assert describe_boxes([], 100, 200, 0.3) == "0 boxes [] -> kept 0 (c >= 0.30)"
