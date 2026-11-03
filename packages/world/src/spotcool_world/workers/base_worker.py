"""BaseWorker: an independently runnable unit on the bus."""

from abc import ABC, abstractmethod
from enum import StrEnum

from spotcool_world.bus import Bus
from spotcool_world.utils.base_object import BaseObject


class WorkerState(StrEnum):
    """Lifecycle of a worker."""

    IDLE = "idle"
    RUNNING = "running"
    FINISHED = "finished"
    CANCELLED = "cancelled"
    FAILED = "failed"


class BaseWorker(BaseObject, ABC):
    """A unit the ``WorkerRunner`` runs, such as a pipeline or a device loop.

    Each worker could become its own process later; until then workers share an in-process
    bus, which the runner assigns to ``bus`` before ``run()``.

    Args:
        name: Unique name in the runner's registry. Defaults to ``ClassName#id``.
    """

    def __init__(self, *, name: str | None = None) -> None:
        super().__init__(name=name)
        self._state = WorkerState.IDLE
        self.bus: Bus | None = None
        """The shared bus, set by the runner; ``None`` when running standalone."""

    @property
    def state(self) -> WorkerState:
        """Where the worker is in its lifecycle."""
        return self._state

    @abstractmethod
    async def run(self) -> None:
        """Run until the work is done or ``stop()`` is called. A worker runs once."""

    @abstractmethod
    async def stop(self, reason: str = "") -> None:
        """Ask the worker to stop soon; ``run()`` returns once it has.

        Args:
            reason: Why it is being stopped (logged and carried on the ``CancelFrame``).
        """
