import json

from app.spotcool_agent.perception import (
    analyze_door,
    attribute_complaints,
    confirm_door,
    extract_complaints,
    observations_from_spark,
    parse_sam_output,
    parse_transcript,
)
from app.spotcool_agent.preprocess import select_keyframes
from app.spotcool_agent.schemas import Complaint, OccupantState, OccupantWire, SparkWire, TranscriptTurn


def test_complaint_lexicon_covers_the_demo_lines():
    complaints = extract_complaints(
        [TranscriptTurn(text="It's freezing in this meeting room.", speaker="A")],
        [
            "The AC is blowing right on my face.",
            "Can someone turn up the AC?",
            "Please set it to 26 degrees.",
            "It's so hot in here, turn the fan up.",
        ],
    )
    by_category = {c.category: c for c in complaints}
    assert by_category["too_cold"].sensation == -2.5 and by_category["too_cold"].speaker == "A"
    assert by_category["draft"].source == "text"
    assert by_category["set_temperature"].value_c == 26.0
    assert by_category["too_hot"].sensation == 2.0
    assert {"more_cooling", "fan_up"} <= set(by_category)


def test_office_talk_is_not_a_complaint():
    lines = ["Let's move on to the quarterly numbers.", "I left the draft in my inbox.", "Where is my sweater?", "Put it in the freezer."]
    assert extract_complaints([TranscriptTurn(text=line) for line in lines], []) == []


def test_transcripts_in_every_format():
    meta = {"sessionId": "s", "transcript": "It's freezing in here. Yes.", "audioDurationMs": 31000,
            "turns": [{"speaker": "A", "startMs": 9800, "transcript": "It's freezing in here."},
                      {"speaker": "B", "startMs": 12000, "transcript": "Yes."}]}  # fmt: skip
    turns = parse_transcript(json.dumps(meta), "clip audio")
    assert [(t.speaker, t.start_s, t.text) for t in turns] == [("A", 9.8, "It's freezing in here."), ("B", 12.0, "Yes.")]
    assert parse_transcript(json.dumps({"transcript": "Too cold.", "audioDurationMs": 900}), "x")[0].text == "Too cold."
    diarized = {"segments": [{"speaker": "A", "start": 9.8, "end": 11.0, "text": "It's freezing in here."},
                             {"speaker": "B", "start": 12.0, "end": 13.0, "text": " "}]}  # fmt: skip
    turns = parse_transcript(json.dumps(diarized), "clip audio")
    assert [(t.speaker, t.start_s, t.text) for t in turns] == [("A", 9.8, "It's freezing in here.")]
    verbose = {"text": "Hello there.", "segments": [{"start": 0, "end": 1, "text": "Hello there."}]}
    assert parse_transcript(json.dumps(verbose), "x")[0].speaker is None
    assert parse_transcript(json.dumps({"text": "Too hot."}), "x")[0].text == "Too hot."
    assert parse_transcript("Too hot.", "x")[0].text == "Too hot."
    assert parse_transcript(json.dumps({"text": "", "segments": []}), "x") == []


def door_text(frames, c=0.9) -> str:
    return "\n".join(f"<{f}f>5<|box;x1=1;y1=1;x2=9;y2=9;w=100;h=100;c={c}|>" for f in frames)


def test_door_runs_bridge_short_gaps_and_report_edges():
    analysis = analyze_door(parse_sam_output(door_text([*range(2, 13), *range(14, 25)]), "open door"), 25, 1.0, 0.6, 2.0)
    state = analysis.state
    assert analysis.run == (2, 24)
    assert (state.longest_run_s, state.start_run_s, state.end_run_s, state.closed_s_at_end) == (23.0, 0.0, 23.0, 0.0)
    assert not state.spans_clip
    low = analyze_door(parse_sam_output(door_text(range(25), c=0.3), "open door"), 25, 1.0, 0.6, 2.0)
    assert low.state.open_frames == 0 and low.state.closed_s_at_end == 25.0
    whole = analyze_door(parse_sam_output(door_text(range(25)), "open door"), 25, 1.0, 0.6, 2.0)
    assert whole.state.spans_clip and whole.state.start_run_s == whole.state.end_run_s == 25.0


def test_spark_confirms_or_rejects_the_door():
    def analysis():
        return analyze_door(parse_sam_output(door_text(range(2, 25)), "open door"), 25, 1.0, 0.6, 2.0)

    keyframes = [3, 8, 13, 18, 22, 24]
    confirmed = analysis()
    confirm_door(confirmed, keyframes, [False, True, True, True, False, True])
    assert confirmed.state.spark_confirmed and confirmed.state.spark_tiles_in_run == 6
    rejected = analysis()
    confirm_door(rejected, keyframes, [False] * 6)
    assert rejected.state.spark_confirmed is False and rejected.state.spark_open_tiles == 0
    unknown = analysis()
    confirm_door(unknown, keyframes, None)
    assert unknown.state.spark_confirmed is None


def test_spark_observations_are_normalized_and_clamped():
    wire = SparkWire(
        occupants=[
            OccupantWire(person_id="Person 3", clothing_clo=5.0, activity_met=0.2, posture="Sitting",
                         visual_discomfort_cues=["Rubbing arms", "put on a jacket", "dancing"], apparent_thermal_sensation=-9),
            OccupantWire(person_id="P7"),
        ]
    )  # fmt: skip
    warnings: list[str] = []
    observations = observations_from_spark(wire, ["3", "4"], warnings)
    seen = observations["3"]
    assert (seen.clothing_clo, seen.activity_met, seen.posture, seen.apparent_sensation) == (2.0, 0.8, "seated", -3.0)
    assert seen.cues == ["rubbing_arms", "putting_on_jacket"]
    assert not observations["4"].from_model and observations["4"].clothing_clo == 0.6
    assert any("P7" in w for w in warnings)


def occupant(key: str, cues=(), jet: float = 0.15) -> OccupantState:
    return OccupantState(
        occupant_key=key, sam_id="P0", present_ratio=1.0, bbox=(0, 0, 1, 1), posture="seated", distance_m=3.0,
        distance_method="foot", position_m=(0, 1.1, 3), yaw_deg=0.0, pitch_deg=-20.0, clothing_clo=0.6,
        activity_met=1.1, cues=list(cues), jet_speed_ms=jet,
    )  # fmt: skip


def test_complaints_are_attributed_to_what_was_seen():
    draft = Complaint(category="draft", text="blowing on my face", source="voice", sensation=-1.5)
    cold = Complaint(category="too_cold", text="freezing", source="voice", sensation=-2.5)
    hot = Complaint(category="too_hot", text="so hot", source="text", sensation=2.0)
    attribute_complaints([draft, cold, hot], [occupant("occ-1", cues=["rubbing_arms"], jet=0.2), occupant("occ-2", jet=1.4)])
    assert draft.attributed_to == ["occ-2"]  # most exposed to the jet
    assert cold.attributed_to == ["occ-1"]  # the occupant with cold cues
    assert hot.attributed_to == []  # nobody looks hot: room level
    alone = Complaint(category="too_cold", text="cold", source="voice", sensation=-2.0)
    attribute_complaints([alone], [occupant("occ-3")])
    assert alone.attributed_to == ["occ-3"]


def test_keyframes_include_motion_peaks():
    scores = [0.0] * 35
    scores[5], scores[15], scores[16] = 9.0, 8.0, 7.5
    frames = select_keyframes(35, scores)
    assert len(frames) == 6 and 5 in frames and 15 in frames and 16 not in frames
    assert select_keyframes(4) == [0, 1, 2, 3]
    assert len(select_keyframes(35)) == 6
