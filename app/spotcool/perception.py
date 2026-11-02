"""Clip perception: one-pass video analysis, SAM 3.1 detection, geometry,
Muse Spark set-of-marks scene assessment, and fusion into planner facts."""

import asyncio
import base64
import logging
import math
import os
import random
import re
import statistics
import time
from dataclasses import dataclass, field
from typing import Any, Optional, Sequence

import cv2
import numpy as np
from openai import AsyncOpenAI, BadRequestError, RateLimitError

from . import config
from .control import SceneFacts
from .physics import Occupant
from .schemas import SceneAssessment, SceneSemantics

LOGGER = logging.getLogger("spotcool.perception")

MOTION_GRID = (80, 45)
MOVING_MOTION_THRESHOLD = 12.0
HEAD_FRACTION = 0.28
FAN_MOTION_THRESHOLD = 10.0

# ASHRAE 55-2020 Table 5.2.2.2 (typical ensembles) and ISO 9920 for outerwear.
CLOTHING_CLO = {
    "shorts_short_sleeve": 0.36,
    "skirt_or_dress_light": 0.54,
    "trousers_short_sleeve": 0.57,
    "trousers_long_sleeve": 0.61,
    "suit_jacket": 0.96,
    "sweater_or_layers": 1.01,
    "winter_outerwear": 1.60,
}
# ASHRAE 55-2020 Table 5.2.1.2 / ISO 8996.
ACTIVITY_MET = {
    "seated_quiet": 1.0,
    "seated_typing": 1.1,
    "standing_relaxed": 1.2,
    "standing_working": 1.4,
    "walking": 1.7,
    "carrying_or_lifting": 2.1,
    "exercising": 3.0,
}
LEGACY_MET = {"resting": 1.0, "sedentary": 1.1, "light_activity": 1.6, "high_activity": 2.5}
LEGACY_CLO = {"minimal": 0.36, "light": 0.57, "moderate": 0.8, "heavy": 1.3}
CHAIR_CLO = 0.1


# --------------------------------------------------------------------------
# Video intelligence
# --------------------------------------------------------------------------

@dataclass
class Keyframe:
    time_s: float
    frame: np.ndarray
    sharpness: float
    motion: float


@dataclass
class ClipAnalysis:
    start_s: float
    end_s: float
    fps: float
    width: int
    height: int
    sample_count: int
    keyframes: list[Keyframe]
    motion_series: list[tuple[float, float]]
    motion_maps: list[tuple[float, np.ndarray]]
    mean_brightness: float
    decode_seconds: float
    source: str

    def summary(self) -> dict[str, Any]:
        motions = [m for _, m in self.motion_series]
        return {
            "source": self.source,
            "fps": round(self.fps, 3),
            "resolution": [self.width, self.height],
            "analysis_samples": self.sample_count,
            "keyframes": [
                {"time_s": round(k.time_s, 2), "sharpness": round(k.sharpness, 1), "motion": round(k.motion, 4)}
                for k in self.keyframes
            ],
            "mean_motion": round(sum(motions) / len(motions), 4) if motions else 0.0,
            "peak_motion": round(max(motions), 4) if motions else 0.0,
            "mean_brightness": round(self.mean_brightness, 1),
            "decode_seconds": round(self.decode_seconds, 2),
        }


def _resize_max_side(frame: np.ndarray, max_side: int) -> np.ndarray:
    height, width = frame.shape[:2]
    scale = max_side / max(height, width)
    if scale >= 1:
        return frame
    return cv2.resize(frame, (int(width * scale), int(height * scale)), interpolation=cv2.INTER_AREA)


def _analysis_gray(frame: np.ndarray) -> np.ndarray:
    height, width = frame.shape[:2]
    small = cv2.resize(frame, (320, max(1, int(320 * height / width))), interpolation=cv2.INTER_AREA)
    return cv2.GaussianBlur(cv2.cvtColor(small, cv2.COLOR_BGR2GRAY), (5, 5), 0)


def analyze_clip(video_path: str, start_s: float, end_s: float) -> ClipAnalysis:
    """Decode the half-open window [start, end) once, scoring every analysis sample."""
    if start_s < 0 or end_s <= start_s:
        raise ValueError("Clip timestamps must satisfy 0 <= start < end.")
    interval = config.positive_float_from_env("ANALYSIS_SAMPLE_INTERVAL_SECONDS", 0.5)
    max_samples = config.positive_int_from_env("MAX_ANALYSIS_FRAMES", 240)
    keyframe_count = config.positive_int_from_env("KEYFRAMES_PER_CLIP", 4)
    max_side = config.positive_int_from_env("ANALYSIS_MAX_SIDE", 1280)

    started = time.monotonic()
    capture = cv2.VideoCapture(str(video_path))
    if not capture.isOpened():
        raise RuntimeError(f"Could not open video: {video_path}")
    try:
        fps = capture.get(cv2.CAP_PROP_FPS)
        if fps <= 0:
            raise RuntimeError(f"Video has an invalid frame rate: {video_path}")
        duration = end_s - start_s
        step = max(interval, duration / max_samples)
        segment = duration / keyframe_count
        last_window_start = end_s - min(segment, 0.25 * duration)
        capture.set(cv2.CAP_PROP_POS_FRAMES, math.ceil(start_s * fps - 1e-9))

        best: dict[int, tuple[float, float, np.ndarray, float]] = {}
        series: list[tuple[float, float]] = []
        maps: list[tuple[float, np.ndarray]] = []
        previous: Optional[np.ndarray] = None
        brightness = 0.0
        next_time = start_s
        width = height = 0
        while len(series) < max_samples and capture.grab():
            time_s = capture.get(cv2.CAP_PROP_POS_MSEC) / 1000.0
            if time_s < start_s - 1e-6:
                continue
            if time_s >= end_s:
                break
            if time_s + 1e-6 < next_time:
                continue
            ok, frame = capture.retrieve()
            if not ok:
                break
            while next_time <= time_s + 1e-6:
                next_time += step
            height, width = frame.shape[:2]
            gray = _analysis_gray(frame)
            sharpness = float(cv2.Laplacian(gray, cv2.CV_64F).var())
            motion = 0.0
            if previous is not None and previous.shape == gray.shape:
                diff = cv2.absdiff(gray, previous)
                motion = float((diff > 18).mean())
                maps.append((time_s, cv2.resize(diff.astype(np.float32), MOTION_GRID, interpolation=cv2.INTER_AREA)))
            previous = gray
            brightness += float(gray.mean())
            series.append((time_s, motion))
            index = min(int((time_s - start_s) / segment), keyframe_count - 1)
            if index == keyframe_count - 1 and time_s < last_window_start:
                continue
            if index not in best or sharpness > best[index][0]:
                best[index] = (sharpness, time_s, _resize_max_side(frame, max_side), motion)
    finally:
        capture.release()

    if not series:
        raise RuntimeError(
            f"No video frames found between {start_s} and {end_s} seconds in {video_path}."
        )
    keyframes = [
        Keyframe(time_s=t, frame=frame, sharpness=sharp, motion=motion)
        for sharp, t, frame, motion in (best[i] for i in sorted(best))
    ]
    return ClipAnalysis(
        start_s=start_s,
        end_s=end_s,
        fps=fps,
        width=width,
        height=height,
        sample_count=len(series),
        keyframes=keyframes,
        motion_series=series,
        motion_maps=maps,
        mean_brightness=brightness / len(series),
        decode_seconds=time.monotonic() - started,
        source="video",
    )


def clip_from_b64_frames(b64_frames: Sequence[str]) -> ClipAnalysis:
    """Build an analysis from pre-sampled JPEG frames (the `process_frames` entry point)."""
    max_side = config.positive_int_from_env("ANALYSIS_MAX_SIDE", 1280)
    frames = []
    for encoded in b64_frames:
        data = np.frombuffer(base64.b64decode(encoded), dtype=np.uint8)
        frame = cv2.imdecode(data, cv2.IMREAD_COLOR)
        if frame is not None:
            frames.append(_resize_max_side(frame, max_side))
    if not frames:
        raise ValueError("At least one decodable video frame is required.")
    series: list[tuple[float, float]] = []
    maps: list[tuple[float, np.ndarray]] = []
    keyframes = []
    previous = None
    brightness = 0.0
    for index, frame in enumerate(frames):
        gray = _analysis_gray(frame)
        motion = 0.0
        if previous is not None and previous.shape == gray.shape:
            diff = cv2.absdiff(gray, previous)
            motion = float((diff > 18).mean())
            maps.append((float(index), cv2.resize(diff.astype(np.float32), MOTION_GRID, interpolation=cv2.INTER_AREA)))
        previous = gray
        brightness += float(gray.mean())
        series.append((float(index), motion))
        keyframes.append(Keyframe(float(index), frame, float(cv2.Laplacian(gray, cv2.CV_64F).var()), motion))
    keep = config.positive_int_from_env("KEYFRAMES_PER_CLIP", 4)
    if len(keyframes) > keep:
        picks = sorted({round(i * (len(keyframes) - 1) / (keep - 1)) for i in range(keep)}) if keep > 1 else [len(keyframes) - 1]
        keyframes = [keyframes[i] for i in picks]
    height, width = frames[0].shape[:2]
    return ClipAnalysis(
        start_s=0.0, end_s=float(len(frames)), fps=1.0, width=width, height=height,
        sample_count=len(frames), keyframes=keyframes, motion_series=series, motion_maps=maps,
        mean_brightness=brightness / len(frames), decode_seconds=0.0, source="frames",
    )


def encode_jpeg_b64(frame: np.ndarray) -> str:
    quality = config.positive_int_from_env("JPEG_QUALITY", 85)
    ok, encoded = cv2.imencode(".jpg", frame, [cv2.IMWRITE_JPEG_QUALITY, quality])
    if not ok:
        raise RuntimeError("Could not encode frame as JPEG.")
    return base64.b64encode(encoded.tobytes()).decode("utf-8")


def storyboard(clip: ClipAnalysis, tile_width: int = 640) -> np.ndarray:
    frames = clip.keyframes[:4]
    height, width = frames[0].frame.shape[:2]
    tile_height = int(tile_width * height / width)
    columns = 2 if len(frames) > 1 else 1
    rows = math.ceil(len(frames) / columns)
    board = np.zeros((rows * tile_height, columns * tile_width, 3), dtype=np.uint8)
    for index, keyframe in enumerate(frames):
        tile = cv2.resize(keyframe.frame, (tile_width, tile_height), interpolation=cv2.INTER_AREA)
        label = f"t+{keyframe.time_s - clip.start_s:.1f}s"
        cv2.rectangle(tile, (0, 0), (150, 34), (0, 0, 0), -1)
        cv2.putText(tile, label, (8, 25), cv2.FONT_HERSHEY_SIMPLEX, 0.8, (255, 255, 255), 2, cv2.LINE_AA)
        row, column = divmod(index, columns)
        board[row * tile_height:(row + 1) * tile_height, column * tile_width:(column + 1) * tile_width] = tile
    return board


# --------------------------------------------------------------------------
# SAM 3.1
# --------------------------------------------------------------------------

_NUMBER = r"(-?\d+(?:\.\d+)?)"
FRAME_MARKER = re.compile(r"<(\d+)f>(?=\d+<\|box)")
BOX_TOKEN = re.compile(
    r"(\d+)<\|box;x1=" + _NUMBER + ";y1=" + _NUMBER + ";x2=" + _NUMBER + ";y2=" + _NUMBER
    + r";w=(\d+(?:\.\d+)?);h=(\d+(?:\.\d+)?)(?:;c=([0-9.eE+-]+))?\|>"
)
LEGACY_BOX = re.compile(r"x1=(\d+);y1=(\d+);x2=(\d+);y2=(\d+);w=(\d+);h=(\d+)")


@dataclass
class Detection:
    object_id: int
    x1: float
    y1: float
    x2: float
    y2: float
    confidence: Optional[float] = None

    @property
    def cx(self) -> float:
        return (self.x1 + self.x2) / 2

    @property
    def cy(self) -> float:
        return (self.y1 + self.y2) / 2

    def as_list(self) -> list[float]:
        return [round(self.x1, 4), round(self.y1, 4), round(self.x2, 4), round(self.y2, 4)]


def _normalized(object_id: int, values: Sequence[str], width: str, height: str, confidence: Optional[str]) -> Optional[Detection]:
    w, h = float(width), float(height)
    if w <= 0 or h <= 0:
        return None
    x1, y1, x2, y2 = (float(v) for v in values)
    detection = Detection(
        object_id,
        max(0.0, min(1.0, x1 / w)), max(0.0, min(1.0, y1 / h)),
        max(0.0, min(1.0, x2 / w)), max(0.0, min(1.0, y2 / h)),
        float(confidence) if confidence else None,
    )
    return detection if detection.x2 > detection.x1 and detection.y2 > detection.y1 else None


def parse_sam_output(text: str) -> dict[int, list[Detection]]:
    """Parse `<Nf>id<|box;...|><|mask;...|>,id...` lines; mask payloads are skipped."""
    frames: dict[int, list[Detection]] = {}
    markers = list(FRAME_MARKER.finditer(text))
    spans = (
        [(int(m.group(1)), m.start(), markers[i + 1].start() if i + 1 < len(markers) else len(text)) for i, m in enumerate(markers)]
        if markers else [(0, 0, len(text))]
    )
    for frame_index, begin, end in spans:
        seen: set[int] = set()
        for match in BOX_TOKEN.finditer(text, begin, end):
            object_id = int(match.group(1))
            if object_id in seen:
                continue
            detection = _normalized(object_id, match.groups()[1:5], match.group(6), match.group(7), match.group(8))
            if detection:
                seen.add(object_id)
                frames.setdefault(frame_index, []).append(detection)
    if not frames:
        for index, match in enumerate(LEGACY_BOX.finditer(text)):
            detection = _normalized(index, match.groups()[:4], match.group(5), match.group(6), None)
            if detection:
                frames.setdefault(0, []).append(detection)
    return frames


@dataclass
class SamScene:
    # Aligned with clip.keyframes; None marks a keyframe whose request failed.
    person_frames: list[Optional[list[Detection]]]
    doors: list[Detection]
    context: dict[str, list[Detection]]
    errors: list[str] = field(default_factory=list)

    @property
    def ok_person_frames(self) -> list[list[Detection]]:
        return [frame for frame in self.person_frames if frame is not None]


async def _sam_image(
    client: AsyncOpenAI,
    b64_frame: str,
    concept: str,
    semaphore: asyncio.Semaphore,
    ledger: list[dict[str, Any]],
) -> list[Detection]:
    model = os.getenv("SAM_MODEL", "sam-3.1")
    min_confidence = float(os.getenv("SAM_MIN_CONFIDENCE", "0.3"))
    started = time.monotonic()
    async with semaphore:
        for attempt in range(3):
            try:
                response = await client.responses.create(
                    model=model,
                    metadata={"include_confidence": "true"},
                    input=[{
                        "type": "message",
                        "role": "user",
                        "content": [
                            {"type": "input_text", "text": concept},
                            {"type": "input_image", "image_url": f"data:image/jpeg;base64,{b64_frame}"},
                        ],
                    }],
                )
                break
            except RateLimitError:
                if attempt == 2:
                    raise
                await asyncio.sleep(0.5 * 2**attempt + random.random() * 0.3)
    frames = parse_sam_output(response.output_text or "")
    detections = frames.get(0) or (frames[min(frames)] if frames else [])
    detections = [d for d in detections if d.confidence is None or d.confidence >= min_confidence]
    config.record_model_call(ledger, "perception", model, f"segment '{concept}'", started, ok=True, detail=f"{len(detections)} found")
    return detections


async def detect_scene(client: AsyncOpenAI, clip: ClipAnalysis, ledger: list[dict[str, Any]]) -> SamScene:
    semaphore = asyncio.Semaphore(config.positive_int_from_env("SAM_MAX_CONCURRENCY", 6))
    person_concept = os.getenv("SAM_PERSON_CONCEPT", "humans")
    door_concept = os.getenv("SAM_DOOR_CONCEPT", "open door")
    context_concepts = config.json_list_from_env(
        "SAM_CONTEXT_CONCEPTS", ["open window", "computer monitor", "ceiling fan"]
    )
    encoded = [encode_jpeg_b64(k.frame) for k in clip.keyframes]
    latest = encoded[-1]
    jobs = [_sam_image(client, frame, person_concept, semaphore, ledger) for frame in encoded]
    jobs.append(_sam_image(client, latest, door_concept, semaphore, ledger))
    jobs.extend(_sam_image(client, latest, concept, semaphore, ledger) for concept in context_concepts)
    results = await asyncio.gather(*jobs, return_exceptions=True)
    errors: list[str] = []
    person_frames: list[Optional[list[Detection]]] = []
    for result in results[: len(encoded)]:
        if isinstance(result, BaseException):
            errors.append(f"{person_concept}: {type(result).__name__}: {result}")
            config.record_model_call(ledger, "perception", os.getenv("SAM_MODEL", "sam-3.1"), f"segment '{person_concept}'", time.monotonic(), ok=False, detail=str(result)[:200])
            person_frames.append(None)
        else:
            person_frames.append(result)

    def detections_or_empty(result: Any, label: str) -> list[Detection]:
        if isinstance(result, BaseException):
            errors.append(f"{label}: {type(result).__name__}: {result}")
            return []
        return result

    doors = detections_or_empty(results[len(encoded)], door_concept)
    context = {
        concept: detections_or_empty(result, concept)
        for concept, result in zip(context_concepts, results[len(encoded) + 1:])
    }
    return SamScene(person_frames=person_frames, doors=doors, context=context, errors=errors)


# --------------------------------------------------------------------------
# Geometry
# --------------------------------------------------------------------------

def _intrinsics(width: int, height: int, camera: dict[str, Any]) -> tuple[float, float, float, float]:
    fx = (width / 2) / math.tan(math.radians(float(camera["horizontal_fov_deg"])) / 2)
    return fx, fx, width / 2, height / 2


def locate(detection: Detection, width: int, height: int, camera: dict[str, Any]) -> dict[str, Any]:
    """Floor position of a detection: ground-plane ray cast from the foot pixel, pinhole size fallback."""
    fx, fy, cx, cy = _intrinsics(width, height, camera)
    tilt = math.radians(float(camera["tilt_down_deg"]))
    mount = float(camera["mount_height_m"])
    u = detection.cx * width
    v = detection.y2 * height
    xr, yr = (u - cx) / fx, (v - cy) / fy
    y_world = yr * math.cos(tilt) + math.sin(tilt)
    z_world = -yr * math.sin(tilt) + math.cos(tilt)
    box_h = (detection.y2 - detection.y1) * height
    box_w = (detection.x2 - detection.x1) * width
    standing = box_h / max(box_w, 1.0) > 2.0
    person_height = float(camera["standing_height_m"] if standing else camera["seated_height_m"])
    slant = fy * person_height / max(box_h, 1.0)
    pinhole_forward = math.sqrt(max(slant**2 - (mount - person_height / 2) ** 2, 0.25))
    pinhole_lateral = (u - cx) * slant / fx
    method = "pinhole_height"
    lateral, forward = pinhole_lateral, pinhole_forward
    if y_world > 0.02 and detection.y2 < 0.985:
        scale = mount / y_world
        ground_lateral, ground_forward = scale * xr, scale * z_world
        if 0 < ground_forward <= 1.6 * pinhole_forward:
            method = "ground_plane"
            lateral, forward = ground_lateral, ground_forward
    forward = max(0.3, min(40.0, forward))
    return {
        "lateral_m": round(lateral, 2),
        "forward_m": round(forward, 2),
        "distance_m": round(math.hypot(lateral, forward), 2),
        "method": method,
        "posture_hint": "standing" if standing else "seated",
    }


def project_floor_point(lateral_m: float, forward_m: float, height_m: float, width: int, height: int, camera: dict[str, Any]) -> Optional[tuple[int, int]]:
    fx, fy, cx, cy = _intrinsics(width, height, camera)
    tilt = math.radians(float(camera["tilt_down_deg"]))
    y_world = float(camera["mount_height_m"]) - height_m
    y_cam = y_world * math.cos(tilt) - forward_m * math.sin(tilt)
    z_cam = y_world * math.sin(tilt) + forward_m * math.cos(tilt)
    if z_cam <= 0.1:
        return None
    return int(cx + fx * lateral_m / z_cam), int(cy + fy * y_cam / z_cam)


def _iou(a: Detection, b: Detection) -> float:
    ix = max(0.0, min(a.x2, b.x2) - max(a.x1, b.x1))
    iy = max(0.0, min(a.y2, b.y2) - max(a.y1, b.y1))
    inter = ix * iy
    union = (a.x2 - a.x1) * (a.y2 - a.y1) + (b.x2 - b.x1) * (b.y2 - b.y1) - inter
    return inter / union if union > 0 else 0.0


def box_motion(detection: Detection, clip: ClipAnalysis, around_s: float, window_s: float = 1.5) -> float:
    grid_w, grid_h = MOTION_GRID
    x1, x2 = int(detection.x1 * grid_w), max(int(detection.x1 * grid_w) + 1, int(math.ceil(detection.x2 * grid_w)))
    y1, y2 = int(detection.y1 * grid_h), max(int(detection.y1 * grid_h) + 1, int(math.ceil(detection.y2 * grid_h)))
    values = [float(grid[y1:y2, x1:x2].mean()) for t, grid in clip.motion_maps if abs(t - around_s) <= window_s]
    return sum(values) / len(values) if values else 0.0


def blur_heads(frame: np.ndarray, detections: Sequence[Detection]) -> np.ndarray:
    out = frame.copy()
    height, width = out.shape[:2]
    for d in detections:
        x1, x2 = int(d.x1 * width), int(d.x2 * width)
        y1 = int(d.y1 * height)
        y2 = int((d.y1 + HEAD_FRACTION * (d.y2 - d.y1)) * height)
        if x2 - x1 < 2 or y2 - y1 < 2:
            continue
        region = out[y1:y2, x1:x2]
        kernel = max(3, (min(region.shape[:2]) // 2) * 2 + 1)
        out[y1:y2, x1:x2] = cv2.GaussianBlur(region, (kernel, kernel), 0)
    return out


def draw_marks(frame: np.ndarray, detections: Sequence[Detection]) -> np.ndarray:
    out = frame.copy()
    height, width = out.shape[:2]
    for number, d in enumerate(detections, start=1):
        p1 = (int(d.x1 * width), int(d.y1 * height))
        p2 = (int(d.x2 * width), int(d.y2 * height))
        cv2.rectangle(out, p1, p2, (0, 255, 255), 2)
        label = str(number)
        (tw, th), _ = cv2.getTextSize(label, cv2.FONT_HERSHEY_SIMPLEX, 0.8, 2)
        cv2.rectangle(out, p1, (p1[0] + tw + 8, p1[1] + th + 10), (0, 0, 0), -1)
        cv2.putText(out, label, (p1[0] + 4, p1[1] + th + 4), cv2.FONT_HERSHEY_SIMPLEX, 0.8, (0, 255, 255), 2, cv2.LINE_AA)
    return out


# --------------------------------------------------------------------------
# Muse Spark scene assessment
# --------------------------------------------------------------------------

SCENE_INSTRUCTIONS = """You are the perception module of an autonomous HVAC agent for a commercial building.
Image 1 is the most recent CCTV frame. Every person detected by SAM 3.1 has a yellow box with a number; faces are blurred on purpose.
Image 2 is a storyboard of the whole clip (timestamps in the corner of each tile), for activity and occupancy trends.
For every numbered person return one entry with that number as `id`:
- clothing_ensemble: the closest ASHRAE 55 ensemble (winter_outerwear = coats/puffer jackets worn indoors).
- activity and posture: what they are doing in image 1, using the storyboard to tell walking from standing.
- in_direct_sunlight: true only if sunlight visibly falls on them.
- discomfort_cue: a visible thermal-comfort behaviour, otherwise none.
Also count everyone visible in image 1 (numbered or not), doors and windows standing open, visible screens/monitors,
the sunlight level, the space type, the lighting level and the occupancy trend across the storyboard.
If an audio transcript is provided, list only explicit thermal or air-quality remarks as voice_comfort_feedback.
Never guess temperatures or HVAC settings; physics models compute those."""


async def _parse_scene(
    client: AsyncOpenAI,
    content: list[dict[str, Any]],
    text_format: Any,
    ledger: list[dict[str, Any]],
    purpose: str,
) -> Any:
    model = os.getenv("SCENE_MODEL", "muse-spark-1.3")
    effort = os.getenv("SCENE_REASONING_EFFORT", "low").strip()
    request: dict[str, Any] = {
        "model": model,
        "input": [{"type": "message", "role": "user", "content": content}],
        "text_format": text_format,
    }
    started = time.monotonic()
    try:
        if effort:
            try:
                response = await client.responses.parse(**request, reasoning={"effort": effort})
            except BadRequestError:
                response = await client.responses.parse(**request)
        else:
            response = await client.responses.parse(**request)
    except Exception as exc:
        config.record_model_call(ledger, "perception", model, purpose, started, ok=False, detail=str(exc)[:200])
        raise
    if response.output_parsed is None:
        config.record_model_call(ledger, "perception", model, purpose, started, ok=False, detail="no parsed output")
        raise RuntimeError(f"{purpose} returned no parsed response.")
    config.record_model_call(ledger, "perception", model, purpose, started, ok=True)
    return response.output_parsed


async def assess_scene(
    client: AsyncOpenAI,
    marked_b64: str,
    storyboard_b64: str,
    sam_summary: str,
    transcript_lines: Sequence[str],
    ledger: list[dict[str, Any]],
) -> SceneAssessment:
    text = SCENE_INSTRUCTIONS
    operator_prompt = os.getenv("SCENE_ANALYSIS_PROMPT")
    if operator_prompt:
        text = f"Operator note: {operator_prompt}\n\n{text}"
    text += f"\n\nSAM 3.1 summary: {sam_summary}"
    if transcript_lines:
        text += "\nAudio transcript (speaker, seconds into clip):\n" + "\n".join(transcript_lines)
    content = [
        {"type": "input_text", "text": text},
        {"type": "input_image", "image_url": f"data:image/jpeg;base64,{marked_b64}"},
        {"type": "input_image", "image_url": f"data:image/jpeg;base64,{storyboard_b64}"},
    ]
    return await _parse_scene(client, content, SceneAssessment, ledger, "set-of-marks scene assessment")


async def legacy_semantics(client: AsyncOpenAI, image_b64s: Sequence[str], ledger: list[dict[str, Any]]) -> SceneSemantics:
    content: list[dict[str, Any]] = [{
        "type": "input_text",
        "text": os.getenv("SCENE_ANALYSIS_PROMPT", "Evaluate room thermal load parameters across these frames."),
    }]
    content.extend({"type": "input_image", "image_url": f"data:image/jpeg;base64,{b}"} for b in image_b64s)
    return await _parse_scene(client, content, SceneSemantics, ledger, "fallback scene semantics")


# --------------------------------------------------------------------------
# Fusion
# --------------------------------------------------------------------------

@dataclass
class PerceptionResult:
    facts: SceneFacts
    occupants: list[dict[str, Any]]
    persons_legacy: list[dict[str, Any]]
    doors_legacy: list[dict[str, Any]]
    scene: dict[str, Any]
    sam_count: Optional[int]
    spark_count: Optional[int]
    latest_people: list[Detection]


def latest_people(sam: Optional[SamScene]) -> list[Detection]:
    """People in the most recent keyframe SAM answered for (an empty room stays empty)."""
    frames = sam.ok_person_frames if sam else []
    return sorted(frames[-1], key=lambda d: (d.cx, d.cy)) if frames else []


def privacy_keyframes(clip: ClipAnalysis, sam: Optional[SamScene]) -> ClipAnalysis:
    """Copy of the clip whose keyframes have every SAM-detected head blurred."""
    keyframes = []
    for index, keyframe in enumerate(clip.keyframes):
        detections = sam.person_frames[index] if sam and index < len(sam.person_frames) else None
        frame = blur_heads(keyframe.frame, detections) if detections else keyframe.frame
        keyframes.append(Keyframe(keyframe.time_s, frame, keyframe.sharpness, keyframe.motion))
    return ClipAnalysis(**{**clip.__dict__, "keyframes": keyframes})


def fuse(
    clip: ClipAnalysis,
    sam: Optional[SamScene],
    people: list[Detection],
    assessment: Optional[SceneAssessment],
    legacy: Optional[SceneSemantics],
    params: Any,
) -> PerceptionResult:
    camera, control, zone = params.camera, params.control, params.zone
    keyframe = clip.keyframes[-1]
    height, width = keyframe.frame.shape[:2]
    observations = {o.id: o for o in assessment.occupants} if assessment else {}
    prior_met = LEGACY_MET[legacy.metabolic_rate_category] if legacy else float(control["design_met"])
    prior_clo = LEGACY_CLO[legacy.thermal_insulation_level] if legacy else float(control["design_clo"])

    occupants: list[Occupant] = []
    telemetry: list[dict[str, Any]] = []
    persons_legacy: list[dict[str, Any]] = []
    frames = sam.ok_person_frames if sam else []
    for number, detection in enumerate(people, start=1):
        position = locate(detection, width, height, camera)
        matched = 1 + sum(
            1 for frame in frames[:-1]
            if any(_iou(detection, other) > 0.25 or math.hypot(detection.cx - other.cx, detection.y2 - other.y2) < 0.06 for other in frame)
        )
        dwell = matched / max(len(frames), 1)
        motion = box_motion(detection, clip, keyframe.time_s)
        moving = motion > MOVING_MOTION_THRESHOLD
        observation = observations.get(number)
        if observation:
            seated = observation.posture == "seated"
            clo = CLOTHING_CLO[observation.clothing_ensemble] + (CHAIR_CLO if seated else 0.0)
            met = ACTIVITY_MET[observation.activity]
            activity = observation.activity
            in_sun = observation.in_direct_sunlight
            cue = observation.discomfort_cue
            clothing = observation.clothing_ensemble
        else:
            seated = position["posture_hint"] == "seated"
            clo = prior_clo + (CHAIR_CLO if seated else 0.0)
            met = prior_met
            activity, in_sun, cue, clothing = "prior", False, "none", "prior"
        if moving and met < ACTIVITY_MET["walking"]:
            met = ACTIVITY_MET["walking"]
            activity = f"{activity}+motion"
        weight = (0.3 + 0.7 * dwell) * (1.3 if cue != "none" else 1.0)
        radiant = float(zone["solar_radiant_delta_c"]) if in_sun else 0.0
        occupants.append(Occupant(weight, met, clo, radiant, position["lateral_m"], position["forward_m"]))
        persons_legacy.append({
            "cx": round(detection.cx, 3),
            "cy": round(detection.cy, 3),
            "z_est_m": position["distance_m"],
        })
        telemetry.append({
            "id": number,
            "box": detection.as_list(),
            "confidence": detection.confidence,
            **position,
            "dwell_fraction": round(dwell, 2),
            "motion": round(motion, 2),
            "moving": moving,
            "clothing_ensemble": clothing,
            "clo": round(clo, 2),
            "activity": activity,
            "met": round(met, 2),
            "in_direct_sunlight": in_sun,
            "discomfort_cue": cue,
            "weight": round(weight, 2),
        })

    sam_count = round(statistics.median(len(f) for f in frames)) if frames else None
    spark_count = assessment.total_people_visible if assessment else (legacy.occupant_count if legacy else None)
    occupancy = max(sam_count or 0, spark_count or 0, len(people))
    # People counted but not located inherit the observed occupants' mean profile.
    observed = [o for o, t in zip(occupants, telemetry) if t["activity"] != "prior"]
    if observed:
        prior_met = sum(o.met for o in observed) / len(observed)
        prior_clo = sum(o.clo for o in observed) / len(observed)
    for _ in range(min(max(0, occupancy - len(people)), 50)):
        occupants.append(Occupant(0.5, prior_met, prior_clo))

    def count_concept(keyword: str) -> list[Detection]:
        found: list[Detection] = []
        if sam:
            for concept, detections in sam.context.items():
                if keyword in concept.lower():
                    found.extend(detections)
        return found

    sam_doors = [d for d in (sam.doors if sam else []) if d.confidence is None or d.confidence >= 0.5]
    sam_windows = [d for d in count_concept("window") if d.confidence is None or d.confidence >= 0.5]
    if assessment:
        doors_open = assessment.doors_open if (sam is None or sam_doors) else 0
        windows_open = assessment.windows_open if (sam is None or sam_windows) else 0
    elif legacy and legacy.envelope_state == "open":
        doors_open, windows_open = max(1, len(sam_doors)), len(sam_windows)
    else:
        doors_open, windows_open = len(sam_doors), len(sam_windows)
    screens = max(len(count_concept("monitor")) + len(count_concept("screen")), assessment.screens_visible if assessment else 0)
    fans = count_concept("fan")
    fan_motion = [box_motion(d, clip, keyframe.time_s, window_s=10.0) for d in fans]
    fans_running = any(m > FAN_MOTION_THRESHOLD for m in fan_motion)

    doors_legacy = [
        {"cx": round(d.cx, 3), "cy": round(d.cy, 3), "z_est_m": locate(d, width, height, camera)["distance_m"]}
        for d in (sam.doors if sam else [])
    ]
    facts = SceneFacts(
        occupancy=occupancy,
        occupants=occupants,
        space_type=assessment.space_type if assessment else "office",
        screens=screens,
        solar_gain=assessment.solar_gain if assessment else "none",
        doors_open=doors_open,
        windows_open=windows_open,
        ceiling_fans_running=fans_running,
        voice_sentiments=[f.sentiment for f in assessment.voice_comfort_feedback] if assessment else [],
        lighting=assessment.lighting if assessment else "normal",
    )
    scene = {
        "source": "muse_spark_set_of_marks" if assessment else ("muse_spark_legacy_semantics" if legacy else "priors"),
        "summary": assessment.summary if assessment else None,
        "space_type": facts.space_type,
        "occupancy_trend": assessment.occupancy_trend if assessment else None,
        "lighting": facts.lighting,
        "solar_gain": facts.solar_gain,
        "people_counted": {"sam_median": sam_count, "spark": spark_count, "fused": occupancy, "located": len(people)},
        "openings": {"doors_open": doors_open, "windows_open": windows_open, "sam_doors": len(sam_doors), "sam_windows": len(sam_windows)},
        "screens": screens,
        "ceiling_fans": {"detected": len(fans), "running": fans_running, "box_motion": [round(m, 1) for m in fan_motion]},
        "voice_comfort_feedback": [f.model_dump() for f in assessment.voice_comfort_feedback] if assessment else [],
        "sam_errors": sam.errors if sam else ["SAM unavailable"],
    }
    return PerceptionResult(
        facts=facts,
        occupants=telemetry,
        persons_legacy=persons_legacy,
        doors_legacy=doors_legacy,
        scene=scene,
        sam_count=sam_count,
        spark_count=spark_count,
        latest_people=people,
    )


def sam_summary(sam: Optional[SamScene], people: list[Detection]) -> str:
    if sam is None:
        return "unavailable"
    counts = [len(f) if f is not None else "failed" for f in sam.person_frames]
    context = ", ".join(f"{len(v)} x '{k}'" for k, v in sam.context.items())
    return (
        f"{len(people)} people numbered in image 1; per-keyframe person counts {counts}; "
        f"{len(sam.doors)} door detections; {context or 'no context objects'}."
    )


def semantics_from_facts(facts: SceneFacts, pmv_mean: float) -> dict[str, Any]:
    """Legacy `SceneSemantics` values, now derived from fused measurements and PMV."""
    occupants = facts.occupants
    total = sum(o.weight for o in occupants) or 1.0
    met = sum(o.weight * o.met for o in occupants) / total if occupants else 1.1
    clo = sum(o.weight * o.clo for o in occupants) / total if occupants else 0.6
    if met <= 1.0:
        metabolic = "resting"
    elif met <= 1.3:
        metabolic = "sedentary"
    elif met <= 2.0:
        metabolic = "light_activity"
    else:
        metabolic = "high_activity"
    if clo < 0.45:
        insulation = "minimal"
    elif clo < 0.75:
        insulation = "light"
    elif clo < 1.1:
        insulation = "moderate"
    else:
        insulation = "heavy"
    if pmv_mean > 0.5:
        comfort = "warm_discomfort"
    elif pmv_mean < -0.5:
        comfort = "cool_discomfort"
    else:
        comfort = "neutral"
    return SceneSemantics(
        occupant_count=facts.occupancy,
        metabolic_rate_category=metabolic,  # type: ignore[arg-type]
        thermal_insulation_level=insulation,  # type: ignore[arg-type]
        comfort_indicator=comfort,  # type: ignore[arg-type]
        envelope_state="open" if facts.doors_open + facts.windows_open > 0 else "sealed",
    ).model_dump()
