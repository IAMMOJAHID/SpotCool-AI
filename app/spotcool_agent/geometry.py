import math
import statistics
from collections import Counter
from dataclasses import dataclass, field
from typing import Optional, Sequence

import numpy as np

from .config import Calibration, ControlLimits, VaneAxis, VrfProfile
from .schemas import ManipulatedVariables, OccupantMemory, OccupantState, Track

MIN_DISTANCE_M = 0.5
MAX_DISTANCE_M = 10.0
MIN_FOOT_DEPRESSION_DEG = 10.0
MIN_HEAD_CLEARANCE_M = 0.5


def focal_px(width: int, hfov_deg: float) -> float:
    return (width / 2) / math.tan(math.radians(hfov_deg) / 2)


def rotation(tilt_deg: float, pan_deg: float) -> np.ndarray:
    """R = R_y(pan) · R_x(tilt): tilt down and pan right are positive; there is no roll."""
    t, p = math.radians(tilt_deg), math.radians(pan_deg)
    r_x = np.array([[1, 0, 0], [0, math.cos(t), -math.sin(t)], [0, math.sin(t), math.cos(t)]])
    r_y = np.array([[math.cos(p), 0, math.sin(p)], [0, 1, 0], [-math.sin(p), 0, math.cos(p)]])
    return r_y @ r_x


@dataclass
class CameraModel:
    """Pinhole camera in the room frame (x right, y up, z forward), standing at (0, height, 0)."""

    width: int
    height: int
    hfov_deg: float
    height_m: float
    tilt_deg: float
    pan_deg: float = 0.0
    f: float = field(init=False)
    R: np.ndarray = field(init=False)
    position: np.ndarray = field(init=False)

    def __post_init__(self) -> None:
        self.f = focal_px(self.width, self.hfov_deg)
        self.R = rotation(self.tilt_deg, self.pan_deg)
        self.position = np.array([0.0, self.height_m, 0.0])

    @classmethod
    def from_calibration(cls, cal: Calibration, width: int, height: int) -> "CameraModel":
        camera = cal.camera
        return cls(width, height, camera.hfov_deg, camera.height_m, camera.tilt_deg, camera.pan_deg)

    def ray(self, u: float, v: float) -> np.ndarray:
        """Room-frame direction through normalized pixel (u, v)."""
        x_n = (u * self.width - self.width / 2) / self.f
        y_n = (v * self.height - self.height / 2) / self.f
        return self.R @ np.array([x_n, -y_n, 1.0])

    def project(self, point: Sequence[float]) -> tuple[float, float]:
        """Normalized pixel of a room-frame point (inverse of ray)."""
        p = self.R.T @ (np.asarray(point, dtype=float) - self.position)
        u = (p[0] / p[2] * self.f + self.width / 2) / self.width
        v = (-p[1] / p[2] * self.f + self.height / 2) / self.height
        return float(u), float(v)

    def intersect(self, u: float, v: float, plane_y: float) -> Optional[np.ndarray]:
        r = self.ray(u, v)
        if r[1] >= -1e-9:
            return None
        t = (plane_y - self.position[1]) / r[1]
        return self.position + t * r if t > 0 else None


def resolve_posture(posture: str, box: Sequence[float], width: int, height: int) -> str:
    if posture in ("seated", "standing", "walking"):
        return posture
    x1, y1, x2, y2 = box
    aspect = ((y2 - y1) * height) / max((x2 - x1) * width, 1e-6)
    return "standing" if aspect >= 2.0 else "seated"


@dataclass
class Location:
    """A person's floor position (x, z) in the room frame and how it was estimated."""

    x: float
    z: float
    method: str
    estimates: dict[str, float]

    @property
    def distance_m(self) -> float:
        return math.hypot(self.x, self.z)


def _clamped(point: np.ndarray) -> tuple[float, float]:
    x, z = float(point[0]), float(point[2])
    distance = math.hypot(x, z)
    if distance < 1e-6:
        return 0.0, MIN_DISTANCE_M
    scale = min(max(distance, MIN_DISTANCE_M), MAX_DISTANCE_M) / distance
    return x * scale, z * scale


def locate(camera: CameraModel, box: Sequence[float], posture: str, cal: Calibration) -> Location:
    """Foot point on the floor, else the head plane, else the bbox-height pinhole."""
    x1, y1, x2, y2 = box
    u = (x1 + x2) / 2
    standing = posture in ("standing", "walking")
    points: dict[str, np.ndarray] = {}
    if y2 <= 0.98:
        ray = camera.ray(u, y2)
        if -ray[1] / np.linalg.norm(ray) >= math.sin(math.radians(MIN_FOOT_DEPRESSION_DEG)):
            foot = camera.intersect(u, y2, 0.0)
            if foot is not None:
                points["foot"] = foot
    head_y = cal.standing_head_m if standing else cal.seated_head_m
    if y1 >= 0.02 and camera.height_m - head_y >= MIN_HEAD_CLEARANCE_M:
        head = camera.intersect(u, y1, head_y)
        if head is not None:
            points["head"] = head
    h_px = max((y2 - y1) * camera.height, 1.0)
    ref = cal.standing_ref_height_m if standing else cal.seated_ref_height_m
    depth = camera.f * ref / h_px
    x_n = (u * camera.width - camera.width / 2) / camera.f
    y_n = ((y1 + y2) / 2 * camera.height - camera.height / 2) / camera.f
    points["pinhole"] = camera.position + camera.R @ np.array([x_n * depth, -y_n * depth, depth])
    estimates = {name: round(math.hypot(p[0], p[2]), 3) for name, p in points.items()}
    if "foot" in points and ("head" not in points or estimates["foot"] <= 1.25 * estimates["head"]):
        method = "foot"
    elif "head" in points:
        method = "head"
    else:
        method = "pinhole"
    x, z = _clamped(points[method])
    return Location(x, z, method, estimates)


def locate_track(
    camera: CameraModel, track: Track, posture: str, cal: Calibration, last: int = 10
) -> Location:
    """Median of the per-frame estimates over the track's last frames: where they are now."""
    locations = [locate(camera, d.box, posture, cal) for d in track.detections[-last:]]
    method = Counter(loc.method for loc in locations).most_common(1)[0][0]
    estimates: dict[str, float] = {}
    for name in ("foot", "head", "pinhole"):
        values = [loc.estimates[name] for loc in locations if name in loc.estimates]
        if values:
            estimates[name] = round(statistics.median(values), 3)
    return Location(
        statistics.median(loc.x for loc in locations),
        statistics.median(loc.z for loc in locations),
        method,
        estimates,
    )


def louver_angles(ac_position: Sequence[float], target: Sequence[float]) -> tuple[float, float]:
    """Yaw (+right as seen from the unit) and pitch (0 horizontal, negative down), in degrees."""
    d = np.asarray(target, dtype=float) - np.asarray(ac_position, dtype=float)
    yaw = math.degrees(math.atan2(d[0], d[2]))
    pitch = math.degrees(math.atan2(d[1], math.hypot(d[0], d[2])))
    return yaw, pitch


def direction(yaw_deg: float, pitch_deg: float) -> np.ndarray:
    yaw, pitch = math.radians(yaw_deg), math.radians(pitch_deg)
    return np.array([math.cos(pitch) * math.sin(yaw), math.sin(pitch), math.cos(pitch) * math.cos(yaw)])


def angle_between(yaw1: float, pitch1: float, yaw2: float, pitch2: float) -> float:
    cosine = float(np.clip(direction(yaw1, pitch1) @ direction(yaw2, pitch2), -1.0, 1.0))
    return math.degrees(math.acos(cosine))


@dataclass
class AimDecision:
    yaw_deg: float
    pitch_deg: float
    louver_mode: str
    targets: list[str]
    avoided: list[str]
    reason: str


def _clip(value: float, low: float, high: float) -> float:
    return max(low, min(high, value))


def choose_aim(
    occupants: Sequence[OccupantState],
    memories: dict[str, OccupantMemory],
    previous: ManipulatedVariables,
    limits: ControlLimits,
    profile: VrfProfile,
) -> AimDecision:
    """Spot-cool the aim set (demand-weighted), keep clear of everyone else."""
    if not occupants:
        return AimDecision(
            previous.louver_yaw_deg, previous.louver_pitch_deg, previous.louver_mode, [], [],
            "no occupants: hold the aim",
        )  # fmt: skip

    def sensitive(o: OccupantState) -> bool:
        memory = memories.get(o.occupant_key)
        return bool(memory and memory.draft_sensitive)

    aim = [o for o in occupants if o.in_aim_set and not sensitive(o)]
    avoid = [o for o in occupants if o not in aim]
    if not aim:
        mean_yaw = statistics.fmean(o.yaw_deg for o in avoid)
        side = -1.0 if mean_yaw >= 0 else 1.0
        yaw = _clip(mean_yaw + side * limits.avoid_margin_deg, limits.yaw_min_deg, limits.yaw_max_deg)
        return AimDecision(
            yaw, 0.0, "fixed", [], [o.occupant_key for o in avoid],
            "nobody wants direct airflow: indirect horizontal throw away from occupants",
        )  # fmt: skip
    weights = [1.0 + max(0.0, o.observed_sensation) for o in aim]
    vector = sum(w * direction(o.yaw_deg, o.pitch_deg) for w, o in zip(weights, aim))
    yaw = math.degrees(math.atan2(vector[0], vector[2]))
    pitch = math.degrees(math.atan2(vector[1], math.hypot(vector[0], vector[2])))
    mode, reason = "fixed", f"demand-weighted aim at {', '.join(o.occupant_key for o in aim)}"
    spread = max(o.yaw_deg for o in aim) - min(o.yaw_deg for o in aim)
    if spread > limits.swing_spread_deg:
        horizontal_swing = profile.vane_horizontal is not None and profile.vane_horizontal.swing
        if horizontal_swing and not any(sensitive(o) for o in occupants):
            mode, reason = "swing", f"aim set spans {spread:.0f} deg: swing"
        else:
            top = max(aim, key=lambda o: o.observed_sensation)
            yaw, pitch = top.yaw_deg, top.pitch_deg
            reason = f"aim set spans {spread:.0f} deg: aim at the warmest ({top.occupant_key})"
    for other in avoid:
        if abs(yaw - other.yaw_deg) < limits.avoid_margin_deg:
            side = math.copysign(1.0, yaw - other.yaw_deg) if yaw != other.yaw_deg else (1.0 if other.yaw_deg <= 0 else -1.0)
            yaw = other.yaw_deg + side * limits.avoid_margin_deg
            reason += f"; kept {limits.avoid_margin_deg:.0f} deg off {other.occupant_key}"
    clipped = _clip(yaw, limits.yaw_min_deg, limits.yaw_max_deg)
    if clipped != yaw and any(abs(clipped - o.yaw_deg) < limits.avoid_margin_deg for o in avoid):
        pitch = 0.0
        reason += "; no clear side left: horizontal throw"
    return AimDecision(
        clipped,
        _clip(pitch, limits.pitch_min_deg, limits.pitch_max_deg),
        mode,
        [o.occupant_key for o in aim],
        [o.occupant_key for o in avoid],
        reason,
    )


def half_up(value: float, step: float) -> float:
    """Round to the grid with halves going up (Python's round() goes to even)."""
    return math.floor(value / step + 0.5 + 1e-9) * step


def quantize_hysteretic(value: float, previous: Optional[float], step: float, hysteresis: float) -> float:
    """Hold the previous grid value unless the input moved more than hysteresis·step from it."""
    if previous is not None and abs(value - previous) <= hysteresis * step:
        return previous
    return half_up(value, step)


def nearest_position(angle: float, axis: VaneAxis) -> int:
    return min(range(len(axis.angles_deg)), key=lambda i: abs(axis.angles_deg[i] - angle)) + 1


def quantize_vane(
    angle: float, axis: Optional[VaneAxis], previous: Optional[int], hysteresis: float
) -> Optional[int]:
    """Nearest vane position (1-based), holding the previous one within the hysteresis band."""
    if axis is None or not axis.angles_deg:
        return None
    nearest = nearest_position(angle, axis)
    angles = axis.angles_deg
    if previous is not None and 1 <= previous <= len(angles) and nearest != previous and len(angles) > 1:
        spacing = min(abs(a - b) for a, b in zip(angles, angles[1:]))
        if abs(angle - angles[previous - 1]) <= hysteresis * spacing:
            return previous
    return nearest
