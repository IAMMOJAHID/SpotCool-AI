import json
import re
from datetime import datetime, timezone
from typing import Any, Literal, Optional, TypeVar

from pydantic import BaseModel, ConfigDict, Field

FanSpeed = Literal["low", "med", "high", "auto"]
CompressorMode = Literal["cool", "eco_idle", "fan_only"]
LouverMode = Literal["fixed", "swing"]
Posture = Literal["seated", "standing", "walking", "unknown"]
DistanceMethod = Literal["foot", "head", "pinhole"]
ComplaintCategory = Literal[
    "too_cold",
    "too_hot",
    "draft",
    "stuffy",
    "set_temperature",
    "more_cooling",
    "less_cooling",
    "fan_up",
    "fan_down",
]

COLD_CUES = ("rubbing_arms", "putting_on_jacket", "hugging_self", "shivering")
HOT_CUES = ("fanning_self", "wiping_sweat", "removing_layer")
DRAFT_CUES = ("shielding_from_airflow",)
VISUAL_CUES = COLD_CUES + HOT_CUES + DRAFT_CUES
COLD_CATEGORIES = ("too_cold", "draft", "less_cooling")
HOT_CATEGORIES = ("too_hot", "stuffy", "more_cooling")


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


class Detection(BaseModel):
    """One SAM box, normalized to [0, 1] with half-open right/bottom edges."""

    frame: int
    object_id: str
    x1: float
    y1: float
    x2: float
    y2: float
    confidence: Optional[float] = None
    has_mask: bool = False

    @property
    def box(self) -> tuple[float, float, float, float]:
        return (self.x1, self.y1, self.x2, self.y2)

    @property
    def center(self) -> tuple[float, float]:
        return ((self.x1 + self.x2) / 2, (self.y1 + self.y2) / 2)


class SamParseResult(BaseModel):
    concept: str
    detections: list[Detection] = Field(default_factory=list)
    frame_size: Optional[tuple[int, int]] = None
    warnings: list[str] = Field(default_factory=list)


class Track(BaseModel):
    track_id: str
    concept: str
    detections: list[Detection]
    merged_ids: list[str] = Field(default_factory=list)

    @property
    def first_frame(self) -> int:
        return self.detections[0].frame

    @property
    def last_frame(self) -> int:
        return self.detections[-1].frame

    def at(self, frame: int) -> Optional[Detection]:
        for detection in self.detections:
            if detection.frame == frame:
                return detection
        return None


class ClipInfo(BaseModel):
    label: str
    source: Optional[str] = None
    segment_start_s: float = 0.0
    duration_s: float
    fps: float = 1.0
    frames: int
    frame_size: tuple[int, int]
    audio_sources: list[str] = Field(default_factory=list)
    texts: list[str] = Field(default_factory=list)
    gap_s: float = 0.0
    keyframes: list[int] = Field(default_factory=list)


class TranscriptTurn(BaseModel):
    text: str
    speaker: Optional[str] = None
    start_s: Optional[float] = None
    end_s: Optional[float] = None
    source: str = "voice"


class Complaint(BaseModel):
    category: ComplaintCategory
    text: str
    source: Literal["voice", "text"]
    speaker: Optional[str] = None
    sensation: Optional[float] = None
    value_c: Optional[float] = None
    # Occupant keys the complaint is attributed to; empty means the whole room.
    attributed_to: list[str] = Field(default_factory=list)


class DoorState(BaseModel):
    longest_run_s: float = 0.0
    start_run_s: float = 0.0
    end_run_s: float = 0.0
    spans_clip: bool = False
    open_frames: int = 0
    closed_s_at_end: float = 0.0
    spark_open_tiles: Optional[int] = None
    spark_tiles_in_run: Optional[int] = None
    spark_confirmed: Optional[bool] = None
    # Filled by the controller: the open streak carried in from the previous turn, the
    # resulting open time, the streak carried into the next turn, and the override state.
    carried_s: float = 0.0
    effective_open_s: float = 0.0
    streak_s: float = 0.0
    override_active: bool = False


class OccupantObservation(BaseModel):
    """Muse Spark's view of one tracked person, clamped to valid ranges."""

    person_id: str
    clothing_clo: float = 0.6
    activity_met: float = 1.1
    cues: list[str] = Field(default_factory=list)
    posture: Posture = "unknown"
    posture_summary: str = ""
    apparent_sensation: Optional[float] = None
    from_model: bool = False


class OccupantState(BaseModel):
    occupant_key: str
    sam_id: str
    present_ratio: float
    bbox: tuple[float, float, float, float]
    posture: Posture
    distance_m: float
    distance_method: DistanceMethod
    position_m: tuple[float, float, float]
    yaw_deg: float
    pitch_deg: float
    clothing_clo: float
    activity_met: float
    cues: list[str] = Field(default_factory=list)
    apparent_sensation: Optional[float] = None
    observed_sensation: float = 0.0
    observed: bool = False
    pmv: float = 0.0
    ppd: float = 5.0
    jet_angle_deg: Optional[float] = None
    jet_speed_ms: Optional[float] = None
    draft_rating_pct: Optional[float] = None
    draft: bool = False
    in_aim_set: bool = True
    distance_estimates_m: dict[str, float] = Field(default_factory=dict)


class TurnState(BaseModel):
    """S_t: everything perceived during one turn's clip."""

    turn: int
    clip: ClipInfo
    occupants: list[OccupantState] = Field(default_factory=list)
    occupant_count: int = 0
    occupied: bool = False
    door: DoorState = Field(default_factory=DoorState)
    transcript: list[TranscriptTurn] = Field(default_factory=list)
    complaints: list[Complaint] = Field(default_factory=list)
    scene_summary: str = ""
    discomfort: float = 0.0
    room_temp_c: float = 24.0
    degraded: list[str] = Field(default_factory=list)


class ManipulatedVariables(BaseModel):
    """A_t. The defaults are A_0, the VRF gateway's relinquish defaults."""

    target_temp_c: float = 24.0
    fan_speed: FanSpeed = "auto"
    louver_yaw_deg: float = 0.0
    louver_pitch_deg: float = -30.0
    louver_mode: LouverMode = "fixed"
    compressor_mode: CompressorMode = "cool"


class VrfCommand(BaseModel):
    """Contract-ready points for VRF-Gateway-MeetingRoom (only changed points need writing)."""

    profile: str
    idu_onoff: Literal["Off", "On"] = "On"
    idu_sp: float
    idu_mode: Literal["Cool", "Dry", "Fan", "Auto"]
    idu_fan: Literal["Auto", "Low", "Mid", "High"]
    idu_vane_v: Optional[str] = None
    idu_vane_h: Optional[str] = None
    vane_v_angle_deg: Optional[float] = None
    vane_h_angle_deg: Optional[float] = None
    changed: list[str] = Field(default_factory=list)

    def points(self) -> dict[str, Any]:
        points: dict[str, Any] = {
            "IDU-ONOFF": self.idu_onoff,
            "IDU-SP": self.idu_sp,
            "IDU-MODE": self.idu_mode,
            "IDU-FAN": self.idu_fan,
        }
        if self.idu_vane_v is not None:
            points["IDU-VANE-V"] = self.idu_vane_v
        if self.idu_vane_h is not None:
            points["IDU-VANE-H"] = self.idu_vane_h
        return points


class Adjustment(BaseModel):
    field: str
    proposed: Any
    applied: Any
    reasons: list[str] = Field(default_factory=list)


class OccupantMemory(BaseModel):
    key: str
    bbox: tuple[float, float, float, float]
    last_seen_turn: int
    absent_turns: int = 0
    profile: str = ""
    temp_offset_c: float = 0.0
    draft_sensitive: bool = False
    in_aim_set: bool = True
    last_sensation: float = 0.0


class ControllerState(BaseModel):
    """Everything the controller carries between turns; stored before and after each turn."""

    applied: ManipulatedVariables = Field(default_factory=ManipulatedVariables)
    vrf: Optional[VrfCommand] = None
    internal_temp_c: float = 24.0
    internal_yaw_deg: float = 0.0
    internal_pitch_deg: float = -30.0
    quant_temp_c: float = 24.0
    quant_v_pos: Optional[int] = 3
    quant_h_pos: Optional[int] = 3
    compressor_off_since_s: Optional[float] = None
    elapsed_s: float = 0.0
    empty_streak_s: float = 0.0
    door_open_streak_s: float = 0.0
    door_override_active: bool = False
    pre_eco_temp_c: Optional[float] = None
    room_temp_c: Optional[float] = None
    room_offset_c: float = 0.0
    occupants: dict[str, OccupantMemory] = Field(default_factory=dict)
    next_occupant_index: int = 1


class RewardBreakdown(BaseModel):
    score: float
    delta_discomfort: float
    discomfort: float
    energy: float
    complaint_penalty: float
    matched_occupants: int
    occupied: bool


class RewardEvaluation(BaseModel):
    score: float
    source: Literal["llm", "grounded"]
    trend: Literal["improved", "unchanged", "degraded", "baseline"]
    explanation: str
    grounded: RewardBreakdown


class TurnRecord(BaseModel):
    turn: int
    created_at: str = Field(default_factory=utc_now)
    mode: str
    state: TurnState
    previous_action: ManipulatedVariables
    reward: RewardEvaluation
    proposal_source: Literal["llm", "fallback"]
    proposed: ManipulatedVariables
    applied: ManipulatedVariables
    adjustments: list[Adjustment] = Field(default_factory=list)
    overrides: list[str] = Field(default_factory=list)
    vrf_command: VrfCommand
    occupancy: dict[str, Any] = Field(default_factory=dict)
    rationale: str = ""
    updated_preferences: dict[str, str] = Field(default_factory=dict)
    state_before: ControllerState
    state_after: ControllerState
    degraded: list[str] = Field(default_factory=list)
    warnings: list[str] = Field(default_factory=list)
    timings_s: dict[str, float] = Field(default_factory=dict)
    response_ids: dict[str, str] = Field(default_factory=dict)


class EpisodeState(BaseModel):
    episode_id: str
    created_at: str = Field(default_factory=utc_now)
    updated_at: str = Field(default_factory=utc_now)
    calibration: dict[str, Any] = Field(default_factory=dict)
    turns: list[TurnRecord] = Field(default_factory=list)


# Wire models: what the LLMs return. Defaults keep parsing lenient; strict_json_schema() turns
# them into a strict JSON schema (every field required, no additional properties).


class _Wire(BaseModel):
    model_config = ConfigDict(extra="ignore")


class OccupantWire(_Wire):
    person_id: str = ""
    clothing_clo: Optional[float] = None
    activity_met: Optional[float] = None
    visual_discomfort_cues: list[str] = Field(default_factory=list)
    posture: str = "unknown"
    posture_summary: str = ""
    apparent_thermal_sensation: Optional[float] = None


class SparkWire(_Wire):
    occupants: list[OccupantWire] = Field(default_factory=list)
    door_open_by_frame: list[bool] = Field(default_factory=list)
    scene_summary: str = ""


class RewardWire(_Wire):
    score: Optional[float] = None
    trend: str = ""
    explanation: str = ""


class PreferenceWire(_Wire):
    occupant_key: str = ""
    profile: str = ""


class MVWire(_Wire):
    target_temp_c: Optional[float] = None
    fan_speed: Optional[str] = None
    louver_yaw_deg: Optional[float] = None
    louver_pitch_deg: Optional[float] = None
    louver_mode: Optional[str] = None
    compressor_mode: Optional[str] = None


class CriticPolicyWire(_Wire):
    reward_evaluation: RewardWire = Field(default_factory=RewardWire)
    updated_preferences: list[PreferenceWire] = Field(default_factory=list)
    manipulated_variables: MVWire = Field(default_factory=MVWire)
    rationale: str = ""


WireT = TypeVar("WireT", bound=BaseModel)
_FENCE = re.compile(r"```(?:json)?\s*(.*?)```", re.DOTALL)


def extract_json(text: str) -> Any:
    """Return the JSON value in a model reply: bare, fenced, or the first balanced object."""
    text = text.strip()
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        pass
    fenced = _FENCE.search(text)
    if fenced:
        try:
            return json.loads(fenced.group(1))
        except json.JSONDecodeError:
            pass
    start = text.find("{")
    while start != -1:
        depth, in_string, escaped = 0, False, False
        for index in range(start, len(text)):
            char = text[index]
            if in_string:
                if escaped:
                    escaped = False
                elif char == "\\":
                    escaped = True
                elif char == '"':
                    in_string = False
            elif char == '"':
                in_string = True
            elif char == "{":
                depth += 1
            elif char == "}":
                depth -= 1
                if depth == 0:
                    try:
                        return json.loads(text[start : index + 1])
                    except json.JSONDecodeError:
                        break
        start = text.find("{", start + 1)
    raise ValueError("no JSON object found in model output")


def parse_wire(model: type[WireT], text: str) -> WireT:
    return model.model_validate(extract_json(text))


def strict_json_schema(model: type[BaseModel]) -> dict[str, Any]:
    """JSON schema for strict structured outputs: all properties required, none additional."""
    schema = model.model_json_schema()
    _make_strict(schema)
    return schema


def _make_strict(node: Any) -> None:
    if isinstance(node, dict):
        properties = node.get("properties")
        if isinstance(properties, dict):
            node["required"] = list(properties)
            node["additionalProperties"] = False
            for name, prop in list(properties.items()):
                if isinstance(prop, dict):
                    prop.pop("default", None)
                    refs = prop.get("allOf")
                    if isinstance(refs, list) and len(refs) == 1 and "$ref" in refs[0]:
                        properties[name] = {"$ref": refs[0]["$ref"]}
        for value in node.values():
            _make_strict(value)
    elif isinstance(node, list):
        for item in node:
            _make_strict(item)
