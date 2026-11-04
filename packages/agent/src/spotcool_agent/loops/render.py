"""RenderLoop: Muse Image's privacy view of the cameras, display only.

Every ``every_s`` of wall time (``RENDER_EVERY_S``), it renders the zone cameras one at a time,
then the meeting room's, each from a fresh snapshot held in memory, and keeps only the latest
render of each camera, in memory (``latest``), for the agent's server to show at
``GET /privacy/{camera_id}.jpg``. A camera the catalog doesn't list is skipped; a render that
fails keeps the camera's last one. A round that sent nothing is tried again after ``RETRY_S``,
because the agent starts before the world. When the renders' call cap is used up, the loop logs it once
and ends; it never stops the agent.

**It is never on an actuation path**: it never touches the BMS, the VRF, the policy or a step's
deadline, and nothing reads its renders but the server. A render is an illustration, never
evidence.
"""

import asyncio
import time
from collections.abc import Sequence

from loguru import logger

from spotcool_agent.services.render import RenderError, RenderService
from spotcool_agent.services.vision import CallCapReached
from spotcool_agent.transports.world_http import WorldHttp, WorldHttpError

RENDER_EVERY_S = 180.0
"""Wall seconds from one round's start to the next: each render takes about 24 s."""
RETRY_S = 5.0
"""Wall seconds to the next round after one that sent nothing: no catalog or no frame yet."""
TIMEOUT_S = 45.0
"""The longest one render may take."""


class RenderLoop:
    """Privacy renders of the cameras, in the background (see the module docstring).

    Args:
        render: The renderer, on a budget of its own.
        world: The world's cameras.
        cameras: The camera ids to render, in order: the zones', then the meeting room's.
        every_s: Wall seconds from one round's start to the next.
    """

    def __init__(
        self,
        render: RenderService,
        world: WorldHttp,
        *,
        cameras: Sequence[str],
        every_s: float = RENDER_EVERY_S,
    ) -> None:
        self._render = render
        self._world = world
        self._cameras = list(dict.fromkeys(cameras))
        self._every_s = every_s
        self._failing: str | None = None  # the last failure, logged once
        self._sent = 0  # renders asked for, so a failed call waits the full round: it costs budget
        self.latest: dict[str, bytes] = {}
        """The latest render of each camera, JPEG bytes in memory."""

    async def run(self) -> None:
        """Render a round every ``every_s`` until the call cap is used up or a cancel."""
        while True:
            began, sent = time.perf_counter(), self._sent
            if not await self.round():
                return
            wait = self._every_s if self._sent > sent else min(RETRY_S, self._every_s)
            await asyncio.sleep(max(0.0, wait - (time.perf_counter() - began)))

    async def round(self) -> bool:
        """Render each listed camera once; ``False`` once the call cap is used up."""
        try:
            catalog = await self._world.cameras()
        except WorldHttpError as exc:
            self._failed(f"the catalog: {exc}")
            return True
        urls = {camera.id: camera.snapshot_url for camera in catalog.cameras}
        for camera in self._cameras:
            if camera not in urls:
                continue
            try:
                jpeg = await self._world.snapshot(urls[camera])
                self._sent += 1
                self.latest[camera] = await self._render.render(jpeg, TIMEOUT_S, camera_id=camera)
            except CallCapReached as exc:
                logger.warning(f"privacy renders: {exc}; the last renders stay on show")
                return False
            except (WorldHttpError, RenderError) as exc:
                self._failed(f"{camera}: {exc}")
                continue
            except Exception as exc:  # a provider's bug: this render is lost, the loop goes on
                self._failed(f"{camera}: {type(exc).__name__}: {exc}")
                continue
            self._failing = None
        return True

    def _failed(self, why: str) -> None:
        if self._failing != why:
            logger.warning(f"privacy renders: {self._render.name}: {why}")
        self._failing = why
