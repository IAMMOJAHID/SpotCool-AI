"""LogObserver: logs chosen frame types as they first move."""

from loguru import logger

from spotcool_world.frames import CancelFrame, EndFrame, ErrorFrame, Frame, StartFrame
from spotcool_world.observers.base_observer import BaseObserver, FramePushed
from spotcool_world.utils.seen_ids import SeenIds

DEFAULT_LOGGED_FRAMES: tuple[type[Frame], ...] = (StartFrame, EndFrame, CancelFrame, ErrorFrame)


class LogObserver(BaseObserver):
    """Logs each frame of the chosen types once, on its first push.

    Args:
        frame_types: Frame classes to log (subclasses included).
        level: Loguru level name.
        name: Display name.
    """

    def __init__(
        self,
        frame_types: tuple[type[Frame], ...] = DEFAULT_LOGGED_FRAMES,
        *,
        level: str = "INFO",
        name: str | None = None,
    ) -> None:
        super().__init__(name=name)
        self._frame_types = frame_types
        self._level = level
        self._seen = SeenIds()

    async def on_push_frame(self, data: FramePushed) -> None:
        """Log the frame if it is one of the chosen types and has not been logged yet."""
        frame = data.frame
        if isinstance(frame, self._frame_types) and self._seen.add(frame.id):
            direction = data.direction.name.lower()
            logger.log(
                self._level,
                f"{frame.name} {direction} from {data.source.name} (sim {frame.sim_time_s})",
            )
