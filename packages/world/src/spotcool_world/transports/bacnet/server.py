"""The BACnet/IP server code both SpotCool devices share: the BMS (``bms``) and the VRF gateway.

- **Points.** ``Point`` holds one point's state in BMS units and ``new_point`` makes it from its
  spec. ``bacnet_object`` and ``device_object`` make the BACnet objects that show the points;
  a network write to any of them goes to the device's write handler, never straight to the
  property.
- **Writes.** ``ServerApplication`` serves each write with the client's address in context, and
  at priority 16 when the request names none (ASHRAE 135 clause 19.2.1). ``screen_write``
  applies the rules both devices share: present-value only, no array index, ``CommandPolicy``.
  ``record_write`` turns a write into the ``BmsWriteData`` the dashboard shows, and logs it.
- **Sockets.** ``local_address`` resolves the address to bind, ``open_application`` binds it at
  once or raises ``OSError`` (BACpypes3 alone retries a failed bind forever), and
  ``close_application`` releases it.
"""

import asyncio
import socket
from collections.abc import Awaitable, Callable, Iterable
from contextvars import ContextVar
from dataclasses import dataclass
from typing import Any, NamedTuple, cast

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
from bacpypes3.ipv4.app import NormalApplication
from bacpypes3.local.analog import AnalogInputObject, AnalogValueObject
from bacpypes3.local.binary import BinaryInputObject, BinaryValueObject
from bacpypes3.local.cov import GenericCriteria
from bacpypes3.local.device import DeviceObject
from bacpypes3.local.multistate import MultiStateValueObject
from bacpypes3.pdu import IPv4Address
from bacpypes3.primitivedata import Null, Real, Unsigned
from loguru import logger

from spotcool_contracts.events import BmsWriteData
from spotcool_contracts.points import DevicePointMap, ObjectType, PointSpec
from spotcool_world import __version__
from spotcool_world.transports.bacnet.priority import (
    ANALOG_TYPES,
    PRIORITY_LEVELS,
    CommandPolicy,
    PointValue,
    PriorityArray,
    Rejection,
)

DEFAULT_PREFIX = 24
"""Network prefix length when the address names none; it sets the BACnet broadcast domain."""

COV_INCREMENT = 0.1
"""Change of an analog present value, in BMS units, that sends a COV notification."""

VENDOR_ID = 999
"""BACpypes3's own vendor identifier; the point maps name no vendor."""

_READY_TIMEOUT_S = 5.0

_CLIENT: ContextVar[str] = ContextVar("spotcool_bacnet_client", default="unknown")
"""``ip:port`` of the client whose request the current task serves."""

WriteHandler = Callable[[int | str, Any, int | None, int | None], Awaitable[None]]
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


def local_address(address: str | None, port: int) -> IPv4Address:
    """The BACnet/IP address to bind: ``address`` (with /24 unless it names a prefix) and ``port``.

    Args:
        address: An IPv4 address, optionally with a prefix length (``192.168.1.20/24``), or
            ``None`` for the machine's primary IPv4 address.
        port: The UDP port.

    Raises:
        ValueError: If ``address`` is not an IPv4 address.
    """
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


def endpoint(address: Any) -> str:
    """``ip:port`` of a BACpypes3 address (its own text form drops port 47808)."""
    host_port = getattr(address, "addrTuple", None)
    return f"{host_port[0]}:{host_port[1]}" if host_port else str(address)


class _Guarded:
    """Mixin: a network write to the object goes to its device, never straight to the property."""

    _on_write: WriteHandler

    async def write_property(
        self,
        attr: int | str,
        value: Any,
        index: int | None = None,
        priority: int | None = None,
    ) -> None:
        """Hand the write to the device, which applies it or raises the error that refuses it."""
        await self._on_write(attr, value, index, priority)


class _AnalogInput(_Guarded, AnalogInputObject):
    """An analog input point."""


class _AnalogValue(_Guarded, AnalogValueObject):
    """An analog value point; a commandable one carries the priority-array properties."""


class _BinaryInput(_Guarded, BinaryInputObject):
    """A binary input point."""


class _BinaryValue(_Guarded, BinaryValueObject):
    """A binary value point; a commandable one carries the priority-array properties."""


class _MultiStateValue(_Guarded, MultiStateValueObject):
    """A multi-state value point; a commandable one carries the priority-array properties."""

    # BACpypes3 gives multi-state values the analog criteria, which need a COV increment.
    _cov_criteria = GenericCriteria


class _Device(_Guarded, DeviceObject):
    """A device object; none of its properties is writable over the network."""


_OBJECT_CLASSES: dict[ObjectType, Callable[..., Any]] = {
    ObjectType.ANALOG_INPUT: _AnalogInput,
    ObjectType.ANALOG_VALUE: _AnalogValue,
    ObjectType.BINARY_INPUT: _BinaryInput,
    ObjectType.BINARY_VALUE: _BinaryValue,
    ObjectType.MULTI_STATE_VALUE: _MultiStateValue,
}


class ServerApplication(NormalApplication):
    """BACpypes3's IPv4 device application, telling the device which client sent each write.

    A write without a priority is a write at priority 16 (ASHRAE 135 clause 19.2.1). That is
    set before BACpypes3 decodes the value, which only takes a NULL (a release) with a priority.
    """

    async def do_WritePropertyRequest(self, apdu: WritePropertyRequest) -> None:
        """Serve a WriteProperty with the client's address in context."""
        request: Any = apdu  # BACpypes3's element descriptors defeat the type checker
        if request.priority is None:
            request.priority = PRIORITY_LEVELS
        token = _CLIENT.set(endpoint(apdu.pduSource))
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
        token = _CLIENT.set(endpoint(apdu.pduSource))
        try:
            await super().do_WritePropertyMultipleRequest(apdu)
        except WritePropertyMultipleError as error:
            # BACpypes3 raises this error PDU instead of answering with it.
            error.set_context(apdu)
            await self.response(error)
        finally:
            _CLIENT.reset(token)


@dataclass(eq=False)
class Point:
    """One point's state in BMS units; its BACnet object shows it while the device runs.

    Parameters:
        spec: The point.
        value: Its present value, or ``None`` while the device has none.
        array: The priority array of a commandable point, else ``None``.
        relinquish_default: The value a commandable point takes with every slot empty.
        obj: Its BACnet object while the device runs, else ``None``.
    """

    spec: PointSpec
    value: PointValue | None = None
    array: PriorityArray | None = None
    relinquish_default: PointValue | None = None
    obj: Any = None


def new_point(spec: PointSpec) -> Point:
    """A point as the map defines it: a commandable one at its relinquish default, else no value.

    A multi-state point's numeric relinquish default is its state number, 1 first, as on the
    wire; the point holds the state's text.
    """
    if not spec.is_commandable:
        return Point(spec)  # no value until the device measures, schedules or scripts it
    default = spec.relinquish_default
    if spec.object_type is ObjectType.MULTI_STATE_VALUE and isinstance(default, float | int):
        default = (spec.states or [])[int(default) - 1]
    return Point(spec, value=default, array=PriorityArray(), relinquish_default=default)


def bacnet_object(point: Point, reliability: str, on_write: WriteHandler) -> Any:
    """The BACnet object that shows a point, with the map's names, units and state texts.

    Args:
        point: The point.
        reliability: Its BACnet reliability, such as ``no-fault-detected``.
        on_write: Where a network write to the object goes.
    """
    spec = point.spec
    states = spec.states or []
    kwargs: dict[str, Any] = {
        "objectIdentifier": (spec.object_type.value, spec.instance),
        "objectName": spec.name,
        "description": spec.description,
        "eventState": "normal",
        "outOfService": False,
        "presentValue": encode(spec, point.value),
        "reliability": reliability,
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
        kwargs.update(command_properties(point))
    obj = _OBJECT_CLASSES[spec.object_type](**kwargs)
    obj._on_write = on_write
    return obj


def device_object(point_map: DevicePointMap, model_name: str, on_write: WriteHandler) -> Any:
    """The device object of a point map's device.

    Args:
        point_map: The device's point map.
        model_name: Its ``Model_Name``.
        on_write: Where a network write to it goes (every one is refused).
    """
    device = _Device(
        objectIdentifier=("device", point_map.device_instance),
        objectName=point_map.device_name,
        description=point_map.description,
        vendorName=point_map.vendor_name,
        vendorIdentifier=VENDOR_ID,
        modelName=model_name,
        firmwareRevision=__version__,
        applicationSoftwareVersion=__version__,
    )
    device._on_write = on_write
    return device


class NetworkWrite(NamedTuple):
    """One network write, as the device records it.

    Parameters:
        spec: The point written, or ``None`` for the device object.
        priority: The priority slot; 16 when the request named none.
        value: The value in BMS units, or ``None`` for NULL (a release); the written text for a
            property other than present-value.
        client: The writer's ``ip:port``.
    """

    spec: PointSpec | None
    priority: int
    value: PointValue | None
    client: str


def screen_write(
    point: Point | None,
    prop: int | str,
    value: Any,
    index: int | None,
    priority: int | None,
    policy: CommandPolicy,
) -> tuple[NetworkWrite, Rejection | None]:
    """Read a network write and apply the rules every SpotCool device shares.

    Commands write a point's present value without an array index, by ``policy``. The client
    is the one whose request the current task serves.

    Args:
        point: The point written, or ``None`` for the device object.
        prop: The property written.
        value: The value as BACpypes3 decoded it.
        index: The array index, if any.
        priority: The priority, if any.
        policy: The network command rules.

    Returns:
        The write, and why it is refused (``None`` if these rules accept it).
    """
    client = _CLIENT.get()
    level = PRIORITY_LEVELS if priority is None else priority  # no priority means 16
    prop_id = PropertyIdentifier(prop)
    if point is None or prop_id != PropertyIdentifier.presentValue:
        written = None if isinstance(value, Null) else str(value)
        write = NetworkWrite(None if point is None else point.spec, level, written, client)
        reason = f"{prop_id} is not writable; commands write present-value"
        return write, Rejection(reason, "write-access-denied")
    write = NetworkWrite(point.spec, level, decode(point.spec, value), client)
    if index is not None:
        return write, Rejection("present-value is not an array", "property-is-not-an-array")
    return write, policy.check(point.spec, write.value, level)


def in_control(point: Point, priority: int) -> str | None:
    """The note for an accepted command that a more urgent slot overrides, else ``None``."""
    assert point.array is not None  # the policy accepts commandable points only
    _, winner = point.array.effective(point.relinquish_default)
    return f"priority {winner} is in control" if winner is not None and winner < priority else None


def record_write(
    name: str, point_map: DevicePointMap, write: NetworkWrite, reason: str | None, *, accepted: bool
) -> BmsWriteData:
    """A write as the dashboard shows it; it is logged, a refusal as a warning.

    Args:
        name: The device's display name, for the log.
        point_map: The device's point map.
        write: The write.
        reason: Why it was refused, or a note on how it was applied.
        accepted: Whether it was applied.
    """
    if write.spec is None:
        object_id = f"device,{point_map.device_instance}"
        object_name = point_map.device_name
    else:
        object_id, object_name = write.spec.object_id, write.spec.name
    # BmsWriteData holds priorities 1-16 only. One outside them was refused with
    # VALUE_OUT_OF_RANGE, so it is clamped here and the reason names the real one.
    data = BmsWriteData(
        device=point_map.device_name,
        object_id=object_id,
        object_name=object_name,
        priority=min(max(write.priority, 1), PRIORITY_LEVELS),
        value=write.value,
        client=write.client,
        accepted=accepted,
        reason=reason,
    )
    outcome = "accepted" if accepted else "rejected"
    logger.log(
        "INFO" if accepted else "WARNING",
        f"{name}: {write.client} wrote {object_name}={write.value} at priority {write.priority}: "
        f"{outcome}{f' ({reason})' if reason else ''}",
    )
    return data


async def open_application(
    device: Any, objects: Iterable[Any], local: IPv4Address
) -> ServerApplication:
    """Serve a device and its objects on ``local``, once the socket is bound.

    Args:
        device: The device object.
        objects: Its other objects.
        local: The address to bind.

    Raises:
        OSError: If the address cannot be bound (the port is in use, or the address is not
            this machine's).
    """
    with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as probe:
        probe.bind(local.addrTuple)  # fail now: BACpypes3 retries a failed bind forever
    app = ServerApplication(device, local)
    try:
        for obj in objects:
            app.add_object(obj)
        # BACpypes3 binds in tasks of its own: serve once they are done.
        bound = asyncio.gather(*app.normal.server._transport_tasks)
        await asyncio.wait_for(bound, _READY_TIMEOUT_S)
    except TimeoutError as exc:
        await close_application(app)
        raise OSError(f"could not bind UDP {endpoint(local)}") from exc
    except BaseException:
        await close_application(app)
        raise
    return app


async def close_application(app: ServerApplication) -> None:
    """Stop serving and release the socket."""
    app.close()
    await asyncio.sleep(0)  # a closed transport releases its socket on the next iteration


def encode(spec: PointSpec, value: PointValue | None) -> Any:
    """A BMS value as the BACnet present value; a placeholder until the value is known."""
    if spec.object_type in ANALOG_TYPES:
        return Real(value if isinstance(value, float | int) else 0.0)
    states = spec.states or []
    index = states.index(value) if value in states else 0
    if spec.object_type is ObjectType.MULTI_STATE_VALUE:
        return Unsigned(index + 1)
    return BinaryPV(index)


def decode(spec: PointSpec, value: Any) -> PointValue | None:
    """A decoded BACnet present value in BMS units; ``None`` for NULL."""
    if isinstance(value, Null):
        return None
    if spec.object_type in ANALOG_TYPES:
        return float(value)
    states = spec.states or []
    index = int(value) - (1 if spec.object_type is ObjectType.MULTI_STATE_VALUE else 0)
    return states[index] if 0 <= index < len(states) else str(int(value))  # policy rejects it


def command_properties(point: Point) -> dict[str, Any]:
    """The priority-array properties of a commandable point's BACnet object."""
    assert point.array is not None
    spec = point.spec
    _, winner = point.array.effective(point.relinquish_default)
    return {
        "priorityArray": [
            PriorityValue(null=()) if slot is None else PriorityValue(encode(spec, slot))
            for slot in point.array.slots
        ],
        "relinquishDefault": encode(spec, point.relinquish_default),
        "currentCommandPriority": (
            OptionalUnsigned(null=()) if winner is None else OptionalUnsigned(unsigned=winner)
        ),
    }
