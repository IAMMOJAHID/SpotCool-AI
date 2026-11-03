"""The agent's BACnet/IP side: ``BacnetClient`` (reads and commands) and ``StepClock``.

``BacnetClient`` is a BACpypes3 device of its own (``SpotCool-Agent``, instance
``DEVICE_INSTANCE``) on its own UDP port, which addresses one device directly by ``ip:port``:
no Who-Is, so it works on the same PC as the world. It reads present values and priority
arrays, writes at priority 10 and releases with NULL. Each request waits ``APDU_TIMEOUT_MS``
for its answer and is resent ``APDU_RETRIES`` time, so one the device never answers costs about
2 s. The helpers that turn BACnet values into BMS units are the stub agent's, copied.

Requests go one at a time: with BACpypes3 0.0.110, several requests in flight while a device
does not answer yet (the agent starts before the world) leave the client's socket deaf for
good, to every device. So each device has a client of its own, with its own socket: the BMS
(``SpotCool-Agent``, 1910, UDP 47820) and the VRF gateway (``SpotCool-Agent-VRF``, 1911, UDP
47821), and neither waits for the other.

``StepClock`` polls the BMS's ``SIM-CLOCK`` (AV 950) at 4 Hz. Each change of its value is the
end of a simulation step: the measurements on the points, and the scene on the cameras, now
describe the step that just ended. The exception is a run's initial state, which the BMS shows
with ``HVAC-ENERGY`` at exactly 0 before the first step.
"""

import asyncio
import math
import statistics
import time
from collections import deque
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from typing import Any

from bacpypes3.apdu import ErrorRejectAbortNack
from bacpypes3.ipv4.app import NormalApplication
from bacpypes3.local.device import DeviceObject
from bacpypes3.pdu import Address, IPv4Address
from bacpypes3.primitivedata import Null
from loguru import logger

from spotcool_agent.domain.events import Value
from spotcool_contracts import load_point_map
from spotcool_contracts.points import ObjectType, PointSpec
from spotcool_contracts.priorities import AGENT_PRIORITY

DEVICE_INSTANCE = 1910
"""The agent's BACnet device instance (the BMS is 1001, the VRF gateway 2001, the stub 1900)."""
DEVICE_NAME = "SpotCool-Agent"
"""The agent's BACnet device name, as a BACnet explorer lists it."""
DEFAULT_PORT = 47820
"""The agent's UDP port: clear of the BMS (47808) and the VRF gateway (47810) on the same PC."""
VRF_DEVICE_INSTANCE = 1911
"""The device instance of the agent's client of the VRF gateway."""
VRF_DEVICE_NAME = "SpotCool-Agent-VRF"
VRF_PORT = 47821
"""The UDP port of the agent's client of the VRF gateway."""
VENDOR_ID = 999
"""BACpypes3's own vendor identifier."""
APDU_TIMEOUT_MS = 1000
"""How long each request waits for its answer: sized for loopback or a LAN."""
APDU_RETRIES = 1
"""Resends of an unanswered request, so one costs about 2 s, not BACpypes3's 12."""
POLL_S = 0.25
"""Wall seconds between two reads of ``SIM-CLOCK`` (4 Hz)."""
DEFAULT_STEP_S = 900.0
"""Simulation seconds per step until two consecutive ``SIM-CLOCK`` values give it."""
DEFAULT_STEP_WALL_S = 15.0
"""Wall seconds per step until the clock has measured it (speed 60)."""
WALL_SAMPLES = 5
"""Step intervals the wall-time estimate is the median of."""
SILENT_WARN_S = 10.0
"""Wall seconds without an answer to ``SIM-CLOCK`` before a warning (once, until it answers)."""
MIN_WALL_SAMPLES = 3
"""Intervals the estimate needs: one can be short, when a reading came late (the agent started
before the BMS, so its first reads waited for answers that never came)."""


class NoAnswer(Exception):
    """The BMS did not answer a request (no device there, or it refused it)."""


class BacnetClient:
    """The agent's BACnet/IP client (see the module docstring).

    ``open()`` binds the UDP socket inside the running event loop; ``close()`` releases it.

    Args:
        device: The device's ``ip:port``: the BMS, or the VRF gateway.
        local: The ``ip:port`` to bind; port 0 takes any free one.
        instance: The client's own device instance.
        name: The client's own device name.
    """

    def __init__(
        self,
        device: str,
        *,
        local: str,
        instance: int = DEVICE_INSTANCE,
        name: str = DEVICE_NAME,
    ) -> None:
        self.device = device
        self._address = Address(device)
        self._local = local
        self._instance = instance
        self._name = name
        self._app: NormalApplication | None = None
        self._one_at_a_time = asyncio.Lock()
        points = load_point_map("office_bms")
        self._clock = points.by_name("SIM-CLOCK")
        self._energy = points.by_name("HVAC-ENERGY")

    @property
    def app(self) -> NormalApplication:
        """The open BACpypes3 application.

        Raises:
            RuntimeError: Before ``open()``.
        """
        if self._app is None:
            raise RuntimeError("the BACnet client is not open")
        return self._app

    async def open(self) -> None:
        """Bind the client's UDP socket. Does nothing if open.

        Raises:
            OSError: If the address cannot be bound, such as a port another program holds.
        """
        if self._app is not None:
            return
        device = DeviceObject(
            objectIdentifier=("device", self._instance),
            objectName=self._name,
            vendorIdentifier=VENDOR_ID,
            apduTimeout=APDU_TIMEOUT_MS,
            numberOfApduRetries=APDU_RETRIES,
        )
        app = NormalApplication(device, IPv4Address(self._local))
        try:  # BACpypes3 binds in tasks of its own; a taken port fails them, not the constructor
            await asyncio.gather(*app.normal.server._transport_tasks)
        except BaseException:
            app.close()
            raise
        self._app = app
        logger.info(f"BACnet client {self._name} ({self._instance}) on {self._local}")

    async def close(self) -> None:
        """Close the socket. Safe to call twice."""
        app, self._app = self._app, None
        if app is not None:
            app.close()
            await asyncio.sleep(0)  # a closed transport releases its socket on the next turn

    async def read(self, point: PointSpec, prop: str = "present-value") -> Any:
        """One property of a point, as BACpypes3 decodes it.

        Raises:
            ErrorRejectAbortNack: If the device refused or never answered (a ``BaseException``).
        """
        return await self.read_object(point.object_id, prop)

    async def read_object(self, object_id: str, prop: str) -> Any:
        """One property of any object, such as ``device,2001``'s ``model-name``.

        Raises:
            ErrorRejectAbortNack: If the device refused or never answered (a ``BaseException``).
        """
        async with self._one_at_a_time:
            return await self.app.read_property(self._address, object_id, prop)

    async def present(self, point: PointSpec) -> Value:
        """A point's present value in BMS units, or ``None`` without an answer."""
        try:
            return present_value(point, await self.read(point))
        except ErrorRejectAbortNack:  # a BaseException in BACpypes3
            return None
        except (RuntimeError, TypeError, ValueError):  # not open yet, or a value of another kind
            return None

    async def model_name(self, instance: int) -> str | None:
        """The device's ``Model_Name``, or ``None`` without an answer.

        Args:
            instance: The device's instance, such as the VRF gateway's 2001.
        """
        try:
            name = await self.read_object(f"device,{instance}", "model-name")
        except ErrorRejectAbortNack:
            return None
        except RuntimeError:  # not open yet
            return None
        return str(name)

    async def clock(self) -> "ClockReading | None":
        """``SIM-CLOCK``'s value and whether it is a run's initial state, or ``None``.

        ``None`` covers a fault in its status flags (the BMS has no measurement yet) and a value
        that is not finite. ``HVAC-ENERGY`` reads 0 exactly at a run's initial state: it counts
        each step's energy from there.

        Raises:
            NoAnswer: If the BMS did not answer.
        """
        try:  # one after the other: the first without an answer ends the reading
            flags = await self.read(self._clock, "status-flags")
            value = await self.read(self._clock)
            energy = await self.read(self._energy)
        except ErrorRejectAbortNack as exc:
            raise NoAnswer(f"{self.device}: {exc}") from None
        if flags[1] or not math.isfinite(float(value)):  # status flags: in-alarm, fault, ...
            return None
        return ClockReading(float(value), initial=float(energy) == 0.0)

    async def slots(self, point: PointSpec) -> list[Value] | None:
        """A point's 16 priority slots in BMS units, or ``None`` if the device gave no answer."""
        try:
            array = await self.read(point, "priority-array")
        except ErrorRejectAbortNack as exc:  # a BaseException in BACpypes3
            logger.warning(f"reading {point.name}'s priority array failed: {exc}")
            return None
        return [_slot_value(point, slot) for slot in array]

    async def write(self, point: PointSpec, value: Value) -> bool:
        """Write a point at priority 10: a state or a setpoint; ``None`` releases it.

        Returns:
            Whether the device took it. A refusal or no answer is logged.
        """
        if value is None:
            present: Any = Null(())
        elif isinstance(value, str):
            present = _state(point, value)
        else:
            present = value  # BACpypes3 makes it the point's Real
        try:
            async with self._one_at_a_time:
                await self.app.write_property(
                    self._address,
                    point.object_id,
                    "present-value",
                    present,
                    priority=AGENT_PRIORITY,
                )
        except ErrorRejectAbortNack as exc:  # a BaseException in BACpypes3
            logger.warning(f"writing {point.name} = {value} failed: {exc}")
            return False
        return True


def _slot_value(point: PointSpec, slot: Any) -> Value:
    """A BACnet ``PriorityValue`` in BMS units: a setpoint, a state text, or ``None`` for NULL."""
    if slot.null is not None:
        return None
    if slot.real is not None:
        return float(slot.real)
    states = point.states or []
    if slot.unsigned is not None:  # a multi-state point: its states are numbered from 1
        number = int(slot.unsigned)
        return states[number - 1] if 1 <= number <= len(states) else str(number)
    index = int(slot.enumerated)
    return states[index] if 0 <= index < len(states) else str(index)


def _state(point: PointSpec, state: str) -> str | int:
    """A state text as its BACnet present value: ``active`` or ``inactive``, or a number.

    A binary point's state is ``inactive`` or ``active``; a multi-state one's, its number from 1.
    """
    index = (point.states or []).index(state)
    if point.object_type is ObjectType.MULTI_STATE_VALUE:
        return index + 1
    return "active" if index == 1 else "inactive"


def present_value(point: PointSpec, value: Any) -> Value:
    """A present value as BACpypes3 decodes it, in BMS units: a state text or a number."""
    states = point.states or []
    if point.object_type is ObjectType.MULTI_STATE_VALUE:
        number = int(value)
        return states[number - 1] if 1 <= number <= len(states) else str(number)
    if states:  # a binary point: BACpypes3 gives "active" or "inactive"
        return states[1] if str(value) == "active" else states[0]
    return float(value)


@dataclass(frozen=True)
class ClockReading:
    """One reading of ``SIM-CLOCK``.

    Parameters:
        sim_s: Its value, in simulation seconds.
        initial: Whether the BMS shows a run's initial state: no step has ended yet.
    """

    sim_s: float
    initial: bool = False


@dataclass(frozen=True)
class Step:
    """The end of a simulation step, as ``SIM-CLOCK`` showed it.

    Parameters:
        end_s: ``SIM-CLOCK``'s new value: the step's end, and the start of the step the
            agent's writes take effect in.
        start_s: The step's start: the stamp of what the agent perceives and decides on it.
        seen_at: When the change was seen (``time.perf_counter``).
        run: Which run it belongs to: the count of new runs the clock has seen.
    """

    end_s: float
    start_s: float
    seen_at: float
    run: int = 0


class StepClock:
    """Turns ``SIM-CLOCK`` changes into ``Step``s (see the module docstring).

    ``run()`` polls until cancelled. A reading without a value (a fault, or no answer) is
    ignored; ``SILENT_WARN_S`` without an answer is logged once, as a warning. A run's initial
    state is no step's end: the cameras show no step of it yet, so it only starts the count.
    ``SIM-CLOCK`` going backwards, or an initial state after steps were seen, is a new run: the
    count of runs (``Step.run``) goes up and the measurements start afresh. The step's simulation length (``step_s``) is the smallest gap between
    two values seen; its wall length (``step_wall_s``) is the median of the last
    ``WALL_SAMPLES`` intervals between changes, from ``MIN_WALL_SAMPLES`` of them on, leaving
    out the one from a run's initial state (the first step is not paced).

    Args:
        read: Reads ``SIM-CLOCK`` (``BacnetClient.clock``).
        poll_s: Wall seconds between reads.
        clock: Wall clock in seconds; tests inject one.
    """

    def __init__(
        self,
        read: Callable[[], Awaitable[ClockReading | None]],  # may raise NoAnswer
        *,
        poll_s: float = POLL_S,
        clock: Callable[[], float] = time.perf_counter,
    ) -> None:
        self._read = read
        self._poll_s = poll_s
        self._clock = clock
        self._changed = asyncio.Event()
        self._latest: Step | None = None
        self._handed: Step | None = None
        self._last: tuple[ClockReading, float] | None = None  # the last value, when first seen
        self._measured_step_s: float | None = None
        self._walls: deque[float] = deque(maxlen=WALL_SAMPLES)
        self._run = 0

    @property
    def latest(self) -> Step | None:
        """The newest step seen."""
        return self._latest

    @property
    def step_s(self) -> float:
        """Simulation seconds per step: the smallest gap seen, or ``DEFAULT_STEP_S``."""
        return self._measured_step_s or DEFAULT_STEP_S

    @property
    def step_wall_s(self) -> float:
        """Wall seconds per step: the last intervals' median, from ``MIN_WALL_SAMPLES`` of them.

        ``DEFAULT_STEP_WALL_S`` before that.
        """
        if len(self._walls) < MIN_WALL_SAMPLES:
            return DEFAULT_STEP_WALL_S
        return statistics.median(self._walls)

    async def run(self) -> None:
        """Poll ``SIM-CLOCK`` every ``poll_s`` until cancelled."""
        silent_since: float | None = None
        warned = False
        while True:
            try:
                reading = await self._read()
            except NoAnswer as exc:
                now = self._clock()
                silent_since = now if silent_since is None else silent_since
                if not warned and now - silent_since >= SILENT_WARN_S:
                    logger.warning(
                        f"SIM-CLOCK unanswered for {SILENT_WARN_S:g} s ({exc}): is the BMS up, "
                        "and its address right (--world-host, --bms-port)?"
                    )
                    warned = True
                reading = None
            else:
                if warned:
                    logger.info("SIM-CLOCK answers again")
                silent_since, warned = None, False
            self.observe(reading)
            await asyncio.sleep(self._poll_s)

    def observe(self, reading: ClockReading | None) -> None:
        """Take one reading of ``SIM-CLOCK`` (``run()`` calls it; tests call it directly).

        Args:
            reading: The reading, or ``None`` when ``SIM-CLOCK`` had no value.
        """
        if reading is None:
            return
        value = float(round(reading.sim_s))  # a 32-bit Real: whole seconds on the step grid
        reading = ClockReading(value, reading.initial)
        last, now = self._last, self._clock()
        if last is not None and last[0].sim_s == value:
            return
        self._last = (reading, now)
        if last is not None and (value < last[0].sim_s or reading.initial and self._latest):
            self._run += 1  # a new run: SIM-CLOCK went back, or a run's initial state is shown
            self._measured_step_s = None
            self._walls.clear()
            logger.info(f"SIM-CLOCK: new run detected (run {self._run})")
        elif last is not None and value > last[0].sim_s:
            gap = value - last[0].sim_s  # one step, or more if a value was missed
            self._measured_step_s = min(gap, self._measured_step_s or gap)
            if not last[0].initial:  # the run's first step is not paced
                self._walls.append((now - last[1]) / max(1, round(gap / self.step_s)))
        if reading.initial:
            return
        start = value - self.step_s
        self._latest = Step(end_s=value, start_s=start, seen_at=now, run=self._run)
        self._changed.set()

    async def next_step(self) -> Step:
        """Wait for a step newer than the one this returned last, and return the newest."""
        while self._latest is None or self._latest is self._handed:
            self._changed.clear()
            await self._changed.wait()
        self._handed = self._latest
        return self._latest
