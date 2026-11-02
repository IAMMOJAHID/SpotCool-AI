from typing import Literal, Optional

from pydantic import BaseModel, Field


class ManipulatedVariables(BaseModel):
    temperature_c: Optional[float] = None
    fan_speed_mode: Optional[Literal["LOW", "MED", "HIGH", "AUTO", "OFF"]] = None
    fan_mode: Optional[Literal["ON", "AUTO", "OFF"]] = None
    airflow_rate_m3h: Optional[float] = None
    louver_vertical_deg: Optional[int] = Field(None, ge=0, le=90)
    louver_horizontal_deg: Optional[int] = Field(None, ge=0, le=180)
    swing_mode: Optional[bool] = None
    compressor_mode: Optional[Literal["COOL", "ECO", "FAN_ONLY", "OFF"]] = None
    compressor_frequency_hz: Optional[float] = None
    valve_open_pct: Optional[int] = Field(None, ge=0, le=100)
    eco_mode: Optional[bool] = None
    sleep_mode: Optional[bool] = None
    turbo_mode: Optional[bool] = None
    humidity_target_pct: Optional[float] = Field(None, ge=0, le=100)
    dehumidification_mode: Optional[bool] = None
    fresh_air_damper_pct: Optional[int] = Field(None, ge=0, le=100)
    ventilation_mode: Optional[Literal["AUTO", "ON", "OFF"]] = None
    filter_cleaning_alert: Optional[bool] = None
    defrost_mode: Optional[bool] = None


MANIPULATED_VARIABLE_NAMES: tuple[str, ...] = tuple(ManipulatedVariables.model_fields)


class SceneSemantics(BaseModel):
    occupant_count: int
    metabolic_rate_category: Literal["resting", "sedentary", "light_activity", "high_activity"]
    thermal_insulation_level: Literal["minimal", "light", "moderate", "heavy"]
    comfort_indicator: Literal["neutral", "warm_discomfort", "cool_discomfort"]
    envelope_state: Literal["sealed", "open"]


ClothingEnsemble = Literal[
    "shorts_short_sleeve",
    "skirt_or_dress_light",
    "trousers_short_sleeve",
    "trousers_long_sleeve",
    "suit_jacket",
    "sweater_or_layers",
    "winter_outerwear",
]
Activity = Literal[
    "seated_quiet",
    "seated_typing",
    "standing_relaxed",
    "standing_working",
    "walking",
    "carrying_or_lifting",
    "exercising",
]


class OccupantObservation(BaseModel):
    id: int = Field(description="The number drawn on the person's box in image 1.")
    clothing_ensemble: ClothingEnsemble
    activity: Activity
    posture: Literal["seated", "standing", "walking", "lying"]
    in_direct_sunlight: bool
    discomfort_cue: Literal[
        "none",
        "fanning_self",
        "wiping_sweat",
        "rolled_up_sleeves",
        "hugging_arms_or_shivering",
        "adjusting_clothing",
    ]


class VoiceComfortFeedback(BaseModel):
    sentiment: Literal["too_hot", "too_cold", "stuffy", "drafty", "humid", "satisfied"]
    excerpt: str


class SceneAssessment(BaseModel):
    occupants: list[OccupantObservation]
    total_people_visible: int = Field(description="All people visible in image 1, numbered or not.")
    doors_open: int
    windows_open: int
    solar_gain: Literal["none", "partial", "strong"]
    screens_visible: int
    space_type: Literal[
        "office",
        "meeting_room",
        "retail",
        "lobby",
        "classroom",
        "restaurant",
        "warehouse",
        "other",
    ]
    occupancy_trend: Literal["arriving", "stable", "leaving", "fluctuating"]
    lighting: Literal["dark", "dim", "normal", "bright"]
    voice_comfort_feedback: list[VoiceComfortFeedback]
    summary: str


class Anomaly(BaseModel):
    kind: Literal[
        "telemetry_stale",
        "cooling_ineffective",
        "co2_without_occupants",
        "envelope_leak",
        "sensor_conflict",
        "occupancy_uncertain",
        "air_quality_filtration",
        "other",
    ]
    severity: Literal["info", "warning", "critical"]
    evidence: str


class SupervisorDecision(BaseModel):
    base_plan: Literal["recommended", "comfort_first", "energy_first"]
    overrides: ManipulatedVariables = Field(
        description="Only the variables you change relative to the base plan; null keeps the base plan value."
    )
    rationale: str
    anomalies: list[Anomaly]
    confidence: float = Field(ge=0, le=1)
