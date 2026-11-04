"""SceneService: the base every scene reader implements (Meta's ``muse-spark-1.3``, a fake).

A provider looks at one camera snapshot, held in memory only, and describes who is there for
thermal comfort: each person's ASHRAE 55 clothing ensemble, activity, posture and any visible
discomfort cue, and the room's open doors and windows, sunlight and space type. Never a
temperature: the comfort physics computes those (``domain/comfort.py``). Any failure (an error,
a timeout, the call cap, an answer that is not the JSON asked for) raises ``SceneError``.

Its answers feed explanations and the meeting room's comfort setpoint only, never the BMS
policy: the building's writes come from SAM 3.1's counts alone.
"""

from abc import ABC, abstractmethod

from spotcool_agent.domain.scene import Scene


class SceneError(Exception):
    """A scene reading that gave no valid answer: it failed, timed out or was refused."""


class SceneService(ABC):
    """Reads one camera snapshot for comfort (see the module docstring).

    Args:
        name: Display name, for logs.
    """

    def __init__(self, name: str) -> None:
        self.name = name

    @abstractmethod
    async def assess(
        self, jpeg: bytes, sam_count: int | None, timeout_s: float, *, camera_id: str
    ) -> Scene:
        """Describe the people and the room in one image, within ``timeout_s`` wall seconds.

        Args:
            jpeg: The image, JPEG bytes in memory.
            sam_count: The people SAM 3.1 counted in it, a hint; ``None`` if unknown.
            timeout_s: Wall seconds the answer may take.
            camera_id: The camera the image came from.

        Raises:
            SceneError: If no valid answer came in time, or the call failed or was refused.
        """

    async def close(self) -> None:  # noqa: B027 (a hook: the fake holds no connection)
        """Release the provider's connections. Safe to call twice."""
