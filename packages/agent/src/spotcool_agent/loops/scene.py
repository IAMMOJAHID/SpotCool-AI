"""SceneNotes: Muse Spark's notes on the building's occupied zones, explanations only.

After a building step has made its writes, ``offer()`` starts a background reading of each
occupied zone's snapshot, the step's own, already in memory, at most once every
``every_steps`` steps per zone. A zone whose last reading is still running is skipped. Each
reading has ``TIMEOUT_S`` and goes out as an ``explanation`` ("Muse Spark, West: 1 person,
typical office clothing; a door is open"); when its count disagrees with SAM 3.1's (the MVP's
rule, ``explain.counts_disagree``), a second one says the policy keeps SAM's. A reading that
fails says nothing. The frame is dropped when the reading ends.

**It never changes the policy's input**: it is offered the counts after the policy has used
them and the writes are made, and nothing it learns flows back.
"""

import asyncio
import math
from collections.abc import Mapping

from loguru import logger

from spotcool_agent.actuator import Publish
from spotcool_agent.domain.events import Explanation
from spotcool_agent.processors import explain
from spotcool_agent.services.scene import SceneError, SceneService
from spotcool_contracts import Zone
from spotcool_contracts.events import EventType

EVERY_STEPS = 4
"""Steps between two readings of one zone, at least."""
TIMEOUT_S = 30.0
"""The longest one reading may take."""


class SceneNotes:
    """Background scene notes on the zones (see the module docstring).

    Args:
        scene: The scene reader, on a budget of its own.
        publish: Where the explanations go.
        every_steps: Steps between two readings of one zone, at least.
    """

    def __init__(
        self, scene: SceneService, publish: Publish, *, every_steps: int = EVERY_STEPS
    ) -> None:
        self._scene = scene
        self._publish = publish
        self._every = every_steps
        self._offers = 0
        self._last: dict[Zone, int] = {}  # zone -> the offer its last reading started at
        self._reading: dict[Zone, asyncio.Task[None]] = {}
        self._failing: str | None = None  # the last failure, logged once

    def offer(
        self,
        jpegs: Mapping[Zone, bytes],
        counts: Mapping[Zone, int],
        cameras: Mapping[Zone, str],
        t_s: float,
    ) -> None:
        """Start a reading of each occupied zone that is due and not being read; never waits.

        Args:
            jpegs: The step's snapshots, by zone.
            counts: SAM 3.1's counts the policy used, by zone.
            cameras: Each zone's camera id.
            t_s: The stamp of the explanations: the step's start.
        """
        self._offers += 1
        for zone, count in counts.items():
            jpeg, camera = jpegs.get(zone), cameras.get(zone)
            if count <= 0 or jpeg is None or camera is None or zone in self._reading:
                continue
            if self._offers - self._last.get(zone, -math.inf) < self._every:
                continue
            self._last[zone] = self._offers
            task = asyncio.create_task(
                self._note(zone, jpeg, count, camera, t_s), name=f"scene-{zone.value}"
            )
            self._reading[zone] = task
            task.add_done_callback(lambda _, zone=zone: self._reading.pop(zone, None))

    async def stop(self) -> None:
        """Cancel the readings under way. Safe to call twice."""
        reading = list(self._reading.values())
        for task in reading:
            task.cancel()
        await asyncio.gather(*reading, return_exceptions=True)

    async def _note(self, zone: Zone, jpeg: bytes, sam: int, camera: str, t_s: float) -> None:
        try:
            scene = await self._scene.assess(jpeg, sam, TIMEOUT_S, camera_id=camera)
        except Exception as exc:  # a SceneError, or a provider's bug: this note is lost
            why = str(exc) if isinstance(exc, SceneError) else f"{type(exc).__name__}: {exc}"
            if self._failing != why:
                logger.warning(f"scene notes: {self._scene.name}: {why}")
            self._failing = why
            return
        self._failing = None
        self._publish(EventType.EXPLANATION, Explanation(text=explain.scene_note(zone, scene)), t_s)
        spark = len(scene.people)
        if explain.counts_disagree(sam, spark):
            text = explain.disagreement(zone, sam, spark)
            self._publish(EventType.EXPLANATION, Explanation(text=text), t_s)
