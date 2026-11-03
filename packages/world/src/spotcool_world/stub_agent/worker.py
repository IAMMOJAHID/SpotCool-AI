"""StubAgentWorker: a stand-in for Person A's agent (the ``bacnet`` extra).

THIS IS A STUB, NOT PERSON A'S AGENT. It exists so the world can be run, scored and filmed
without A. It talks real BACnet/IP to the BMS, over loopback or the LAN and on its own UDP port,
exactly as A's agent will. Its perception, though, is perfect, and openly so: it reads the
scenario's ground truth (the ``scenario_state`` events on ``"events"``, people per zone), which
a real agent must never read, and every event it publishes says ``stub: true``.

What it does (``StubPolicy`` decides, the worker writes):

- An empty zone: ``ZN-<Z>-OCC-SENSOR`` (BV 450+i) Unoccupied and ``ZN-<Z>-CLG-SP`` (AV 200+i)
  28.0 degC at priority 10. The BMS then runs occupied-standby.
- A zone with people: ``OCC-SENSOR`` Occupied and ``CLG-SP`` released (NULL), so the building
  schedule applies, as in the baseline run.
- Pre-cooling: from the ``calendar`` events, a booked room's zone is held as a zone with people
  from 30 minutes before the booking to its end.
- From 19:00, and at ``stop()``, every priority-10 slot it holds is released (at ``stop()`` the
  heartbeat's too).
- ``SPOTCOOL-HEARTBEAT`` (BV 901) is toggled every 30 s of wall time.

It commands the zones at the close of each step (the step's ``tick`` event), from that step's
state, so every write takes effect from the next step, however long its round trips take, as a
real agent's do. Every write happens only on a change. Before writing a zone point, value or
release, at 19:00 and at ``stop()`` included, the stub reads its priority array:

- A slot more urgent than its own (1-9, an operator at 8) holds a value: it backs off, does not
  write, says so once in a ``decision`` and an ``explanation``, and tries again at the next
  step, so it resumes when the operator releases.
- It never releases a slot it did not write: a release goes out only while slot 10 still holds
  the stub's own value.

So the dashboard's agent panel and video mode work in stub runs, it publishes on ``"events"``,
with ``source: "agent"``: a ``perception`` event per scenario state (people per zone) and, per
command, a ``decision`` and an ``explanation``.
"""

import asyncio
from collections.abc import Sequence
from typing import Any

from bacpypes3.apdu import ErrorRejectAbortNack
from bacpypes3.ipv4.app import NormalApplication
from bacpypes3.local.device import DeviceObject
from bacpypes3.pdu import Address, IPv4Address
from bacpypes3.primitivedata import Null
from loguru import logger
from pydantic import BaseModel

from spotcool_contracts import ZONES, Zone, load_point_map
from spotcool_contracts.events import (
    CalendarData,
    EventEnvelope,
    EventSource,
    EventType,
    ScenarioStateData,
)
from spotcool_contracts.points import PointSpec
from spotcool_contracts.priorities import AGENT_PRIORITY, HEARTBEAT_PERIOD_S, OPERATOR_PRIORITY
from spotcool_world.bus import Bus, Subscription
from spotcool_world.processors.event_emitter import EVENTS_TOPIC
from spotcool_world.stub_agent.policy import StubPolicy
from spotcool_world.workers.base_worker import BaseWorker, WorkerState

DEVICE_INSTANCE = 1900
"""The stub's own BACnet device instance (the BMS is 1001, the VRF gateway 2001)."""
DEVICE_NAME = "SpotCool-Stub-Agent"
"""The stub's BACnet device name, as a BACnet explorer lists it."""
VENDOR_ID = 999
"""BACpypes3's own vendor identifier."""
EVENTS_QUEUE_SIZE = 10_000
"""Events the stub may fall behind by before the oldest are dropped."""

Value = str | float | None
"""A point's value in BMS units: a state text or a setpoint in degC; ``None`` is NULL."""
Hold = tuple[int, str | float]
"""A slot more urgent than the stub's that holds a value: its priority and the value."""


class Perception(BaseModel):
    """The data of a stub ``perception`` event.

    Parameters:
        stub: Always true: the counts are the scenario's ground truth, not a camera's.
        people: The people in each zone.
    """

    stub: bool = True
    people: dict[Zone, int]


class Decision(BaseModel):
    """The data of a stub ``decision`` event: one BACnet command.

    Parameters:
        stub: Always true.
        zone: The zone commanded.
        point: The BMS point written.
        value: The state text or the setpoint (degC) written, or ``None`` for a release (NULL).
        priority: The priority slot written.
        reason: Why.
    """

    stub: bool = True
    zone: Zone
    point: str
    value: Value
    priority: int
    reason: str


class Explanation(BaseModel):
    """The data of a stub ``explanation`` event.

    Parameters:
        stub: Always true.
        text: One sentence for the agent panel.
    """

    stub: bool = True
    text: str


class StubAgentWorker(BaseWorker):
    """The stub agent: ground-truth perception, real BACnet writes (see the module docstring).

    ``run()`` subscribes to ``"events"`` before its first wait, so start it with the pipeline
    under one ``WorkerRunner``; it needs the runner's bus. It runs until ``stop()``: the world
    stops it from the pipeline worker's ``on_pipeline_finished``, while the BMS still serves. A
    write the BMS refuses or never answers, or a priority array it does not answer for, is
    logged, and tried again at the next step.

    Args:
        device: The BMS's ``ip:port``. The stub binds its own UDP port, any free one, on the
            same address.
        heartbeat_s: Wall seconds between two heartbeat toggles.
        name: Unique worker name.
    """

    def __init__(
        self, device: str, *, heartbeat_s: float = HEARTBEAT_PERIOD_S, name: str | None = None
    ) -> None:
        super().__init__(name=name)
        self._device = device
        self._address = Address(device)
        self._heartbeat_s = heartbeat_s
        self._policy = StubPolicy()
        points = load_point_map("office_bms")
        self._points = {
            zone: (
                points.by_name(f"ZN-{zone.value.upper()}-OCC-SENSOR"),
                points.by_name(f"ZN-{zone.value.upper()}-CLG-SP"),
            )
            for zone in Zone
        }
        self._heartbeat = points.by_name("SPOTCOOL-HEARTBEAT")
        self._held: dict[str, str | float] = {}  # point name -> the value written at priority 10
        self._said: dict[str, tuple[Hold, Value]] = {}  # point name -> the back-off last announced
        self._latest: tuple[ScenarioStateData, float] | None = None  # the latest state, its time
        self._events: Subscription | None = None
        self._stopping = False

    async def run(self) -> None:
        """Drive the BMS until ``stop()``, then release its writes; the state is then ``finished``.

        Raises:
            RuntimeError: If the worker has already run, or has no bus.
        """
        if self._state is not WorkerState.IDLE:
            raise RuntimeError(f"{self.name} has already run")
        bus = self.bus
        if bus is None:
            raise RuntimeError(f"{self.name} needs a bus: run it with a WorkerRunner")
        self._state = WorkerState.RUNNING
        events = self._events = bus.subscribe(EVENTS_TOPIC, maxsize=EVENTS_QUEUE_SIZE)
        if self._stopping:
            events.close()
        app: NormalApplication | None = None
        try:
            app = self._application()
            logger.info(
                f"{self.name}: STUB agent, perception from the scenario's ground truth, "
                f"driving the BMS at {self._device}"
            )
            self.create_task(self._beat(app), "heartbeat")
            async for envelope in events:
                await self._handle(app, bus, envelope)
            await self.cancel_tasks()  # the heartbeat must not come back after the release
            await self._release(app, bus)
            self._state = WorkerState.FINISHED
        except asyncio.CancelledError:
            self._state = WorkerState.CANCELLED
            raise
        except Exception:
            self._state = WorkerState.FAILED
            raise
        finally:
            events.close()
            await self.cleanup()
            if app is not None:
                app.close()
                await asyncio.sleep(0)  # a closed transport releases its socket on the next turn

    async def stop(self, reason: str = "") -> None:
        """Stop taking events; ``run()`` then releases every priority-10 slot it holds.

        Args:
            reason: Why it is being stopped (logged).
        """
        logger.info(f"{self.name}: stopping{f' ({reason})' if reason else ''}")
        self._stopping = True
        if self._events is not None:
            self._events.close()

    def _application(self) -> NormalApplication:
        """The stub's BACnet/IP device, on any free UDP port of the BMS's address."""
        host = self._device.rpartition(":")[0]
        device = DeviceObject(
            objectIdentifier=("device", DEVICE_INSTANCE),
            objectName=DEVICE_NAME,
            vendorIdentifier=VENDOR_ID,
        )
        return NormalApplication(device, IPv4Address(f"{host}:0"))

    async def _handle(self, app: NormalApplication, bus: Bus, envelope: object) -> None:
        """Take a world event: a scenario state, a room's bookings, or a step's close (``tick``).

        The zones are commanded at the step's close, from its state: a step's ``tick`` event
        comes after the BMS has taken the step's commands.
        """
        if not isinstance(envelope, EventEnvelope) or envelope.source is not EventSource.WORLD:
            return
        t_s = envelope.sim_time_s
        if t_s is None:
            return
        if envelope.type is EventType.SCENARIO_STATE:
            state = ScenarioStateData.model_validate(envelope.data)
            self._latest = (state, t_s)
            people = Perception(people={scene.zone: scene.people for scene in state.zones})
            await _publish(bus, EventType.PERCEPTION, people, t_s)
        elif envelope.type is EventType.CALENDAR:
            self._policy.calendar(CalendarData.model_validate(envelope.data))
        elif envelope.type is EventType.TICK and self._latest is not None:
            state, state_s = self._latest
            for zone, command in self._policy.commands(state, state_s).items():
                sensor, setpoint = self._points[zone]
                writes = [
                    (point, value)
                    for point, value in ((sensor, command.sensor), (setpoint, command.setpoint_c))
                    if self._held.get(point.name) != value
                ]
                if writes:
                    await self._command(app, bus, zone, command.reason, writes, state_s)

    async def _command(
        self,
        app: NormalApplication,
        bus: Bus,
        zone: Zone,
        reason: str,
        writes: Sequence[tuple[PointSpec, Value]],
        t_s: float,
    ) -> None:
        """Check each point's priority array, then publish and make the writes it allows.

        A point a slot more urgent than 10 holds is backed off from (``_back_off``); a release
        whose slot 10 no longer holds the stub's value is dropped, since another client wrote
        it or the BMS released it. The rest get a decision each and the zone one explanation.
        """
        allowed: list[tuple[PointSpec, Value]] = []
        for point, value in writes:
            slots = await self._slots(app, point)
            if slots is None:
                continue  # unread: tried again at the next step
            hold = _hold(slots)
            if hold is not None:
                await self._back_off(bus, zone, point, value, hold, t_s)
                continue
            self._said.pop(point.name, None)
            if value is None and slots[AGENT_PRIORITY - 1] != self._held.get(point.name):
                self._held.pop(point.name, None)  # never release a slot the stub did not write
                continue
            allowed.append((point, value))
        if not allowed:
            return
        for point, value in allowed:
            decision = Decision(
                zone=zone, point=point.name, value=value, priority=AGENT_PRIORITY, reason=reason
            )
            await _publish(bus, EventType.DECISION, decision, t_s)
        done = ", ".join(f"{p.name} {'released' if v is None else v}" for p, v in allowed)
        text = f"{ZONES[zone].display_name}: {reason} -> {done} at priority {AGENT_PRIORITY}"
        await _publish(bus, EventType.EXPLANATION, Explanation(text=text), t_s)
        for point, value in allowed:
            await self._write(app, point, value)

    async def _back_off(
        self, bus: Bus, zone: Zone, point: PointSpec, value: Value, hold: Hold, t_s: float
    ) -> None:
        """Leave a point a more urgent slot holds; say so once per hold and wanted value."""
        if self._said.get(point.name) == (hold, value):
            return
        self._said[point.name] = (hold, value)
        priority, held = hold
        who = (
            f"an operator at priority {priority}"
            if priority == OPERATOR_PRIORITY
            else f"priority {priority}"
        )
        decision = Decision(
            zone=zone,
            point=point.name,
            value=value,
            priority=AGENT_PRIORITY,
            reason=f"backed off: held at {held} by {who}",
        )
        await _publish(bus, EventType.DECISION, decision, t_s)
        wanted = "releasing it" if value is None else f"writing {value}"
        retry = "" if self._stopping else "; trying again each step"
        text = (
            f"{ZONES[zone].display_name}: {point.name} is held at {held} by {who} -> backed off "
            f"from {wanted}{retry}"
        )
        logger.info(f"{self.name}: {text}")
        await _publish(bus, EventType.EXPLANATION, Explanation(text=text), t_s)

    async def _beat(self, app: NormalApplication) -> None:
        """Toggle the heartbeat every ``heartbeat_s`` wall seconds, On first (a background task)."""
        off, on = self._heartbeat.states or ["Off", "On"]
        beat = True
        while True:
            await self._write(app, self._heartbeat, on if beat else off)
            beat = not beat
            await asyncio.sleep(self._heartbeat_s)

    async def _release(self, app: NormalApplication, bus: Bus) -> None:
        """Release every priority-10 slot the stub holds, by the same rules as any write."""
        every = [point for pair in self._points.values() for point in pair]
        held = [point for point in (*every, self._heartbeat) if point.name in self._held]
        if held:
            logger.info(f"{self.name}: releasing {', '.join(point.name for point in held)}")
        if self._latest is not None:  # zone points are written only after a state
            for zone, pair in self._points.items():
                releases = [(point, None) for point in pair if point.name in self._held]
                if releases:
                    reason = "the stub is stopping"
                    await self._command(app, bus, zone, reason, releases, self._latest[1])
        if self._heartbeat.name in self._held:
            await self._write(app, self._heartbeat, None)

    async def _slots(self, app: NormalApplication, point: PointSpec) -> list[Value] | None:
        """A point's 16 priority slots in BMS units, or ``None`` if the BMS gave no answer."""
        try:
            array = await app.read_property(self._address, point.object_id, "priority-array")
        except ErrorRejectAbortNack as exc:  # a BaseException in BACpypes3
            logger.warning(f"{self.name}: reading {point.name}'s priority array failed: {exc}")
            return None
        return [_slot_value(point, slot) for slot in array]

    async def _write(self, app: NormalApplication, point: PointSpec, value: Value) -> None:
        """Write a point at priority 10: a binary state or a setpoint; ``None`` releases it.

        A write the BMS refuses or never answers is logged, and the point counts as not written.
        """
        if value is None:
            present: Any = Null(())
        elif isinstance(value, str):
            present = _binary(point, value)
        else:
            present = value  # BACpypes3 makes it the point's Real
        try:
            await app.write_property(
                self._address, point.object_id, "present-value", present, priority=AGENT_PRIORITY
            )
        except ErrorRejectAbortNack as exc:  # a BaseException in BACpypes3
            logger.warning(f"{self.name}: writing {point.name} = {value} failed: {exc}")
            return
        if value is None:
            self._held.pop(point.name, None)
        else:
            self._held[point.name] = value


def _hold(slots: list[Value]) -> Hold | None:
    """The most urgent slot above the stub's (1-9) that holds a value, if any."""
    for index, value in enumerate(slots[: AGENT_PRIORITY - 1]):
        if value is not None:
            return index + 1, value
    return None


def _slot_value(point: PointSpec, slot: Any) -> Value:
    """A BACnet ``PriorityValue`` in BMS units: a setpoint, a state text, or ``None`` for NULL."""
    if slot.null is not None:
        return None
    if slot.real is not None:
        return float(slot.real)
    states, index = point.states or [], int(slot.enumerated)
    return states[index] if 0 <= index < len(states) else str(index)


def _binary(point: PointSpec, state: str) -> str:
    """A binary point's state text as its BACnet present value: ``inactive`` or ``active``."""
    return "active" if (point.states or []).index(state) == 1 else "inactive"


async def _publish(bus: Bus, kind: EventType, data: BaseModel, t_s: float) -> None:
    """Put a stub event on the events topic, as the agent."""
    await bus.publish(EVENTS_TOPIC, EventEnvelope.build(kind, EventSource.AGENT, data, t_s))
