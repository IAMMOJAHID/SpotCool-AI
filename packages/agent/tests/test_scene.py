"""Muse Spark's scene service, on fakes."""

import asyncio
import io
import json
from types import SimpleNamespace
from typing import Any, cast

import pytest
from loguru import logger
from openai import AsyncOpenAI
from PIL import Image
from pydantic import SecretStr

from spotcool_agent.domain.scene import parse_scene
from spotcool_agent.services.meta.client import MetaClient
from spotcool_agent.services.meta.scene import SparkScene
from spotcool_agent.services.scene import SceneError

ANSWER = {
    "people": [
        {
            "clothing_ensemble": "trousers_long_sleeve",
            "activity": "seated_typing",
            "posture": "seated",
            "discomfort_cue": "none",
        },
        {
            "clothing_ensemble": "suit_jacket",
            "activity": "standing_relaxed",
            "posture": "standing",
            "discomfort_cue": "fanning_self",
        },
    ],
    "doors_open": 1,
    "windows_open": 0,
    "sunlight": "partial",
    "space_type": "office",
}


def jpeg() -> bytes:
    buffer = io.BytesIO()
    Image.new("RGB", (64, 48), (90, 90, 90)).save(buffer, format="JPEG")
    return buffer.getvalue()


class FakeResponses:
    def __init__(self, text: str, delay_s: float = 0.0) -> None:
        self.text = text
        self.delay_s = delay_s
        self.requests: list[dict[str, Any]] = []

    async def create(self, **kwargs: Any) -> Any:
        self.requests.append(kwargs)
        await asyncio.sleep(self.delay_s)
        answer = SimpleNamespace(id="resp_spark", output_text=self.text)
        headers = {"x-request-id": "rid-spark"}
        return SimpleNamespace(headers=headers, status_code=200, parse=lambda: answer)

    @property
    def with_raw_response(self) -> "FakeResponses":
        return self


async def _none() -> None:
    return None


def spark(responses: FakeResponses) -> SparkScene:
    fake = SimpleNamespace(responses=responses, close=_none)
    client = MetaClient(
        SecretStr("sk-x"), base_url="x", make_openai=lambda: cast(AsyncOpenAI, fake)
    )
    return SparkScene(client)


def test_a_scene_parses_from_a_code_fence_and_refuses_a_temperature() -> None:
    scene = parse_scene(f"```json\n{json.dumps(ANSWER)}\n```")
    assert len(scene.people) == 2 and scene.doors_open == 1
    with pytest.raises(ValueError):
        parse_scene(json.dumps({**ANSWER, "room_temperature_c": 27.5}))  # never accepted
    with pytest.raises(ValueError):
        parse_scene(json.dumps({**ANSWER, "sunlight": "some"}))  # not one of A's levels
    with pytest.raises(ValueError):
        parse_scene("I see two people.")


@pytest.mark.asyncio
async def test_spark_sends_the_frame_and_sams_count_and_traces_only_a_tally() -> None:
    responses = FakeResponses(json.dumps(ANSWER))
    logged: list[str] = []
    sink = logger.add(lambda message: logged.append(message.record["message"]), level="INFO")
    try:
        scene = await spark(responses).assess(jpeg(), 2, 20.0, camera_id="cam-sou")
    finally:
        logger.remove(sink)
    assert [p.posture for p in scene.people] == ["seated", "standing"]
    (request,) = responses.requests
    assert request["model"] == "muse-spark-1.3"
    text, image = request["input"][0]["content"]
    assert "SAM 3.1 counted 2 people" in text["text"]
    assert "Never guess temperatures" in text["text"]
    assert image["image_url"].startswith("data:image/jpeg;base64,")
    assert logged[0].startswith(
        "META -> muse-spark-1.3 | POST x/responses | read the scene on cam-sou | in: JPEG 64x48"
    )
    assert logged[1].endswith(
        "out: 2 people (trousers_long_sleeve 1, suit_jacket 1), doors open 1, windows open 0, "
        "sunlight partial, office"
    )
    assert not any("base64" in line or "/9j/" in line for line in logged)


@pytest.mark.asyncio
async def test_an_invalid_or_late_answer_is_a_scene_error() -> None:
    with pytest.raises(SceneError, match="invalid scene"):
        await spark(FakeResponses('{"people": []}')).assess(jpeg(), 0, 5.0, camera_id="c")
    with pytest.raises(SceneError, match="no answer within 0.1 s"):
        await spark(FakeResponses(json.dumps(ANSWER), delay_s=2.0)).assess(
            jpeg(), 0, 0.1, camera_id="c"
        )
    with pytest.raises(SceneError, match="not an image"):
        await spark(FakeResponses(json.dumps(ANSWER))).assess(b"no", 0, 5.0, camera_id="c")
