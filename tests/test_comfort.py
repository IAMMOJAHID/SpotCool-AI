import math

import pytest

from app.spotcool_agent.comfort import (
    draft_rating,
    energy_proxy,
    fuse_sensation,
    grounded_reward,
    pmv_neutral_setpoint,
    pmv_ppd,
    ppd_from_pmv,
    update_room_temp,
)
from app.spotcool_agent.config import Calibration, ControlLimits
from app.spotcool_agent.schemas import ClipInfo, Complaint, ManipulatedVariables, OccupantState, TurnState

# ISO 7730:2005 Table D.1 rows: ta, tr, relative air speed, rh, met, clo -> PMV, PPD.
ISO_7730_TABLE_D1 = [
    (22.0, 22.0, 0.1, 60, 1.2, 0.5, -0.75, 17),
    (27.0, 27.0, 0.1, 60, 1.2, 0.5, 0.77, 17),
    (27.0, 27.0, 0.3, 60, 1.2, 0.5, 0.44, 9),
    (23.5, 25.5, 0.1, 60, 1.2, 0.5, -0.01, 5),
    (19.0, 19.0, 0.1, 40, 1.2, 1.0, -0.60, 13),
    (22.0, 22.0, 0.1, 60, 1.6, 0.5, 0.05, 5),
    (27.0, 27.0, 0.1, 60, 1.6, 0.5, 1.17, 34),
]


@pytest.mark.parametrize("ta,tr,vel,rh,met,clo,pmv,ppd", ISO_7730_TABLE_D1)
def test_pmv_matches_iso_7730(ta, tr, vel, rh, met, clo, pmv, ppd):
    got_pmv, got_ppd = pmv_ppd(ta, tr, vel, rh, met, clo)
    assert got_pmv == pytest.approx(pmv, abs=0.05)
    assert got_ppd == pytest.approx(ppd, abs=1.0)


def test_ppd_minimum_is_five_percent():
    assert ppd_from_pmv(0.0) == pytest.approx(5.0)


def test_draft_rating():
    assert draft_rating(20.0, 0.15) == pytest.approx(18.0, abs=0.05)
    assert draft_rating(23.0, 0.20) == pytest.approx(20.7, abs=0.05)
    assert draft_rating(24.0, 0.04) == 0.0
    assert draft_rating(24.0, 3.0) == draft_rating(24.0, 0.5)  # the model is valid only to 0.5 m/s


def occupant(key: str, sensation: float = 0.0, cues=(), apparent=None) -> OccupantState:
    return OccupantState(
        occupant_key=key, sam_id="P1", present_ratio=1.0, bbox=(0, 0, 1, 1), posture="seated",
        distance_m=3.0, distance_method="foot", position_m=(0, 1.1, 3), yaw_deg=0.0, pitch_deg=-20.0,
        clothing_clo=0.6, activity_met=1.1, cues=list(cues), apparent_sensation=apparent,
        observed_sensation=sensation,
    )  # fmt: skip


def test_sensation_fusion_weights():
    freezing = Complaint(category="too_cold", text="freezing", source="voice", sensation=-2.5, attributed_to=["occ-1"])
    value, observed = fuse_sensation(occupant("occ-1", cues=["rubbing_arms"], apparent=-1.0), [freezing])
    assert observed and value == pytest.approx((3 * -2.5 + 2 * -1.5 + 1 * -1.0) / 6)
    room = Complaint(category="too_hot", text="hot", source="text", sensation=2.0)
    value, _ = fuse_sensation(occupant("occ-2"), [room])
    assert value == pytest.approx(2.0)  # a room-level complaint alone sets the value (weight 1.5)
    assert fuse_sensation(occupant("occ-3"), []) == (0.0, False)


def state(occupants, complaints=(), occupied=True) -> TurnState:
    clip = ClipInfo(label="x", duration_s=35.0, frames=35, frame_size=(640, 360))
    discomfort = sum(abs(o.observed_sensation) for o in occupants) / len(occupants) if occupants else 0.0
    return TurnState(turn=2, clip=clip, occupants=list(occupants), occupied=occupied, complaints=list(complaints), discomfort=discomfort)


def test_grounded_reward_counts_only_reidentified_occupants():
    previous = state([occupant("occ-1", -2.0), occupant("occ-2", -2.0)])
    current = state([occupant("occ-1", -1.0)])  # occ-2 left: that is not an improvement
    reward = grounded_reward(current, previous, ManipulatedVariables(target_temp_c=27.0, fan_speed="low"))
    assert reward.matched_occupants == 1
    assert reward.delta_discomfort == pytest.approx(1.0)
    energy = energy_proxy(ManipulatedVariables(target_temp_c=27.0, fan_speed="low"))
    assert reward.score == pytest.approx(1.0 / 3 - 0.5 * 1.0 / 3 - 0.2 * energy, abs=1e-3)


def test_grounded_reward_first_turn_has_no_improvement_term_and_penalizes_complaints():
    complaint = Complaint(category="too_cold", text="cold", source="voice", sensation=-2.0)
    reward = grounded_reward(state([occupant("occ-1", -2.0)], [complaint]), None, ManipulatedVariables())
    assert reward.delta_discomfort == 0.0 and reward.matched_occupants == 0
    assert reward.complaint_penalty == 0.3


def test_energy_proxy_orders_modes():
    cool = energy_proxy(ManipulatedVariables(target_temp_c=24.0, compressor_mode="cool"))
    eco = energy_proxy(ManipulatedVariables(target_temp_c=24.0, compressor_mode="eco_idle"))
    fan = energy_proxy(ManipulatedVariables(target_temp_c=24.0, compressor_mode="fan_only"))
    assert fan < eco < cool


def test_room_temperature_lags_the_setpoint_and_drifts_without_compressor():
    cal = Calibration()
    cooled = update_room_temp(26.0, ManipulatedVariables(target_temp_c=24.0), 900.0, cal)
    assert cooled == pytest.approx(26.0 - (1 - math.exp(-1)) * 2.0)
    assert update_room_temp(24.0, ManipulatedVariables(compressor_mode="fan_only"), 900.0, cal) > 24.0
    assert update_room_temp(26.0, ManipulatedVariables(target_temp_c=24.0), 35.0, cal) > 25.9  # a 35 s clip barely moves it


def test_pmv_neutral_setpoint_is_warmer_for_lighter_clothing():
    cal, limits = Calibration(), ControlLimits()
    light = occupant("occ-1").model_copy(update={"clothing_clo": 0.4})
    heavy = occupant("occ-2").model_copy(update={"clothing_clo": 1.0})
    assert pmv_neutral_setpoint([light], cal, limits) > pmv_neutral_setpoint([heavy], cal, limits)
