"""Observers watch frames move between processors without changing anything."""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING

from spotcool_world.frames import Frame
from spotcool_world.utils.base_object import BaseObject

if TYPE_CHECKING:
    from spotcool_world.processors.frame_processor import FrameDirection, FrameProcessor


@dataclass(kw_only=True)
class FramePushed:
    """One frame handed from one processor to its neighbour.

    Parameters:
        source: The processor that pushed the frame.
        destination: The processor about to receive it.
        frame: The frame.
        direction: Downstream or upstream.
        timestamp_ns: Wall-clock time of the push, ``time.perf_counter_ns()``.
    """

    source: FrameProcessor
    destination: FrameProcessor
    frame: Frame
    direction: FrameDirection
    timestamp_ns: int


class BaseObserver(BaseObject):
    """Base class for observers.

    ``on_push_frame`` is awaited on every push before the neighbour receives the frame, so it
    must be quick and must never modify the frame.
    """

    async def on_push_frame(self, data: FramePushed) -> None:
        """Called for every push in the pipeline the observer is attached to.

        Args:
            data: The push.
        """
