"""FramePublisher: puts chosen pipeline frames on the bus, for workers outside the pipeline."""

from spotcool_world.bus import Bus
from spotcool_world.frames import Frame
from spotcool_world.processors.frame_processor import FrameDirection, FrameProcessor

WORLD_FRAMES_TOPIC = "world"
"""The topic pipeline frames are published on for the world's other workers (the occupant
worker, the calendar store)."""


class FramePublisher(FrameProcessor):
    """Publishes every frame of the given types (the frame itself) and passes every frame on.

    The bridge from the lockstep pipeline to the bus: workers that run beside the pipeline
    (``OccupantWorker``, the calendar's store) subscribe to the topic and pick the frame types
    they need. Publishing never blocks: a slow subscriber drops its oldest messages.

    Args:
        bus: The bus to publish on.
        frame_types: The frame classes to publish (subclasses included).
        topic: Topic name.
        name: Display name.
    """

    def __init__(
        self,
        bus: Bus,
        frame_types: tuple[type[Frame], ...],
        topic: str = WORLD_FRAMES_TOPIC,
        *,
        name: str | None = None,
    ) -> None:
        super().__init__(name=name)
        self._bus = bus
        self._frame_types = frame_types
        self._topic = topic

    async def process_frame(self, frame: Frame, direction: FrameDirection) -> None:
        """Publish the frame if it is of a chosen type, then pass it on."""
        await super().process_frame(frame, direction)
        if isinstance(frame, self._frame_types):
            await self._bus.publish(self._topic, frame)
        await self.push_frame(frame, direction)
