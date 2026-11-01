"""PlantService: the building physics behind one run (SpotCool or baseline)."""

from abc import ABC, abstractmethod
from collections.abc import Mapping

from pydantic import BaseModel, ConfigDict, Field


class PlantStartParams(BaseModel):
    """How to start a plant.

    Parameters:
        start_time_s: Simulation time of the first step (seconds since 1 January 00:00).
        step_s: Simulation seconds per step.
        warmup_s: Simulated seconds to run before ``start_time_s`` so the building is not cold.
        time_period: BOPTEST scenario time period (e.g. ``peak_cool_day``), if any.
    """

    model_config = ConfigDict(extra="forbid")

    start_time_s: float = Field(ge=0)
    step_s: float = Field(gt=0)
    warmup_s: float = Field(default=0.0, ge=0)
    time_period: str | None = None


class PlantService(ABC):
    """Base of every plant provider (hosted or local BOPTEST, RC model, replay).

    Values in and out are in BOPTEST names and units (K, W, m3/s, fractions); converting to
    BMS units is the transports' job (``adapters.units``). Providers report trouble through
    the processor that owns them (``push_error``), and must always release external
    resources in ``stop()``: BOPTEST is a shared free service.
    """

    @property
    @abstractmethod
    def step_s(self) -> float:
        """Simulation seconds each ``step()`` advances."""

    @abstractmethod
    async def start(self, params: PlantStartParams) -> dict[str, float]:
        """Start (or restart) the plant.

        Args:
            params: Start time, step and warm-up.

        Returns:
            The initial measurements, BOPTEST output name to value.
        """

    @abstractmethod
    async def step(self, overrides: Mapping[str, float | None]) -> dict[str, float]:
        """Advance one step with the given inputs.

        Args:
            overrides: BOPTEST input name to value. ``None`` releases the input
                (``_activate = 0``), handing control back to the plant's own controller.

        Returns:
            Measurements after the step, BOPTEST output name to value.
        """

    @abstractmethod
    async def kpis(self) -> dict[str, float | None]:
        """BOPTEST-style KPIs so far (``ener_tot``, ``tdis_tot``, ...); ``None`` if unknown."""

    @abstractmethod
    async def stop(self) -> None:
        """Stop the plant and release what it holds. Safe to call twice."""
