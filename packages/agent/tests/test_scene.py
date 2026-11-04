"""Muse Spark's scene service, the zones' scene notes and Muse Wonder's narration, on fakes."""

import asyncio
import io
import json
from types import SimpleNamespace
from typing import Any, cast

import pytest
from loguru import logger
from openai import AsyncOpenAI
from PIL import Image
from pydantic import BaseModel, SecretStr

from spotcool_agent.domain.scene import parse_scene
from spotcool_agent.loops.narration import Narrator, narration_prompt
from spotcool_agent.loops.scene import SceneNotes
from spotcool_agent.processors import explain
from spotcool_agent.services.fake.llm import FakeLLM
from spotcool_agent.services.fake.scene import FakeScene, office
from spotcool_agent.services.meta.client import MetaClient
from spotcool_agent.services.meta.scene import SparkScene
from spotcool_agent.services.scene import SceneError
from spotcool_contracts import Zone
from spotcool_contracts.events import EventType

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


def test_the_explanations_read_plainly() -> None:
    scene = parse_scene(json.dumps(ANSWER))
    assert explain.scene_note(Zone.WES, office(1, doors_open=1)) == (
        "Muse Spark, West: 1 person, typical office clothing; a door is open"
    )
    assert explain.scene_note(Zone.SOU, scene).endswith("visible discomfort: fanning self")
    assert explain.counts_disagree(2, 5) and explain.counts_disagree(10, 15)
    assert not explain.counts_disagree(2, 4) and not explain.counts_disagree(10, 14)
    assert explain.comfort(office(2).people, 0.71, 1.1, 24.5) == (
        "Muse Spark: 2 people, seated, typical office clothing (0.71 clo, 1.1 met) -> comfort "
        "setpoint 24.5 degC (PMV 0)"
    )


class Events:
    def __init__(self) -> None:
        self.texts: list[tuple[str, float | None]] = []

    def __call__(self, kind: EventType, data: BaseModel, t_s: float | None) -> None:
        assert kind is EventType.EXPLANATION
        self.texts.append((data.model_dump()["text"], t_s))


async def settle() -> None:
    for _ in range(10):
        await asyncio.sleep(0)


@pytest.mark.asyncio
async def test_notes_read_occupied_zones_every_4_steps_and_skip_a_zone_being_read() -> None:
    scene, events = FakeScene(office(6)), Events()
    notes = SceneNotes(scene, events, every_steps=4)
    cameras = {Zone.SOU: "cam-sou", Zone.WES: "cam-wes"}
    for _ in range(9):
        notes.offer({Zone.SOU: b"j", Zone.WES: b"j"}, {Zone.SOU: 2, Zone.WES: 0}, cameras, 60.0)
        await settle()
    assert scene.asked == [("cam-sou", 2)] * 3  # steps 0, 4 and 8; West is empty
    assert events.texts[:2] == [
        ("Muse Spark, South: 6 people, typical office clothing", 60.0),
        ("South: SAM 3.1 saw 2, Muse Spark 6: the policy keeps SAM's", 60.0),
    ]

    slow, events = FakeScene(delay_s=10.0), Events()
    notes = SceneNotes(slow, events, every_steps=1)
    for _ in range(3):
        notes.offer({Zone.SOU: b"j"}, {Zone.SOU: 1}, cameras, 0.0)
        await settle()
    assert len(slow.asked) == 1  # the zone's last reading is still running
    await notes.stop()
    assert events.texts == []

    failing, events = FakeScene(None), Events()
    notes = SceneNotes(failing, events)
    notes.offer({Zone.SOU: b"j"}, {Zone.SOU: 1}, cameras, 0.0)
    await settle()
    assert len(failing.asked) == 1 and events.texts == []  # a failed reading says nothing


@pytest.mark.asyncio
async def test_wonder_narrates_one_sentence_marked_and_a_failure_says_nothing() -> None:
    answer = "The agent eased off the empty West zone. It also says more."
    llm, events = FakeLLM({"narrate": answer}), Events()
    narrator = Narrator(llm, events)
    decisions = ["West: 0 people counted; the zone is empty"]
    narrator.narrate(decisions, 900.0)
    narrator.narrate(decisions, 900.0)  # one at a time: this one is not narrated
    await settle()
    assert events.texts == [("Muse Wonder: The agent eased off the empty West zone.", 900.0)]
    ((purpose, prompt),) = llm.prompts
    assert purpose == "narrate" and prompt == narration_prompt(decisions)
    assert "West: 0 people counted; the zone is empty" in prompt

    silent, events = FakeLLM({"narrate": None}), Events()
    Narrator(silent, events).narrate(decisions, 0.0)
    await settle()
    assert events.texts == []
