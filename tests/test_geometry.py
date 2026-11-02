import math

import numpy as np
import pytest

from app.spotcool_agent.config import VRF_PROFILES, Calibration, ControlLimits
from app.spotcool_agent.geometry import (
    CameraModel,
    choose_aim,
    focal_px,
    half_up,
    locate,
    louver_angles,
    quantize_hysteretic,
    quantize_vane,
    rotation,
)
from app.spotcool_agent.schemas import ManipulatedVariables, OccupantMemory, OccupantState


def test_focal_length():
    assert focal_px(1280, 70.0) == pytest.approx(914.0, abs=0.05)


def test_projection_matches_hand_computed_pixels():
    camera = CameraModel(1280, 720, 70.0, 2.5, 30.0)
    assert camera.project((0, 1.7, 3.0))[1] * 720 == pytest.approx(113.9, abs=0.1)
    assert camera.project((0, 0, 3.0))[1] * 720 == pytest.approx(518.0, abs=0.1)


def test_foot_point_recovers_distance_where_bbox_height_overestimates():
    camera = CameraModel(1280, 720, 70.0, 2.5, 30.0)
    v_head, v_feet = camera.project((0, 1.7, 3.0))[1], camera.project((0, 0, 3.0))[1]
    location = locate(camera, (0.46, v_head, 0.54, v_feet), "standing", Calibration())
    assert location.method == "foot"
    assert location.distance_m == pytest.approx(3.0, abs=0.01)
    assert location.estimates["pinhole"] > 3.3  # the brief's bbox-height estimate is biased far


def test_hidden_feet_fall_back_to_the_head_plane():
    # Seated behind a table: the box bottom is the table edge, so the foot ray lands far behind.
    camera = CameraModel(1280, 720, 70.0, 2.5, 25.0)
    u, v_head = camera.project((0.5, 1.30, 3.0))
    v_bottom = camera.project((0.8, 0.0, 4.8))[1]
    location = locate(camera, (u - 0.04, v_head, u + 0.04, v_bottom), "seated", Calibration())
    assert location.method == "head"
    assert location.distance_m == pytest.approx(math.hypot(0.5, 3.0), abs=0.02)


def test_pinhole_fallback_when_head_and_feet_are_cut_off():
    camera = CameraModel(1280, 720, 70.0, 2.5, 25.0)
    assert locate(camera, (0.4, 0.0, 0.5, 1.0), "standing", Calibration()).method == "pinhole"


def test_rotation_has_no_roll_and_tilt_points_down():
    matrix = rotation(25.0, 45.0)
    assert (matrix @ np.array([1.0, 0, 0]))[1] == pytest.approx(0.0, abs=1e-12)
    assert (matrix @ np.array([0, 0, 1.0]))[1] < 0


def test_louver_angles_and_mitsubishi_positions():
    yaw, pitch = louver_angles((0, 2.2, 0), (1, 1, 3))
    assert yaw == pytest.approx(18.43, abs=0.01)
    assert pitch == pytest.approx(-20.78, abs=0.01)
    profile = VRF_PROFILES["mitsubishi"]
    assert quantize_vane(yaw, profile.vane_horizontal, None, 0.6) == 4
    assert quantize_vane(pitch, profile.vane_vertical, None, 0.6) == 2


def test_daikin_and_lg_profiles():
    assert quantize_vane(10.0, VRF_PROFILES["daikin"].vane_horizontal, None, 0.6) is None
    assert quantize_vane(-20.0, VRF_PROFILES["daikin"].vane_vertical, None, 0.6) == 2
    assert quantize_vane(-20.0, VRF_PROFILES["lg"].vane_vertical, None, 0.6) is None


def test_vane_hysteresis():
    axis = VRF_PROFILES["mitsubishi"].vane_horizontal
    assert quantize_vane(11.0, axis, 3, 0.6) == 3  # nearer Pos4 (20) but within 12 deg of Pos3
    assert quantize_vane(13.0, axis, 3, 0.6) == 4


def test_half_up_and_hysteretic_quantizer():
    assert half_up(24.25, 0.5) == 24.5
    assert round(48.5) / 2 == 24.0  # why half_up exists: round() goes to even
    assert quantize_hysteretic(24.25, 24.0, 0.5, 0.6) == 24.0
    assert quantize_hysteretic(24.31, 24.0, 0.5, 0.6) == 24.5


def occupant(key: str, yaw: float, sensation: float = 0.0, in_aim_set: bool = True) -> OccupantState:
    return OccupantState(
        occupant_key=key, sam_id="P1", present_ratio=1.0, bbox=(0, 0, 1, 1), posture="seated",
        distance_m=3.0, distance_method="foot", position_m=(0, 1.1, 3), yaw_deg=yaw, pitch_deg=-20.0,
        clothing_clo=0.6, activity_met=1.1, observed_sensation=sensation, in_aim_set=in_aim_set,
    )  # fmt: skip


def test_aim_keeps_clear_of_cold_occupants():
    aim = choose_aim(
        [occupant("occ-1", 10.0, 1.0), occupant("occ-2", 15.0, -1.0, in_aim_set=False)],
        {}, ManipulatedVariables(), ControlLimits(), VRF_PROFILES["mitsubishi"],
    )  # fmt: skip
    assert aim.targets == ["occ-1"] and aim.avoided == ["occ-2"]
    assert abs(aim.yaw_deg - 15.0) >= 20.0 - 1e-9


def test_wide_aim_set_swings_unless_someone_is_draft_sensitive():
    limits, profile, previous = ControlLimits(), VRF_PROFILES["mitsubishi"], ManipulatedVariables()
    spread = choose_aim([occupant("occ-1", -30.0, 1.0), occupant("occ-2", 25.0, 1.0)], {}, previous, limits, profile)
    assert spread.louver_mode == "swing"
    memories = {"occ-1": OccupantMemory(key="occ-1", bbox=(0, 0, 1, 1), last_seen_turn=1, draft_sensitive=True)}
    careful = choose_aim([occupant("occ-1", -30.0, 1.0), occupant("occ-2", 25.0, 2.0)], memories, previous, limits, profile)
    assert careful.louver_mode == "fixed" and careful.targets == ["occ-2"]


def test_no_occupants_holds_the_aim():
    previous = ManipulatedVariables(louver_yaw_deg=12.0, louver_pitch_deg=-15.0)
    aim = choose_aim([], {}, previous, ControlLimits(), VRF_PROFILES["mitsubishi"])
    assert (aim.yaw_deg, aim.pitch_deg) == (12.0, -15.0)
