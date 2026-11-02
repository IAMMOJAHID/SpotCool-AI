import math
import re
import statistics
from collections import Counter, defaultdict
from dataclasses import dataclass, field
from typing import Optional, Sequence

from .schemas import (
    COLD_CUES,
    DRAFT_CUES,
    HOT_CUES,
    VISUAL_CUES,
    Complaint,
    Detection,
    DoorState,
    OccupantObservation,
    OccupantState,
    SamParseResult,
    SparkWire,
    Track,
    TranscriptTurn,
    extract_json,
)

# SAM 3.1 wire format: "<Nf>" frame token, then comma-separated records
# "ID<|box;x1=..;y1=..;x2=..;y2=..;w=..;h=..[;c=..]|><|mask;...;data=H,W,<base85>|>".
# Box fields are numeric, so records are found by their box token and masks are skipped.
FRAME_TOKEN = re.compile(r"^\s*<(\d+)f>")
BOX_TOKEN = re.compile(r"(\d+)<\|box;([^|>]*)\|>")
MASK_PAYLOAD = re.compile(r"(<\|mask;[^|]*?data=)[^|]*(\|>)")


def _clamp(value: float, low: float, high: float) -> float:
    return max(low, min(high, value))


def _box_fields(body: str) -> dict[str, str]:
    fields = {}
    for item in body.split(";"):
        key, sep, value = item.partition("=")
        if sep and key.strip():
            fields[key.strip()] = value.strip()
    return fields


def parse_sam_output(text: str, concept: str) -> SamParseResult:
    """Parse SAM 3.1 output_text into normalized, half-open detections."""
    best: dict[tuple[int, str], Detection] = {}
    sizes: Counter = Counter()
    warnings: list[str] = []
    for line_no, line in enumerate(text.splitlines(), 1):
        if not line.strip():
            continue
        frame_match = FRAME_TOKEN.match(line)
        if frame_match is None:
            warnings.append(f"{concept} line {line_no}: no frame token")
            continue
        frame = int(frame_match.group(1))
        body = line[frame_match.end() :]
        tokens = 0
        for record in BOX_TOKEN.finditer(body):
            tokens += 1
            fields = _box_fields(record.group(2))
            try:
                x1, y1, x2, y2, w, h = (float(fields[k]) for k in ("x1", "y1", "x2", "y2", "w", "h"))
            except (KeyError, ValueError):
                warnings.append(f"{concept} line {line_no}: malformed box {record.group(2)!r}")
                continue
            if w <= 0 or h <= 0 or x2 < x1 or y2 < y1:
                warnings.append(f"{concept} line {line_no}: invalid box {record.group(2)!r}")
                continue
            confidence: Optional[float] = None
            if "c" in fields:
                try:
                    confidence = float(fields["c"])
                    if not 0.0 <= confidence <= 1.0:
                        raise ValueError
                except ValueError:
                    warnings.append(f"{concept} line {line_no}: ignored confidence {fields['c']!r}")
                    confidence = None
            # Wire x2/y2 are inclusive; stored boxes are half-open.
            detection = Detection(
                frame=frame,
                object_id=record.group(1),
                x1=_clamp(x1 / w, 0.0, 1.0),
                y1=_clamp(y1 / h, 0.0, 1.0),
                x2=_clamp((x2 + 1) / w, 0.0, 1.0),
                y2=_clamp((y2 + 1) / h, 0.0, 1.0),
                confidence=confidence,
                has_mask=body[record.end() :].lstrip().startswith("<|mask"),
            )
            sizes[(int(w), int(h))] += 1
            key = (frame, detection.object_id)
            current = best.get(key)
            if current is None or (detection.confidence or 0.0) > (current.confidence or 0.0):
                best[key] = detection
        if tokens == 0:
            warnings.append(f"{concept} line {line_no}: no box records")
    detections = sorted(best.values(), key=lambda d: (d.frame, int(d.object_id)))
    frame_size = sizes.most_common(1)[0][0] if sizes else None
    return SamParseResult(
        concept=concept, detections=detections, frame_size=frame_size, warnings=warnings
    )


def strip_masks(text: str) -> str:
    """Drop mask payloads (person silhouettes) before a response is recorded to disk."""
    return MASK_PAYLOAD.sub(r"\1\2", text)


def iou(a: Sequence[float], b: Sequence[float]) -> float:
    left, top = max(a[0], b[0]), max(a[1], b[1])
    right, bottom = min(a[2], b[2]), min(a[3], b[3])
    inter = max(0.0, right - left) * max(0.0, bottom - top)
    union = (a[2] - a[0]) * (a[3] - a[1]) + (b[2] - b[0]) * (b[3] - b[1]) - inter
    return inter / union if union > 0 else 0.0


def _center_distance(a: Detection, b: Detection) -> float:
    (ax, ay), (bx, by) = a.center, b.center
    return math.hypot(ax - bx, ay - by)


def merge_fragments(
    tracks: list[Track], max_gap: int = 5, min_iou: float = 0.3, max_center: float = 0.05
) -> list[Track]:
    """Join tracks that do not overlap in time and hand off at the same place (ID switches)."""
    tracks = sorted(tracks, key=lambda t: t.first_frame)
    merged = True
    while merged:
        merged = False
        for first in tracks:
            candidates = [
                second
                for second in tracks
                if second is not first
                and first.last_frame < second.first_frame <= first.last_frame + max_gap
            ]
            for second in sorted(candidates, key=lambda t: t.first_frame):
                tail, head = first.detections[-1], second.detections[0]
                if iou(tail.box, head.box) >= min_iou or _center_distance(tail, head) <= max_center:
                    first.detections.extend(second.detections)
                    first.merged_ids.extend([second.track_id, *second.merged_ids])
                    tracks.remove(second)
                    merged = True
                    break
            if merged:
                break
    return tracks


def build_tracks(
    result: SamParseResult, n_frames: int, min_confidence: float, warnings: list[str]
) -> list[Track]:
    by_id: dict[str, list[Detection]] = defaultdict(list)
    beyond = 0
    for detection in result.detections:
        if detection.frame >= n_frames:
            beyond += 1
            continue
        if detection.confidence is not None and detection.confidence < min_confidence:
            continue
        by_id[detection.object_id].append(detection)
    if beyond:
        warnings.append(f"{result.concept}: {beyond} detections beyond frame {n_frames - 1} dropped")
    tracks = [
        Track(track_id=object_id, concept=result.concept, detections=sorted(dets, key=lambda d: d.frame))
        for object_id, dets in by_id.items()
    ]
    tracks = merge_fragments(tracks)
    min_frames = min(n_frames, max(3, math.ceil(0.15 * n_frames)))
    return sorted(
        (t for t in tracks if len(t.detections) >= min_frames), key=lambda t: int(t.track_id)
    )


def occupancy_count(tracks: Sequence[Track], n_frames: int) -> int:
    counts = [0] * n_frames
    for track in tracks:
        for detection in track.detections:
            counts[detection.frame] += 1
    return int(statistics.median_high(counts)) if counts else 0


def median_box(track: Track, last: int = 10) -> tuple[float, float, float, float]:
    recent = track.detections[-last:]
    return tuple(statistics.median(getattr(d, k) for d in recent) for k in ("x1", "y1", "x2", "y2"))  # type: ignore[return-value]


@dataclass
class DoorAnalysis:
    state: DoorState
    run: Optional[tuple[int, int]] = None
    detections_by_frame: dict[int, list[Detection]] = field(default_factory=dict)


def analyze_door(
    result: Optional[SamParseResult], n_frames: int, fps: float, min_confidence: float, bridge_s: float
) -> DoorAnalysis:
    """Open-door runs from SAM: gaps up to bridge_s are bridged; runs report edge contact."""
    flags = [False] * n_frames
    by_frame: dict[int, list[Detection]] = defaultdict(list)
    for detection in result.detections if result else []:
        if detection.frame >= n_frames:
            continue
        if detection.confidence is not None and detection.confidence < min_confidence:
            continue
        flags[detection.frame] = True
        by_frame[detection.frame].append(detection)
    open_frames = sum(flags)
    gap = int(round(bridge_s * fps))
    opened = [i for i, flag in enumerate(flags) if flag]
    bridged = list(flags)
    for a, b in zip(opened, opened[1:]):
        if 1 < b - a <= gap + 1:
            for i in range(a + 1, b):
                bridged[i] = True
    runs: list[tuple[int, int]] = []
    start: Optional[int] = None
    for i, flag in enumerate(bridged + [False]):
        if flag and start is None:
            start = i
        elif not flag and start is not None:
            runs.append((start, i - 1))
            start = None

    def seconds(run: tuple[int, int]) -> float:
        return (run[1] - run[0] + 1) / fps

    longest = max(runs, key=lambda r: r[1] - r[0]) if runs else None
    state = DoorState(
        longest_run_s=seconds(longest) if longest else 0.0,
        start_run_s=seconds(runs[0]) if runs and runs[0][0] == 0 else 0.0,
        end_run_s=seconds(runs[-1]) if runs and runs[-1][1] == n_frames - 1 else 0.0,
        spans_clip=runs == [(0, n_frames - 1)],
        open_frames=open_frames,
        closed_s_at_end=(n_frames - 1 - runs[-1][1]) / fps if runs else n_frames / fps,
    )
    return DoorAnalysis(state=state, run=longest, detections_by_frame=dict(by_frame))


def confirm_door(analysis: DoorAnalysis, keyframes: Sequence[int], spark_flags: Optional[Sequence[bool]]) -> None:
    """Spark must see the door open in at least half of the keyframes inside SAM's longest run."""
    state = analysis.state
    if spark_flags is None or len(spark_flags) < len(keyframes) or not keyframes:
        return
    flags = list(spark_flags)[: len(keyframes)]
    state.spark_open_tiles = sum(flags)
    if analysis.run is None:
        return
    start, end = analysis.run
    inside = [i for i, k in enumerate(keyframes) if start <= k <= end]
    if not inside:
        inside = [i for i, k in enumerate(keyframes) if start - 2 <= k <= end + 2]
    if not inside:
        return
    state.spark_tiles_in_run = len(inside)
    state.spark_confirmed = 2 * sum(flags[i] for i in inside) >= len(inside)


SPARK_PROMPT = """You are a thermal-comfort observer for an HVAC controller.
The image is a 3x2 storyboard of {tiles} keyframes from one {duration:.0f}-second CCTV clip, ordered left to right, top to bottom. Each tile shows its number and timestamp: {timeline}.
People are outlined and labelled with tracked IDs that stay the same across tiles: {ids}.
For each labelled person report:
- clothing_clo: clothing insulation in clo (0.5 short-sleeve summer wear, 0.6-0.7 shirt and trousers, 1.0 with a jacket or sweater);
- activity_met: metabolic rate in met (1.0 seated quiet, 1.1 typing, 1.2-1.4 standing, 2.0 walking);
- visual_discomfort_cues: zero or more of rubbing_arms, putting_on_jacket, hugging_self, shivering, fanning_self, wiping_sweat, removing_layer, shielding_from_airflow (empty list if none);
- posture: seated, standing, walking or other; posture_summary: a few words;
- apparent_thermal_sensation: your estimate on the ASHRAE scale from -3 (cold) to +3 (hot), 0 when neutral.
Also report door_open_by_frame: for each of the {tiles} tiles in order, whether a door is visibly standing open; and scene_summary: one sentence.
Describe only the labelled people and never try to identify anyone. Return JSON only."""


def spark_prompt(person_ids: Sequence[str], keyframes: Sequence[int], fps: float, duration_s: float) -> str:
    timeline = ", ".join(f"#{n} t={k / fps:.0f}s" for n, k in enumerate(keyframes, 1))
    ids = ", ".join(f"P{pid}" for pid in person_ids) or "none (no people were tracked)"
    return SPARK_PROMPT.format(tiles=len(keyframes), duration=duration_s, timeline=timeline, ids=ids)


_CUE_SYNONYMS = (
    ("jacket", "putting_on_jacket"),
    ("sweater", "putting_on_jacket"),
    ("rubbing", "rubbing_arms"),
    ("shiver", "shivering"),
    ("hug", "hugging_self"),
    ("fanning", "fanning_self"),
    ("sweat", "wiping_sweat"),
    ("removing", "removing_layer"),
    ("taking_off", "removing_layer"),
    ("shield", "shielding_from_airflow"),
    ("blocking", "shielding_from_airflow"),
)


def normalize_cue(cue: str) -> Optional[str]:
    name = re.sub(r"[\s\-]+", "_", cue.strip().lower())
    if name in VISUAL_CUES:
        return name
    for fragment, canonical in _CUE_SYNONYMS:
        if fragment in name:
            return canonical
    return None


def normalize_posture(posture: str) -> str:
    text = posture.strip().lower()
    for name in ("seated", "standing", "walking"):
        if name[:4] in text:
            return name
    return "seated" if "sit" in text else "unknown"


def normalize_person_id(person_id: str) -> str:
    digits = re.search(r"\d+", person_id or "")
    return digits.group(0) if digits else (person_id or "").strip()


def observations_from_spark(
    wire: Optional[SparkWire], track_ids: Sequence[str], warnings: list[str]
) -> dict[str, OccupantObservation]:
    observations: dict[str, OccupantObservation] = {}
    for occupant in wire.occupants if wire else []:
        person_id = normalize_person_id(occupant.person_id)
        if person_id not in track_ids:
            warnings.append(f"spark: ignored unknown person {occupant.person_id!r}")
            continue
        cues = []
        for cue in occupant.visual_discomfort_cues:
            name = normalize_cue(cue)
            if name and name not in cues:
                cues.append(name)
        sensation = occupant.apparent_thermal_sensation
        observations[person_id] = OccupantObservation(
            person_id=person_id,
            clothing_clo=_clamp(occupant.clothing_clo, 0.0, 2.0) if occupant.clothing_clo is not None else 0.6,
            activity_met=_clamp(occupant.activity_met, 0.8, 4.0) if occupant.activity_met is not None else 1.1,
            cues=cues,
            posture=normalize_posture(occupant.posture),  # type: ignore[arg-type]
            posture_summary=occupant.posture_summary[:120],
            apparent_sensation=_clamp(sensation, -3.0, 3.0) if sensation is not None else None,
            from_model=True,
        )
    for track_id in track_ids:
        observations.setdefault(track_id, OccupantObservation(person_id=track_id))
    return observations


def _seconds(segment: dict, key: str) -> Optional[float]:
    if segment.get(key) is not None:
        return float(segment[key])
    millis = segment.get(f"{key}Ms")
    return float(millis) / 1000 if millis is not None else None


def parse_transcript(raw: str, source: str) -> list[TranscriptTurn]:
    """Transcription output -> speaker turns.

    Meta's /asr/transcribe returns {"transcript", "turns": [{"speaker", "startMs", "transcript"}]};
    OpenAI-style output uses {"text", "segments": [{"speaker", "start", "end", "text"}]}.
    """
    try:
        data = extract_json(raw)
    except ValueError:
        text = raw.strip()
        return [TranscriptTurn(text=text, source=source)] if text else []
    turns: list[TranscriptTurn] = []
    if isinstance(data, dict):
        for segment in data.get("turns") or data.get("segments") or []:
            if not isinstance(segment, dict):
                continue
            text = str(segment.get("transcript") or segment.get("text") or "").strip()
            if text:
                turns.append(
                    TranscriptTurn(
                        text=text,
                        speaker=str(segment["speaker"]) if segment.get("speaker") is not None else None,
                        start_s=_seconds(segment, "start"),
                        end_s=_seconds(segment, "end"),
                        source=source,
                    )
                )
        whole = str(data.get("transcript") or data.get("text") or "").strip()
        if not turns and whole:
            turns.append(TranscriptTurn(text=whole, source=source))
    elif isinstance(data, str) and data.strip():
        turns.append(TranscriptTurn(text=data.strip(), source=source))
    return turns


_AC = r"(?:ac|a\.c\.?|aircon|air[\s-]?con(?:ditioner|ditioning)?|air)"
# (category, sensation, pattern); within a category the first match wins, most severe first.
COMFORT_PATTERNS: list[tuple[str, float, re.Pattern]] = [
    # "Draft" alone is usually a document in an office, so it needs drafty or a draft "in here"/"on me".
    ("draft", -1.5, re.compile(
        r"\bblow\w*\b[^.?!]*\b(?:on|at|in|into)\s+(?:me|my|our|us)\b|\b(?:drafty|draughty)\b"
        r"|\bthere'?s\s+a\s+(?:draft|draught)\b|\b(?:draft|draught)\s+(?:in\s+here|on\s+(?:me|my))\b",
        re.I,
    )),
    ("too_cold", -2.5, re.compile(r"\bfreezing\b|\bi'?m\s+frozen\b|\bice[\s-]?cold\b", re.I)),
    ("too_cold", -2.0, re.compile(r"\b(?:too|so|very|really|quite)\s+(?:cold|chilly)\b|\bit'?s\s+cold\b|\bcold\s+in\s+here\b|\bshivering\b", re.I)),
    ("too_cold", -1.0, re.compile(r"\bchilly\b|\ba\s+(?:bit|little)\s+cold\b", re.I)),
    ("too_hot", 2.0, re.compile(r"\b(?:too|so|very|really|quite)\s+(?:hot|warm)\b|\bit'?s\s+(?:hot|warm|boiling)\b|\bboiling\b|\bsweat(?:ing|y)\b|\b(?:hot|warm)\s+in\s+here\b", re.I)),
    ("stuffy", 1.5, re.compile(r"\bstuffy\b|\bno\s+air\b|\bairless\b", re.I)),
]
SET_TEMPERATURE = re.compile(r"\b(?:set|put|make|keep)\b[^.?!]*?\b(?:to|at)\s+(\d{2}(?:\.\d)?)\b", re.I)
MORE_COOLING = re.compile(
    rf"\bturn\s+(?:up|on)\s+the\s+{_AC}\b|\bmore\s+cooling\b|\bcool\s+(?:it\s+|the\s+room\s+|us\s+)?down\b"
    r"|\bturn\s+down\s+the\s+(?:temp\w*|heat)\b|\blower\s+the\s+temp\w*",
    re.I,
)
LESS_COOLING = re.compile(
    rf"\bturn\s+(?:down|off)\s+the\s+{_AC}\b|\bless\s+cooling\b|\bwarm\s+(?:it\s+|the\s+room\s+|us\s+)?up\b"
    r"|\bturn\s+up\s+the\s+(?:temp\w*|heat)\b|\braise\s+the\s+temp\w*",
    re.I,
)
FAN_UP = re.compile(r"\b(?:fan|airflow)\b[^.?!]*\b(?:up|higher|faster|stronger)\b|\bmore\s+air(?:flow)?\b", re.I)
FAN_DOWN = re.compile(r"\b(?:fan|airflow)\b[^.?!]*\b(?:down|lower|slower|weaker)\b|\bless\s+air(?:flow)?\b", re.I)


def extract_complaints(turns: Sequence[TranscriptTurn], texts: Sequence[str]) -> list[Complaint]:
    utterances = [(t.text, "voice", t.speaker) for t in turns]
    utterances += [(text, "text", None) for text in texts if text.strip()]
    complaints: list[Complaint] = []
    for text, source, speaker in utterances:
        found: set[str] = set()

        def add(category: str, **extra) -> None:
            found.add(category)
            complaints.append(Complaint(category=category, text=text, source=source, speaker=speaker, **extra))

        for category, sensation, pattern in COMFORT_PATTERNS:
            if category not in found and pattern.search(text):
                add(category, sensation=sensation)
        match = SET_TEMPERATURE.search(text)
        if match and 16.0 <= float(match.group(1)) <= 30.0:
            add("set_temperature", value_c=float(match.group(1)))
        if MORE_COOLING.search(text) and "too_hot" not in found:
            add("more_cooling", sensation=1.0)
        if LESS_COOLING.search(text) and "too_cold" not in found:
            add("less_cooling", sensation=-1.0)
        if FAN_UP.search(text):
            add("fan_up")
        elif FAN_DOWN.search(text):
            add("fan_down")
    return complaints


def cue_sign(cues: Sequence[str]) -> int:
    cold = any(c in COLD_CUES or c in DRAFT_CUES for c in cues)
    hot = any(c in HOT_CUES for c in cues)
    return 0 if cold == hot else (-1 if cold else 1)


def attribute_complaints(complaints: Sequence[Complaint], occupants: Sequence[OccupantState]) -> None:
    """Sidecar audio has no shared clock with the clip, so attribution uses what was seen."""
    if not occupants:
        return
    for complaint in complaints:
        if complaint.category == "draft":
            exposed = max(occupants, key=lambda o: o.jet_speed_ms or 0.0)
            complaint.attributed_to = [exposed.occupant_key]
        elif complaint.category in ("too_cold", "too_hot", "stuffy"):
            sign = -1 if complaint.category == "too_cold" else 1
            matching = [o.occupant_key for o in occupants if cue_sign(o.cues) == sign]
            if matching:
                complaint.attributed_to = matching
            elif len(occupants) == 1:
                complaint.attributed_to = [occupants[0].occupant_key]


