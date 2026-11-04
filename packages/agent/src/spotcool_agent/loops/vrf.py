"""VrfLoop: the meeting room's spot cooling, in wall time, and what complaints do to its unit.

The meeting room's VRF indoor unit runs on the wall clock, whatever the simulation's speed, so
this loop does too. It speaks to the gateway through a BACnet client of its own (its own
socket), and finds people with a vision provider whose budget is its own (2 calls at once,
``--vrf-call-cap``), so nothing it does can hold up the building loop. Every ``TICK_S``:

1. **Brand.** Until it is known, it reads the gateway's ``Model_Name``, which names the brand's
   profile label (``load_vrf_profiles()``), every ``RETRY_S`` while the gateway does not
   answer; meanwhile no vane is written. ``--vrf-brand`` is used only when the gateway answers
   with a name that is no known brand's. Without a brand, the vanes are left alone.
2. **State.** Once the gateway has answered, a task of its own reads the unit's room
   temperature, setpoint, fan and vanes every ``REFRESH_S``; replies use these last-known
   values, so a complaint never waits on the gateway for them.
3. **Camera.** It finds ``vrf_camera`` (``cam-meeting-live``) in the catalog, retrying every
   ``RETRY_S`` until it is there.
4. **Look.** With fewer than ``IN_FLIGHT`` looks under way, and at most at the camera's frame
   rate, it takes a snapshot (memory only) and asks the vision provider for every person
   (``detect(jpeg, "humans")``), in a task of its own. A result older than one already applied
   is dropped. Each result is a ``perception`` event with the camera's boxes as ``masks`` and
   no zone counts (the building loop owns those), stamped with no simulation time. When its
   call cap is used up it stops looking and releases the vanes; the building is not touched.
5. **Aim.** Everyone seen is located on the floor and weighted by their box's area; the
   louvers aim at the weighted centroid, and swing when the people are spread too wide
   (``domain/louvers.py``: Person A's geometry and rule, mapped onto the brand's positions).
   With nobody seen for ``hold_s``, the vanes are released: the unit goes back to its
   relinquish default. A draft complaint sets the vertical vane to Pos1 (a level throw) and
   pauses the aiming for ``draft_hold_s``.
6. **Write.** The vanes, and the setpoint and fan a complaint overrides for
   ``complaint_hold_s``, are written through an ``Actuator`` of the gateway's points: priority
   10, only on a change, backing off from slots 1-9, releasing only its own slot, and at most
   one write per point every ``WRITE_EVERY_S``. Each write is a ``decision`` (zone ``sou``);
   an aim change is explained at most once every ``EXPLAIN_EVERY_S``, any other write each
   time. None of it touches the BMS zones, and its events carry no simulation time, so the
   world's report never pairs them with BMS steps.

7. **Comfort** (with a scene reader, ``--no-comfort`` turns it off). Every ``comfort_every_s``
   while people are seen, a task of its own takes a live snapshot and asks the scene reader
   (Muse Spark) who is there; Person A's comfort physics (``domain/comfort.py``) then gives the
   air temperature at PMV 0 for them, at 50 % RH (the gateway has no RH point), on the
   setpoint's grid within 16-30 degC. It is written to ``IDU-SP`` by the same rules, and
   explained when it changes. A complaint's override comes before it, and the operator before
   both (the actuator's back-off). With nobody seen for ``hold_s``, or a reading that fails, no
   new one is taken: the last stands while people are seen, and is released when nobody is.

A complaint (``act``) first reads the point's priority array, waiting at most
``READ_TIMEOUT_S``: a point a more urgent slot holds (the building operator at 8) or one that
does not answer is left as it is, and the action says so rather than claim a change.
``stop()`` cancels the looks and releases every slot the loop holds.
"""

import asyncio
import math
import time
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from typing import Protocol, TypeVar

from loguru import logger

from spotcool_agent.actuator import Actuator, BacnetSeam, Publish
from spotcool_agent.domain.comfort import comfort_setpoint, occupants
from spotcool_agent.domain.events import Explanation, Perception, Value
from spotcool_agent.domain.geometry import Detections
from spotcool_agent.domain.louvers import (
    VANE_H,
    VANE_V,
    Aim,
    CameraPose,
    locate,
    louver_aim,
    vane_states,
)
from spotcool_agent.processors import explain
from spotcool_agent.processors.intents import Intent
from spotcool_agent.services.scene import SceneError, SceneService
from spotcool_agent.services.vision import CallCapReached, VisionError, VisionService
from spotcool_agent.transports.world_http import WorldHttp, WorldHttpError
from spotcool_contracts import Zone, load_point_map, load_vrf_profiles
from spotcool_contracts.events import EventType
from spotcool_contracts.points import PointSpec
from spotcool_contracts.priorities import AGENT_PRIORITY, OPERATOR_PRIORITY
from spotcool_contracts.vrf import VrfProfile

TICK_S = 0.25
"""Wall seconds between two rounds of the loop."""
IN_FLIGHT = 2
"""Looks (a snapshot and a detection) under way at once, at most."""
SHOT_EVERY_S = 0.5
"""Wall seconds between two snapshots, at least: the live camera's 2 frames a second."""
SNAPSHOT_TIMEOUT_S = 2.0
DETECT_TIMEOUT_S = 8.0
WRITE_EVERY_S = 1.0
"""Wall seconds between two writes of one point, at least (the gateway's guidance)."""
EXPLAIN_EVERY_S = 10.0
"""Wall seconds between two explanations of an aim change, at least."""
RETRY_S = 5.0
"""Wall seconds before asking again for the brand or the catalog."""
REFRESH_S = 2.0
"""Wall seconds between two reads of the unit's state."""
READ_TIMEOUT_S = 1.5
"""The longest a complaint waits for a priority array."""
HOLD_S = 10.0
DRAFT_HOLD_S = 600.0
COMPLAINT_HOLD_S = 900.0
SETPOINT_STEP_C = 1.0
SETPOINT_GRID_C = 0.5
FAN_STEPS = ("Low", "Mid", "High")
"""The fan speeds a complaint steps through; Auto runs as Mid."""
COMFORT_EVERY_S = 60.0
"""Wall seconds between two comfort readings, at least."""
SCENE_TIMEOUT_S = 20.0
"""The longest one comfort reading may take."""
COMFORT_REASON = "comfort: PMV 0 for the people seen"
CONCEPT = "humans"
ZONE = Zone.SOU
"""The meeting room's zone, for the decisions."""

GATEWAY = load_point_map("vrf_gateway")
SETPOINT = GATEWAY.by_name("IDU-SP")
FAN = GATEWAY.by_name("IDU-FAN")
ROOM_T = GATEWAY.by_name("IDU-ROOM-T")
VANES = {VANE_V: GATEWAY.by_name(VANE_V), VANE_H: GATEWAY.by_name(VANE_H)}
COMMANDED = (VANES[VANE_V], VANES[VANE_H], SETPOINT, FAN)
"""Every point the loop writes, and releases at stop."""
SHOWN = (
    ("room_temperature_c", ROOM_T),
    ("setpoint_c", SETPOINT),
    ("fan", FAN),
    ("vertical_louver", VANES[VANE_V]),
    ("horizontal_louver", VANES[VANE_H]),
)
"""The unit's state a reply may quote, and the points it comes from."""
REASONS: dict[Intent, str] = {
    "cold": "a complaint: too cold",
    "hot": "a complaint: too hot",
    "draft": "a complaint: a draft",
    "stuffy": "a complaint: stuffy air",
    "none": "a complaint",
}
Result = TypeVar("Result")


class VrfGateway(BacnetSeam, Protocol):
    """What the loop needs from the VRF gateway (its ``BacnetClient``; tests pass a fake)."""

    async def present(self, point: PointSpec) -> Value:
        """A point's present value in BMS units, or ``None`` without an answer."""
        ...

    async def model_name(self, instance: int) -> str | None:
        """The device's ``Model_Name``, or ``None`` without an answer."""
        ...


@dataclass(frozen=True)
class Action:
    """What a complaint did to the meeting room's unit.

    Parameters:
        intent: The complaint's intent.
        summary: What was done, in plain words after "I've", such as "raised the meeting
            room's setpoint to 25.0 degC and turned its fan down to Low".
        changed: Whether anything will be written.
    """

    intent: Intent
    summary: str
    changed: bool


class VrfLoop:
    """The meeting room's spot cooling (see the module docstring); ``run()`` until cancelled.

    Args:
        gateway: The VRF gateway on BACnet.
        world: The world's cameras.
        vision: The people finder, on a budget of its own.
        publish: Where the events go.
        camera_id: The camera watching the meeting room.
        pose: Its pose, and the unit's outlet.
        brand: The brand to assume when the gateway names one we do not know.
        mirror: The camera faces the unit: the pan flips.
        hold_s: Wall seconds with nobody seen before the vanes are released.
        draft_hold_s: Wall seconds a draft complaint pauses the aiming.
        complaint_hold_s: Wall seconds a setpoint or fan override lasts.
        scene: The scene reader for the comfort setpoint; ``None`` for no comfort setpoint.
        comfort_every_s: Wall seconds between two comfort readings, at least.
        clock: Wall clock in seconds; tests inject one.
    """

    def __init__(
        self,
        *,
        gateway: VrfGateway,
        world: WorldHttp,
        vision: VisionService,
        publish: Publish,
        camera_id: str = "cam-meeting-live",
        pose: CameraPose | None = None,
        brand: str | None = None,
        mirror: bool = False,
        hold_s: float = HOLD_S,
        draft_hold_s: float = DRAFT_HOLD_S,
        complaint_hold_s: float = COMPLAINT_HOLD_S,
        scene: SceneService | None = None,
        comfort_every_s: float = COMFORT_EVERY_S,
        clock: Callable[[], float] = time.perf_counter,
    ) -> None:
        self.actuator = Actuator(gateway, publish, points={ZONE: COMMANDED})
        """The gateway's writer, by the BMS actuator's rules."""
        self._gateway = gateway
        self._world = world
        self._vision = vision
        self._publish = publish
        self._camera_id = camera_id
        self._pose = pose or CameraPose()
        self._brand = brand
        self._mirror = mirror
        self._hold_s = hold_s
        self._draft_hold_s = draft_hold_s
        self._complaint_hold_s = complaint_hold_s
        self._scene = scene
        self._comfort_every_s = comfort_every_s
        self._clock = clock
        self.comfort_c: float | None = None
        """The comfort setpoint for the people last read, while they are seen."""
        self._comfort_next = -math.inf
        self._comforting: asyncio.Task[None] | None = None
        self.profile: VrfProfile | None = None
        """The brand's gateway profile, once known."""
        self._brand_settled = False
        self._brand_next = -math.inf
        self._known: dict[str, Value] = {}  # point -> its last-known present value
        self._refreshing: asyncio.Task[None] | None = None
        self._refresh_next = -math.inf
        self._snapshot_url: str | None = None
        self._shot_every_s = SHOT_EVERY_S
        self._catalog_next = -math.inf
        self._shot_next = -math.inf
        self._shots = 0  # the number of the last look started
        self._applied = 0  # the number of the newest look applied
        self._looking: set[asyncio.Task[None]] = set()
        self.capped = False
        """Whether the loop's call cap is used up: it looks no more and leaves the vanes."""
        self.aim: Aim | None = None
        """The last aim, at the people last seen."""
        self._seen_at = -math.inf  # when somebody was last seen
        self._draft_until = -math.inf
        self._overrides: dict[str, tuple[Value, float, str]] = {}  # point -> value, until, why
        self._tried: dict[str, float] = {}  # point -> when it was last written
        self._explained_at = -math.inf
        self._failing: dict[str, str] = {}
        self._errors: set[str] = set()

    async def run(self) -> None:
        """Run a round every ``TICK_S`` until cancelled; one bad round never ends it."""
        try:
            while True:
                try:
                    await self.tick()
                except Exception as exc:
                    kind = type(exc).__name__
                    if kind not in self._errors:
                        self._errors.add(kind)
                        logger.error(f"VRF: a round failed, the loop goes on: {kind}: {exc}")
                await asyncio.sleep(TICK_S)
        finally:
            await self._stop_looking()

    async def stop(self) -> None:
        """Cancel the looks, then release every slot the loop holds. Safe to call twice.

        A state read under way is let finish first: the client sends one request at a time.
        """
        await self._stop_looking()
        refreshing = self._refreshing
        if refreshing is not None:
            await asyncio.gather(refreshing, return_exceptions=True)
        await self.actuator.stop()

    async def tick(self) -> None:
        """One round: learn the brand, find the camera, start a look, write what changed."""
        await self._learn_brand(self._clock())
        self._refresh_again(self._clock())
        await self._find_camera(self._clock())
        self._look_again(self._clock())
        self._comfort_again(self._clock())
        await self._write(self._clock())

    def seen(self, found: Detections, look: int) -> bool:
        """Apply one look's result: publish it and aim at whoever it shows.

        Args:
            found: The people found.
            look: The look's number; a result older than one applied is dropped.

        Returns:
            Whether it was applied.
        """
        if look <= self._applied or self.capped:
            return False
        self._applied = look
        masks = {self._camera_id: found.polygons()}
        self._publish(EventType.PERCEPTION, Perception(people={}, masks=masks), None)
        people = [
            (locate(d, found.width, found.height, self._pose), (d.x2 - d.x1) * (d.y2 - d.y1))
            for d in found.boxes
        ]
        aim = louver_aim(people, self._pose)
        if aim is not None:
            self.aim, self._seen_at = aim, self._clock()
        return True

    async def act(self, intent: Intent) -> Action:
        """Do what a complaint about the meeting room asks; the next rounds write it.

        - cold: the setpoint up a degree, the fan a step down;
        - hot: the setpoint down a degree, the fan a step up;
        - stuffy: the fan a step up;
        - draft: the vertical vane to Pos1 (a level throw), the aiming paused.

        The setpoint stays on 16-30 degC on the 0.5 grid, and an override lasts
        ``complaint_hold_s`` (a draft, ``draft_hold_s``). A point held by a more urgent slot,
        or whose priority array does not come within ``READ_TIMEOUT_S``, is not changed, and
        the action says why.
        """
        if intent == "draft":
            return await self._draft()
        if intent == "none":
            return Action(intent, "left the meeting room's AC as it is", changed=False)
        until, reason = self._clock() + self._complaint_hold_s, REASONS[intent]
        done: list[str] = []
        notes: list[str] = []
        if intent in ("cold", "hot"):
            up = intent == "cold"
            step_c = SETPOINT_STEP_C if up else -SETPOINT_STEP_C
            found = await self._change(SETPOINT, "setpoint", lambda v: _setpoint(_c(v) + step_c))
            if isinstance(found, str):
                notes.append(found)
            elif found[1] == found[0]:
                notes.append(f"its setpoint is already at its {'highest' if up else 'lowest'}, "
                             f"{_c(found[0]):.1f} degC")  # fmt: skip
            else:
                self._overrides[SETPOINT.name] = (found[1], until, reason)
                done.append(f"{'raised' if up else 'lowered'} the meeting room's setpoint to "
                            f"{_c(found[1]):.1f} degC")  # fmt: skip
        step = -1 if intent == "cold" else 1
        found = await self._change(FAN, "fan", lambda v: _fan_step(str(v), step))
        if isinstance(found, str):
            notes.append(found)
        elif found[1] == found[0]:
            notes.append(f"its fan is already at its {'lowest' if step < 0 else 'highest'}")
        else:
            self._overrides[FAN.name] = (found[1], until, reason)
            whose = "its" if done else "the meeting room's"
            done.append(f"turned {whose} fan {'down' if step < 0 else 'up'} to {found[1]}")
        summary = " and ".join(done) or "left the meeting room's AC as it is"
        if notes:
            summary += ("; " if done else ": ") + " and ".join(notes)
        return Action(intent, summary, changed=bool(done))

    def state(self) -> dict[str, object]:
        """The meeting room's unit as last read, for a reply's facts; unknown values left out."""
        state: dict[str, object] = {}
        for label, point in SHOWN:
            value = self._known.get(point.name)
            if value is not None:
                state[label] = round(value, 1) if isinstance(value, float) else value
        if self.profile is not None and self.profile.vane_horizontal is None:
            state.pop("horizontal_louver", None)  # the brand has none: the gateway shows Pos3
        if self.profile is not None:
            state["brand"] = self.profile.vendor
        aiming = self.aim is not None and self._clock() - self._seen_at < self._hold_s
        state["people_the_louvers_follow"] = self.aim.people if aiming and self.aim else 0
        return state

    async def _draft(self) -> Action:
        vertical = self.profile.vane_vertical if self.profile else None
        if vertical is None:
            return Action("draft", "noted the draft, but I can't move the louvers yet", False)
        found = await self._change(VANES[VANE_V], "vertical louver", lambda v: v)
        if isinstance(found, str):
            return Action("draft", f"left the meeting room's louvers as they are: {found}", False)
        self._draft_until = self._clock() + self._draft_hold_s
        minutes = f"{self._draft_hold_s / 60:g} minutes"
        if vertical.positions:
            text = f"turned the meeting room's louvers to blow level, for the next {minutes}"
        else:
            text = f"stopped the meeting room's louvers swinging, for the next {minutes}"
        return Action("draft", text, changed=True)

    async def _change(
        self, point: PointSpec, label: str, step: Callable[[Value], Value]
    ) -> tuple[Value, Value] | str:
        """A point's value in force and the one a complaint wants, or why it cannot change.

        Its priority array is read first, at most ``READ_TIMEOUT_S``: a point held by a more
        urgent slot, or one that does not answer, is not changed.
        """
        slots = await _within(READ_TIMEOUT_S, self._gateway.slots(point))
        if slots is None:
            return f"its {label} did not answer"
        urgent = enumerate(slots[: AGENT_PRIORITY - 1], start=1)
        held = next(((n, v) for n, v in urgent if v is not None), None)
        if held is not None:
            priority, value = held
            who = (
                "the building operator" if priority == OPERATOR_PRIORITY else f"priority {priority}"
            )
            shown = f"{value:.1f} degC" if isinstance(value, float) else value
            return f"its {label} is held at {shown} by {who}"
        before = self._in_force(point, slots)
        return before, step(before)

    def _in_force(self, point: PointSpec, slots: list[Value]) -> Value:
        """A point's value now: an override of ours, else its slots', else its default.

        The last-known value is the fallback only for a point without a relinquish default:
        just after a release it may still show the value released.
        """
        override = self._overrides.get(point.name)
        if override is not None and self._clock() < override[1]:
            return override[0]
        commanded = next((value for value in slots if value is not None), None)
        if commanded is not None:
            return commanded
        default = _default(point)
        return default if default is not None else self._known.get(point.name)

    async def _learn_brand(self, now: float) -> None:
        if self._brand_settled or now < self._brand_next:
            return
        self._brand_next = now + RETRY_S
        name = await self._gateway.model_name(GATEWAY.device_instance)
        if name is None:  # no vane is written meanwhile, even with --vrf-brand
            self._failed("gateway", "the VRF gateway does not answer; asking again")
            return
        self._brand_settled = True
        self._recovered("gateway")
        profiles = load_vrf_profiles()
        named = next((p for p in profiles.values() if p.label in name), None)
        if named is not None and self._brand not in (None, named.vendor):
            logger.warning(f"VRF: the gateway is {named.vendor}; --vrf-brand {self._brand} ignored")
        profile = named or (profiles[self._brand] if self._brand else None)
        if profile is None:
            logger.warning(
                f"VRF: the gateway names no known brand ({name!r}) and --vrf-brand is not set: "
                "the vanes are left alone"
            )
            return
        self.profile = profile
        source = "the gateway's Model_Name" if named else "--vrf-brand"
        logger.info(f"VRF: {profile.vendor} profile, from {source}")

    def _refresh_again(self, now: float) -> None:
        if not self._brand_settled or self._refreshing is not None or now < self._refresh_next:
            return
        self._refresh_next = now + REFRESH_S
        self._refreshing = asyncio.create_task(self._refresh(), name="vrf-state")

    async def _refresh(self) -> None:
        """Read the unit's state into the last-known values; stop at the first no-answer."""
        try:
            for _, point in SHOWN:
                value = await self._gateway.present(point)
                if value is None:
                    return  # the gateway does not answer: the next refresh tries again
                self._known[point.name] = value
        finally:
            self._refreshing = None

    async def _find_camera(self, now: float) -> None:
        if self._snapshot_url is not None or now < self._catalog_next:
            return
        self._catalog_next = now + RETRY_S
        try:
            catalog = await self._world.cameras()
        except WorldHttpError as exc:
            self._failed("catalog", str(exc))
            return
        camera = next((c for c in catalog.cameras if c.id == self._camera_id), None)
        if camera is None:
            self._failed("catalog", f"no camera {self._camera_id} in the catalog")
            return
        self._recovered("catalog")
        self._snapshot_url = camera.snapshot_url
        self._shot_every_s = max(SHOT_EVERY_S, 1.0 / camera.fps_hint)

    def _look_again(self, now: float) -> None:
        url = self._snapshot_url
        if url is None or self.capped or len(self._looking) >= IN_FLIGHT or now < self._shot_next:
            return
        self._shot_next = now + self._shot_every_s
        self._shots += 1
        task = asyncio.create_task(self._look(url, self._shots), name=f"vrf-look-{self._shots}")
        self._looking.add(task)
        task.add_done_callback(self._looking.discard)

    async def _look(self, url: str, look: int) -> None:
        """One snapshot and its detection; the frame stays in memory and is dropped after."""
        try:
            jpeg = await self._world.snapshot(url, SNAPSHOT_TIMEOUT_S)
            found = await self._vision.detect(
                jpeg, CONCEPT, DETECT_TIMEOUT_S, camera_id=self._camera_id
            )
        except WorldHttpError as exc:
            self._failed(self._camera_id, str(exc))
            return
        except CallCapReached as exc:
            if not self.capped:
                logger.warning(f"VRF: {exc}: no more looks, the vanes are released")
            self.capped, self.aim = True, None
            return
        except VisionError as exc:
            self._failed(self._camera_id, f"{self._vision.name}: {exc}")
            return
        except Exception as exc:  # a provider's bug: this look is lost, the loop goes on
            self._failed(self._camera_id, f"{self._vision.name}: {type(exc).__name__}: {exc}")
            return
        self._recovered(self._camera_id)
        self.seen(found, look)

    def _people_seen(self, now: float) -> bool:
        return self.aim is not None and now - self._seen_at < self._hold_s

    def _comfort_again(self, now: float) -> None:
        if not self._people_seen(now):
            self.comfort_c = None  # nobody: the setpoint goes back to the unit's default
            return
        url, scene = self._snapshot_url, self._scene
        if url is None or scene is None or self._comforting is not None or self.capped:
            return
        if now < self._comfort_next:
            return
        self._comfort_next = now + self._comfort_every_s
        seen = self.aim.people if self.aim else None
        self._comforting = asyncio.create_task(
            self._read_comfort(scene, url, seen), name="vrf-comfort"
        )

    async def _read_comfort(self, scene: SceneService, url: str, sam_count: int | None) -> None:
        """One comfort reading: a snapshot, the scene, then the setpoint at PMV 0."""
        try:
            jpeg = await self._world.snapshot(url, SNAPSHOT_TIMEOUT_S)
            found = await scene.assess(jpeg, sam_count, SCENE_TIMEOUT_S, camera_id=self._camera_id)
        except (WorldHttpError, SceneError) as exc:
            self._failed("comfort", f"{scene.name}: {exc}")
            return
        except Exception as exc:  # a provider's bug: this reading is lost, the loop goes on
            self._failed("comfort", f"{scene.name}: {type(exc).__name__}: {exc}")
            return
        finally:
            self._comforting = None
        self._recovered("comfort")
        if not found.people or not self._people_seen(self._clock()):
            return
        target = _setpoint(comfort_setpoint(found.people))
        if target != self.comfort_c:
            people = occupants(found.people)
            clo = sum(o.clo for o in people) / len(people)
            met = sum(o.met for o in people) / len(people)
            text = explain.comfort(found.people, clo, met, target)
            logger.info(f"VRF: {text}")
            self._publish(EventType.EXPLANATION, Explanation(text=text), None)
        self.comfort_c = target

    async def _stop_looking(self) -> None:
        comforting = self._comforting
        if comforting is not None:
            comforting.cancel()
            await asyncio.gather(comforting, return_exceptions=True)
        looking = list(self._looking)
        for task in looking:
            task.cancel()
        await asyncio.gather(*looking, return_exceptions=True)

    def _wanted(self, now: float) -> list[tuple[PointSpec, Value, str, bool]]:
        """Each point's wanted value, the reason, and whether its write is explained at once."""
        wanted: list[tuple[PointSpec, Value, str, bool]] = []
        profile = self.profile
        if profile is not None:
            axes = [
                name
                for name, axis in (
                    (VANE_V, profile.vane_vertical),
                    (VANE_H, profile.vane_horizontal),
                )
                if axis is not None
            ]
            vertical = profile.vane_vertical
            if now < self._draft_until and vertical is not None:
                state = "Pos1" if vertical.positions else None  # LG: swing off
                wanted.append((VANES[VANE_V], state, REASONS["draft"], True))  # H stays put
            elif self._people_seen(now) and self.aim is not None:
                for name, state in vane_states(self.aim, profile, mirror=self._mirror).items():
                    wanted.append((VANES[name], state, self.aim.reason(), False))
            else:
                reason = (
                    "the VRF loop's call cap is reached"
                    if self.capped
                    else f"nobody seen for {self._hold_s:g} s"
                )
                wanted += [(VANES[name], None, reason, True) for name in axes]
        for point in (SETPOINT, FAN):
            value, until, reason = self._overrides.get(point.name, (None, -math.inf, ""))
            if now < until:  # a complaint's override comes before the comfort setpoint
                wanted.append((point, value, reason, True))
            elif point is SETPOINT and self.comfort_c is not None:
                wanted.append((point, self.comfort_c, COMFORT_REASON, True))
            else:
                comforted = point is SETPOINT and self.actuator.held.get(point.name) != value
                why = (
                    f"nobody seen for {self._hold_s:g} s"
                    if comforted
                    else "the complaint's hold ended"
                )
                wanted.append((point, None, why, True))
        return wanted

    async def _write(self, now: float) -> None:
        """Write what changed, at most once a second per point, through the actuator."""
        held = self.actuator.held
        batches: dict[tuple[str, bool], list[tuple[PointSpec, Value]]] = {}
        for point, value, reason, explained in self._wanted(now):
            if held.get(point.name) == value:
                continue  # in force already (or nothing to release)
            if now - self._tried.get(point.name, -math.inf) < WRITE_EVERY_S:
                continue  # written less than a second ago: next round
            self._tried[point.name] = now
            batches.setdefault((reason, explained), []).append((point, value))
        for (reason, explained), values in batches.items():
            await self.actuator.command(ZONE, reason, values, None, explained=explained)
        after = self.actuator.held
        moved = [(name, after.get(name)) for name in VANES if after.get(name) != held.get(name)]
        aimed = any(not explained for _, explained in batches)
        if moved and aimed and self.aim and now - self._explained_at >= EXPLAIN_EVERY_S:
            self._explained_at = now
            text = explain.commanded(ZONE, self.aim.reason(), moved)
            self._publish(EventType.EXPLANATION, Explanation(text=text), None)

    def _failed(self, what: str, message: str) -> None:
        if self._failing.get(what) != message:
            logger.warning(f"VRF {what}: {message}")
        self._failing[what] = message

    def _recovered(self, what: str) -> None:
        if self._failing.pop(what, None) is not None:
            logger.info(f"VRF {what}: answering again")


async def _within(seconds: float, request: Awaitable[Result]) -> Result | None:
    """A request's answer if it comes within ``seconds``, else ``None``.

    A late request is not cancelled but left to finish, its answer dropped: the gateway's client
    sends one request at a time, and one cut off in flight could leave it deaf.
    """
    task = asyncio.ensure_future(request)
    try:
        return await asyncio.wait_for(asyncio.shield(task), seconds)
    except TimeoutError:
        task.add_done_callback(lambda done: done.cancelled() or done.exception())
        return None


def _c(value: Value) -> float:
    """A setpoint's value as degrees Celsius; 24.0 (the gateway's default) if it is not one."""
    return float(value) if isinstance(value, float) else 24.0


def _setpoint(value_c: float) -> float:
    """A setpoint on the gateway's 0.5 grid, within 16-30 degC."""
    low, high = SETPOINT.min_value or 16.0, SETPOINT.max_value or 30.0
    return min(max(round(value_c / SETPOINT_GRID_C) * SETPOINT_GRID_C, low), high)


def _fan_step(speed: str, step: int) -> str:
    """The fan speed one step down (-1) or up (+1), within Low-High; Auto runs as Mid."""
    level = FAN_STEPS.index(speed) if speed in FAN_STEPS else 1
    return FAN_STEPS[min(max(level + step, 0), len(FAN_STEPS) - 1)]


def _default(point: PointSpec) -> Value:
    """A point's relinquish default in BMS units: a multi-state point's is its state number."""
    default = point.relinquish_default
    states = point.states or []
    if isinstance(default, float) and states:
        return states[int(default) - 1]
    return default
