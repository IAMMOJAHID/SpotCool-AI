"""The VRF loop on a fake gateway and a fake clock: aim, hold, rate limit, overrides, decay."""

import asyncio
import time
from typing import cast

import pytest
from pydantic import BaseModel

from spotcool_agent.domain.events import Value
from spotcool_agent.domain.geometry import Detection, Detections
from spotcool_agent.domain.louvers import CameraPose, project_floor_point
from spotcool_agent.domain.scene import PersonScene
from spotcool_agent.loops.vrf import VrfLoop, _setpoint
from spotcool_agent.services.fake.scene import FakeScene, office
from spotcool_agent.services.fake.scripted import ScriptedVision
from spotcool_agent.transports.world_http import WorldHttp, WorldHttpError
from spotcool_contracts import Zone, load_vrf_profiles
from spotcool_contracts.cameras import CameraCatalog, CameraInfo, CameraSource
from spotcool_contracts.events import EventType
from spotcool_contracts.points import PointSpec

W, H = 1280, 720
POSE = CameraPose()
NO_ANSWER_S = 2.0
"""What a request costs when the device does not answer (APDU timeout and one retry)."""


class Clock:
    def __init__(self) -> None:
        self.now = 1000.0

    def __call__(self) -> float:
        return self.now


class FakeGateway:
    """The VRF gateway's seam: priority arrays, present values and the Model_Name.

    ``down`` makes every request wait ``NO_ANSWER_S`` and get no answer, as a gateway not up.
    """

    def __init__(self, vendor: str = "mitsubishi") -> None:
        self.label: str | None = load_vrf_profiles()[vendor].label
        self.down = False
        self.arrays: dict[str, list[Value]] = {}
        self.writes: list[tuple[str, Value]] = []
        self.values: dict[str, Value] = {"IDU-SP": 24.0, "IDU-FAN": "Auto", "IDU-ROOM-T": 26.04}

    async def _answers(self) -> bool:
        if self.down:
            await asyncio.sleep(NO_ANSWER_S)
        return not self.down

    async def slots(self, point: PointSpec) -> list[Value] | None:
        if not await self._answers():
            return None
        return list(self.arrays.setdefault(point.name, [None] * 16))

    async def write(self, point: PointSpec, value: Value) -> bool:
        if not await self._answers():
            return False
        self.arrays.setdefault(point.name, [None] * 16)[9] = value
        self.writes.append((point.name, value))
        return True

    async def present(self, point: PointSpec) -> Value:
        if not await self._answers():
            return None
        slots = self.arrays.get(point.name, [None] * 16)
        held = next((v for v in slots if v is not None), None)
        return held if held is not None else self.values.get(point.name, "Pos3")

    async def model_name(self, instance: int) -> str | None:
        assert instance == 2001
        if not await self._answers():
            return None
        return f"SpotCool simulated VRF gateway ({self.label or 'Acme VRF-9000'})"

    def written(self, name: str) -> list[Value]:
        return [value for point, value in self.writes if point == name]


def slot(priority: int, value: Value) -> list[Value]:
    """A priority array with one slot holding a value."""
    array: list[Value] = [None] * 16
    array[priority - 1] = value
    return array


class NoCameras:
    async def cameras(self) -> None:
        raise WorldHttpError("no world in a unit test")


def at(*places: tuple[float, float]) -> Detections:
    """Standing people where they stand, as a vision provider would find them."""
    boxes = []
    for k, (lateral, forward) in enumerate(places):
        feet = project_floor_point(lateral, forward, 0.0, W, H, POSE)
        head = project_floor_point(lateral, forward, 1.7, W, H, POSE)
        assert feet is not None and head is not None
        half = (feet[1] - head[1]) / 6
        boxes.append(
            Detection(k, (feet[0] - half) / W, head[1] / H, (feet[0] + half) / W, feet[1] / H)
        )
    return Detections(tuple(boxes), W, H)


def make(
    gateway: FakeGateway,
    clock: Clock,
    events: list[tuple[EventType, dict[str, object]]],
    world: object = None,
    **kw: object,
) -> VrfLoop:
    def publish(kind: EventType, data: BaseModel, t_s: float | None) -> None:
        assert t_s is None  # wall time: never paired with a BMS step
        events.append((kind, data.model_dump()))

    return VrfLoop(
        gateway=gateway,
        world=cast(WorldHttp, world or NoCameras()),
        vision=ScriptedVision(),
        publish=publish,
        clock=clock,
        **kw,  # type: ignore[arg-type]
    )


@pytest.mark.asyncio
async def test_it_aims_at_a_person_and_releases_when_nobody_is_seen_for_the_hold() -> None:
    gateway, clock, events = FakeGateway(), Clock(), []
    loop = make(gateway, clock, events)
    await loop.tick()
    assert loop.profile is not None and loop.profile.vendor == "mitsubishi"  # from Model_Name
    assert loop.seen(at((1.2, 3.0)), 1)
    await loop.tick()
    assert gateway.arrays["IDU-VANE-V"][9] == "Pos3" and gateway.arrays["IDU-VANE-H"][9] == "Pos4"
    decisions = [data for kind, data in events if kind is EventType.DECISION]
    assert {d["point"] for d in decisions} == {"IDU-VANE-V", "IDU-VANE-H"}
    assert all(d["zone"] == "sou" and d["priority"] == 10 for d in decisions)
    assert decisions[0]["reason"] == "aim at 1 person at 3.2 m, 1.2 m right"
    perception = next(data for kind, data in events if kind is EventType.PERCEPTION)
    assert perception["people"] == {} and len(perception["masks"]["cam-meeting-live"]) == 1
    explained = [data["text"] for kind, data in events if kind is EventType.EXPLANATION]
    assert explained == [
        "South: aim at 1 person at 3.2 m, 1.2 m right -> IDU-VANE-V Pos3, IDU-VANE-H Pos4 at "
        "priority 10"
    ]

    clock.now += 9.0
    assert loop.seen(at(), 2)  # nobody: the aim holds
    await loop.tick()
    assert gateway.arrays["IDU-VANE-H"][9] == "Pos4"
    clock.now += 1.0  # 10 s since the person was seen
    await loop.tick()
    assert gateway.arrays["IDU-VANE-V"][9] is None and gateway.arrays["IDU-VANE-H"][9] is None
    assert loop.actuator.held == {}


@pytest.mark.asyncio
async def test_each_point_is_written_on_a_change_only_and_at_most_once_a_second() -> None:
    gateway, clock, events = FakeGateway(), Clock(), []
    loop = make(gateway, clock, events)
    await loop.tick()
    loop.seen(at((1.2, 3.0)), 1)
    for _ in range(4):
        await loop.tick()
    assert gateway.written("IDU-VANE-H") == ["Pos4"]  # unchanged: written once
    clock.now += 0.4
    loop.seen(at((2.6, 3.0)), 2)  # 41 deg right: Pos5
    await loop.tick()
    assert gateway.written("IDU-VANE-H") == ["Pos4"]  # 0.4 s after the last write: not yet
    clock.now += 0.6
    await loop.tick()
    assert gateway.written("IDU-VANE-H") == ["Pos4", "Pos5"]
    explanations = [kind for kind, _ in events if kind is EventType.EXPLANATION]
    assert len(explanations) == 1  # the second aim change came within 10 s


@pytest.mark.asyncio
async def test_a_look_older_than_one_applied_is_dropped() -> None:
    gateway, clock, events = FakeGateway(), Clock(), []
    loop = make(gateway, clock, events)
    assert loop.seen(at((1.2, 3.0)), 2)
    assert not loop.seen(at((-1.2, 3.0)), 1)  # it came back late
    assert loop.aim is not None and loop.aim.lateral_m > 0


@pytest.mark.asyncio
async def test_daikin_never_gets_a_horizontal_write_and_lg_only_swings() -> None:
    for vendor, expected in (("daikin", {"IDU-VANE-V": "Pos3"}), ("lg", {"IDU-VANE-V": "Swing"})):
        gateway, clock, events = FakeGateway(vendor), Clock(), []
        loop = make(gateway, clock, events)
        await loop.tick()
        loop.seen(at((-1.8, 3.0), (1.8, 3.0)) if vendor == "lg" else at((1.2, 3.0)), 1)
        await loop.tick()
        assert {name: slots[9] for name, slots in gateway.arrays.items()} == expected
        clock.now += 11.0
        await loop.tick()
        assert gateway.written("IDU-VANE-H") == []
        await loop.stop()
        assert all(slots[9] is None for slots in gateway.arrays.values())


@pytest.mark.asyncio
async def test_the_brand_flag_counts_only_when_the_gateway_names_an_unknown_brand() -> None:
    gateway, clock, events = FakeGateway(), Clock(), []
    gateway.down = True
    flagged = make(gateway, clock, events, brand="daikin")
    flagged.seen(at((1.2, 3.0)), 1)
    await flagged.tick()
    assert flagged.profile is None and gateway.writes == []  # silent gateway: no vane, no flag
    gateway.down = False
    clock.now += 4.0
    await flagged.tick()
    assert flagged.profile is None  # asked again only every 5 s
    clock.now += 1.0
    await flagged.tick()
    assert flagged.profile is not None and flagged.profile.vendor == "mitsubishi"  # it says so

    gateway.label = None  # it names a brand we do not know
    unknown = make(gateway, clock, events, brand="daikin")
    await unknown.tick()
    assert unknown.profile is not None and unknown.profile.vendor == "daikin"
    bare = make(gateway, clock, events)
    bare.seen(at((1.2, 3.0)), 1)
    await bare.tick()
    assert bare.profile is None  # nothing to go on: the vanes are left alone


@pytest.mark.asyncio
async def test_cold_hot_and_stuffy_override_the_setpoint_and_fan_then_decay() -> None:
    gateway, clock, events = FakeGateway(), Clock(), []
    loop = make(gateway, clock, events, complaint_hold_s=900.0)
    await loop.tick()
    cold = await loop.act("cold")
    assert cold.changed
    assert (
        cold.summary
        == "raised the meeting room's setpoint to 25.0 degC and turned its fan down to Low"
    )
    await loop.tick()
    assert (gateway.arrays["IDU-SP"][9], gateway.arrays["IDU-FAN"][9]) == (25.0, "Low")
    reasons = {data["reason"] for kind, data in events if kind is EventType.DECISION}
    assert reasons == {"a complaint: too cold"}
    clock.now += 1.0
    again = await loop.act("cold")  # from the override in force, not the gateway's old value
    assert again.summary.startswith("raised the meeting room's setpoint to 26.0 degC")
    assert "its fan is already at its lowest" in again.summary
    clock.now += 1.0
    await loop.tick()
    assert gateway.arrays["IDU-SP"][9] == 26.0

    clock.now += 900.0
    await loop.tick()
    assert gateway.arrays["IDU-SP"][9] is None and gateway.arrays["IDU-FAN"][9] is None

    hot = await loop.act("hot")
    assert (
        hot.summary
        == "lowered the meeting room's setpoint to 23.0 degC and turned its fan up to High"
    )
    clock.now += 1.0
    stuffy = await loop.act("stuffy")
    assert (
        stuffy.summary == "left the meeting room's AC as it is: its fan is already at its highest"
    )
    assert not stuffy.changed


@pytest.mark.asyncio
async def test_the_setpoint_stays_within_16_to_30_degc() -> None:
    gateway, clock, events = FakeGateway(), Clock(), []
    gateway.arrays["IDU-SP"] = slot(16, 30.0)  # a schedule's slot 16 holds them
    gateway.arrays["IDU-FAN"] = slot(16, "High")
    loop = make(gateway, clock, events)
    cold = await loop.act("cold")
    assert cold.summary == (
        "turned the meeting room's fan down to Mid; its setpoint is already at its highest, 30.0 degC"
    )


@pytest.mark.asyncio
async def test_a_point_the_operator_holds_is_left_and_the_reply_says_so() -> None:
    gateway, clock, events = FakeGateway(), Clock(), []
    gateway.arrays["IDU-SP"] = slot(8, 22.0)  # the building operator, at 8
    loop = make(gateway, clock, events)
    await loop.tick()
    cold = await loop.act("cold")
    assert cold.changed and cold.summary == (
        "turned the meeting room's fan down to Low; its setpoint is held at 22.0 degC by the "
        "building operator"
    )
    gateway.arrays["IDU-FAN"] = slot(5, "High")  # priority 5
    clock.now += 1.0
    hot = await loop.act("hot")
    assert not hot.changed
    assert hot.summary == (
        "left the meeting room's AC as it is: its setpoint is held at 22.0 degC by the building "
        "operator and its fan is held at High by priority 5"
    )
    await loop.tick()
    assert gateway.written("IDU-SP") == []  # never claimed, never written
    gateway.arrays["IDU-VANE-V"] = slot(8, "Pos5")
    draft = await loop.act("draft")
    assert not draft.changed and draft.summary == (
        "left the meeting room's louvers as they are: its vertical louver is held at Pos5 by "
        "the building operator"
    )


@pytest.mark.asyncio
async def test_with_the_gateway_down_a_complaint_is_answered_without_a_change() -> None:
    gateway, clock, events = FakeGateway(), Clock(), []
    loop = make(gateway, clock, events)
    await loop.tick()  # the brand, while it answers
    gateway.down = True
    began = time.perf_counter()
    cold = await loop.act("cold")
    assert time.perf_counter() - began < 3.5  # two reads of 1.5 s at most
    assert not cold.changed and cold.summary == (
        "left the meeting room's AC as it is: its setpoint did not answer and its fan did not "
        "answer"
    )
    assert loop.state() == {"brand": "mitsubishi", "people_the_louvers_follow": 0}
    await asyncio.sleep(NO_ANSWER_S)  # the late reads finish


@pytest.mark.asyncio
async def test_a_draft_levels_the_vertical_vane_and_pauses_the_aim_for_its_hold() -> None:
    gateway, clock, events = FakeGateway(), Clock(), []
    loop = make(gateway, clock, events, draft_hold_s=600.0)
    await loop.tick()
    loop.seen(at((1.2, 3.0)), 1)
    await loop.tick()
    draft = await loop.act("draft")
    assert (
        draft.summary == "turned the meeting room's louvers to blow level, for the next 10 minutes"
    )
    clock.now += 1.0
    loop.seen(at((-1.2, 3.0)), 2)  # the person moves left: the aim is paused
    await loop.tick()
    assert (gateway.arrays["IDU-VANE-V"][9], gateway.arrays["IDU-VANE-H"][9]) == ("Pos1", "Pos4")
    clock.now += 600.0
    loop.seen(at((-1.2, 3.0)), 3)
    await loop.tick()
    assert (gateway.arrays["IDU-VANE-V"][9], gateway.arrays["IDU-VANE-H"][9]) == ("Pos3", "Pos2")


@pytest.mark.asyncio
async def test_the_state_card_shows_the_unit_as_last_read_and_stop_releases_every_slot() -> None:
    gateway, clock, events = FakeGateway("daikin"), Clock(), []
    loop = make(gateway, clock, events)
    await loop.tick()
    loop.seen(at((0.0, 3.0)), 1)
    await loop.act("cold")
    await loop.tick()
    clock.now += 2.0  # the next read of the unit's state
    await loop.tick()
    await asyncio.sleep(0)  # it runs in a task of its own
    state = loop.state()
    assert state == {
        "room_temperature_c": 26.0,
        "setpoint_c": 25.0,
        "fan": "Low",
        "vertical_louver": "Pos3",
        "brand": "daikin",
        "people_the_louvers_follow": 1,
    }
    await loop.stop()
    assert loop.actuator.held == {}
    assert all(slots[9] is None for slots in gateway.arrays.values())


class LiveCamera:
    """The world's live meeting-room camera, frames in memory."""

    async def cameras(self) -> CameraCatalog:
        live = CameraInfo(
            id="cam-meeting-live",
            name="meeting room",
            zone=Zone.SOU,
            snapshot_url="http://world/live",
            width=W,
            height=H,
            fps_hint=2,
            source=CameraSource.LIVE,
        )
        return CameraCatalog(cameras=[live])

    async def snapshot(self, url: str, timeout_s: float = 5.0) -> bytes:
        return b"jpeg"


async def settle() -> None:
    for _ in range(10):
        await asyncio.sleep(0)


def comfortable(
    scene: FakeScene,
    gateway: FakeGateway,
    clock: Clock,
    events: list[tuple[EventType, dict[str, object]]],
) -> VrfLoop:
    return make(gateway, clock, events, world=LiveCamera(), scene=scene, comfort_every_s=60.0)


@pytest.mark.asyncio
async def test_spark_sets_the_comfort_setpoint_while_people_are_seen() -> None:
    gateway, clock, events = FakeGateway(), Clock(), []
    scene = FakeScene(office(2))
    loop = comfortable(scene, gateway, clock, events)
    await loop.tick()
    assert scene.asked == []  # nobody seen yet: no reading
    loop.seen(at((0.0, 3.0), (1.0, 3.0)), 10**6)  # newer than any scripted look
    await loop.tick()
    await settle()
    assert scene.asked == [("cam-meeting-live", 2)]  # SAM's count goes with the frame
    clock.now += 1.0
    await loop.tick()
    assert gateway.arrays["IDU-SP"][9] == 24.5  # the MVP's 24.415 degC on the 0.5 grid
    explained = [data["text"] for kind, data in events if kind is EventType.EXPLANATION]
    assert (
        "Muse Spark: 2 people, seated, typical office clothing (0.71 clo, 1.1 met) -> comfort "
        "setpoint 24.5 degC (PMV 0)"
    ) in explained
    reasons = {d["reason"] for k, d in events if k is EventType.DECISION and d["point"] == "IDU-SP"}
    assert reasons == {"comfort: PMV 0 for the people seen"}

    clock.now += 30.0
    await loop.tick()
    await settle()
    assert len(scene.asked) == 1  # one reading a minute at most
    clock.now += 10.0  # nobody seen for the hold: the comfort setpoint is released
    await loop.tick()
    assert gateway.arrays["IDU-SP"][9] is None and loop.comfort_c is None


@pytest.mark.asyncio
async def test_a_complaint_comes_before_comfort_and_the_operator_before_both() -> None:
    gateway, clock, events = FakeGateway(), Clock(), []
    loop = comfortable(FakeScene(office(1)), gateway, clock, events)
    await loop.tick()
    loop.seen(at((0.0, 3.0)), 10**6)
    await loop.tick()
    await settle()
    clock.now += 1.0
    await loop.tick()
    assert gateway.arrays["IDU-SP"][9] == 24.5
    hot = await loop.act("hot")  # from the comfort setpoint in force
    assert hot.summary.startswith("lowered the meeting room's setpoint to 23.5 degC")
    clock.now += 1.0
    loop.seen(at((0.0, 3.0)), 10**6 + 1)
    await loop.tick()
    assert gateway.arrays["IDU-SP"][9] == 23.5  # the complaint's override wins
    clock.now += 900.0  # the override ends; people still seen: comfort again
    loop.seen(at((0.0, 3.0)), 10**6 + 2)
    await loop.tick()
    await settle()
    clock.now += 1.0
    await loop.tick()
    assert gateway.arrays["IDU-SP"][9] == 24.5

    gateway.arrays["IDU-SP"][7] = 22.0  # the operator, at 8
    loop.comfort_c = 25.0  # a new comfort reading wants another value
    clock.now += 1.0
    loop.seen(at((0.0, 3.0)), 10**6 + 3)
    await loop.tick()
    assert gateway.arrays["IDU-SP"][9] == 24.5  # backed off: slot 10 not rewritten
    assert any(d["reason"].startswith("backed off") for k, d in events if k is EventType.DECISION)


@pytest.mark.asyncio
async def test_the_comfort_setpoint_is_clamped_and_an_invalid_scene_writes_nothing() -> None:
    bundled = PersonScene(
        clothing_ensemble="winter_outerwear",
        activity="exercising",
        posture="standing",
        discomfort_cue="none",
    )
    scene = office(1).model_copy(update={"people": [bundled]})
    gateway, clock, events = FakeGateway(), Clock(), []
    loop = comfortable(FakeScene(scene), gateway, clock, events)
    await loop.tick()
    loop.seen(at((0.0, 3.0)), 10**6)
    await loop.tick()
    await settle()
    assert loop.comfort_c == 23.0  # under 23 degC at PMV 0: the comfort band's lowest
    assert _setpoint(31.2) == 30.0 and _setpoint(24.26) == 24.5

    for failing in (FakeScene(None), None):  # Spark's answer was invalid; --no-comfort
        gateway, clock, events = FakeGateway(), Clock(), []
        loop = (
            comfortable(failing, gateway, clock, events)
            if failing
            else make(gateway, clock, events, world=LiveCamera())
        )
        await loop.tick()
        loop.seen(at((0.0, 3.0)), 10**6)
        await loop.tick()
        await settle()
        clock.now += 1.0
        await loop.tick()
        assert loop.comfort_c is None and gateway.written("IDU-SP") == []
