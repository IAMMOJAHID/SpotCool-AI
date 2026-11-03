"""Person A's geometry and louver aim, and the aim mapped onto each brand's vanes."""

import math

import pytest

from spotcool_agent.domain.geometry import Detection
from spotcool_agent.domain.louvers import (
    SWING,
    VANE_H,
    VANE_V,
    Aim,
    CameraPose,
    FloorPoint,
    locate,
    louver_aim,
    louver_angles_to,
    nearest_position,
    project_floor_point,
    vane_states,
)
from spotcool_contracts import load_vrf_profiles

POSE = CameraPose()  # A's defaults: 2.8 m, 30 deg, 90 deg; the unit under it at 2.6 m
W, H = 1280, 720
PROFILES = load_vrf_profiles()


def person(lateral_m: float, forward_m: float, pose: CameraPose = POSE) -> Detection:
    """A standing person's box, drawn from where they stand by A's projection."""
    feet = project_floor_point(lateral_m, forward_m, 0.0, W, H, pose)
    head = project_floor_point(lateral_m, forward_m, pose.standing_height_m, W, H, pose)
    assert feet is not None and head is not None
    tall = feet[1] - head[1]
    half = tall / 6  # a third as wide as tall: standing
    return Detection(0, (feet[0] - half) / W, head[1] / H, (feet[0] + half) / W, feet[1] / H)


def aim_at(*places: tuple[float, float]) -> Aim:
    found = [(FloorPoint(lat, fwd, "ground_plane", True), 1.0) for lat, fwd in places]
    aim = louver_aim(found, POSE)
    assert aim is not None
    return aim


def test_a_person_straight_ahead_at_3_m_with_as_numbers() -> None:
    where = locate(person(0.0, 3.0), W, H, POSE)
    assert where.method == "ground_plane" and where.standing
    assert where.lateral_m == pytest.approx(0.0, abs=0.05)
    assert where.forward_m == pytest.approx(3.0, abs=0.05)
    pan, tilt, distance = louver_angles_to(0.0, 3.0, POSE)
    assert pan == 0.0
    assert tilt == pytest.approx(math.degrees(math.atan2(2.6 - 1.1, 3.0)))  # 26.57 deg down
    assert distance == pytest.approx(math.hypot(3.0, 1.5))
    aim = aim_at((where.lateral_m, where.forward_m))
    assert aim.pan_deg == pytest.approx(0.0, abs=1.0) and not aim.swing
    for mirror in (False, True):  # straight ahead is the same either way
        states = vane_states(aim, PROFILES["mitsubishi"], mirror=mirror)
        assert states == {VANE_V: "Pos3", VANE_H: "Pos3"}  # -26.6 deg -> -30, the nearest


def test_a_person_to_the_right_pans_right_and_mirrors_left() -> None:
    where = locate(person(1.2, 3.0), W, H, POSE)
    assert where.lateral_m == pytest.approx(1.2, abs=0.05)
    aim = aim_at((where.lateral_m, where.forward_m))
    assert aim.pan_deg == pytest.approx(math.degrees(math.atan2(1.2, 3.0)), abs=0.5)  # 21.8
    assert vane_states(aim, PROFILES["mitsubishi"])[VANE_H] == "Pos4"  # +20 deg
    assert vane_states(aim, PROFILES["mitsubishi"], mirror=True)[VANE_H] == "Pos2"  # -20 deg
    assert aim.reason() == "aim at 1 person at 3.2 m, 1.2 m right"


def test_angles_snap_to_the_nearest_position_clamped_to_the_range() -> None:
    mitsubishi = PROFILES["mitsubishi"]
    vertical, horizontal = mitsubishi.vane_vertical, mitsubishi.vane_horizontal
    assert vertical is not None and horizontal is not None
    assert [nearest_position(a, vertical) for a in (5, -7, -8, -50, -89)] == [
        "Pos1",
        "Pos1",
        "Pos2",
        "Pos4",
        "Pos5",
    ]
    assert [nearest_position(a, horizontal) for a in (-90, -31, 9, 11, 90)] == [
        "Pos1",
        "Pos1",
        "Pos3",
        "Pos4",
        "Pos5",
    ]


def test_each_brand_gets_only_the_axes_and_states_it_has() -> None:
    near = aim_at((1.2, 3.0))
    assert vane_states(near, PROFILES["mitsubishi"]) == {VANE_V: "Pos3", VANE_H: "Pos4"}
    assert vane_states(near, PROFILES["daikin"]) == {VANE_V: "Pos3"}  # no horizontal axis
    assert vane_states(near, PROFILES["lg"]) == {VANE_V: None}  # swing only: off is a release


def test_people_spread_wider_than_one_and_a_half_beams_swing() -> None:
    together = aim_at((-0.5, 3.0), (0.5, 3.0))  # pans of -9.5 and +9.5 deg
    apart = aim_at((-1.8, 3.0), (1.8, 3.0))  # -31 and +31 deg: a spread over 22.5
    assert not together.swing and together.spread_deg == pytest.approx(9.46, abs=0.05)
    assert apart.swing and apart.spread_deg > 1.5 * 15.0
    assert together.reason() == "aim at 2 people at 3.0 m, 0.0 m right"
    assert vane_states(apart, PROFILES["mitsubishi"]) == {VANE_V: "Pos3", VANE_H: SWING}
    assert vane_states(apart, PROFILES["daikin"]) == {VANE_V: SWING}  # the unit's one swing
    assert vane_states(apart, PROFILES["lg"]) == {VANE_V: SWING}


def test_the_centroid_is_weighted() -> None:
    found = [
        (FloorPoint(-1.0, 3.0, "ground_plane", True), 3.0),
        (FloorPoint(1.0, 3.0, "ground_plane", True), 1.0),
    ]
    aim = louver_aim(found, POSE)
    assert aim is not None and aim.pan_deg < 0 and aim.lateral_m == pytest.approx(-0.5)
    assert louver_aim([], POSE) is None
