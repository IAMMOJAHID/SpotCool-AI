"""BacnetBmsTransport: the office's virtual BMS, a BACnet/IP device built from its point map.

The device is ``spotcool_contracts.load_point_map("office_bms")`` and nothing else: one BACnet
object per point, with the map's names, units and state texts. Commandable points carry a real
16-slot priority array, a relinquish default and a current command priority, and every object
answers COV subscriptions. A BACnet client (YABE, or the agent) sees what it would see on a
real building network.

- ``input()``, ahead of the plants: on each ``TickFrame`` it runs the BMS's own logic
  (occupied-standby, the heartbeat watchdog), pushes a ``BmsWriteFrame`` for every network
  command since the last tick, then the SpotCool run's ``SetpointCommandFrame`` for all ten
  managed inputs, then the tick.
- ``output()``, after the plants: shows the SpotCool run's measurements on the points (in BMS
  units), takes the building schedule from the baseline run's measurements, and the West door
  contact from the scenario.

Network commands arrive on the same asyncio loop, between ticks, and change the priority
arrays at once; ``input()`` reads that state at tick time, so the lockstep stays deterministic.
"""

import asyncio
import errno
import functools
import socket
import time
from collections.abc import Callable
from contextvars import ContextVar
from dataclasses import dataclass
from typing import Any, cast

from bacpypes3.apdu import (
    WritePropertyMultipleError,
    WritePropertyMultipleRequest,
    WritePropertyRequest,
)
from bacpypes3.basetypes import (
    BinaryPV,
    EngineeringUnits,
    OptionalUnsigned,
    PriorityValue,
    PropertyIdentifier,
)
from bacpypes3.errors import PropertyError
from bacpypes3.ipv4.app import NormalApplication
from bacpypes3.local.analog import AnalogInputObject, AnalogValueObject
from bacpypes3.local.binary import BinaryInputObject, BinaryValueObject
from bacpypes3.local.cov import GenericCriteria
from bacpypes3.local.device import DeviceObject
from bacpypes3.local.multistate import MultiStateValueObject
from bacpypes3.pdu import IPv4Address
from bacpypes3.primitivedata import Null, Real, Unsigned
from loguru import logger

from spotcool_contracts import Zone, load_point_map
from spotcool_contracts.events import BmsWriteData
from spotcool_contracts.points import Conversion, ObjectType, PointSpec
from spotcool_contracts.priorities import AGENT_PRIORITY
from spotcool_world import __version__
from spotcool_world.adapters.units import to_bms, to_boptest
from spotcool_world.frames import (
    BmsWriteFrame,
    EndFrame,
    Frame,
    PlantMeasurementsFrame,
    ScenarioStateFrame,
    SetpointCommandFrame,
    StartFrame,
    TickFrame,
)
from spotcool_world.processors.frame_processor import FrameDirection, FrameProcessor
from spotcool_world.processors.telemetry import EnergyMeter
from spotcool_world.services.boptest.catalog import MANAGED_INPUTS, load_catalog, zone_damper
from spotcool_world.transports.bacnet.bms_logic import (
    OCCUPIED_HOURS,
    HeartbeatWatchdog,
    OccupancyMode,
    ZoneStandby,
)
from spotcool_world.transports.bacnet.priority import (
    ANALOG_TYPES,
    PRIORITY_LEVELS,
    CommandPolicy,
    PointValue,
    PriorityArray,
)
from spotcool_world.transports.base_transport import BaseTransport

DEFAULT_PREFIX = 24
"""Network prefix length when the address names none; it sets the BACnet broadcast domain."""

COV_INCREMENT = 0.1
"""Change of an analog present value, in BMS units, that sends a COV notification."""

WATCHDOG_PERIOD_S = 1.0
"""Wall seconds between heartbeat checks while the device runs; every tick also checks."""

VENDOR_ID = 999
"""BACpypes3's own vendor identifier; the point map names no vendor."""

_READY_TIMEOUT_S = 5.0

_CLIENT: ContextVar[str] = ContextVar("spotcool_bacnet_client", default="unknown")
"""``ip:port`` of the client whose request the current task serves."""

WriteHandler = Callable[[int | str, Any, int | None, int | None], None]
"""Applies one network write, ``(property, decoded value, array index, priority)``, or raises
the BACnet error that refuses it."""


def primary_ipv4() -> str:
    """The machine's primary IPv4 address (its default route's), or 127.0.0.1 if it has none."""
    with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as probe:
        try:
            probe.connect(("10.255.255.255", 1))  # a UDP connect sends nothing; it picks a route
            return str(probe.getsockname()[0])
        except OSError:
            return "127.0.0.1"


def is_local_ipv4(ip: str) -> bool:
    """Whether ``ip`` is still one of this machine's addresses: a socket can bind it."""
    with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as probe:
        try:
            probe.bind((ip, 0))
        except OSError as exc:
            return exc.errno != errno.EADDRNOTAVAIL  # any other failure says nothing of it
    return True


class _Guarded:
    """Mixin: a network write to the object goes to the BMS, never straight to the property."""

    _on_write: WriteHandler

    async def write_property(
        self,
        attr: int | str,
        value: Any,
        index: int | None = None,
        priority: int | None = None,
    ) -> None:
        """Hand the write to the BMS, which applies it or raises the error that refuses it."""
        self._on_write(attr, value, index, priority)


class _AnalogInput(_Guarded, AnalogInputObject):
    """An analog input point."""


class _AnalogValue(_Guarded, AnalogValueObject):
    """An analog value point; a commandable one carries the priority-array properties."""


class _BinaryInput(_Guarded, BinaryInputObject):
    """A binary input point."""


class _BinaryValue(_Guarded, BinaryValueObject):
    """A binary value point; a commandable one carries the priority-array properties."""


class _MultiStateValue(_Guarded, MultiStateValueObject):
    """A multi-state value point."""

    # BACpypes3 gives multi-state values the analog criteria, which need a COV increment.
    _cov_criteria = GenericCriteria


class _Device(_Guarded, DeviceObject):
    """The BMS's device object; none of its properties is writable over the network."""


_OBJECT_CLASSES: dict[ObjectType, Callable[..., Any]] = {
    ObjectType.ANALOG_INPUT: _AnalogInput,
    ObjectType.ANALOG_VALUE: _AnalogValue,
    ObjectType.BINARY_INPUT: _BinaryInput,
    ObjectType.BINARY_VALUE: _BinaryValue,
    ObjectType.MULTI_STATE_VALUE: _MultiStateValue,
}


class _BmsApplication(NormalApplication):
    """BACpypes3's IPv4 device application, telling the BMS which client sent each write.

    A write without a priority is a write at priority 16 (ASHRAE 135 clause 19.2.1). That is
    set before BACpypes3 decodes the value, which only takes a NULL (a release) with a priority.
    """

    async def do_WritePropertyRequest(self, apdu: WritePropertyRequest) -> None:
        """Serve a WriteProperty with the client's address in context."""
        request: Any = apdu  # BACpypes3's element descriptors defeat the type checker
        if request.priority is None:
            request.priority = PRIORITY_LEVELS
        token = _CLIENT.set(_endpoint(apdu.pduSource))
        try:
            await super().do_WritePropertyRequest(apdu)
        finally:
            _CLIENT.reset(token)

    async def do_WritePropertyMultipleRequest(self, apdu: WritePropertyMultipleRequest) -> None:
        """Serve a WritePropertyMultiple with the client's address in context."""
        request: Any = apdu
        for spec in request.listOfWriteAccessSpecs:
            for prop in spec.listOfProperties:
                if prop.priority is None:
                    prop.priority = PRIORITY_LEVELS
        token = _CLIENT.set(_endpoint(apdu.pduSource))
        try:
            await super().do_WritePropertyMultipleRequest(apdu)
        except WritePropertyMultipleError as error:
            # BACpypes3 raises this error PDU instead of answering with it.
            error.set_context(apdu)
            await self.response(error)
        finally:
            _CLIENT.reset(token)


@dataclass(eq=False)
class _Point:
    """One point's state in BMS units; its BACnet object shows it while the device runs."""

    spec: PointSpec
    value: PointValue | None = None
    array: PriorityArray | None = None
    relinquish_default: PointValue | None = None
    obj: Any = None


@dataclass(eq=False)
class _Read:
    """A point the SpotCool run's measurements update."""

    point: _Point
    key: str
    conversion: Conversion


@dataclass(eq=False)
class _Zone:
    """A zone's points, its occupied-standby state and its BOPTEST signals."""

    standby: ZoneStandby
    temp: _Point
    cooling: _Point
    heating: _Point
    sensor: _Point
    mode: _Point
    cooling_input: str
    schedule_key: str
    conversion: Conversion
    damper_input: str
    schedule_c: float | None = None


class BacnetBmsTransport(BaseTransport):
    """The office's virtual BMS on BACnet/IP (see the module docstring).

    ``start()`` binds the UDP socket and serves the device in a background task, which also
    checks the heartbeat every ``WATCHDOG_PERIOD_S``; ``stop()`` cancels the task and closes the
    socket. ``input()`` and ``output()`` work without ``start()``, only without a network.
    While it runs, each tick checks that the bound address is still one of the PC's; the first
    time it is not (the PC changed networks, so nobody can reach the BMS), ``input()`` reports a
    non-fatal error.

    Network commands are WriteProperty or WritePropertyMultiple of a present value; they follow
    ``CommandPolicy``, and writes to any other property are refused. Every command, accepted or
    rejected, and every release the BMS makes itself, becomes a ``BmsWriteFrame`` stamped with
    the BMS's sim time: that of the last measurement (the run's start time before the first).
    A value BACpypes3 cannot decode as the property's datatype is refused by the stack itself,
    before the BMS sees it.

    A point with no value yet (before the run's first measurements, the schedule or the
    scenario's door) shows a placeholder with reliability COMMUNICATION_FAILURE, so its status
    flags read fault, as a real BMS shows a point it cannot read; its first value clears both.
    Each ``StartFrame`` begins a new run: the measurements, the energy meter, the schedule and
    the zones' standby state are forgotten, while the network's commands (the priority arrays)
    and the heartbeat watchdog carry on.

    The BMS's logic (``bms_logic``) drives the SpotCool run only. The baseline run is the same
    building without occupancy sensors, so it never gets a command.

    Args:
        address: IPv4 address to bind, optionally with a prefix length that sets the broadcast
            domain (``192.168.1.20/24``); the prefix is /24 when it names none. Defaults to the
            machine's primary IPv4 address.
        port: UDP port. Defaults to the point map's, 47808.
        policy: The network command rules.
        clock: Wall clock in seconds for the heartbeat watchdog; inject a fake one in tests.
        has_address: Whether an IPv4 address is still this machine's, checked on each tick
            while the device runs; inject a fake one in tests.
        occupied_hours: Hours of the day, ``[start, end)``, the building schedule can be
            occupied; BOPTEST's office by default.
        name: Display name.
    """

    def __init__(
        self,
        *,
        address: str | None = None,
        port: int | None = None,
        policy: CommandPolicy | None = None,
        clock: Callable[[], float] = time.perf_counter,
        has_address: Callable[[str], bool] = is_local_ipv4,
        occupied_hours: tuple[float, float] = OCCUPIED_HOURS,
        name: str | None = None,
    ) -> None:
        super().__init__(name=name)
        self._map = load_point_map("office_bms")
        self._address = address
        self._port = self._map.udp_port if port is None else port
        self._policy = policy or CommandPolicy()
        self._watchdog = HeartbeatWatchdog(clock=clock)
        self._has_address = has_address
        self._address_lost = False  # reported once per start()
        self._occupied_hours = occupied_hours
        self._points = {spec.name: _new_point(spec) for spec in self._map.points}
        self._zones = {zone: self._new_zone(zone) for zone in Zone}
        self._reads = [
            _Read(point, point.spec.boptest.read, point.spec.boptest.conversion)
            for point in self._points.values()
            if point.array is None and point.spec.boptest and point.spec.boptest.read
        ]
        meters = set(load_catalog().electric_power)
        self._meters = [read.point for read in self._reads if read.key in meters]
        self._total = self._points["HVAC-TOTAL-PWR"]
        self._energy = self._points["HVAC-ENERGY"]
        self._sim_clock = self._points["SIM-CLOCK"]
        self._door = self._points["WES-ENTRANCE-DOOR"]
        self._heartbeat = self._points["SPOTCOOL-HEARTBEAT"]
        self._meter = EnergyMeter()  # HVAC-ENERGY: each step's mean power times the step
        self._now_s: float | None = None
        self._writes: list[BmsWriteFrame] = []
        self._app: _BmsApplication | None = None
        self._bound: str | None = None
        self._input = _BmsInput(self, name=f"{self.name}::input")
        self._output = _BmsOutput(self, name=f"{self.name}::output")

    @property
    def address(self) -> str | None:
        """The bound ``ip:port`` while the device runs, else ``None``."""
        return self._bound

    def input(self) -> FrameProcessor:
        """The processor ahead of the plants: BMS logic, write frames, setpoint commands."""
        return self._input

    def output(self) -> FrameProcessor:
        """The processor after the plants: measurements, schedule and door onto the points."""
        return self._output

    async def start(self) -> None:
        """Bind the device's UDP socket and serve it until ``stop()``. Does nothing if started.

        Raises:
            OSError: If the address cannot be bound (the port is in use, or the address is not
                this machine's).
            ValueError: If ``address`` is not an IPv4 address.
        """
        if self._app is not None:
            return
        local = _local_address(self._address, self._port)
        with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as probe:
            probe.bind(local.addrTuple)  # fail now: BACpypes3 retries a failed bind forever
        self._app = _BmsApplication(self._device_object(), local)
        try:
            for point in self._points.values():
                point.obj = self._bacnet_object(point)
                self._app.add_object(point.obj)
            # BACpypes3 binds in tasks of its own: serve once they are done.
            bound = asyncio.gather(*self._app.normal.server._transport_tasks)
            await asyncio.wait_for(bound, _READY_TIMEOUT_S)
        except TimeoutError as exc:
            await self._close()
            raise OSError(f"could not bind UDP {_endpoint(local)}") from exc
        except BaseException:
            await self._close()
            raise
        self._bound, self._address_lost = _endpoint(local), False
        self.create_task(self._serve(), "bacnet-device")
        logger.info(
            f"{self.name}: BACnet device {self._map.device_name} ({self._map.device_instance}),"
            f" {len(self._points)} objects, on {self._bound} (/{local.network.prefixlen})"
        )

    async def stop(self) -> None:
        """Stop serving: cancel the device task and close the socket. Safe to call twice."""
        await self.cancel_tasks()
        await self._close()  # the task closes it too, unless cancelled before it ever ran

    async def _serve(self) -> None:
        try:
            while True:
                await asyncio.sleep(WATCHDOG_PERIOD_S)
                self._check_heartbeat()
        finally:
            await self._close()

    async def _close(self) -> None:
        app, self._app, self._bound = self._app, None, None
        if app is None:
            return
        app.close()
        for point in self._points.values():
            point.obj = None
        await asyncio.sleep(0)  # a closed transport releases its socket on the next iteration
        logger.info(f"{self.name}: BACnet device closed")

    def _new_zone(self, zone: Zone) -> _Zone:
        prefix = f"ZN-{zone.value.upper()}-"
        cooling = self._points[f"{prefix}CLG-SP"]
        binding = cooling.spec.boptest
        if binding is None or binding.write is None or binding.relinquish_from_baseline is None:
            raise KeyError(f"{cooling.spec.name} must write BOPTEST and follow the baseline")
        return _Zone(
            standby=ZoneStandby(self._occupied_hours),
            temp=self._points[f"{prefix}T"],
            cooling=cooling,
            heating=self._points[f"{prefix}HTG-SP"],
            sensor=self._points[f"{prefix}OCC-SENSOR"],
            mode=self._points[f"{prefix}OCC-MODE"],
            cooling_input=binding.write,
            schedule_key=binding.relinquish_from_baseline,
            conversion=binding.conversion,
            damper_input=zone_damper(zone),
        )

    def _device_object(self) -> Any:
        device = _Device(
            objectIdentifier=("device", self._map.device_instance),
            objectName=self._map.device_name,
            description=self._map.description,
            vendorName=self._map.vendor_name,
            vendorIdentifier=VENDOR_ID,
            modelName="SpotCool virtual BMS",
            firmwareRevision=__version__,
            applicationSoftwareVersion=__version__,
        )
        device._on_write = functools.partial(self._network_write, None)
        return device

    def _bacnet_object(self, point: _Point) -> Any:
        spec = point.spec
        states = spec.states or []
        kwargs: dict[str, Any] = {
            "objectIdentifier": (spec.object_type.value, spec.instance),
            "objectName": spec.name,
            "description": spec.description,
            "eventState": "normal",
            "outOfService": False,
            "presentValue": _encode(spec, point.value),
            "reliability": _reliability(point.value),
        }
        if spec.object_type in ANALOG_TYPES:
            kwargs["units"] = EngineeringUnits(spec.units or "no-units")
            kwargs["covIncrement"] = COV_INCREMENT
            limits = {
                "minPresValue": spec.min_value,
                "maxPresValue": spec.max_value,
                "resolution": spec.resolution,
            }
            kwargs.update({prop: limit for prop, limit in limits.items() if limit is not None})
        elif spec.object_type is ObjectType.MULTI_STATE_VALUE:
            kwargs.update(numberOfStates=len(states), stateText=states)
        else:
            kwargs.update(inactiveText=states[0], activeText=states[1])
            if spec.object_type is ObjectType.BINARY_INPUT:
                kwargs["polarity"] = "normal"
        if point.array is not None:
            kwargs.update(_command_properties(point))
        obj = _OBJECT_CLASSES[spec.object_type](**kwargs)
        obj._on_write = functools.partial(self._network_write, point)
        return obj

    def _network_write(
        self,
        point: _Point | None,
        prop: int | str,
        value: Any,
        index: int | None,
        priority: int | None,
    ) -> None:
        """Apply one network write to a point (``None``: the device object), or refuse it."""
        client = _CLIENT.get()
        level = PRIORITY_LEVELS if priority is None else priority  # no priority means 16
        prop_id = PropertyIdentifier(prop)
        if point is None or prop_id != PropertyIdentifier.presentValue:
            written = None if isinstance(value, Null) else str(value)
            reason = f"{prop_id} is not writable; commands write present-value"
            self._record(None if point is None else point.spec, level, written, client, reason)
            raise PropertyError("write-access-denied")
        spec = point.spec
        command = _decode(spec, value)
        if index is not None:
            self._record(spec, level, command, client, "present-value is not an array")
            raise PropertyError("property-is-not-an-array")
        rejection = self._policy.check(spec, command, level)
        if rejection is not None:
            self._record(spec, level, command, client, rejection.reason)
            raise PropertyError(rejection.error_code)
        self._apply(point, command, level)
        if point is self._heartbeat:
            self._watchdog.beat(point.value)
        assert point.array is not None  # the policy accepts commandable points only
        _, winner = point.array.effective(point.relinquish_default)
        note = f"priority {winner} is in control" if winner is not None and winner < level else None
        self._record(spec, level, command, client, note, accepted=True)

    def _apply(self, point: _Point, command: PointValue | None, priority: int) -> None:
        assert point.array is not None
        if command is None:
            point.array.release(priority)
        else:
            point.array.write(command, priority)
        self._refresh(point)
        zone = point.spec.zone
        if zone is not None and point is self._zones[zone].sensor:
            self._zones[zone].standby.sensor(point.value == OccupancyMode.OCCUPIED, self._now_s)
            self._show_zone(zone)

    def _record(
        self,
        spec: PointSpec | None,
        priority: int,
        value: PointValue | None,
        client: str,
        reason: str | None,
        *,
        accepted: bool = False,
    ) -> None:
        if spec is None:
            object_id, object_name = f"device,{self._map.device_instance}", self._map.device_name
        else:
            object_id, object_name = spec.object_id, spec.name
        # BmsWriteData holds priorities 1-16 only. One outside them was refused with
        # VALUE_OUT_OF_RANGE, so it is clamped here and the reason names the real one.
        data = BmsWriteData(
            device=self._map.device_name,
            object_id=object_id,
            object_name=object_name,
            priority=min(max(priority, 1), PRIORITY_LEVELS),
            value=value,
            client=client,
            accepted=accepted,
            reason=reason,
        )
        self._writes.append(BmsWriteFrame(data=data, sim_time_s=self._now_s))
        outcome = "accepted" if accepted else "rejected"
        logger.log(
            "INFO" if accepted else "WARNING",
            f"{self.name}: {client} wrote {object_name}={value} at priority {priority}: "
            f"{outcome}{f' ({reason})' if reason else ''}",
        )

    def _check_heartbeat(self) -> None:
        """Release every priority-10 command once the agent's heartbeat has stopped."""
        if not self._watchdog.expired():
            return
        logger.warning(
            f"{self.name}: {self._heartbeat.spec.name} stopped changing: releasing every "
            f"priority-{AGENT_PRIORITY} command"
        )
        client = self._bound or self._map.device_name
        for point in self._points.values():
            if point.array is not None and point.array.slots[AGENT_PRIORITY - 1] is not None:
                self._apply(point, None, AGENT_PRIORITY)
                self._record(
                    point.spec, AGENT_PRIORITY, None, client, "heartbeat timeout", accepted=True
                )

    def _lost_address(self) -> str | None:
        """The error to report the first time the bound address is no longer this PC's."""
        if self._bound is None or self._address_lost:
            return None
        if self._has_address(self._bound.rpartition(":")[0]):
            return None
        self._address_lost = True
        return (
            f"the PC's network changed: the BMS at {self._bound} is unreachable; restart the "
            "run on one network"
        )

    def _tick(self, tick: TickFrame) -> list[Frame]:
        """Run the BMS logic for a tick; return the frames to push ahead of it."""
        now_s = tick.sim_time_s
        assert now_s is not None  # a TickFrame always has one
        self._check_heartbeat()
        values: dict[str, float | None] = {}
        for zone, z in self._zones.items():
            z.standby.tick(now_s)
            self._show_zone(zone)
            assert z.cooling.array is not None
            cooling, priority = z.cooling.array.effective(z.cooling.relinquish_default)
            values[z.cooling_input] = values[z.damper_input] = None
            if not isinstance(cooling, float):  # no schedule yet and nothing commanded
                continue
            # Release the input for the plain schedule only: BOPTEST applies that itself.
            if priority is not None or z.standby.standby:
                values[z.cooling_input] = to_boptest(cooling, z.conversion)
            temp, heating = z.temp.value, z.heating.value
            if isinstance(temp, float) and isinstance(heating, float):
                values[z.damper_input] = z.standby.damper(temp, heating, cooling)
        overrides = {name: values[name] for name in MANAGED_INPUTS}
        command = SetpointCommandFrame(run="spotcool", overrides=overrides, sim_time_s=now_s)
        return [*self._take_writes(), command]

    def _take_writes(self) -> list[BmsWriteFrame]:
        writes, self._writes = self._writes, []
        return writes

    def _measured(self, frame: PlantMeasurementsFrame) -> None:
        """Show one run's measurements: the points (SpotCool) or the schedule (baseline)."""
        t_s = frame.sim_time_s
        if t_s is not None:
            self._now_s = t_s
        if frame.run == "baseline":
            for zone, z in self._zones.items():
                raw = frame.raw.get(z.schedule_key)
                if raw is not None:
                    z.schedule_c = to_bms(float(raw), z.conversion)
                    z.standby.schedule(z.schedule_c, t_s)
                    self._show_zone(zone)
            return
        for read in self._reads:
            raw = frame.raw.get(read.key)
            if raw is not None:
                self._show(read.point, to_bms(float(raw), read.conversion))
        total_kw = sum(p.value for p in self._meters if isinstance(p.value, float))
        self._show(self._total, total_kw)
        if t_s is not None:
            self._show(self._energy, self._meter.add(t_s, total_kw))
            self._show(self._sim_clock, t_s)

    def _scenario(self, frame: ScenarioStateFrame) -> None:
        """Show the West zone's door contact."""
        states = self._door.spec.states or []
        for scene in frame.state.zones:
            if scene.zone is self._door.spec.zone and scene.door_open is not None:
                self._show(self._door, states[1] if scene.door_open else states[0])

    def _started(self, frame: StartFrame) -> None:
        """Begin a run: forget the last one's measurements, energy, schedule and zone states."""
        self._now_s = frame.start_time_s
        self._meter = EnergyMeter()
        unknown = (self._total, self._energy, self._sim_clock, self._door)
        for point in (*(read.point for read in self._reads), *unknown):
            self._show(point, None)
        for zone, z in self._zones.items():
            z.schedule_c = None
            z.standby = ZoneStandby(self._occupied_hours)
            z.standby.sensor(z.sensor.value == OccupancyMode.OCCUPIED, self._now_s)
            z.cooling.relinquish_default = None
            self._refresh(z.cooling)
            self._show_zone(zone)

    def _show(self, point: _Point, value: PointValue | None) -> None:
        point.value = value
        if point.obj is not None:
            point.obj.reliability = _reliability(value)
            point.obj.presentValue = _encode(point.spec, value)

    def _refresh(self, point: _Point) -> None:
        """Recompute a commandable point's present value; show it and its priority array."""
        assert point.array is not None
        if point.obj is not None:
            for prop, value in _command_properties(point).items():
                setattr(point.obj, prop, value)
        self._show(point, point.array.effective(point.relinquish_default)[0])

    def _show_zone(self, zone: Zone) -> None:
        """Show a zone's occupancy mode and its cooling setpoint's relinquish default."""
        z = self._zones[zone]
        if z.schedule_c is None:  # the mode is unknown until the schedule is
            self._show(z.mode, None)
            return
        z.cooling.relinquish_default = z.standby.cooling_setpoint(z.schedule_c)
        self._refresh(z.cooling)
        self._show(z.mode, z.standby.mode.value)


class _BmsInput(FrameProcessor):
    """``BacnetBmsTransport.input()``: the network's commands into the pipeline, every tick.

    The first tick that finds the BMS's bound address gone from the PC (its network changed)
    reports a non-fatal error.

    Args:
        bms: The transport.
        name: Display name.
    """

    def __init__(self, bms: BacnetBmsTransport, *, name: str) -> None:
        super().__init__(name=name)
        self._bms = bms

    async def process_frame(self, frame: Frame, direction: FrameDirection) -> None:
        """Push write frames and the setpoint command ahead of each tick; pass every frame on."""
        await super().process_frame(frame, direction)
        if direction is FrameDirection.DOWNSTREAM:
            if isinstance(frame, StartFrame):
                self._bms._started(frame)
            elif isinstance(frame, TickFrame):
                if (lost := self._bms._lost_address()) is not None:
                    await self.push_error(lost)  # never fatal: the plants and the record go on
                for derived in self._bms._tick(frame):
                    await self.push_frame(derived)
            elif isinstance(frame, EndFrame):
                for write in self._bms._take_writes():
                    await self.push_frame(write)
        await self.push_frame(frame, direction)


class _BmsOutput(FrameProcessor):
    """``BacnetBmsTransport.output()``: the pipeline's state onto the BACnet points.

    Args:
        bms: The transport.
        name: Display name.
    """

    def __init__(self, bms: BacnetBmsTransport, *, name: str) -> None:
        super().__init__(name=name)
        self._bms = bms

    async def process_frame(self, frame: Frame, direction: FrameDirection) -> None:
        """Show measurements and the scenario's door contact; pass every frame on."""
        await super().process_frame(frame, direction)
        if direction is FrameDirection.DOWNSTREAM:
            if isinstance(frame, PlantMeasurementsFrame):
                self._bms._measured(frame)
            elif isinstance(frame, ScenarioStateFrame):
                self._bms._scenario(frame)
        await self.push_frame(frame, direction)


def _new_point(spec: PointSpec) -> _Point:
    if not spec.is_commandable:
        return _Point(spec)  # no value until the run measures, schedules or scripts it
    default = spec.relinquish_default
    return _Point(spec, value=default, array=PriorityArray(), relinquish_default=default)


def _reliability(value: PointValue | None) -> str:
    """BACnet's reliability for a point: a value the BMS has not got is a communication failure."""
    return "communication-failure" if value is None else "no-fault-detected"


def _endpoint(address: Any) -> str:
    """``ip:port`` of a BACpypes3 address (its own text form drops port 47808)."""
    host_port = getattr(address, "addrTuple", None)
    return f"{host_port[0]}:{host_port[1]}" if host_port else str(address)


def _local_address(address: str | None, port: int) -> IPv4Address:
    host = address or primary_ipv4()
    if "/" not in host:
        host = f"{host}/{DEFAULT_PREFIX}"
    try:
        return cast(IPv4Address, IPv4Address(f"{host}:{port}"))  # Address.__new__ dispatches
    except ValueError as exc:
        raise ValueError(
            f"invalid BMS address {address!r}: expected an IPv4 address such as 192.168.1.20 "
            "or 192.168.1.20/24"
        ) from exc


def _encode(spec: PointSpec, value: PointValue | None) -> Any:
    """A BMS value as the BACnet present value; a placeholder until the value is known."""
    if spec.object_type in ANALOG_TYPES:
        return Real(value if isinstance(value, float | int) else 0.0)
    states = spec.states or []
    index = states.index(value) if value in states else 0
    if spec.object_type is ObjectType.MULTI_STATE_VALUE:
        return Unsigned(index + 1)
    return BinaryPV(index)


def _decode(spec: PointSpec, value: Any) -> PointValue | None:
    """A decoded BACnet present value in BMS units; ``None`` for NULL."""
    if isinstance(value, Null):
        return None
    if spec.object_type in ANALOG_TYPES:
        return float(value)
    states = spec.states or []
    index = int(value) - (1 if spec.object_type is ObjectType.MULTI_STATE_VALUE else 0)
    return states[index] if 0 <= index < len(states) else str(int(value))  # policy rejects it


def _command_properties(point: _Point) -> dict[str, Any]:
    """The priority-array properties of a commandable point's BACnet object."""
    assert point.array is not None
    spec = point.spec
    _, winner = point.array.effective(point.relinquish_default)
    return {
        "priorityArray": [
            PriorityValue(null=()) if slot is None else PriorityValue(_encode(spec, slot))
            for slot in point.array.slots
        ],
        "relinquishDefault": _encode(spec, point.relinquish_default),
        "currentCommandPriority": (
            OptionalUnsigned(null=()) if winner is None else OptionalUnsigned(unsigned=winner)
        ),
    }
