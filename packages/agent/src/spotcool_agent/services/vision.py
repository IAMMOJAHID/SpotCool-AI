"""VisionService: the base every people counter implements (SAM 3.1, the blob counter, scripts).

A provider takes one JPEG snapshot, held in memory only, and returns the objects it found as
``Detections``. Any failure (an error, a timeout, a refused call) raises ``VisionError``, which
the building loop counts as "unknown" for that zone.
"""

import io
from abc import ABC, abstractmethod

from PIL import Image

from spotcool_agent.domain.geometry import Detections


class VisionError(Exception):
    """A detection that gave no answer: it failed, timed out or was refused."""


class CallCapReached(VisionError):
    """The run's cap on model calls is reached: no more calls are made."""


class VisionService(ABC):
    """Counts objects (people, by default) in one camera snapshot.

    Args:
        name: Display name, for logs.
    """

    def __init__(self, name: str) -> None:
        self.name = name

    @abstractmethod
    async def detect(
        self, jpeg: bytes, concept: str, timeout_s: float, *, camera_id: str | None = None
    ) -> Detections:
        """Find every ``concept`` in one image within ``timeout_s`` wall seconds.

        Args:
            jpeg: The image, JPEG bytes in memory.
            concept: What to find, such as ``"humans"``.
            timeout_s: Wall seconds the answer may take.
            camera_id: The camera the image came from; only scripted providers use it.

        Raises:
            VisionError: If no answer came in time, or the call failed or was refused.
        """

    async def close(self) -> None:  # noqa: B027 (a hook: most providers hold no connection)
        """Release the provider's connections. Safe to call twice."""


def image_size(jpeg: bytes) -> tuple[int, int]:
    """An image's width and height in pixels, from its header alone.

    Raises:
        VisionError: If the bytes are not an image.
    """
    try:
        with Image.open(io.BytesIO(jpeg)) as image:
            return image.size
    except (OSError, ValueError) as exc:  # PIL's UnidentifiedImageError is an OSError
        raise VisionError(f"not an image: {type(exc).__name__}") from None
