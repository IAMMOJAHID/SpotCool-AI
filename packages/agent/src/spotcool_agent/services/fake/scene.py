"""FakeScene: a scripted scene, for tests and offline rehearsals.

It never looks at the image. It answers ``scene`` to every call, or fails with ``SceneError``
when ``scene`` is ``None``; ``delay_s`` makes it slow. It counts its calls and keeps each one's
SAM count, never the frame.
"""

import asyncio

from spotcool_agent.domain.scene import PersonScene, Scene
from spotcool_agent.services.scene import SceneError, SceneService

OFFICE_WORKER = PersonScene(
    clothing_ensemble="trousers_long_sleeve",
    activity="seated_typing",
    posture="seated",
    discomfort_cue="none",
)
"""One seated person in typical office clothing."""


def office(people: int = 1, *, doors_open: int = 0) -> Scene:
    """A scene of ``people`` seated office workers."""
    return Scene(
        people=[OFFICE_WORKER] * people,
        doors_open=doors_open,
        windows_open=0,
        sunlight="none",
        space_type="office",
    )


OFFICE = office()
"""The default answer: one seated office worker, doors and windows shut."""


class FakeScene(SceneService):
    """A scripted scene (see the module docstring).

    Args:
        scene: The answer; ``None`` fails. By default one seated office worker, doors shut.
        delay_s: Wall seconds each answer takes.
    """

    def __init__(self, scene: Scene | None = OFFICE, *, delay_s: float = 0.0) -> None:
        super().__init__(name="FakeScene")
        self.scene = scene
        self.delay_s = delay_s
        self.asked: list[tuple[str, int | None]] = []
        """Each call's camera and SAM count."""

    async def assess(
        self, jpeg: bytes, sam_count: int | None, timeout_s: float, *, camera_id: str
    ) -> Scene:
        """The scripted scene (see ``SceneService.assess``)."""
        self.asked.append((camera_id, sam_count))
        try:
            async with asyncio.timeout(timeout_s):
                await asyncio.sleep(self.delay_s)
        except TimeoutError:
            raise SceneError(f"no answer within {timeout_s:.1f} s") from None
        if self.scene is None:
            raise SceneError("scripted failure")
        return self.scene
