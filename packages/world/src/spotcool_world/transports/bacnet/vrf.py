"""BacnetVrfGateway: the meeting-room VRF indoor unit's BACnet gateway, VRF-Gateway-MeetingRoom.

The device is ``spotcool_contracts.load_point_map("vrf_gateway")`` (device 2001, UDP 47810),
behaving like the brand's real gateway that ``settings.vrf_profile`` names
(``load_vrf_profiles()``): Mitsubishi, Daikin or LG. It lives in wall time, outside the
simulation pipeline. A network write takes effect on the ``VrfUnit`` at once; ``VrfWorker``
moves the unit on at 20 Hz and refreshes the analog inputs (``advance``).

- **Commands** follow the BMS's rules (``CommandPolicy``: priorities 8-16, the agent at 10, the
  operator at 8 wins, NULL releases, values off range or off the grid refused, never clamped),
  then the brand's (``brand_refusal``): a write to a louver axis the brand does not expose is
  refused with write-access-denied, a louver state or fan speed it lacks with
  value-out-of-range. A release is always accepted. A louver whose state in force the brand
  lacks (LG's relinquish default, Pos3) holds its angle with swing off.
- **Inputs.** ``IDU-ROOM-T``, ``IDU-PWR`` and the louver angles show the unit. The angle of an
  axis the brand does not expose reads Reliability no-sensor (its status flags show fault).
- **Every write**, accepted or refused, is published at once as a ``bms_write`` event on
  ``EVENTS_TOPIC`` (``frame_events`` of a ``BmsWriteFrame``), stamped with the shared
  ``SimClock``'s time, so the recorder and the dashboard get it as they get the BMS's.

Everything runs on the event loop: no threads.
"""

import functools
from typing import Any

from bacpypes3.errors import PropertyError
from loguru import logger

from spotcool_contracts import load_point_map
from spotcool_contracts.points import PointSpec
from spotcool_contracts.vrf import VrfProfile
from spotcool_world.bus import Bus
from spotcool_world.clocks import SimClock
from spotcool_world.devices.vrf.model import SWING, VrfUnit
from spotcool_world.frames import BmsWriteFrame
from spotcool_world.processors.event_emitter import EVENTS_TOPIC, frame_events
from spotcool_world.transports.bacnet.priority import CommandPolicy, PointValue, Rejection
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
from spotcool_world.utils.base_object import BaseObject

INPUTS = ("IDU-ROOM-T", "IDU-PWR", "IDU-VANE-V-ANGLE", "IDU-VANE-H-ANGLE")
"""The analog inputs that show the unit, refreshed by ``advance``."""


def brand_refusal(
    profile: VrfProfile, spec: PointSpec, value: PointValue | None
) -> Rejection | None:
    """Why the brand's gateway refuses a command ``CommandPolicy`` accepted, or ``None``.

    Args:
        profile: The brand's gateway profile.
        spec: The point commanded.
        value: The value in BMS units, or ``None`` for a release, which is always accepted.
    """
    if value is None:
        return None
    brand = profile.label.split()[0]  # every label starts with the brand's name
    if spec.name == "IDU-FAN" and value not in profile.fan_speeds:
        return Rejection(f"{brand}'s gateway has no fan speed {value}", "value-out-of-range")
    louvers = {"IDU-VANE-V": profile.vane_vertical, "IDU-VANE-H": profile.vane_horizontal}
    if spec.name not in louvers:
        return None
    axis, label = louvers[spec.name], "vertical" if spec.name == "IDU-VANE-V" else "horizontal"
    if axis is None:
        return Rejection(f"{brand}'s gateway exposes no {label} louver", "write-access-denied")
    states = [f"Pos{n}" for n in range(1, axis.positions + 1)] + ([SWING] if axis.swing else [])
    if value not in states:
        return Rejection(
            f"{brand}'s gateway has no {label} louver state {value} ({states})",
            "value-out-of-range",
        )
    return None


class BacnetVrfGateway(BaseObject):
    """The meeting-room VRF gateway on BACnet/IP (see the module docstring).

    ``start()`` binds the UDP socket and serves the device; ``stop()`` closes it. It is not a
    ``BaseTransport``: it has no place in the pipeline. ``unit`` and ``advance`` work without
    ``start()``, only without a network.

    Args:
        profile: The brand's gateway profile.
        bus: Where every write is published, on ``EVENTS_TOPIC``.
        clock: The simulation clock whose time stamps the writes.
        address: IPv4 address to bind, as the BMS takes it (``192.168.1.20`` or
            ``192.168.1.20/24``). Defaults to the machine's primary IPv4 address.
        port: UDP port. Defaults to the point map's, 47810.
        policy: The network command rules.
        name: Display name.
    """

    def __init__(
        self,
        profile: VrfProfile,
        bus: Bus,
        clock: SimClock,
        *,
        address: str | None = None,
        port: int | None = None,
        policy: CommandPolicy | None = None,
        name: str | None = None,
    ) -> None:
        super().__init__(name=name)
        self._map = load_point_map("vrf_gateway")
        self._profile = profile
        self._bus = bus
        self._clock = clock
        self._address = address
        self._port = self._map.udp_port if port is None else port
        self._policy = policy or CommandPolicy()
        self._points = {spec.name: new_point(spec) for spec in self._map.points}
        self.unit = VrfUnit(profile)
        """The indoor unit the gateway commands."""
        for point in self._points.values():
            if point.array is not None:
                self._command(point)
        self._show_inputs()
        self._app: ServerApplication | None = None
        self._bound: str | None = None

    @property
    def address(self) -> str | None:
        """The bound ``ip:port`` while the device runs, else ``None``."""
        return self._bound

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
        # The objects are attached before the bind, so a write served while it completes shows.
        for point in self._points.values():
            on_write = functools.partial(self._network_write, point)
            point.obj = bacnet_object(point, _reliability(point.value), on_write)
        objects = [point.obj for point in self._points.values()]
        model = f"SpotCool simulated VRF gateway ({self._profile.label})"
        device = device_object(self._map, model, functools.partial(self._network_write, None))
        try:
            self._app = await open_application(device, objects, local)
        except BaseException:
            for point in self._points.values():
                point.obj = None
            raise
        self._bound = endpoint(local)
        logger.info(
            f"{self.name}: BACnet device {self._map.device_name} ({self._map.device_instance}),"
            f" {self._profile.vendor} profile, {len(objects)} objects, on {self._bound}"
            f" (/{local.network.prefixlen})"
        )

    async def stop(self) -> None:
        """Stop serving and close the socket. Safe to call twice."""
        app, self._app, self._bound = self._app, None, None
        if app is None:
            return
        for point in self._points.values():
            point.obj = None
        await close_application(app)
        logger.info(f"{self.name}: BACnet device closed")

    def advance(self, dt_s: float) -> None:
        """Move the unit on by ``dt_s`` wall seconds and show it on the analog inputs.

        Args:
            dt_s: Wall seconds since the last call (``VrfUnit.advance`` clips it).
        """
        self.unit.advance(dt_s)
        self._show_inputs()

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
        if rejection is None and write.spec is not None:
            rejection = brand_refusal(self._profile, write.spec, write.value)
        if rejection is not None:
            await self._record(write, rejection.reason, accepted=False)
            raise PropertyError(rejection.error_code)
        assert point is not None and point.array is not None  # the policy's rules
        if write.value is None:
            point.array.release(write.priority)
        else:
            point.array.write(write.value, write.priority)
        if point.obj is not None:
            for prop_name, prop_value in command_properties(point).items():
                setattr(point.obj, prop_name, prop_value)
        self._show(point, point.array.effective(point.relinquish_default)[0])
        self._command(point)
        await self._record(write, in_control(point, write.priority), accepted=True)

    async def _record(self, write: NetworkWrite, reason: str | None, *, accepted: bool) -> None:
        """Publish a write as a ``bms_write`` event, stamped with the simulation's time."""
        data = record_write(self.name, self._map, write, reason, accepted=accepted)
        for envelope in frame_events(BmsWriteFrame(data=data, sim_time_s=self._clock.now_s)):
            await self._bus.publish(EVENTS_TOPIC, envelope)

    def _command(self, point: Point) -> None:
        """Give the unit a commandable point's value in force."""
        value, unit = point.value, self.unit
        match point.spec.name:
            case "IDU-ONOFF":
                unit.power_on = value == (point.spec.states or [])[-1]  # the active text, On
            case "IDU-SP":
                assert isinstance(value, float)
                unit.setpoint_c = value
            case "IDU-MODE":
                unit.mode = str(value)
            case "IDU-FAN":
                unit.fan = str(value)
            case "IDU-VANE-V":
                unit.vane_v = str(value)
            case "IDU-VANE-H":
                unit.vane_h = str(value)

    def _show_inputs(self) -> None:
        unit = self.unit
        values = (unit.room_c, unit.power_kw, unit.vane_v_angle_deg, unit.vane_h_angle_deg)
        for name, value in zip(INPUTS, values, strict=True):
            self._show(self._points[name], value)

    def _show(self, point: Point, value: PointValue | None) -> None:
        point.value = value
        if point.obj is not None:
            point.obj.reliability = _reliability(value)
            point.obj.presentValue = encode(point.spec, value)


def _reliability(value: PointValue | None) -> str:
    """BACnet's reliability for a point: only the angle of an axis the brand lacks has no value."""
    return "no-sensor" if value is None else "no-fault-detected"
