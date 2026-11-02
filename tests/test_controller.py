import pytest

from app.spotcool_agent.config import AppConfig
from app.spotcool_agent.rl_controller import (
    apply_safety,
    critic_from_wire,
    fan_step,
    mv_from_wire,
    resolve_door,
    update_preferences,
)
from app.spotcool_agent.schemas import (
    ClipInfo,
    Complaint,
    ControllerState,
    DoorState,
    ManipulatedVariables,
    MVWire,
    OccupantMemory,
    OccupantState,
    PreferenceWire,
    RewardBreakdown,
    RewardWire,
    TurnState,
)

CFG = AppConfig()


def occupant(key: str = "occ-1", yaw: float = 7.0, cues=(), draft: bool = False, sensation: float = 0.0) -> OccupantState:
    return OccupantState(
        occupant_key=key, sam_id="P0", present_ratio=1.0, bbox=(0.5, 0.3, 0.6, 0.8), posture="seated",
        distance_m=3.0, distance_method="foot", position_m=(0.3, 1.1, 3.0), yaw_deg=yaw, pitch_deg=-25.0,
        clothing_clo=0.6, activity_met=1.1, cues=list(cues), draft=draft, observed_sensation=sensation,
    )  # fmt: skip


def turn_state(occupants=None, complaints=(), door: DoorState = None, duration: float = 35.0) -> TurnState:
    occupants = [occupant()] if occupants is None else list(occupants)
    clip = ClipInfo(label="t", duration_s=duration, frames=int(duration), frame_size=(640, 360))
    return TurnState(
        turn=2, clip=clip, occupants=occupants, occupied=bool(occupants), complaints=list(complaints),
        door=door or DoorState(closed_s_at_end=duration),
    )  # fmt: skip


def door(longest=0.0, start=0.0, end=0.0, spans=False, closed_end=0.0, confirmed=None, open_tiles=None) -> DoorState:
    return DoorState(
        longest_run_s=longest, start_run_s=start, end_run_s=end, spans_clip=spans, closed_s_at_end=closed_end,
        spark_confirmed=confirmed, spark_open_tiles=open_tiles, open_frames=int(longest),
    )  # fmt: skip


def with_door(state: TurnState, before: ControllerState) -> TurnState:
    state.door = resolve_door(state.door, before, CFG.limits)
    return state


def reasons(result, field: str) -> list[str]:
    return next((a.reasons for a in result.adjustments if a.field == field), [])


def test_rate_clamp_limits_policy_moves():
    result = apply_safety(ManipulatedVariables(target_temp_c=30.0), turn_state(), ControllerState(), CFG)
    assert result.applied.target_temp_c == 25.5  # EMA gives 27.0, the clamp allows +1.5 from 24.0
    assert any("rate clamp" in r for r in reasons(result, "target_temp_c"))


def test_smoothing_reaches_the_target_without_stalling_on_the_grid():
    before, applied = ControllerState(), []
    for _ in range(4):
        result = apply_safety(ManipulatedVariables(target_temp_c=24.4), turn_state(), before, CFG)
        applied.append(result.applied.target_temp_c)
        before = result.state
    assert applied[0] == 24.0 and applied[-1] == 24.5


def test_override_sets_the_internal_state_so_ema_does_not_undo_it():
    freezing = Complaint(category="too_cold", text="It's freezing", source="voice", sensation=-2.5, attributed_to=["occ-1"])
    first = apply_safety(ManipulatedVariables(target_temp_c=24.0), turn_state(complaints=[freezing]), ControllerState(), CFG)
    assert first.applied.target_temp_c == 25.0 and first.state.internal_temp_c == 25.0
    second = apply_safety(ManipulatedVariables(target_temp_c=24.0), turn_state(), first.state, CFG)
    assert second.applied.target_temp_c == 24.5  # smoothed from 25.0, not snapped back to 24.0


@pytest.mark.parametrize(
    "longest,confirmed,expected",
    [(25.0, True, "fan_only"), (25.0, None, "fan_only"), (25.0, False, "cool"), (15.0, True, "cool")],
)
def test_door_override_needs_duration_and_no_contradiction(longest, confirmed, expected):
    before = ControllerState()
    state = with_door(turn_state(door=door(longest=longest, end=longest, confirmed=confirmed)), before)
    assert apply_safety(ManipulatedVariables(), state, before, CFG).applied.compressor_mode == expected


def test_door_already_closed_at_the_end_of_the_clip_does_not_stop_the_compressor():
    before = ControllerState()
    state = with_door(turn_state(door=door(longest=22.0, start=22.0, closed_end=13.0, confirmed=True)), before)
    assert apply_safety(ManipulatedVariables(), state, before, CFG).applied.compressor_mode == "cool"


def test_open_streak_carries_across_turns_and_clears_when_closed():
    first = resolve_door(door(longest=12.0, end=12.0), ControllerState(), CFG.limits)
    assert first.streak_s == 12.0 and not first.override_active
    carried = ControllerState(door_open_streak_s=first.streak_s)
    second = resolve_door(door(longest=15.0, start=15.0, end=15.0, spans=True), carried, CFG.limits)
    assert second.effective_open_s == 27.0 and second.override_active and second.streak_s == 27.0
    third = resolve_door(door(closed_end=35.0), ControllerState(door_override_active=True), CFG.limits)
    assert not third.override_active


def test_restart_lockout_defers_cooling_after_the_door_closes():
    before = ControllerState()
    stopped = apply_safety(ManipulatedVariables(), with_door(turn_state(door=door(longest=30.0, end=30.0)), before), before, CFG)
    assert stopped.applied.compressor_mode == "fan_only"
    state, modes = stopped.state, []
    for _ in range(6):
        closed = with_door(turn_state(door=door(closed_end=35.0)), state)
        result = apply_safety(ManipulatedVariables(compressor_mode="cool"), closed, state, CFG)
        modes.append(result.applied.compressor_mode)
        state = result.state
    assert modes == ["fan_only"] * 5 + ["cool"]  # off 35..175 s < 180 s, then 210 s
    assert stopped.state.compressor_off_since_s == 35.0


def test_draft_deflects_away_by_a_vane_position_and_warms_the_setpoint():
    state = turn_state(occupants=[occupant(yaw=7.0, cues=["rubbing_arms"], draft=True)])
    result = apply_safety(ManipulatedVariables(), state, ControllerState(), CFG)
    assert (result.vrf.idu_vane_h, result.vrf.idu_vane_v) == ("Pos2", "Pos1")
    assert result.applied.target_temp_c == 25.0 and result.applied.fan_speed == "low"
    assert any(o.startswith("draft at occ-1") for o in result.overrides)
    left = apply_safety(ManipulatedVariables(), turn_state(occupants=[occupant(yaw=-25.0, draft=True)]), ControllerState(), CFG)
    assert left.vrf.idu_vane_h == "Pos4"  # occupant on the left: the jet turns right


@pytest.mark.parametrize("vendor,vane_v,vane_h,mode", [("daikin", "Pos1", None, "fixed"), ("lg", "Swing", None, "swing")])
def test_draft_on_brands_without_horizontal_vanes(vendor, vane_v, vane_h, mode):
    cfg = AppConfig()
    cfg.calibration.vrf_profile = vendor
    result = apply_safety(ManipulatedVariables(), turn_state(occupants=[occupant(draft=True)]), ControllerState(), cfg)
    assert (result.vrf.idu_vane_v, result.vrf.idu_vane_h, result.applied.louver_mode) == (vane_v, vane_h, mode)


def test_empty_room_goes_eco_after_the_debounce_and_restores_on_return():
    first = apply_safety(ManipulatedVariables(), turn_state(occupants=[]), ControllerState(), CFG)
    assert first.applied.compressor_mode == "cool"  # 35 s empty is under the 60 s debounce
    second = apply_safety(ManipulatedVariables(), turn_state(occupants=[]), first.state, CFG)
    assert second.applied.compressor_mode == "eco_idle"
    assert (second.vrf.idu_mode, second.vrf.idu_fan, second.vrf.idu_sp) == ("Cool", "Low", 27.0)
    back = apply_safety(ManipulatedVariables(target_temp_c=26.0), turn_state(), second.state, CFG)
    assert back.applied.compressor_mode == "cool" and back.applied.target_temp_c == 24.0


def test_door_open_with_a_hot_complaint_runs_the_fan_high():
    hot = Complaint(category="too_hot", text="so hot", source="voice", sensation=2.0)
    before = ControllerState()
    state = with_door(turn_state(complaints=[hot], door=door(longest=25.0, end=25.0, confirmed=True)), before)
    result = apply_safety(ManipulatedVariables(), state, before, CFG)
    assert (result.applied.compressor_mode, result.applied.fan_speed, result.applied.target_temp_c) == ("fan_only", "high", 23.0)


def test_opposite_complaints_cancel():
    cold = Complaint(category="too_cold", text="cold", source="voice", sensation=-2.0)
    hot = Complaint(category="too_hot", text="hot", source="text", sensation=2.0)
    result = apply_safety(ManipulatedVariables(target_temp_c=24.0), turn_state(complaints=[cold, hot]), ControllerState(), CFG)
    assert result.applied.target_temp_c == 24.0
    assert "cold and hot complaints cancel" in reasons(result, "target_temp_c")


def test_explicit_request_beats_the_rate_clamp_within_the_comfort_band():
    request = Complaint(category="set_temperature", text="set it to 27", source="text", value_c=27.0)
    assert apply_safety(ManipulatedVariables(), turn_state(complaints=[request]), ControllerState(), CFG).applied.target_temp_c == 27.0
    too_low = Complaint(category="set_temperature", text="set it to 17", source="text", value_c=17.0)
    assert apply_safety(ManipulatedVariables(), turn_state(complaints=[too_low]), ControllerState(), CFG).applied.target_temp_c == 20.0


def test_bounds_while_occupied_and_vane_range():
    before = ControllerState(internal_temp_c=20.5, quant_temp_c=20.5)
    proposed = ManipulatedVariables(target_temp_c=16.0, louver_yaw_deg=90.0, louver_pitch_deg=60.0)
    result = apply_safety(proposed, turn_state(), before, CFG)
    assert result.applied.target_temp_c == 20.0
    assert (result.applied.louver_yaw_deg, result.applied.louver_pitch_deg) == (40.0, 0.0)


def test_vrf_command_is_on_grid_and_reports_changed_points():
    first = apply_safety(ManipulatedVariables(target_temp_c=25.3), turn_state(), ControllerState(), CFG)
    assert first.vrf.idu_sp % 0.5 == 0 and "IDU-SP" in first.vrf.changed
    second = apply_safety(first.applied, turn_state(), first.state, CFG)
    assert second.vrf.changed == []


def test_wonder_proposals_are_normalized_field_by_field():
    fallback = ManipulatedVariables(target_temp_c=25.0, fan_speed="auto")
    wire = MVWire(target_temp_c=float("nan"), fan_speed="Mid", louver_yaw_deg=5, louver_pitch_deg=-10,
                  louver_mode="Swing", compressor_mode="FAN_ONLY")  # fmt: skip
    mv, notes = mv_from_wire(wire, fallback)
    assert (mv.target_temp_c, mv.fan_speed, mv.louver_mode, mv.compressor_mode) == (25.0, "med", "swing", "fan_only")
    assert len(notes) == 1
    mv, notes = mv_from_wire(MVWire(fan_speed="turbo-ish"), fallback)
    assert mv.fan_speed == "auto" and any("fan_speed" in n for n in notes)


def test_critic_score_is_clamped_and_the_first_turn_is_a_baseline():
    grounded = RewardBreakdown(score=-0.2, delta_discomfort=0.0, discomfort=0.5, energy=0.4,
                               complaint_penalty=0.0, matched_occupants=0, occupied=True)  # fmt: skip
    first = critic_from_wire(RewardWire(score=3.0, trend="improved", explanation="x"), grounded, first_turn=True)
    assert (first.score, first.trend, first.source) == (1.0, "baseline", "llm")
    missing = critic_from_wire(RewardWire(score=None), grounded, first_turn=False)
    assert (missing.source, missing.score) == ("grounded", -0.2)


def test_fan_steps_treat_auto_as_mid():
    assert (fan_step("auto", -1), fan_step("auto", 1), fan_step("high", 1), fan_step("low", -1)) == ("low", "high", "high", "low")


def test_preferences_follow_evidence_and_the_llm_only_writes_bounded_text():
    after = ControllerState(occupants={"occ-1": OccupantMemory(key="occ-1", bbox=(0, 0, 1, 1), last_seen_turn=1)})
    state = turn_state(occupants=[occupant(cues=["rubbing_arms"], draft=True)])
    wire = [PreferenceWire(occupant_key="occ-1", profile="likes it warm " * 40), PreferenceWire(occupant_key="occ-9", profile="x")]
    profiles = update_preferences(after, state, wire, CFG.limits)
    memory = after.occupants["occ-1"]
    assert memory.temp_offset_c == 0.5 and memory.draft_sensitive
    assert len(profiles["occ-1"]) <= 300 and "occ-9" not in profiles
