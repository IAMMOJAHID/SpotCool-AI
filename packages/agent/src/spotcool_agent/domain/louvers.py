"""Where the people are on the floor, where the louvers should point, and the vane states for it.

``CameraPose``, ``locate``, ``project_floor_point``, ``louver_angles_to`` and ``louver_aim`` are
Person A's, from the MVP on ``third_level_improvement`` (``b653cbf``), kept near-verbatim:
``perception.py`` (``_intrinsics``, ``locate``, ``project_floor_point``), ``physics.py``
(``louver_angles_to``), ``control.py`` (``louver_aim``) and ``config.py`` (``CAMERA_DEFAULTS``,
``louver_beam_half_angle_deg``). The camera dict became a typed ``CameraPose``; ``louver_aim``
weights each person by their box's area (A weighted by dwell) and leaves out A's draft offset,
because a draft complaint sets the vertical vane itself.

``vane_states`` maps an aim onto a brand's gateway profile: vertical = -tilt, horizontal = pan,
each clamped to the axis's range and snapped to its nearest position; the swing goes to the
horizontal axis, or to the vertical one when the brand has no horizontal axis (A's swing is one
flag for the unit); an axis the brand lacks is left out, and LG, with swing only, gets Swing or
a release (its relinquish default, Pos3, is swing off).

Frames: ``lateral_m`` is positive to the camera's right, ``forward_m`` away from it along the
floor; a pan is positive to the right; a tilt is degrees below horizontal.
"""

import math
from dataclasses import dataclass
from typing import Literal

from spotcool_agent.domain.geometry import Detection
from spotcool_contracts.vrf import VaneAxis, VrfProfile

BEAM_HALF_ANGLE_DEG = 15.0
"""The louver jet's half-angle (A's ``louver_beam_half_angle_deg``)."""
SWING_SPREAD = 1.5
"""The pan spread, in beam half-angles, above which the louvers swing (A's rule)."""
SWING = "Swing"
"""The louver state that sweeps the axis."""
VANE_V = "IDU-VANE-V"
VANE_H = "IDU-VANE-H"


@dataclass(frozen=True)
class CameraPose:
    """The live camera's pose, and the indoor unit's outlet in the same floor frame.

    The defaults are A's ``CAMERA_DEFAULTS``: the camera 2.8 m up, tilted 30 deg down, 90 deg
    across; the unit right under it at 2.6 m, aiming at a torso 1.1 m up.

    Parameters:
        mount_height_m: The camera's height above the floor.
        tilt_down_deg: Its tilt below horizontal.
        horizontal_fov_deg: Its horizontal field of view.
        ac_lateral_m: The unit's outlet, to the camera's right.
        ac_forward_m: The outlet, forward of the camera.
        ac_height_m: The outlet's height.
        standing_height_m: A standing person's height, for the pinhole fallback.
        seated_height_m: A seated person's height.
        target_height_m: Where on a person the jet aims (the torso).
    """

    mount_height_m: float = 2.8
    tilt_down_deg: float = 30.0
    horizontal_fov_deg: float = 90.0
    ac_lateral_m: float = 0.0
    ac_forward_m: float = 0.0
    ac_height_m: float = 2.6
    standing_height_m: float = 1.7
    seated_height_m: float = 1.3
    target_height_m: float = 1.1


@dataclass(frozen=True)
class FloorPoint:
    """Where a person stands on the floor (A's ``locate`` result).

    Parameters:
        lateral_m: To the camera's right.
        forward_m: Away from the camera.
        method: ``ground_plane`` (a ray from the feet) or ``pinhole_height`` (from box height).
        standing: Whether the box looks like a standing person.
    """

    lateral_m: float
    forward_m: float
    method: Literal["ground_plane", "pinhole_height"]
    standing: bool

    @property
    def distance_m(self) -> float:
        """The distance from the camera's foot."""
        return math.hypot(self.lateral_m, self.forward_m)


@dataclass(frozen=True)
class Aim:
    """Where the louvers should point (A's ``louver_aim``).

    Parameters:
        pan_deg: The weighted centroid's pan, positive to the unit's right.
        tilt_deg: Its tilt below horizontal.
        swing: Whether the people are spread too wide for one jet.
        spread_deg: The pan spread (weighted standard deviation).
        people: How many people it aims at.
        distance_m: The weighted mean distance from the camera's foot.
        lateral_m: The weighted mean lateral position, positive to the right.
    """

    pan_deg: float
    tilt_deg: float
    swing: bool
    spread_deg: float
    people: int
    distance_m: float
    lateral_m: float

    def reason(self) -> str:
        """The decision's reason, such as ``aim at 1 person at 2.1 m, 0.4 m right``."""
        who = "1 person" if self.people == 1 else f"{self.people} people"
        side = "right" if self.lateral_m >= 0 else "left"
        where = f"at {self.distance_m:.1f} m, {abs(self.lateral_m):.1f} m {side}"
        spread = f"; spread {self.spread_deg:.0f} deg, swinging" if self.swing else ""
        return f"aim at {who} {where}{spread}"


def _intrinsics(width: int, height: int, camera: CameraPose) -> tuple[float, float, float, float]:
    fx = (width / 2) / math.tan(math.radians(camera.horizontal_fov_deg) / 2)
    return fx, fx, width / 2, height / 2


def locate(detection: Detection, width: int, height: int, camera: CameraPose) -> FloorPoint:
    """A detection's floor position: a ground-plane ray from the foot pixel, else pinhole size.

    Args:
        detection: The person's box, in fractions of the image.
        width: The image's width in pixels.
        height: The image's height in pixels.
        camera: The camera's pose.
    """
    fx, fy, cx, cy = _intrinsics(width, height, camera)
    tilt = math.radians(camera.tilt_down_deg)
    mount = camera.mount_height_m
    u = detection.cx * width
    v = detection.y2 * height
    xr, yr = (u - cx) / fx, (v - cy) / fy
    y_world = yr * math.cos(tilt) + math.sin(tilt)
    z_world = -yr * math.sin(tilt) + math.cos(tilt)
    box_h = (detection.y2 - detection.y1) * height
    box_w = (detection.x2 - detection.x1) * width
    standing = box_h / max(box_w, 1.0) > 2.0
    person_height = camera.standing_height_m if standing else camera.seated_height_m
    slant = fy * person_height / max(box_h, 1.0)
    pinhole_forward = math.sqrt(max(slant**2 - (mount - person_height / 2) ** 2, 0.25))
    pinhole_lateral = (u - cx) * slant / fx
    method: Literal["ground_plane", "pinhole_height"] = "pinhole_height"
    lateral, forward = pinhole_lateral, pinhole_forward
    if y_world > 0.02 and detection.y2 < 0.985:
        scale = mount / y_world
        ground_lateral, ground_forward = scale * xr, scale * z_world
        if 0 < ground_forward <= 1.6 * pinhole_forward:
            method = "ground_plane"
            lateral, forward = ground_lateral, ground_forward
    forward = max(0.3, min(40.0, forward))
    return FloorPoint(round(lateral, 2), round(forward, 2), method, standing)


def project_floor_point(
    lateral_m: float, forward_m: float, height_m: float, width: int, height: int, camera: CameraPose
) -> tuple[int, int] | None:
    """The pixel a point ``height_m`` above the floor shows at, or ``None`` behind the camera."""
    fx, fy, cx, cy = _intrinsics(width, height, camera)
    tilt = math.radians(camera.tilt_down_deg)
    y_world = camera.mount_height_m - height_m
    y_cam = y_world * math.cos(tilt) - forward_m * math.sin(tilt)
    z_cam = y_world * math.sin(tilt) + forward_m * math.cos(tilt)
    if z_cam <= 0.1:
        return None
    return int(cx + fx * lateral_m / z_cam), int(cy + fy * y_cam / z_cam)


def louver_angles_to(
    lateral_m: float, forward_m: float, camera: CameraPose
) -> tuple[float, float, float]:
    """``(pan_deg, tilt_down_deg, distance_m)`` from the unit's outlet to an occupant's torso."""
    dx = lateral_m - camera.ac_lateral_m
    dz = forward_m - camera.ac_forward_m
    dh = camera.ac_height_m - camera.target_height_m
    horizontal = math.hypot(dx, dz)
    return (
        math.degrees(math.atan2(dx, dz)),
        math.degrees(math.atan2(dh, horizontal)),
        math.hypot(horizontal, dh),
    )


def louver_aim(
    people: list[tuple[FloorPoint, float]],
    camera: CameraPose,
    *,
    beam_half_angle_deg: float = BEAM_HALF_ANGLE_DEG,
) -> Aim | None:
    """The weighted centroid of everyone's louver angles, and whether to swing (A's rule).

    Args:
        people: Each person's floor position and weight (their box's area).
        camera: The camera's pose, with the unit's outlet.
        beam_half_angle_deg: The jet's half-angle; the louvers swing when the pan spread is
            more than ``SWING_SPREAD`` of it.

    Returns:
        The aim, or ``None`` for nobody.
    """
    if not people:
        return None
    total = sum(weight for _, weight in people) or 1.0
    angles = [louver_angles_to(p.lateral_m, p.forward_m, camera) for p, _ in people]
    pan = sum(w * a[0] for (_, w), a in zip(people, angles, strict=True)) / total
    tilt = sum(w * a[1] for (_, w), a in zip(people, angles, strict=True)) / total
    spread = math.sqrt(
        sum(w * (a[0] - pan) ** 2 for (_, w), a in zip(people, angles, strict=True)) / total
    )
    return Aim(
        pan_deg=pan,
        tilt_deg=tilt,
        swing=spread > SWING_SPREAD * beam_half_angle_deg,
        spread_deg=spread,
        people=len(people),
        distance_m=sum(w * p.distance_m for p, w in people) / total,
        lateral_m=sum(w * p.lateral_m for p, w in people) / total,
    )


def nearest_position(angle_deg: float, axis: VaneAxis) -> str:
    """The axis's position nearest an angle, clamped to its range: ``Pos1``-``Pos5``.

    Raises:
        ValueError: If the axis has no positions (swing only).
    """
    if not axis.angles_deg:
        raise ValueError("the axis has no positions")
    low, high = min(axis.angles_deg), max(axis.angles_deg)
    clamped = min(max(angle_deg, low), high)
    index = min(range(len(axis.angles_deg)), key=lambda k: abs(axis.angles_deg[k] - clamped))
    return f"Pos{index + 1}"


def vane_states(aim: Aim, profile: VrfProfile, *, mirror: bool = False) -> dict[str, str | None]:
    """Each vane point's state for an aim on a brand's gateway (see the module docstring).

    Args:
        aim: Where to point.
        profile: The brand's gateway profile.
        mirror: The camera faces the unit, so its right is the unit's left: the pan flips.

    Returns:
        ``IDU-VANE-V`` and ``IDU-VANE-H`` states; ``None`` releases one; an axis the brand
        lacks is left out.
    """
    vertical, horizontal = profile.vane_vertical, profile.vane_horizontal
    swing_h = aim.swing and horizontal is not None and horizontal.swing
    swing_v = aim.swing and not swing_h and vertical is not None and vertical.swing
    pan = -aim.pan_deg if mirror else aim.pan_deg
    states: dict[str, str | None] = {}
    if vertical is not None:
        states[VANE_V] = _state(-aim.tilt_deg, vertical, swing=swing_v)
    if horizontal is not None:
        states[VANE_H] = _state(pan, horizontal, swing=swing_h)
    return states


def _state(angle_deg: float, axis: VaneAxis, *, swing: bool) -> str | None:
    """Swing, the nearest position, or ``None`` (a release) on a swing-only axis not swinging."""
    if swing:
        return SWING
    return nearest_position(angle_deg, axis) if axis.positions else None
