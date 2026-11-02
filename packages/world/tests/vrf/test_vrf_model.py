"""The VRF unit's model: louver slew and swing, the room, power. Pure: no sockets, no waiting."""

import pytest

from spotcool_contracts import load_vrf_profiles
from spotcool_world.devices.vrf.model import (
    AMBIENT_C,
    FAN_KW,
    MAX_DT_S,
    RATED_INPUT_KW,
    SLEW_DEG_S,
    VrfUnit,
)

PROFILES = load_vrf_profiles()
DT_S = 0.05  # the worker's tick


def run(unit: VrfUnit, seconds: float, dt_s: float = DT_S) -> list[tuple[float, float, float]]:
    """Advance ``unit`` for ``seconds``; return (time, vertical angle, room) after each step."""
    trace, t = [], 0.0
    for _ in range(round(seconds / dt_s)):
        unit.advance(dt_s)
        t += dt_s
        trace.append((t, unit.vane_v_angle_deg, unit.room_c))
    return trace


def test_a_slew_from_0_to_minus_60_takes_4_s_and_never_steps_faster_than_15_deg_s() -> None:
    unit = VrfUnit(PROFILES["mitsubishi"])
    unit.vane_v = "Pos1"
    run(unit, 1.0)
    assert unit.vane_v_angle_deg == 0.0
    unit.vane_v = "Pos5"
    trace = run(unit, 6.0)
    angles = [0.0, *(angle for _, angle, _ in trace)]
    assert max(abs(b - a) for a, b in zip(angles, angles[1:], strict=False)) <= SLEW_DEG_S * DT_S
    arrived = next(t for t, angle, _ in trace if angle == -60.0)
    assert arrived >= 4.0 - 1e-9
    assert arrived == pytest.approx(4.0)
    assert angles[-1] == -60.0  # and it stays


def test_a_stall_is_clipped_never_a_jump() -> None:
    unit = VrfUnit(PROFILES["mitsubishi"])
    unit.vane_v = "Pos5"
    unit.advance(3.0)  # the caller stalled for 3 s
    assert unit.vane_v_angle_deg == pytest.approx(-SLEW_DEG_S * MAX_DT_S)
    with pytest.raises(ValueError, match="negative"):
        unit.advance(-0.01)


@pytest.mark.parametrize(
    ("vendor", "low", "high"),
    [("mitsubishi", -60.0, 0.0), ("lg", -60.0, 0.0)],  # LG: the illustrative swing range
)
def test_swing_covers_the_vertical_range_and_reverses(vendor: str, low: float, high: float) -> None:
    unit = VrfUnit(PROFILES[vendor])
    unit.vane_v = "Swing"
    angles = [angle for _, angle, _ in run(unit, 20.0)]
    assert (min(angles), max(angles)) == (pytest.approx(low), pytest.approx(high))
    steps = [b - a for a, b in zip(angles, angles[1:], strict=False)]
    assert max(abs(step) for step in steps) <= SLEW_DEG_S * DT_S + 1e-9
    reversals = sum(a * b < 0 for a, b in zip(steps, steps[1:], strict=False))
    assert reversals >= 4  # 20 s at 15 deg/s over a 60 deg range: five ends


def test_swing_covers_the_horizontal_range() -> None:
    unit = VrfUnit(PROFILES["mitsubishi"])
    unit.vane_h = "Swing"
    seen = []
    for _ in range(400):
        unit.advance(DT_S)
        assert unit.vane_h_angle_deg is not None
        seen.append(unit.vane_h_angle_deg)
    assert (min(seen), max(seen)) == (pytest.approx(-40.0), pytest.approx(40.0))


def test_positions_are_the_profiles_angles() -> None:
    unit = VrfUnit(PROFILES["mitsubishi"])
    for k, (v, h) in enumerate(zip([0, -15, -30, -45, -60], [-40, -20, 0, 20, 40], strict=True)):
        unit.vane_v = unit.vane_h = f"Pos{k + 1}"
        run(unit, 6.0)
        assert (unit.vane_v_angle_deg, unit.vane_h_angle_deg) == (v, h)


def test_a_state_the_brand_lacks_holds_the_angle_with_swing_off() -> None:
    unit = VrfUnit(PROFILES["lg"])  # relinquish default Pos3: LG has no positions
    run(unit, 2.0)
    assert unit.vane_v_angle_deg == 0.0
    unit.vane_v = "Swing"
    run(unit, 1.0)
    held = unit.vane_v_angle_deg
    assert held == pytest.approx(-15.0)
    unit.vane_v = "Pos3"  # the release: back to the relinquish default
    run(unit, 5.0)
    assert unit.vane_v_angle_deg == held


def test_an_axis_the_brand_does_not_expose_has_no_angle() -> None:
    for vendor in ("daikin", "lg"):
        unit = VrfUnit(PROFILES[vendor])
        unit.vane_h = "Swing"
        run(unit, 1.0)
        assert unit.vane_h_angle_deg is None
        state = unit.state()
        assert (state.vane_h_pos, state.vane_h_angle_deg) == (None, None)


def test_off_the_louvers_hold_and_swing_stops() -> None:
    unit = VrfUnit(PROFILES["mitsubishi"])
    unit.vane_v = "Swing"
    run(unit, 1.0)
    unit.power_on = False
    held = unit.vane_v_angle_deg
    run(unit, 3.0)
    assert unit.vane_v_angle_deg == held


def test_cooling_converges_to_the_setpoint_and_never_below_it() -> None:
    for mode in ("Cool", "Dry", "Auto"):
        unit = VrfUnit(PROFILES["mitsubishi"])
        unit.mode = mode
        assert unit.room_c == AMBIENT_C  # the room starts at ambient
        rooms = [room for _, _, room in run(unit, 1800.0, dt_s=0.1)]
        assert min(rooms) >= unit.setpoint_c
        assert rooms[-1] == pytest.approx(24.0, abs=0.01)
        assert all(b <= a for a, b in zip(rooms, rooms[1:], strict=False))


def test_a_raised_setpoint_lets_the_room_rise_to_it_only() -> None:
    unit = VrfUnit(PROFILES["mitsubishi"])
    run(unit, 1800.0, dt_s=0.1)
    unit.setpoint_c = 26.0
    rooms = [room for _, _, room in run(unit, 1800.0, dt_s=0.1)]
    assert max(rooms) <= 26.0
    assert rooms[-1] == pytest.approx(26.0, abs=0.01)


@pytest.mark.parametrize("change", ["off", "fan"])
def test_the_room_rises_toward_ambient_off_or_in_fan_mode(change: str) -> None:
    unit = VrfUnit(PROFILES["mitsubishi"])
    run(unit, 1800.0, dt_s=0.1)
    if change == "off":
        unit.power_on = False
    else:
        unit.mode = "Fan"
    rooms = [room for _, _, room in run(unit, 1800.0, dt_s=0.1)]
    assert all(b > a for a, b in zip(rooms, rooms[1:], strict=False))
    assert rooms[-1] == pytest.approx(AMBIENT_C, abs=0.01)


def test_power_is_0_off_and_grows_with_the_room_above_the_setpoint() -> None:
    unit = VrfUnit(PROFILES["mitsubishi"])
    unit.fan = "High"
    assert unit.power_kw == RATED_INPUT_KW + FAN_KW["High"]  # 6 K above: capped
    powers = []
    for _ in range(60):
        run(unit, 10.0, dt_s=0.1)
        powers.append((unit.room_c - unit.setpoint_c, unit.power_kw))
    assert all(p1 >= p2 for (_, p1), (_, p2) in zip(powers, powers[1:], strict=False))
    assert powers[-1][1] < 0.5 * powers[0][1]
    assert powers[-1][1] == pytest.approx(FAN_KW["High"], abs=0.03)  # at the setpoint: the fan
    low = VrfUnit(PROFILES["mitsubishi"])
    low.fan, low.room_c = "Low", 25.0
    high = VrfUnit(PROFILES["mitsubishi"])
    high.fan, high.room_c = "High", 25.0
    assert low.power_kw < high.power_kw  # the fan level counts too
    unit.mode = "Fan"
    assert unit.power_kw == FAN_KW["High"]
    unit.power_on = False
    assert unit.power_kw == 0.0


def test_the_state_is_rounded_as_published() -> None:
    unit = VrfUnit(PROFILES["mitsubishi"])
    unit.vane_v = "Pos5"
    unit.advance(0.0333)
    state = unit.state()
    assert state.vane_v_angle_deg == -0.5
    assert state.room_temp_c == round(unit.room_c, 2)
    assert state.power_kw == round(unit.power_kw, 3)
    assert (state.profile, state.power_on, state.mode, state.setpoint_c, state.fan) == (
        "mitsubishi",
        True,
        "Cool",
        24.0,
        "Auto",
    )
    assert (state.vane_v_pos, state.vane_h_pos, state.vane_h_angle_deg) == ("Pos5", "Pos3", 0.0)
