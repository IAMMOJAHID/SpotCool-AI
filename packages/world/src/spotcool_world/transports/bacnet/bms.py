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
from dataclasses import dataclass
from typing import Any

from bacpypes3.errors import PropertyError
from loguru import logger

from spotcool_contracts import Zone, load_point_map
from spotcool_contracts.points import Conversion
from spotcool_contracts.priorities import AGENT_PRIORITY
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
from spotcool_world.transports.bacnet.priority import CommandPolicy, PointValue
from spotcool_world.transports.bacnet.server import (
    NetworkWrite,
    Point,
    ServerApplication,
    bacnet_object,
    close_application,
    command_properties,
    device_object,
    encode,
    endpoint,
    in_control,
    local_address,
    new_point,
    open_application,
    record_write,
    screen_write,
)
from spotcool_world.transports.base_transport import BaseTransport

WATCHDOG_PERIOD_S = 1.0
"""Wall seconds between heartbeat checks while the device runs; every tick also checks."""

ADDRESS_LOST_TICKS = 2
"""Ticks in a row without its bound address before the BMS reports it lost; one is a blip."""


def is_local_ipv4(ip: str) -> bool:
    """Whether ``ip`` is still one of this machine's addresses: a socket can bind it."""
    with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as probe:
        try:
            probe.bind((ip, 0))
        except OSError as exc:
            return exc.errno != errno.EADDRNOTAVAIL  # any other failure says nothing of it
    return True


@dataclass(eq=False)
class _Read:
    """A point the SpotCool run's measurements update."""

    point: Point
    key: str
    conversion: Conversion


@dataclass(eq=False)
class _Zone:
    """A zone's points, its occupied-standby state and its BOPTEST signals."""

    standby: ZoneStandby
    temp: Point
    cooling: Point
    heating: Point
    sensor: Point
    mode: Point
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
    While it runs, each tick checks that the bound address is still one of the PC's. When it has
    been gone for ``ADDRESS_LOST_TICKS`` ticks in a row (the PC changed networks, so nobody can
    reach the BMS), ``input()`` reports a non-fatal error, once until the address is back.

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
        self._ticks_without_address = 0
        self._occupied_hours = occupied_hours
        self._points = {spec.name: new_point(spec) for spec in self._map.points}
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
        self._app: ServerApplication | None = None
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
        local = local_address(self._address, self._port)
        for point in self._points.values():
            on_write = functools.partial(self._network_write, point)
            point.obj = bacnet_object(point, _reliability(point.value), on_write)
        objects = [point.obj for point in self._points.values()]
        try:
            self._app = await open_application(self._device_object(), objects, local)
        except BaseException:
            for point in self._points.values():
                point.obj = None
            raise
        self._bound, self._ticks_without_address = endpoint(local), 0
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
        for point in self._points.values():
            point.obj = None
        await close_application(app)
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
        on_write = functools.partial(self._network_write, None)
        return device_object(self._map, "SpotCool virtual BMS", on_write)

    async def _network_write(
        self,
        point: Point | None,
        prop: int | str,
        value: Any,
        index: int | None,
        priority: int | None,
    ) -> None:
        """Apply one network write to a point (``None``: the device object), or refuse it."""
        write, rejection = screen_write(point, prop, value, index, priority, self._policy)
        if rejection is not None:
            self._record(write, rejection.reason)
            raise PropertyError(rejection.error_code)
        assert point is not None  # the device object refuses every write
        self._apply(point, write.value, write.priority)
        if point is self._heartbeat:
            self._watchdog.beat(point.value)
        self._record(write, in_control(point, write.priority), accepted=True)

    def _apply(self, point: Point, command: PointValue | None, priority: int) -> None:
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

    def _record(self, write: NetworkWrite, reason: str | None, *, accepted: bool = False) -> None:
        data = record_write(self.name, self._map, write, reason, accepted=accepted)
        self._writes.append(BmsWriteFrame(data=data, sim_time_s=self._now_s))

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
                write = NetworkWrite(point.spec, AGENT_PRIORITY, None, client)
                self._record(write, "heartbeat timeout", accepted=True)

    def _lost_address(self) -> str | None:
        """Check the bound address; return the error to report when it has just been lost.

        It is lost after ``ADDRESS_LOST_TICKS`` ticks in a row without it, and reported once
        until it is back (logged), so a reconnect to the same network clears it.
        """
        if self._bound is None:
            return None
        ip = self._bound.rpartition(":")[0]
        if self._has_address(ip):
            if self._ticks_without_address >= ADDRESS_LOST_TICKS:
                logger.info(f"{self.name}: the PC's address {ip} is back")
            self._ticks_without_address = 0
            return None
        self._ticks_without_address += 1
        if self._ticks_without_address != ADDRESS_LOST_TICKS:
            return None
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

    def _show(self, point: Point, value: PointValue | None) -> None:
        point.value = value
        if point.obj is not None:
            point.obj.reliability = _reliability(value)
            point.obj.presentValue = encode(point.spec, value)

    def _refresh(self, point: Point) -> None:
        """Recompute a commandable point's present value; show it and its priority array."""
        assert point.array is not None
        if point.obj is not None:
            for prop, value in command_properties(point).items():
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

    A tick that finds the BMS's bound address gone from the PC for ``ADDRESS_LOST_TICKS`` ticks
    in a row (its network changed) reports a non-fatal error.

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


def _reliability(value: PointValue | None) -> str:
    """BACnet's reliability for a point: a value the BMS has not got is a communication failure."""
    return "communication-failure" if value is None else "no-fault-detected"
