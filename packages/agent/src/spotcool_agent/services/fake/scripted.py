"""ScriptedVision: fixed people counts per camera, for unit tests and dry rehearsals.

It never looks at the image. ``counts[camera_id]`` people are "found" in every snapshot of that
camera, as boxes side by side; a camera mapped to ``None`` fails as a call that timed out.
Tests change ``counts`` between steps.
"""

from collections.abc import Mapping

from spotcool_agent.domain.geometry import Detection, Detections
from spotcool_agent.services.vision import VisionError, VisionService

FRAME_SIZE = (1280, 720)
"""The frame size the scripted boxes are given in: the world's zone cameras'."""


class ScriptedVision(VisionService):
    """Fixed counts per camera (see the module docstring).

    Args:
        counts: People per camera id; ``None`` makes that camera's calls fail.
        default: People in a camera ``counts`` does not name.
    """

    def __init__(self, counts: Mapping[str, int | None] | None = None, *, default: int = 0) -> None:
        super().__init__(name="ScriptedVision")
        self.counts: dict[str, int | None] = dict(counts or {})
        self.default = default
        self.calls = 0

    async def detect(
        self, jpeg: bytes, concept: str, timeout_s: float, *, camera_id: str | None = None
    ) -> Detections:
        """The camera's scripted count as boxes (see ``VisionService.detect``)."""
        self.calls += 1
        people = self.counts.get(camera_id, self.default) if camera_id else self.default
        if people is None:
            raise VisionError(f"scripted failure on {camera_id}")
        width = 1.0 / max(people, 1)
        boxes = tuple(Detection(k, k * width, 0.2, (k + 0.8) * width, 0.9) for k in range(people))
        return Detections(boxes, *FRAME_SIZE)
