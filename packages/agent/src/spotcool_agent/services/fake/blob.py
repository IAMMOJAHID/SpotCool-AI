"""BlobVision: people counted as blobs of saturated colour, for the world's synthetic frames.

The world draws each person in a synthetic frame as one silhouette of saturated colour on an
unsaturated room, and SAM 3.1 sees nobody in them. This counter is the world's own test counter
(``_count_people`` in ``packages/world/tests/cameras/conftest.py``), ported from OpenCV to
Pillow and NumPy: pixels with HSV saturation above ``SATURATION`` are joined into 8-connected
blobs, and each blob of more than ``MIN_AREA`` pixels is one person. It is exact on synthetic
frames, which is what tests, CI and the end-to-end eval need; on real footage it means nothing.
"""

import asyncio
import io

import numpy as np
import numpy.typing as npt
from PIL import Image

from spotcool_agent.domain.geometry import Detection, Detections
from spotcool_agent.services.vision import VisionError, VisionService

SATURATION = 120
"""HSV saturation (0-255) above which a pixel belongs to a person."""
MIN_AREA = 400
"""Pixels a blob needs to count as a person: JPEG fringes stay below it."""


class BlobVision(VisionService):
    """Counts the colour blobs of synthetic frames (see the module docstring).

    The concept is ignored. The work runs in a worker thread.
    """

    def __init__(self) -> None:
        super().__init__(name="BlobVision")

    async def detect(
        self, jpeg: bytes, concept: str, timeout_s: float, *, camera_id: str | None = None
    ) -> Detections:
        """Count the blobs in the image (see ``VisionService.detect``)."""
        if timeout_s <= 0:
            raise VisionError("no time left for the count")
        try:
            async with asyncio.timeout(timeout_s):
                return await asyncio.to_thread(blobs, jpeg)
        except TimeoutError:
            raise VisionError(f"no count within {timeout_s:.2f} s") from None
        except (OSError, ValueError) as exc:  # PIL's UnidentifiedImageError is an OSError
            raise VisionError(f"not an image: {type(exc).__name__}") from None


def blobs(jpeg: bytes) -> Detections:
    """The saturated blobs of more than ``MIN_AREA`` pixels in a JPEG, left to right.

    Raises:
        OSError: If the bytes are not an image.
    """
    with Image.open(io.BytesIO(jpeg)) as image:
        width, height = image.size
        saturation = np.asarray(image.convert("HSV"))[:, :, 1]
    boxes = [
        (x1, y1, x2, y2)
        for area, x1, y1, x2, y2 in _components(saturation > SATURATION)
        if area > MIN_AREA
    ]
    boxes.sort()
    found = tuple(
        Detection(i, x1 / width, y1 / height, (x2 + 1) / width, (y2 + 1) / height)
        for i, (x1, y1, x2, y2) in enumerate(boxes)
    )
    return Detections(found, width, height)


def _components(mask: npt.NDArray[np.bool_]) -> list[tuple[int, int, int, int, int]]:
    """Each 8-connected blob of ``mask`` as ``(area, x1, y1, x2, y2)``, corners inclusive.

    Each row's runs of set pixels are joined to the touching runs of the row above
    (diagonals included) with a union-find, so the work grows with the runs, not the pixels.
    """
    padded = np.zeros((mask.shape[0], mask.shape[1] + 2), np.int8)
    padded[:, 1:-1] = mask
    edges = np.diff(padded, axis=1)
    rows, starts = np.nonzero(edges == 1)  # a run's first column
    _, ends = np.nonzero(edges == -1)  # one past a run's last column
    row, start, end = rows.tolist(), starts.tolist(), ends.tolist()
    parent = list(range(len(row)))

    def find(i: int) -> int:
        while parent[i] != i:
            parent[i] = parent[parent[i]]
            i = parent[i]
        return i

    above = (0, 0)  # the previous row's runs, as an index range, if that row is adjacent
    begin = 0  # the current row's first run
    for i in range(len(row)):
        if i == 0 or row[i] != row[i - 1]:
            above = (begin, i) if i and row[i] == row[i - 1] + 1 else (i, i)
            begin = i
        for j in range(*above):
            if start[j] <= end[i] and start[i] <= end[j]:  # touching, diagonals included
                parent[find(j)] = find(i)
    found: dict[int, list[int]] = {}
    for i in range(len(row)):
        blob = found.setdefault(find(i), [0, start[i], row[i], end[i] - 1, row[i]])
        blob[0] += end[i] - start[i]
        blob[1], blob[3] = min(blob[1], start[i]), max(blob[3], end[i] - 1)
        blob[2], blob[4] = min(blob[2], row[i]), max(blob[4], row[i])
    return [(a, x1, y1, x2, y2) for a, x1, y1, x2, y2 in found.values()]
