"""Detections from a vision model, and SAM 3.1's text output parsed into them.

``Detection``, ``parse_sam_output`` and their patterns are Person A's, from the MVP
(``app/spotcool/perception.py`` on ``third_level_improvement``, ``b653cbf``), kept near-verbatim:
typed, documented, and ``Detection`` frozen. SAM 3.1 answers in ``response.output_text``, one
token per object, in the image's pixels with the image's size:

``<0f>0<|box;x1=980;y1=126;x2=1014;y2=273;w=1920;h=1072;c=0.8358|><|mask;...|>``

An empty string means nobody. Boxes are kept as fractions of the image (0 to 1); mask payloads
are skipped. ``Detections`` adds the image's size, so the boxes can be drawn in its pixels.
"""

import re
from collections.abc import Sequence
from dataclasses import dataclass

_NUMBER = r"(-?\d+(?:\.\d+)?)"
FRAME_MARKER = re.compile(r"<(\d+)f>(?=\d+<\|box)")
"""The start of one video frame's objects: ``<Nf>``."""
BOX_TOKEN = re.compile(
    r"(\d+)<\|box;x1="
    + _NUMBER
    + ";y1="
    + _NUMBER
    + ";x2="
    + _NUMBER
    + ";y2="
    + _NUMBER
    + r";w=(\d+(?:\.\d+)?);h=(\d+(?:\.\d+)?)(?:;c=([0-9.eE+-]+))?\|>"
)
"""One object's box: its id, corners, the image's size and, optionally, the confidence."""
LEGACY_BOX = re.compile(r"x1=(\d+);y1=(\d+);x2=(\d+);y2=(\d+);w=(\d+);h=(\d+)")
"""The older box format, without ids or confidence."""


@dataclass(frozen=True)
class Detection:
    """One object found in an image, its box as fractions of the image's width and height.

    Parameters:
        object_id: The model's id for the object.
        x1: Left edge (0-1).
        y1: Top edge (0-1).
        x2: Right edge (0-1).
        y2: Bottom edge (0-1).
        confidence: The model's confidence, if it gave one.
    """

    object_id: int
    x1: float
    y1: float
    x2: float
    y2: float
    confidence: float | None = None

    @property
    def cx(self) -> float:
        """The box's horizontal centre (0-1)."""
        return (self.x1 + self.x2) / 2

    @property
    def cy(self) -> float:
        """The box's vertical centre (0-1)."""
        return (self.y1 + self.y2) / 2

    def as_list(self) -> list[float]:
        """The box as ``[x1, y1, x2, y2]``, rounded to 4 places."""
        return [round(self.x1, 4), round(self.y1, 4), round(self.x2, 4), round(self.y2, 4)]


@dataclass(frozen=True)
class Detections:
    """What a vision provider found in one image.

    Parameters:
        boxes: The objects found.
        width: The image's width in pixels.
        height: The image's height in pixels.
    """

    boxes: tuple[Detection, ...]
    width: int
    height: int

    def polygons(self) -> list[list[list[float]]]:
        """Each box as a rectangle (four ``[x, y]`` corners from the top left) in pixels."""
        w, h = self.width, self.height
        return [
            [
                [round(d.x1 * w, 1), round(d.y1 * h, 1)],
                [round(d.x2 * w, 1), round(d.y1 * h, 1)],
                [round(d.x2 * w, 1), round(d.y2 * h, 1)],
                [round(d.x1 * w, 1), round(d.y2 * h, 1)],
            ]
            for d in self.boxes
        ]


def _normalized(
    object_id: int, values: Sequence[str], width: str, height: str, confidence: str | None
) -> Detection | None:
    """A box in fractions of the image, or ``None`` for an empty box or a sizeless image."""
    w, h = float(width), float(height)
    if w <= 0 or h <= 0:
        return None
    x1, y1, x2, y2 = (float(v) for v in values)
    detection = Detection(
        object_id,
        max(0.0, min(1.0, x1 / w)),
        max(0.0, min(1.0, y1 / h)),
        max(0.0, min(1.0, x2 / w)),
        max(0.0, min(1.0, y2 / h)),
        float(confidence) if confidence else None,
    )
    return detection if detection.x2 > detection.x1 and detection.y2 > detection.y1 else None


def parse_sam_output(text: str) -> dict[int, list[Detection]]:
    """Parse ``<Nf>id<|box;...|><|mask;...|>,id...`` into each frame's detections.

    Text without frame markers is one frame, 0. An id repeated within a frame keeps its first
    box. Text with no box tokens falls back to the legacy format. Mask payloads are skipped.

    Args:
        text: SAM 3.1's ``output_text``.

    Returns:
        Each frame's detections, by frame index; empty when nobody was found.
    """
    frames: dict[int, list[Detection]] = {}
    markers = list(FRAME_MARKER.finditer(text))
    spans = (
        [
            (
                int(m.group(1)),
                m.start(),
                markers[i + 1].start() if i + 1 < len(markers) else len(text),
            )
            for i, m in enumerate(markers)
        ]
        if markers
        else [(0, 0, len(text))]
    )
    for frame_index, begin, end in spans:
        seen: set[int] = set()
        for match in BOX_TOKEN.finditer(text, begin, end):
            object_id = int(match.group(1))
            if object_id in seen:
                continue
            detection = _normalized(
                object_id, match.groups()[1:5], match.group(6), match.group(7), match.group(8)
            )
            if detection:
                seen.add(object_id)
                frames.setdefault(frame_index, []).append(detection)
    if not frames:
        for index, match in enumerate(LEGACY_BOX.finditer(text)):
            detection = _normalized(index, match.groups()[:4], match.group(5), match.group(6), None)
            if detection:
                frames.setdefault(0, []).append(detection)
    return frames
