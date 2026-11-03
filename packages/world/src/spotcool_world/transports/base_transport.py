"""BaseTransport: the processors that sit around one protocol the agent touches."""

from abc import ABC, abstractmethod

from spotcool_world.processors.frame_processor import FrameProcessor
from spotcool_world.utils.base_object import BaseObject


class BaseTransport(BaseObject, ABC):
    """Base of every transport (BACnet BMS, VRF gateway, cameras, calendar, dashboard).

    ``input()`` brings what the outside world did into the pipeline (e.g. the agent's BACnet
    writes as ``SetpointCommandFrame``s); ``output()`` shows the pipeline's state to the outside
    world (e.g. zone temperatures as BACnet present values). Both return the same processor
    every time, so the transport can be placed in a pipeline once.

    Args:
        name: Display name.
    """

    @abstractmethod
    def input(self) -> FrameProcessor:
        """The processor that turns outside events into frames."""

    @abstractmethod
    def output(self) -> FrameProcessor:
        """The processor that publishes frames to the outside world."""

    @abstractmethod
    async def start(self) -> None:
        """Open sockets or servers. Called before the run starts."""

    @abstractmethod
    async def stop(self) -> None:
        """Close everything ``start()`` opened. Safe to call twice."""
