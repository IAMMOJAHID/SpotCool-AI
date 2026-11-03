"""Complaints: intents by keywords then the model, acting on the meeting room only, replies."""

import asyncio
import json
import time
from typing import cast

import pytest
from pydantic import BaseModel

from spotcool_agent.domain.events import Value
from spotcool_agent.loops.complaints import (
    NOT_HERE,
    UNCLEAR,
    UNHEARD,
    ComplaintLoop,
    template,
    trimmed,
)
from spotcool_agent.loops.vrf import Action, VrfLoop
from spotcool_agent.processors.intents import classify, keyword_intents, parse_intent
from spotcool_agent.services.fake.llm import FakeLLM
from spotcool_agent.services.fake.scripted import ScriptedVision
from spotcool_agent.services.fake.stt import FakeSTT
from spotcool_agent.transports.server import Complaint
from spotcool_agent.transports.world_http import WorldHttp, WorldHttpError
from spotcool_contracts import Zone, load_vrf_profiles
from spotcool_contracts.events import EventType
from spotcool_contracts.occupants import ComplaintText
from spotcool_contracts.points import PointSpec

Events = list[tuple[EventType, dict[str, object], float | None]]


class Clock:
    now = 1000.0

    def __call__(self) -> float:
        return self.now


class FakeGateway:
    """A Mitsubishi gateway's seam, as in ``test_vrf_loop``."""

    def __init__(self) -> None:
        self.arrays: dict[str, list[Value]] = {}
        self.writes: list[tuple[str, Value]] = []
        self.down = False
        """Every read waits 2 s for no answer, as a gateway that is not up."""

    async def slots(self, point: PointSpec) -> list[Value] | None:
        if self.down:
            await asyncio.sleep(2.0)
            return None
        return list(self.arrays.setdefault(point.name, [None] * 16))

    async def write(self, point: PointSpec, value: Value) -> bool:
        self.arrays.setdefault(point.name, [None] * 16)[9] = value
        self.writes.append((point.name, value))
        return True

    async def present(self, point: PointSpec) -> Value:
        held = (self.arrays.get(point.name) or [None] * 16)[9]
        known = {"IDU-SP": 24.0, "IDU-FAN": "Auto", "IDU-ROOM-T": 26.0}
        return held if held is not None else known.get(point.name, "Pos3")

    async def model_name(self, instance: int) -> str | None:
        return f"SpotCool simulated VRF gateway ({load_vrf_profiles()['mitsubishi'].label})"


class NoCameras:
    async def cameras(self) -> None:
        raise WorldHttpError("no world in a unit test")


def chat(
    text: str, *, room: str | None = "meeting-south", source: str = "occupant-sim"
) -> Complaint:
    said = ComplaintText(text=text, room_hint=room, source=source, sim_time_s=17_270_100.0)  # type: ignore[arg-type]
    return Complaint("chat", said)


def loop_with(
    events: Events, *, llm: FakeLLM | None = None, vrf: VrfLoop | None = None, **kw: float
) -> ComplaintLoop:
    def publish(kind: EventType, data: BaseModel, t_s: float | None) -> None:
        events.append((kind, data.model_dump(), t_s))

    queue: asyncio.Queue[Complaint] = asyncio.Queue()
    return ComplaintLoop(
        queue,
        publish,
        stt=FakeSTT("It's too cold in the meeting room."),
        llm=llm,
        vrf=vrf,
        counts=lambda: {Zone.SOU: 2, Zone.COR: 0},
        **kw,
    )


def vrf_loop(gateway: FakeGateway, clock: Clock) -> VrfLoop:
    return VrfLoop(
        gateway=gateway,
        world=cast(WorldHttp, NoCameras()),
        vision=ScriptedVision(),
        publish=lambda kind, data, t_s: None,
        clock=clock,
    )


@pytest.mark.parametrize(
    ("text", "intents"),
    [
        ("It is too cold in the meeting room, can you turn the air conditioning down?", {"cold"}),
        ("I'm freezing", {"cold"}),
        ("Could you make it warmer?", {"cold"}),
        ("It's warm and I'm sweating", {"hot"}),
        ("The AC is blowing right on my face.", {"draft"}),
        ("It's so stuffy in here", {"stuffy"}),
        ("Cold air is blowing on me", {"cold", "draft"}),
        ("Can someone help?", set()),
        ("The coldplay concert was great", set()),  # whole words only
    ],
)
def test_keywords(text: str, intents: set[str]) -> None:
    assert keyword_intents(text) == intents


@pytest.mark.asyncio
async def test_keywords_settle_first_and_the_model_settles_the_rest() -> None:
    llm = FakeLLM({"classify": 'Sure! {"intent": "draft"}'})
    assert await classify("I'm freezing", llm) == ("cold", "keywords")
    assert llm.prompts == []  # never asked
    assert await classify("Cold air is blowing on me", llm) == ("draft", "llm")
    (purpose, prompt), *_ = llm.prompts
    assert (
        purpose == "classify" and "JSON only" in prompt and '"Cold air is blowing on me"' in prompt
    )
    assert await classify("Can someone help?", FakeLLM({"classify": "no idea"})) == ("none", "llm")
    assert await classify("Can someone help?", FakeLLM({"classify": None})) == ("none", "unsettled")
    assert await classify("Can someone help?", None) == ("none", "unsettled")
    slow = FakeLLM({"classify": '{"intent": "hot"}'}, delay_s=1.0)
    assert await classify("Can someone help?", slow, timeout_s=0.05) == ("none", "unsettled")
    assert parse_intent('{"intent": "sleepy"}') == "none"
    assert parse_intent('```json\n{"intent": "stuffy"}\n```') == "stuffy"


@pytest.mark.asyncio
async def test_a_complaint_about_another_zone_changes_nothing() -> None:
    gateway, clock = FakeGateway(), Clock()
    vrf = vrf_loop(gateway, clock)
    events: Events = []
    complaints = loop_with(events, vrf=vrf)
    for complaint in (
        chat("I'm freezing", room="meeting-north"),  # another room
        chat("I'm freezing", room=None),  # a scripted occupant of a zone without a room
    ):
        assert await complaints.handle(complaint) == template(Action("cold", NOT_HERE, False))
    await vrf.tick()
    assert gateway.writes == []
    explained = [data["text"] for kind, data, _ in events if kind is EventType.EXPLANATION]
    assert explained[0] == f"Complaint by chat (meeting-north): cold -> I've {NOT_HERE}"


@pytest.mark.asyncio
async def test_a_meeting_room_complaint_acts_on_the_vrf_and_says_so() -> None:
    gateway, clock = FakeGateway(), Clock()
    vrf = vrf_loop(gateway, clock)
    events: Events = []
    complaints = loop_with(events, vrf=vrf)
    reply = await complaints.handle(chat("I'm freezing"))
    assert reply == (
        "Thanks, I've raised the meeting room's setpoint to 25.0 degC and turned its fan down to Low."
    )
    await vrf.tick()
    assert gateway.arrays["IDU-SP"][9] == 25.0
    dashboard = await complaints.handle(chat("Hello there", room=None, source="occupant"))
    assert dashboard == template(Action("none", UNCLEAR, False))  # the dashboard: this room


@pytest.mark.asyncio
async def test_the_reply_comes_from_the_model_on_the_live_state_or_the_template_if_late() -> None:
    gateway, clock = FakeGateway(), Clock()
    vrf = vrf_loop(gateway, clock)
    await vrf.tick()
    await asyncio.sleep(0)  # its first read of the unit's state, in a task of its own
    events: Events = []
    talkative = FakeLLM({"reply": "Done:  I raised it.\nOne more.  And a third sentence."})
    complaints = loop_with(events, llm=talkative, vrf=vrf)
    assert await complaints.handle(chat("I'm freezing")) == "Done: I raised it. One more."
    (purpose, prompt), *_ = talkative.prompts
    facts = json.loads(prompt.split("Facts: ", 1)[1])
    assert purpose == "reply" and facts["understood_as"] == "cold"
    assert facts["people_seen_per_zone"] == {"South": 2, "Core": 0}
    assert facts["meeting_room_unit"]["setpoint_c"] == 24.0  # the write comes at the next round
    assert facts["meeting_room_unit"]["brand"] == "mitsubishi"
    assert facts["action_taken"].startswith("raised the meeting room's setpoint to 25.0 degC")

    late = loop_with(events, llm=FakeLLM(delay_s=1.0), vrf=vrf, reply_timeout_s=0.05)
    reply = await late.handle(chat("It's so stuffy in here"))
    assert reply == "Thanks, I've turned the meeting room's fan up to Mid."  # from the cold's Low
    assert trimmed("   ") == ""


@pytest.mark.asyncio
async def test_a_voice_message_is_transcribed_and_one_it_cannot_hear_asks_to_type() -> None:
    events: Events = []
    complaints = loop_with(events)
    voice = Complaint("voice", None, b"RIFF....", 1.0)
    assert await complaints.handle(voice) == (
        "Thanks, I've noted it, but the meeting room's unit isn't under my control right now, "
        "so nothing changed."
    )
    deaf = ComplaintLoop(asyncio.Queue(), lambda kind, data, t_s: None, stt=FakeSTT())
    assert await deaf.handle(voice) == UNHEARD == "Sorry, I couldn't hear that; please type it."


@pytest.mark.asyncio
async def test_with_the_gateway_down_a_complaint_is_answered_within_10_s() -> None:
    gateway, clock = FakeGateway(), Clock()
    vrf = vrf_loop(gateway, clock)
    await vrf.tick()
    gateway.down = True
    events: Events = []
    complaints = loop_with(events, llm=FakeLLM({"reply": None}), vrf=vrf)
    began = time.perf_counter()
    reply = await complaints.handle(chat("I'm freezing"))
    assert time.perf_counter() - began < 10.0
    assert reply == (
        "Thanks, I've left the meeting room's AC as it is: its setpoint did not answer and its "
        "fan did not answer."
    )
    await asyncio.sleep(2.0)  # the late reads finish


@pytest.mark.asyncio
async def test_the_loop_answers_every_complaint_once_even_when_handling_fails() -> None:
    events: Events = []
    queue: asyncio.Queue[Complaint] = asyncio.Queue()

    def publish(kind: EventType, data: BaseModel, t_s: float | None) -> None:
        events.append((kind, data.model_dump(), t_s))

    class Broken(FakeLLM):
        async def complete(self, prompt: str, timeout_s: float, *, purpose: str) -> str:
            raise RuntimeError("a bug")

    loop = ComplaintLoop(queue, publish, llm=Broken())
    running = asyncio.create_task(loop.run())
    queue.put_nowait(chat("Can someone help?"))
    deadline = asyncio.get_running_loop().time() + 2.0
    while loop.answered < 1:
        assert asyncio.get_running_loop().time() < deadline, "not answered"
        await asyncio.sleep(0.01)
    running.cancel()
    await asyncio.gather(running, return_exceptions=True)
    chats = [(data["text"], t_s) for kind, data, t_s in events if kind is EventType.CHAT]
    assert chats == [
        ("Sorry, something went wrong with your message. Could you try again?", 17_270_100.0)
    ]
