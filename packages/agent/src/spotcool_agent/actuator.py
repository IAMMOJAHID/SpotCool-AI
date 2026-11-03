"""Actuator: turns the policy's commands into BACnet writes, safely (the stub agent's rules).

- **Only on a change.** A point is written when the value it should hold differs from the one
  the agent last wrote there; a release goes out only for a point the agent holds.
- **Back off from more urgent slots.** Before writing a zone point, a release included, it reads
  the point's priority array. If a slot 1-9 holds a value (an operator writes at 8), it does
  not write, says so once per hold and wanted value (a ``decision`` and an ``explanation``), and
  tries again at the next step.
- **Never release a slot that is not its own.** A release goes out only while slot 10 still
  holds the value the agent wrote; otherwise (another client wrote 10, or the BMS's heartbeat
  watchdog released it) the agent forgets the point.
- **Heartbeat.** ``SPOTCOOL-HEARTBEAT`` (BV 901) is toggled every ``heartbeat_s`` wall seconds,
  On first, without the check: it is the agent's liveness signal, not a command.
- **Release.** ``stop()`` cancels the heartbeat first, so it never comes back after the
  release, then releases every zone point the agent holds, by the rules above, then the
  heartbeat. The building loop calls it on a normal stop, on Ctrl+C and on a crash.
"""

import asyncio
import math
from collections.abc import Callable, Mapping, Sequence
from typing import Protocol

from loguru import logger
from pydantic import BaseModel

from spotcool_agent.domain.events import Decision, Explanation, Value
from spotcool_agent.processors import explain
from spotcool_agent.processors.policy import Command
from spotcool_contracts import Zone, load_point_map
from spotcool_contracts.events import EventType
from spotcool_contracts.points import PointSpec
from spotcool_contracts.priorities import AGENT_PRIORITY, HEARTBEAT_PERIOD_S

STOPPING = "the agent is stopping"
"""The reason of the releases ``stop()`` makes."""

Hold = tuple[int, str | float]
"""A slot more urgent than the agent's that holds a value: its priority and the value."""
Publish = Callable[[EventType, BaseModel, float | None], None]
"""Puts one agent event on the dashboard: its type, its data and its simulation time."""


class BacnetSeam(Protocol):
    """What the actuator needs from BACnet (``BacnetClient``; tests pass a fake)."""

    async def slots(self, point: PointSpec) -> list[Value] | None:
        """A point's 16 priority slots in BMS units, or ``None`` without an answer."""
        ...

    async def write(self, point: PointSpec, value: Value) -> bool:
        """Write a point at priority 10 (``None`` releases it); whether the BMS took it."""
        ...


class Actuator:
    """Writes the policy's commands to the BMS (see the module docstring).

    Args:
        bacnet: The BMS client.
        publish: Where the decisions and explanations go.
        heartbeat_s: Wall seconds between two heartbeat toggles.
    """

    def __init__(
        self, bacnet: BacnetSeam, publish: Publish, *, heartbeat_s: float = HEARTBEAT_PERIOD_S
    ) -> None:
        self._bacnet = bacnet
        self._publish = publish
        self._heartbeat_s = heartbeat_s
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
        self._said: dict[str, tuple[Hold, Value]] = {}  # point name -> the back-off announced
        self._beating: asyncio.Task[None] | None = None
        self._stopping = False
        self._last_t_s: float | None = None

    @property
    def held(self) -> dict[str, str | float]:
        """The points the agent holds at priority 10, with the values it wrote (a copy)."""
        return dict(self._held)

    def start(self) -> None:
        """Start the heartbeat task. Does nothing if it runs."""
        if self._beating is None:
            self._beating = asyncio.create_task(self._beat(), name="heartbeat")

    async def apply(self, commands: Mapping[Zone, Command], t_s: float) -> None:
        """Write what changed in the commands, zone by zone.

        Args:
            commands: Each zone's command; a zone left out keeps what it holds.
            t_s: The stamp of the decisions: the start of the step they were made on.
        """
        self._last_t_s = t_s
        for zone, command in commands.items():
            sensor, setpoint = self._points[zone]
            writes = [
                (point, value)
                for point, value in ((sensor, command.sensor), (setpoint, command.setpoint_c))
                if self._held.get(point.name) != value
            ]
            if writes:
                await self._command(zone, command.reason, writes, t_s)

    async def stop(self) -> None:
        """Stop the heartbeat, then release every slot the agent holds. Safe to call twice."""
        self._stopping = True
        beating, self._beating = self._beating, None
        if beating is not None:
            beating.cancel()
            await asyncio.gather(beating, return_exceptions=True)
        every = [point for pair in self._points.values() for point in pair]
        if held := [point.name for point in (*every, self._heartbeat) if point.name in self._held]:
            logger.info(f"agent: releasing {', '.join(held)}")
        for zone, pair in self._points.items():
            releases = [(point, None) for point in pair if point.name in self._held]
            if releases:
                await self._command(zone, STOPPING, releases, self._last_t_s)
        if self._heartbeat.name in self._held and await self._bacnet.write(self._heartbeat, None):
            self._held.pop(self._heartbeat.name, None)

    async def _command(
        self,
        zone: Zone,
        reason: str,
        writes: Sequence[tuple[PointSpec, Value]],
        t_s: float | None,
    ) -> None:
        """Check each point's priority array, then publish and make the writes it allows."""
        allowed: list[tuple[PointSpec, Value]] = []
        for point, value in writes:
            slots = await self._bacnet.slots(point)
            if slots is None:
                continue  # unread: tried again at the next step
            hold = _hold(slots)
            if hold is not None:
                self._back_off(zone, point, value, hold, t_s)
                continue
            self._said.pop(point.name, None)
            if value is None and not _same(slots[AGENT_PRIORITY - 1], self._held.get(point.name)):
                self._held.pop(point.name, None)  # never release a slot the agent did not write
                continue
            allowed.append((point, value))
        if not allowed:
            return
        for point, value in allowed:
            decision = Decision(
                zone=zone, point=point.name, value=value, priority=AGENT_PRIORITY, reason=reason
            )
            self._publish(EventType.DECISION, decision, t_s)
        text = explain.commanded(zone, reason, [(p.name, v) for p, v in allowed])
        self._publish(EventType.EXPLANATION, Explanation(text=text), t_s)
        for point, value in allowed:
            if not await self._bacnet.write(point, value):
                continue  # refused or unanswered: tried again at the next step
            if value is None:
                self._held.pop(point.name, None)
            else:
                self._held[point.name] = value

    def _back_off(
        self, zone: Zone, point: PointSpec, value: Value, hold: Hold, t_s: float | None
    ) -> None:
        """Leave a point a more urgent slot holds; say so once per hold and wanted value."""
        if self._said.get(point.name) == (hold, value):
            return
        self._said[point.name] = (hold, value)
        priority, held = hold
        decision = Decision(
            zone=zone,
            point=point.name,
            value=value,
            priority=AGENT_PRIORITY,
            reason=f"backed off: held at {held} by {explain.holder(priority)}",
        )
        self._publish(EventType.DECISION, decision, t_s)
        text = explain.backed_off(zone, point.name, held, priority, value, retry=not self._stopping)
        logger.info(f"agent: {text}")
        self._publish(EventType.EXPLANATION, Explanation(text=text), t_s)

    async def _beat(self) -> None:
        """Toggle the heartbeat every ``heartbeat_s`` wall seconds, On first."""
        off, on = self._heartbeat.states or ["Off", "On"]
        beat = True
        while True:
            value = on if beat else off
            if await self._bacnet.write(self._heartbeat, value):
                self._held[self._heartbeat.name] = value
            beat = not beat
            await asyncio.sleep(self._heartbeat_s)


def _hold(slots: list[Value]) -> Hold | None:
    """The most urgent slot above the agent's (1-9) that holds a value, if any."""
    for index, value in enumerate(slots[: AGENT_PRIORITY - 1]):
        if value is not None:
            return index + 1, value
    return None


def _same(slot: Value, held: Value) -> bool:
    """Whether a slot holds the value the agent wrote; a setpoint comes back as a 32-bit Real."""
    if isinstance(slot, float) and isinstance(held, float):
        return math.isclose(slot, held, abs_tol=1e-4)
    return slot == held
