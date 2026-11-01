import json
from importlib import resources

import pytest
from pydantic import ValidationError

from spotcool_contracts import ZONES, Zone, load_point_map, load_vrf_profiles
from spotcool_contracts.cameras import CameraCatalog, CameraInfo, CameraSource
from spotcool_contracts.events import (
    WORLD_PAYLOADS,
    EventEnvelope,
    EventSource,
    EventType,
    TickData,
    parse_world_payload,
)
from spotcool_contracts.points import (
    Conversion,
    DevicePointMap,
    ObjectType,
    PointSpec,
    Writability,
)
from spotcool_contracts.priorities import AGENT_PRIORITY, OPERATOR_PRIORITY, SCHEDULE_PRIORITY

CATALOG = json.loads(
    resources.files("spotcool_contracts")
    .joinpath("data", "boptest_multizone_office_simple_air.json")
    .read_text(encoding="utf-8")
)
INPUTS = {p["name"] for p in CATALOG["points"] if p["causality"] == "input"}
OUTPUTS = {p["name"] for p in CATALOG["points"] if p["causality"] == "output"}
UNITS = {p["name"]: p["unit"] for p in CATALOG["points"]}
SOURCE_UNIT = {
    Conversion.KELVIN_TO_CELSIUS: "K",
    Conversion.WATT_TO_KILOWATT: "W",
    Conversion.M3S_TO_LPS: "m3/s",
    Conversion.FRACTION_TO_PERCENT: "1",
}
AGENT_EVENT_TYPES = {
    EventType.PERCEPTION,
    EventType.DECISION,
    EventType.EXPLANATION,
    EventType.CHAT,
}


@pytest.fixture(scope="module")
def bms() -> DevicePointMap:
    return load_point_map("office_bms")


def test_office_bms_shape(bms: DevicePointMap) -> None:
    assert bms.device_instance == 1001
    assert bms.udp_port == 47808
    assert len(bms.points) == 51


@pytest.mark.parametrize("zone", list(Zone))
def test_every_zone_has_the_full_point_set(bms: DevicePointMap, zone: Zone) -> None:
    names = {p.name for p in bms.zone_points(zone)}
    up = zone.value.upper()
    for suffix in ("T", "CLG-SP", "HTG-SP", "CO2", "SA-FLOW", "OCC-MODE", "OCC-SENSOR"):
        assert f"ZN-{up}-{suffix}" in names


def test_boptest_bindings_exist_in_the_catalog(bms: DevicePointMap) -> None:
    for point in bms.points:
        if point.boptest is None:
            continue
        if point.boptest.read:
            assert point.boptest.read in OUTPUTS, point.name
        if point.boptest.relinquish_from_baseline:
            assert point.boptest.relinquish_from_baseline in OUTPUTS, point.name
        if point.boptest.write:
            assert point.boptest.write in INPUTS, point.name


def test_conversions_match_catalog_units(bms: DevicePointMap) -> None:
    for point in bms.points:
        binding = point.boptest
        if binding is None or binding.read is None:
            continue
        if binding.conversion is Conversion.NONE:
            assert UNITS[binding.read] in ("ppm", "1", ""), point.name
        else:
            assert UNITS[binding.read] == SOURCE_UNIT[binding.conversion], point.name


def test_only_zone_cooling_setpoints_drive_the_plant(bms: DevicePointMap) -> None:
    writers = sorted(p.name for p in bms.points if p.boptest and p.boptest.write)
    assert writers == sorted(f"ZN-{z.value.upper()}-CLG-SP" for z in Zone)


def test_commandable_analogs_are_bounded(bms: DevicePointMap) -> None:
    for point in bms.commandable():
        if point.object_type is ObjectType.ANALOG_VALUE:
            assert point.min_value is not None and point.max_value is not None
            assert point.resolution == 0.5


def test_fail_to_comfort_defaults(bms: DevicePointMap) -> None:
    for zone in Zone:
        sensor = bms.by_name(f"ZN-{zone.value.upper()}-OCC-SENSOR")
        assert sensor.relinquish_default == "Occupied"
    assert bms.by_name("SPOTCOOL-HEARTBEAT").relinquish_default == "Off"


def test_vrf_gateway_and_profiles(bms: DevicePointMap) -> None:
    vrf = load_point_map("vrf_gateway")
    assert vrf.udp_port != bms.udp_port
    assert vrf.device_instance != bms.device_instance
    assert vrf.by_name("IDU-VANE-V").states == ["Pos1", "Pos2", "Pos3", "Pos4", "Pos5", "Swing"]
    profiles = load_vrf_profiles()
    assert set(profiles) == {"mitsubishi", "daikin", "lg"}
    assert profiles["mitsubishi"].vane_horizontal is not None
    assert profiles["lg"].vane_horizontal is None
    lg_vertical = profiles["lg"].vane_vertical
    assert lg_vertical is not None and lg_vertical.positions == 0 and lg_vertical.swing


def test_priority_order() -> None:
    # Lower number = more urgent. Operators must beat the agent, the agent must beat schedules.
    assert OPERATOR_PRIORITY < AGENT_PRIORITY < SCHEDULE_PRIORITY


def test_zone_info_is_complete() -> None:
    assert set(ZONES) == set(Zone)
    cameras = [info.camera_id for info in ZONES.values()]
    assert len(set(cameras)) == len(cameras)


def test_point_validation_rejects_bad_specs() -> None:
    with pytest.raises(ValidationError):
        PointSpec(
            object_type=ObjectType.ANALOG_INPUT,
            instance=1,
            name="X",
            description="inputs cannot be commandable",
            writability=Writability.COMMANDABLE,
            source="derived",
        )
    with pytest.raises(ValidationError):
        PointSpec(
            object_type=ObjectType.BINARY_VALUE,
            instance=1,
            name="Y",
            description="binary needs two state texts",
            states=["Only"],
            source="derived",
        )
    point = PointSpec(
        object_type=ObjectType.ANALOG_INPUT, instance=1, name="Z", description="d", source="derived"
    )
    with pytest.raises(ValidationError):
        DevicePointMap(
            device_name="D",
            device_instance=1,
            udp_port=47808,
            vendor_name="v",
            description="duplicate ids",
            points=[point, point],
        )


def test_every_world_event_type_has_a_payload_model() -> None:
    assert set(WORLD_PAYLOADS) == set(EventType) - AGENT_EVENT_TYPES


def test_envelope_round_trip() -> None:
    tick = TickData(index=3, step_s=900.0, clock="08:45", day_of_year=199)
    env = EventEnvelope.build(EventType.TICK, EventSource.WORLD, tick, sim_time_s=17_177_100.0)
    again = EventEnvelope.model_validate_json(env.model_dump_json())
    assert again.v == 1
    assert parse_world_payload(again) == tick


def test_camera_catalog_zone_lookup() -> None:
    catalog = CameraCatalog(
        cameras=[
            CameraInfo(
                id="cam-sou",
                name="South meeting room",
                zone=Zone.SOU,
                snapshot_url="http://127.0.0.1:8081/cameras/cam-sou/snapshot.jpg",
                width=1280,
                height=720,
                fps_hint=1.0,
                source=CameraSource.SCENARIO,
            )
        ]
    )
    assert [c.id for c in catalog.for_zone(Zone.SOU)] == ["cam-sou"]
    assert catalog.for_zone(Zone.COR) == []


def test_json_schemas_generate() -> None:
    DevicePointMap.model_json_schema()
    EventEnvelope.model_json_schema()
    for model in WORLD_PAYLOADS.values():
        model.model_json_schema()
