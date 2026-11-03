"""The brands' rules: which louver states and fan speeds each gateway accepts. No sockets."""

import pytest

from spotcool_contracts import load_point_map, load_vrf_profiles
from spotcool_contracts.vrf import VrfProfile
from spotcool_world.bus import InProcessBus
from spotcool_world.clocks import SimClock
from spotcool_world.transports.bacnet.vrf import BacnetVrfGateway, brand_refusal

PROFILES = load_vrf_profiles()
MAP = load_point_map("vrf_gateway")
VANE_V, VANE_H, FAN = (MAP.by_name(n) for n in ("IDU-VANE-V", "IDU-VANE-H", "IDU-FAN"))
STATES = ["Pos1", "Pos2", "Pos3", "Pos4", "Pos5", "Swing"]


def refusals(vendor: str, point: str) -> dict[str, str | None]:
    spec = MAP.by_name(point)
    found = {state: brand_refusal(PROFILES[vendor], spec, state) for state in STATES}
    return {state: None if r is None else r.error_code for state, r in found.items()}


def test_mitsubishi_accepts_every_state_on_both_axes() -> None:
    assert refusals("mitsubishi", "IDU-VANE-V") == dict.fromkeys(STATES)
    assert refusals("mitsubishi", "IDU-VANE-H") == dict.fromkeys(STATES)


def test_daikin_refuses_the_horizontal_louver_with_write_access_denied() -> None:
    assert refusals("daikin", "IDU-VANE-V") == dict.fromkeys(STATES)
    assert refusals("daikin", "IDU-VANE-H") == dict.fromkeys(STATES, "write-access-denied")
    rejection = brand_refusal(PROFILES["daikin"], VANE_H, "Pos1")
    assert rejection is not None
    assert rejection.reason == "Daikin's gateway exposes no horizontal louver"


def test_lg_accepts_only_swing_and_refuses_positions_with_value_out_of_range() -> None:
    assert refusals("lg", "IDU-VANE-V") == {
        **dict.fromkeys(STATES[:5], "value-out-of-range"),
        "Swing": None,
    }
    assert refusals("lg", "IDU-VANE-H") == dict.fromkeys(STATES, "write-access-denied")
    rejection = brand_refusal(PROFILES["lg"], VANE_V, "Pos4")
    assert rejection is not None
    assert rejection.reason == "LG's gateway has no vertical louver state Pos4 (['Swing'])"


@pytest.mark.parametrize("vendor", sorted(PROFILES))
def test_a_release_is_always_accepted(vendor: str) -> None:
    for spec in MAP.commandable():
        assert brand_refusal(PROFILES[vendor], spec, None) is None


def test_a_fan_speed_the_brand_lacks_is_refused() -> None:
    every = [brand_refusal(p, FAN, speed) for p in PROFILES.values() for speed in FAN.states or []]
    assert every == [None] * 12  # every brand has all four
    two_speeds = VrfProfile.model_validate(
        PROFILES["daikin"].model_dump() | {"fan_speeds": ["Auto", "High"]}
    )
    rejection = brand_refusal(two_speeds, FAN, "Low")
    assert rejection is not None
    assert (rejection.error_code, rejection.reason) == (
        "value-out-of-range",
        "Daikin's gateway has no fan speed Low",
    )


@pytest.mark.parametrize("vendor", sorted(PROFILES))
def test_the_relinquish_defaults_start_the_unit(vendor: str) -> None:
    gateway = BacnetVrfGateway(PROFILES[vendor], InProcessBus(), SimClock(0.0, 900.0))
    unit = gateway.unit  # never started: no sockets
    assert (unit.power_on, unit.mode, unit.setpoint_c, unit.fan) == (True, "Cool", 24.0, "Auto")
    assert (unit.vane_v, unit.vane_h) == ("Pos3", "Pos3")


def test_lgs_relinquish_default_holds_the_angle() -> None:
    gateway = BacnetVrfGateway(PROFILES["lg"], InProcessBus(), SimClock(0.0, 900.0))
    for _ in range(100):
        gateway.advance(0.05)
    assert gateway.unit.vane_v_angle_deg == 0.0  # Pos3, which LG lacks: swing off, held
    assert gateway.unit.state().vane_v_pos == "Pos3"
