"""RenderService: the base every privacy renderer implements (Meta's ``muse-image-1.0``, a fake).

A provider redraws one camera snapshot, held in memory only, as a flat, faceless illustration,
so an operator can follow the building without seeing anyone's face. The render is an
illustration, never evidence: it is for display only, and nothing the agent decides reads it.
Any failure (an error, a timeout, an answer without an image) raises ``RenderError``; a used-up
call cap raises ``CallCapReached``.
"""

from abc import ABC, abstractmethod


class RenderError(Exception):
    """A render that gave no image: it failed, timed out or was refused."""


class RenderService(ABC):
    """Redraws one camera snapshot for privacy (see the module docstring).

    Args:
        name: Display name, for logs.
    """

    def __init__(self, name: str) -> None:
        self.name = name

    @abstractmethod
    async def render(self, jpeg: bytes, timeout_s: float, *, camera_id: str) -> bytes:
        """The privacy render of one image, as JPEG bytes in memory, within ``timeout_s``.

        Args:
            jpeg: The snapshot, JPEG bytes in memory.
            timeout_s: Wall seconds the render may take.
            camera_id: The camera the snapshot came from.

        Raises:
            RenderError: If no image came in time, or the call failed or was refused.
            CallCapReached: If the renders' call cap is used up; nothing is sent.
        """

    async def close(self) -> None:  # noqa: B027 (a hook: the fake holds no connection)
        """Release the provider's connections. Safe to call twice."""
