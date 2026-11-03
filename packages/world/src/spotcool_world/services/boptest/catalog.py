"""The BOPTEST input and output catalog of the office test case, from the contract snapshot.

Every plant validates its overrides against this catalog before stepping: BOPTEST rejects an
unknown input name with HTTP 400, and the RC and replay plants reject the same names so a typo
fails the same way on every plant.
"""

import json
import math
from collections.abc import Mapping
from functools import cache
from importlib import resources
from typing import Literal

from pydantic import BaseModel, ConfigDict

from spotcool_contracts import ZONES, Zone, load_point_map

SECONDS_PER_DAY = 86_400.0


class BoptestPoint(BaseModel):
    """One input or output of the test case.

    Parameters:
        name: BOPTEST signal name (``..._u`` for inputs, ``..._y`` for outputs).
        causality: ``input`` or ``output``.
        description: What the signal is.
        unit: Unit (``K``, ``W``, ``m3/s``, ``1``, ``ppm``, ...).
        min: Lowest value BOPTEST accepts (inputs only); BOPTEST clips below it.
        max: Highest value BOPTEST accepts (inputs only); BOPTEST clips above it.
    """

    model_config = ConfigDict(extra="ignore", frozen=True)

    name: str
    causality: Literal["input", "output"]
    description: str
    unit: str | None = None
    min: float | None = None
    max: float | None = None


class BoptestCatalog(BaseModel):
    """Inputs, outputs and scenario days of one BOPTEST test case.

    Parameters:
        testcase: Test case name.
        area_m2: Floor area BOPTEST normalises its energy KPIs by.
        scenario_days: Day each scenario time period is centred on (BOPTEST's ``days.json``).
        electric_power: The power meters (W) BOPTEST's ElectricPower KPI sums.
        inputs: Overwritable inputs by name (``..._u``; each has an implied ``..._activate``).
        outputs: Outputs by name.
    """

    model_config = ConfigDict(frozen=True)

    testcase: str
    area_m2: float
    scenario_days: dict[str, int]
    electric_power: tuple[str, ...]
    inputs: dict[str, BoptestPoint]
    outputs: dict[str, BoptestPoint]

    def check_overrides(self, overrides: Mapping[str, float | None]) -> None:
        """Reject overrides BOPTEST would refuse, before anything is sent.

        Args:
            overrides: Input name to value; ``None`` releases the input.

        Raises:
            ValueError: If a name is not an input of the test case, or a value is not finite.
        """
        unknown = sorted(set(overrides) - self.inputs.keys())
        if unknown:
            raise ValueError(f"unknown BOPTEST inputs: {', '.join(unknown)}")
        bad = sorted(k for k, v in overrides.items() if v is not None and not math.isfinite(v))
        if bad:
            raise ValueError(f"BOPTEST inputs need finite values: {', '.join(bad)}")

    def period_start_s(self, time_period: str) -> float:
        """Start of a scenario time period: BOPTEST starts it seven days before its day.

        Args:
            time_period: Scenario time period (e.g. ``peak_cool_day``).

        Raises:
            ValueError: If the test case has no such time period.
        """
        day = self.scenario_days.get(time_period)
        if day is None:
            raise ValueError(
                f"unknown time_period {time_period!r}; one of {sorted(self.scenario_days)}"
            )
        return (day - 7) * SECONDS_PER_DAY


@cache
def load_catalog() -> BoptestCatalog:
    """Load the office test case's catalog from the contract snapshot (cached, shared)."""
    text = (
        resources.files("spotcool_contracts")
        .joinpath("data", "boptest_multizone_office_simple_air.json")
        .read_text(encoding="utf-8")
    )
    data = json.loads(text)
    points = [BoptestPoint.model_validate(p) for p in data["points"]]
    return BoptestCatalog(
        testcase=data["testcase"],
        area_m2=data["config"]["area"],
        scenario_days=data["scenario_days"],
        electric_power=tuple(data["kpi_mapping"]["ElectricPower"]),
        inputs={p.name: p for p in points if p.causality == "input"},
        outputs={p.name: p for p in points if p.causality == "output"},
    )


def bms_signal(point: str, *, write: bool = False) -> str:
    """The BOPTEST signal an office BMS point reads, or with ``write`` the one it writes.

    Args:
        point: BMS object name (e.g. ``ZN-COR-T``).
        write: Return the input the point writes instead of the output it reads.

    Raises:
        KeyError: If the point has no such BOPTEST binding.
    """
    binding = load_point_map("office_bms").by_name(point).boptest
    name = None if binding is None else binding.write if write else binding.read
    if name is None:
        raise KeyError(
            f"office_bms point {point} {'writes' if write else 'reads'} no BOPTEST signal"
        )
    return name


def zone_damper(zone: Zone) -> str:
    """The input that overrides a zone's VAV damper position (0 closed, 1 fully open).

    The BMS has no BACnet object for it: WP2's occupied-standby closes it through
    ``SetpointCommandFrame.overrides``.

    Args:
        zone: The zone.
    """
    return f"hvac_oveZonAct{ZONES[zone].boptest_suffix}_yDam_u"


MANAGED_INPUTS: tuple[str, ...] = (
    *(bms_signal(f"ZN-{zone.value.upper()}-CLG-SP", write=True) for zone in Zone),
    *(zone_damper(zone) for zone in Zone),
)
"""The ten inputs the world drives: each zone's cooling setpoint, then each zone's damper."""
