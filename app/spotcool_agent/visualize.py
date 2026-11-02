import math
import zlib
from typing import Optional, Sequence

import cv2
import numpy as np

from .config import Calibration
from .schemas import Detection, DoorState, ManipulatedVariables, OccupantState, Track, VrfCommand

TILE_SIZE = (640, 360)
FONT = cv2.FONT_HERSHEY_SIMPLEX
DOOR_COLOR = (0, 140, 255)
JET_LENGTH_M = {"low": 3.0, "med": 4.5, "auto": 4.5, "high": 6.0}


def id_color(identifier: str) -> tuple[int, int, int]:
    hue = zlib.crc32(identifier.encode()) % 180
    bgr = cv2.cvtColor(np.uint8([[[hue, 200, 255]]]), cv2.COLOR_HSV2BGR)[0, 0]
    return int(bgr[0]), int(bgr[1]), int(bgr[2])


def sensation_color(sensation: float) -> tuple[int, int, int]:
    if sensation <= -0.5:
        return (255, 160, 40)
    if sensation >= 0.5:
        return (40, 60, 255)
    return (80, 200, 80)


def letterbox(image: np.ndarray, size: tuple[int, int]) -> tuple[np.ndarray, float, int, int]:
    """Fit the image into size (w, h); returns the canvas, scale and offsets."""
    width, height = size
    scale = min(width / image.shape[1], height / image.shape[0])
    resized = cv2.resize(image, (max(1, round(image.shape[1] * scale)), max(1, round(image.shape[0] * scale))))
    canvas = np.zeros((height, width, 3), dtype=np.uint8)
    dx, dy = (width - resized.shape[1]) // 2, (height - resized.shape[0]) // 2
    canvas[dy : dy + resized.shape[0], dx : dx + resized.shape[1]] = resized
    return canvas, scale, dx, dy


def _label(image: np.ndarray, text: str, origin: tuple[int, int], color: tuple[int, int, int], scale: float = 0.5) -> None:
    (w, h), base = cv2.getTextSize(text, FONT, scale, 1)
    x, y = origin
    y = max(y, h + 4)
    cv2.rectangle(image, (x, y - h - 4), (x + w + 4, y + base - 2), (0, 0, 0), -1)
    cv2.putText(image, text, (x + 2, y - 2), FONT, scale, color, 1, cv2.LINE_AA)


def _draw_box(image: np.ndarray, box: Sequence[float], frame_w: int, frame_h: int, scale: float, dx: int, dy: int,
              color: tuple[int, int, int], text: str) -> None:  # fmt: skip
    x1, y1 = int(box[0] * frame_w * scale) + dx, int(box[1] * frame_h * scale) + dy
    x2, y2 = int(box[2] * frame_w * scale) + dx, int(box[3] * frame_h * scale) + dy
    cv2.rectangle(image, (x1, y1), (x2, y2), color, 2)
    _label(image, text, (x1, y1), color)


def build_grid(
    frames: Sequence[np.ndarray],
    keyframes: Sequence[int],
    tracks: Sequence[Track],
    doors: dict[int, list[Detection]],
    fps: float,
) -> np.ndarray:
    """3x2 storyboard of the keyframes with SAM person IDs (P<n>) and open-door boxes."""
    tile_w, tile_h = TILE_SIZE
    tiles = []
    for number, frame_index in enumerate(keyframes[:6], 1):
        frame = frames[frame_index]
        frame_h, frame_w = frame.shape[:2]
        tile, scale, dx, dy = letterbox(frame, TILE_SIZE)
        for door in doors.get(frame_index, []):
            _draw_box(tile, door.box, frame_w, frame_h, scale, dx, dy, DOOR_COLOR, "door")
        for track in tracks:
            detection = track.at(frame_index)
            if detection is not None:
                _draw_box(tile, detection.box, frame_w, frame_h, scale, dx, dy, id_color(track.track_id), f"P{track.track_id}")
        _label(tile, f"#{number}  t={frame_index / fps:.0f}s", (6, tile_h - 8), (255, 255, 255), 0.6)
        tiles.append(tile)
    while len(tiles) < 6:
        tiles.append(np.zeros((tile_h, tile_w, 3), dtype=np.uint8))
    return np.vstack([np.hstack(tiles[:3]), np.hstack(tiles[3:6])])


def encode_jpeg(image: np.ndarray, quality: int = 85) -> bytes:
    ok, buffer = cv2.imencode(".jpg", image, [cv2.IMWRITE_JPEG_QUALITY, quality])
    if not ok:
        raise RuntimeError("JPEG encoding failed")
    return buffer.tobytes()


def encode_png(image: np.ndarray) -> bytes:
    ok, buffer = cv2.imencode(".png", image)
    if not ok:
        raise RuntimeError("PNG encoding failed")
    return buffer.tobytes()


def _schematic(
    occupants: Sequence[OccupantState], applied: ManipulatedVariables, cal: Calibration, size: int = 340
) -> np.ndarray:
    """Top-down room view: AC at the bottom centre, occupants by sensation, the airflow cone."""
    panel = np.full((size, size, 3), 32, dtype=np.uint8)
    depth_m, half_width_m = 7.0, 4.5
    margin = 24
    scale = (size - 2 * margin) / depth_m
    ac_x, _, ac_z = cal.ac_position

    def to_px(x: float, z: float) -> tuple[int, int]:
        return int(size / 2 + x * scale), int(size - margin - z * scale)

    for metres in range(1, int(depth_m) + 1):
        cv2.line(panel, to_px(-half_width_m, metres), to_px(half_width_m, metres), (55, 55, 55), 1)
        cv2.putText(panel, f"{metres}m", (4, to_px(0, metres)[1] - 2), FONT, 0.35, (120, 120, 120), 1, cv2.LINE_AA)
    origin = to_px(ac_x, ac_z)
    length = JET_LENGTH_M.get(applied.fan_speed, 4.5) * scale
    half_angle = 40.0 if applied.louver_mode == "swing" else 12.0
    yaw = 0.0 if applied.louver_mode == "swing" else applied.louver_yaw_deg
    color = (150, 150, 150) if applied.compressor_mode == "fan_only" else (255, 200, 90)
    points = [origin]
    for step in range(-6, 7):
        angle = math.radians(yaw + half_angle * step / 6)
        points.append((int(origin[0] + length * math.sin(angle)), int(origin[1] - length * math.cos(angle))))
    overlay = panel.copy()
    cv2.fillPoly(overlay, [np.array(points, dtype=np.int32)], color)
    panel = cv2.addWeighted(overlay, 0.35, panel, 0.65, 0)
    cv2.rectangle(panel, (origin[0] - 18, origin[1]), (origin[0] + 18, origin[1] + 8), (230, 230, 230), -1)
    for occupant in occupants:
        x, _, z = occupant.position_m
        centre = to_px(x, z)
        cv2.circle(panel, centre, 9, sensation_color(occupant.observed_sensation), -1)
        cv2.putText(panel, occupant.occupant_key, (centre[0] + 11, centre[1] + 4), FONT, 0.4, (235, 235, 235), 1, cv2.LINE_AA)
    cv2.putText(panel, "top view (AC at bottom)", (6, 14), FONT, 0.4, (200, 200, 200), 1, cv2.LINE_AA)
    return panel


def render_hud(
    frame: np.ndarray,
    boxes: dict[str, tuple[float, float, float, float]],
    occupants: Sequence[OccupantState],
    applied: ManipulatedVariables,
    vrf: VrfCommand,
    door: DoorState,
    cal: Calibration,
    title: str,
) -> np.ndarray:
    """Middle keyframe with occupant labels, a top-down airflow schematic and the VRF command."""
    canvas, scale, dx, dy = letterbox(frame, (1280, 720))
    frame_h, frame_w = frame.shape[:2]
    for occupant in occupants:
        box = boxes.get(occupant.occupant_key)
        if box is None:
            continue
        text = f"{occupant.occupant_key} s={occupant.observed_sensation:+.1f} {occupant.distance_m:.1f}m"
        if occupant.draft:
            text += " DRAFT"
        _draw_box(canvas, box, frame_w, frame_h, scale, dx, dy, sensation_color(occupant.observed_sensation), text)
    panel = _schematic(occupants, applied, cal)
    canvas[10 : 10 + panel.shape[0], 1280 - panel.shape[1] - 10 : 1270] = panel
    vanes = f"V {vrf.idu_vane_v or '-'} H {vrf.idu_vane_h or '-'}"
    status = f"SP {vrf.idu_sp:.1f}C  {vrf.idu_mode}  fan {vrf.idu_fan}  {vanes}  door {door.effective_open_s:.0f}s"
    cv2.rectangle(canvas, (0, 680), (1280, 720), (0, 0, 0), -1)
    cv2.putText(canvas, status, (12, 707), FONT, 0.7, (255, 255, 255), 1, cv2.LINE_AA)
    _label(canvas, title, (10, 10), (255, 255, 255), 0.6)
    return canvas


def muse_hud_prompt(applied: ManipulatedVariables, target: Optional[str]) -> str:
    who = f" toward {target}" if target else ""
    return (
        "Overlay a translucent cool-blue airflow cone coming from the wall-mounted AC unit"
        f"{who}, yaw {applied.louver_yaw_deg:+.0f} degrees (positive = right) and pitch "
        f"{applied.louver_pitch_deg:+.0f} degrees (0 = horizontal). Keep the room and people unchanged; "
        "add a small legend with the setpoint "
        f"{applied.target_temp_c:.1f} C and fan {applied.fan_speed}."
    )
