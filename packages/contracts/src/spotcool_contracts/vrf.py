"""Brand profiles for the simulated VRF indoor-unit gateway."""

from functools import cache
from importlib import resources
from typing import Literal

import yaml
from pydantic import BaseModel, ConfigDict, Field, model_validator

VrfVendor = Literal["mitsubishi", "daikin", "lg"]


class VaneAxis(BaseModel):
    """Louver control available on one axis.

    Parameters:
        positions: Number of fixed positions (0 when only swing is available).
        swing: Whether a swing (oscillate) state exists.
        angles_deg: Louver angle for each position, position 1 first.
    """

    model_config = ConfigDict(extra="forbid", frozen=True)

    positions: int = Field(ge=0, le=5)
    swing: bool
    angles_deg: list[float]

    @model_validator(mode="after")
    def _angles_match_positions(self) -> "VaneAxis":
        if len(self.angles_deg) != self.positions:
            raise ValueError("angles_deg must list one angle per position")
        return self


class VrfProfile(BaseModel):
    """Which controls a brand's BACnet gateway exposes.

    Parameters:
        vendor: Brand key.
        label: Human-readable product description.
        vane_vertical: Vertical louver capability, or ``None`` if not exposed.
        vane_horizontal: Horizontal louver capability, or ``None`` if not exposed.
        fan_speeds: Fan speed state texts, in BACnet state order.
        source_url: Vendor document the capability is taken from.
    """

    model_config = ConfigDict(extra="forbid", frozen=True)

    vendor: VrfVendor
    label: str
    vane_vertical: VaneAxis | None
    vane_horizontal: VaneAxis | None
    fan_speeds: list[str]
    source_url: str


class _ProfileFile(BaseModel):
    model_config = ConfigDict(extra="forbid")

    profiles: list[VrfProfile]


@cache
def load_vrf_profiles() -> dict[str, VrfProfile]:
    """Load the bundled VRF brand profiles, keyed by vendor."""
    text = (
        resources.files("spotcool_contracts")
        .joinpath("points", "vrf_profiles.yaml")
        .read_text(encoding="utf-8")
    )
    parsed = _ProfileFile.model_validate(yaml.safe_load(text))
    return {p.vendor: p for p in parsed.profiles}
