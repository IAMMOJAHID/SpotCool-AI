import json
import os
from pathlib import Path
from typing import Literal, Optional

from pydantic import BaseModel, Field

Mode = Literal["live", "mock", "replay"]
VrfVendor = Literal["mitsubishi", "daikin", "lg"]


class ModelNames(BaseModel):
    sam: str = "sam-3.1"
    spark: str = "muse-spark-1.3"
    transcribe: str = "muse-voice-transcribe-1.0"
    wonder: str = "muse-wonder-1.0"
    image: str = "muse-image"


class Settings(BaseModel):
    mode: Mode = "live"
    base_url: str = Field(
        default_factory=lambda: os.getenv("META_API_BASE_URL", "https://api.meta.ai/v1")
    )
    models: ModelNames = Field(default_factory=ModelNames)
    wonder_reasoning_effort: str = "low"
    upload_timeout_s: float = 180.0
    sam_timeout_s: float = 300.0
    spark_timeout_s: float = 120.0
    transcribe_timeout_s: float = 120.0
    wonder_timeout_s: float = 180.0
    image_timeout_s: float = 120.0
    ffmpeg_timeout_s: float = 300.0
    upload_expires_s: int = 3600
    max_retries: int = 2
    sam_include_confidence: bool = True


class CameraCalibration(BaseModel):
    hfov_deg: float = 70.0
    height_m: float = 2.5
    # Downward tilt of the optical axis, positive down.
    tilt_deg: float = 25.0
    # Camera heading relative to the AC's facing direction, positive right.
    pan_deg: float = 0.0


class AcCalibration(BaseModel):
    # Room frame: origin on the floor below the camera; x right as seen from the unit, y up,
    # z forward along the AC's facing. The VRF is virtual, so by default it sits at the camera.
    x_m: float = 0.0
    y_m: Optional[float] = None  # mounting height; None means the camera height
    z_m: float = 0.0


class Calibration(BaseModel):
    camera: CameraCalibration = Field(default_factory=CameraCalibration)
    ac: AcCalibration = Field(default_factory=AcCalibration)
    vrf_profile: VrfVendor = "mitsubishi"
    standing_head_m: float = 1.70
    seated_head_m: float = 1.30
    standing_aim_m: float = 1.40
    seated_aim_m: float = 1.10
    # Reference heights for the bbox-height pinhole fallback (visible body height).
    standing_ref_height_m: float = 1.70
    seated_ref_height_m: float = 1.25
    rh_pct: float = 50.0
    # Measured room temperature; None means the first-order lag model is used.
    room_temp_c: Optional[float] = None
    ambient_temp_c: float = 30.0
    room_tau_s: float = 900.0
    room_air_speed_ms: float = 0.15

    @property
    def ac_position(self) -> tuple[float, float, float]:
        y = self.camera.height_m if self.ac.y_m is None else self.ac.y_m
        return (self.ac.x_m, y, self.ac.z_m)


class ControlLimits(BaseModel):
    max_temp_step_c: float = 1.5
    ema_alpha: float = 0.5
    temp_min_c: float = 16.0
    temp_max_c: float = 30.0
    occupied_temp_min_c: float = 20.0
    occupied_temp_max_c: float = 28.0
    temp_grid_c: float = 0.5
    quantizer_hysteresis: float = 0.6
    complaint_step_c: float = 1.0
    yaw_min_deg: float = -40.0
    yaw_max_deg: float = 40.0
    pitch_min_deg: float = -60.0
    pitch_max_deg: float = 0.0
    door_open_threshold_s: float = 20.0
    door_gap_bridge_s: float = 2.0
    door_close_clear_s: float = 10.0
    empty_debounce_s: float = 60.0
    eco_setpoint_c: float = 27.0
    compressor_min_off_s: float = 180.0
    draft_deflect_deg: float = 15.0
    draft_attribution_deg: float = 20.0
    avoid_margin_deg: float = 20.0
    swing_spread_deg: float = 30.0
    aim_enter_sensation: float = 0.5
    aim_leave_sensation: float = -0.5
    pref_offset_step_c: float = 0.5
    pref_offset_max_c: float = 3.0
    room_offset_max_c: float = 2.0
    profile_max_chars: int = 300
    person_min_confidence: float = 0.4
    door_min_confidence: float = 0.6
    reid_gate: float = 0.12
    reid_max_absent_turns: int = 3


class VaneAxis(BaseModel):
    angles_deg: list[float]
    swing: bool


class VrfProfile(BaseModel):
    vendor: VrfVendor
    vane_vertical: Optional[VaneAxis]
    vane_horizontal: Optional[VaneAxis]


# Mirrors origin/world/main:packages/contracts/src/spotcool_contracts/points/vrf_profiles.yaml
# (contract v0.1). The contracts package needs Python >= 3.11, so the values are copied here.
# Vertical: position 1 = horizontal throw ... 5 = steepest down. Horizontal: 1 = far left.
VRF_PROFILES: dict[str, VrfProfile] = {
    "mitsubishi": VrfProfile(
        vendor="mitsubishi",
        vane_vertical=VaneAxis(angles_deg=[0, -15, -30, -45, -60], swing=True),
        vane_horizontal=VaneAxis(angles_deg=[-40, -20, 0, 20, 40], swing=True),
    ),
    "daikin": VrfProfile(
        vendor="daikin",
        vane_vertical=VaneAxis(angles_deg=[0, -15, -30, -45, -60], swing=True),
        vane_horizontal=None,
    ),
    "lg": VrfProfile(
        vendor="lg",
        vane_vertical=VaneAxis(angles_deg=[], swing=True),
        vane_horizontal=None,
    ),
}

# Relinquish defaults of origin/world/main:.../points/vrf_gateway.yaml, used as A_0.
RELINQUISH_SETPOINT_C = 24.0
RELINQUISH_VANE_POSITION = 3


class AppConfig(BaseModel):
    settings: Settings = Field(default_factory=Settings)
    calibration: Calibration = Field(default_factory=Calibration)
    limits: ControlLimits = Field(default_factory=ControlLimits)

    @property
    def profile(self) -> VrfProfile:
        return VRF_PROFILES[self.calibration.vrf_profile]


def load_config(path: Optional[Path] = None) -> AppConfig:
    """Load an AppConfig; a JSON file may hold partial "settings", "calibration" and "limits"."""
    if path is None:
        return AppConfig()
    return AppConfig.model_validate(json.loads(Path(path).read_text(encoding="utf-8")))
