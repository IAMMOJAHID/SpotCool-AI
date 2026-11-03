"""BusPublisher: puts dashboard events on the bus as they flow past."""

from spotcool_world.bus import Bus
from spotcool_world.frames import EventFrame, Frame
from spotcool_world.processors.frame_processor import FrameDirection, FrameProcessor


class BusPublisher(FrameProcessor):
    """Publishes the envelope of every ``EventFrame`` on a topic and passes every frame on.

    Args:
        bus: The bus to publish on.
        topic: Topic name.
        name: Display name.
    """

    def __init__(self, bus: Bus, topic: str = "events", *, name: str | None = None) -> None:
        super().__init__(name=name)
        self._bus = bus
        self._topic = topic

    async def process_frame(self, frame: Frame, direction: FrameDirection) -> None:
        """Publish event envelopes, then pass the frame on."""
        await super().process_frame(frame, direction)
        if isinstance(frame, EventFrame):
            await self._bus.publish(self._topic, frame.envelope)
        await self.push_frame(frame, direction)
