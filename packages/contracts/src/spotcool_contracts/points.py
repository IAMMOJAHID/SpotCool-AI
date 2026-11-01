"""BACnet point maps served by the world and read or written by the agent.

A point map lists every BACnet object a simulated device exposes, the engineering units on the
wire, who may write it, its Haystack tags and Brick class, and where its value comes from in
the building simulator. The world builds its BACnet devices from these maps and the agent
builds its client-side point table from the same files, so the two never drift apart.
"""

from enum import StrEnum
from functools import cache
from importlib import resources
from typing import Literal

import yaml
from pydantic import BaseModel, ConfigDict, Field, model_validator

from spotcool_contracts.zones import Zone


class ObjectType(StrEnum):
    """BACnet object types used by SpotCool devices (ASN.1 names)."""

    ANALOG_INPUT = "analog-input"
    ANALOG_VALUE = "analog-value"
    BINARY_INPUT = "binary-input"
    BINARY_VALUE = "binary-value"
    MULTI_STATE_VALUE = "multi-state-value"


class Writability(StrEnum):
    """Who may change a point's present value."""

    READ_ONLY = "read-only"
    COMMANDABLE = "commandable"
    """Present value backed by a 16-slot priority array with a relinquish default."""


class Conversion(StrEnum):
    """Unit conversion between the simulator's value and the BACnet value."""

    NONE = "none"
    KELVIN_TO_CELSIUS = "K->degC"
    WATT_TO_KILOWATT = "W->kW"
    M3S_TO_LPS = "m3/s->L/s"
    FRACTION_TO_PERCENT = "1->%"


PointSource = Literal["boptest", "scenario", "bms", "agent", "derived", "device"]
"""Where a point's value originates: the plant simulator, the scenario script, BMS logic,
an agent command, a calculation over other points, or a simulated device model."""


class BoptestBinding(BaseModel):
    """How a point maps onto BOPTEST signals.

    Parameters:
        read: BOPTEST output read to update the point (SpotCool run).
        write: BOPTEST input that receives the point's effective value (SpotCool run).
            When no priority slot is active the input is released (``_activate = 0``),
            which hands control back to BOPTEST's own controller.
        relinquish_from_baseline: BOPTEST output of the baseline run used as the point's
            relinquish default, i.e. the value the building's own schedule would apply.
        conversion: Unit conversion from BOPTEST units to BACnet units.
    """

    model_config = ConfigDict(extra="forbid")

    read: str | None = None
    write: str | None = None
    relinquish_from_baseline: str | None = None
    conversion: Conversion = Conversion.NONE


class PointSpec(BaseModel):
    """One BACnet object on a SpotCool device.

    Parameters:
        object_type: BACnet object type.
        instance: Object instance number, unique per type within the device.
        name: BACnet ``Object_Name``, unique within the device.
        description: Plain-language meaning of the point.
        units: BACnet engineering units (ASN.1 name, e.g. ``degrees-celsius``).
        writability: Read-only, or commandable through the priority array.
        zone: Thermal zone the point belongs to, if any.
        states: State texts. Multi-state values list states 1..N in order; binary
            objects list ``[inactive_text, active_text]``.
        min_value: Lowest value a client may command (analog only).
        max_value: Highest value a client may command (analog only).
        resolution: Grid the commanded value must sit on (e.g. 0.5 for setpoints).
        relinquish_default: Static relinquish default, when not taken from the baseline run.
        haystack: Project Haystack marker tags.
        brick: Brick Schema class name.
        source: Where the value originates.
        boptest: Mapping onto BOPTEST signals when ``source`` is ``boptest``.
    """

    model_config = ConfigDict(extra="forbid")

    object_type: ObjectType
    instance: int = Field(ge=0, le=4_194_302)
    name: str = Field(min_length=1, max_length=64)
    description: str
    units: str | None = None
    writability: Writability = Writability.READ_ONLY
    zone: Zone | None = None
    states: list[str] | None = None
    min_value: float | None = None
    max_value: float | None = None
    resolution: float | None = None
    relinquish_default: float | str | None = None
    haystack: list[str] = Field(default_factory=list)
    brick: str | None = None
    source: PointSource = "boptest"
    boptest: BoptestBinding | None = None

    @property
    def object_id(self) -> str:
        """Object identifier in ``type,instance`` form."""
        return f"{self.object_type.value},{self.instance}"

    @property
    def is_commandable(self) -> bool:
        """Whether clients command this point through the priority array."""
        return self.writability is Writability.COMMANDABLE

    @model_validator(mode="after")
    def _check_consistency(self) -> "PointSpec":
        binary = self.object_type in (ObjectType.BINARY_INPUT, ObjectType.BINARY_VALUE)
        if self.object_type is ObjectType.MULTI_STATE_VALUE and not self.states:
            raise ValueError(f"{self.name}: multi-state points need state texts")
        if binary and (self.states is None or len(self.states) != 2):
            raise ValueError(f"{self.name}: binary points need [inactive_text, active_text]")
        if self.is_commandable and self.object_type in (
            ObjectType.ANALOG_INPUT,
            ObjectType.BINARY_INPUT,
        ):
            raise ValueError(f"{self.name}: input objects cannot be commandable")
        if self.source == "boptest" and self.boptest is None:
            raise ValueError(f"{self.name}: source 'boptest' needs a boptest binding")
        if self.boptest is not None and self.boptest.write and not self.is_commandable:
            raise ValueError(f"{self.name}: only commandable points may write to BOPTEST")
        if (
            self.min_value is not None
            and self.max_value is not None
            and self.min_value > self.max_value
        ):
            raise ValueError(f"{self.name}: min_value is above max_value")
        return self


class DevicePointMap(BaseModel):
    """Every point one simulated BACnet device exposes.

    Parameters:
        device_name: BACnet ``Object_Name`` of the device object.
        device_instance: BACnet device instance number.
        udp_port: BACnet/IP UDP port the device listens on.
        vendor_name: Value of the device's ``Vendor_Name`` property.
        description: What the device represents.
        points: The device's objects.
    """

    model_config = ConfigDict(extra="forbid")

    device_name: str
    device_instance: int = Field(ge=0, le=4_194_302)
    udp_port: int = Field(ge=1, le=65_535)
    vendor_name: str
    description: str
    points: list[PointSpec]

    @model_validator(mode="after")
    def _check_unique(self) -> "DevicePointMap":
        ids = [p.object_id for p in self.points]
        names = [p.name for p in self.points]
        dup_ids = {i for i in ids if ids.count(i) > 1}
        dup_names = {n for n in names if names.count(n) > 1}
        if dup_ids or dup_names:
            raise ValueError(f"duplicate object ids {dup_ids or ''} names {dup_names or ''}")
        return self

    def by_name(self, name: str) -> PointSpec:
        """Return the point with the given object name.

        Args:
            name: BACnet object name.

        Raises:
            KeyError: If the device has no such point.
        """
        for point in self.points:
            if point.name == name:
                return point
        raise KeyError(name)

    def zone_points(self, zone: Zone) -> list[PointSpec]:
        """Return every point that belongs to a zone, in map order."""
        return [p for p in self.points if p.zone is zone]

    def commandable(self) -> list[PointSpec]:
        """Return every commandable point, in map order."""
        return [p for p in self.points if p.is_commandable]


PointMapName = Literal["office_bms", "vrf_gateway"]


@cache
def load_point_map(name: PointMapName) -> DevicePointMap:
    """Load one of the bundled point maps.

    Args:
        name: ``office_bms`` for the building's BMS, ``vrf_gateway`` for the meeting-room
            VRF indoor-unit gateway.

    Returns:
        The validated point map. The result is cached and shared; do not mutate it.
    """
    text = (
        resources.files("spotcool_contracts")
        .joinpath("points", f"{name}.yaml")
        .read_text(encoding="utf-8")
    )
    return DevicePointMap.model_validate(yaml.safe_load(text))
