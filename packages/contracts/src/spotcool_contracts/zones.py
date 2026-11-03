"""Thermal zones of the simulated office (BOPTEST ``multizone_office_simple_air``)."""

from enum import StrEnum

from pydantic import BaseModel, ConfigDict


class Zone(StrEnum):
    """The five thermal zones, using BOPTEST's lower-case zone names."""

    COR = "cor"
    NOR = "nor"
    SOU = "sou"
    EAS = "eas"
    WES = "wes"


class ZoneInfo(BaseModel):
    """Human-facing description of a zone and the camera that watches it.

    Parameters:
        zone: Zone identifier.
        display_name: Short name shown on the dashboard.
        role: What the space is used for in the demo story.
        boptest_suffix: Zone name as it appears inside BOPTEST point names (e.g. ``Cor``).
        camera_id: Identifier of the CCTV camera covering the zone.
    """

    model_config = ConfigDict(extra="forbid", frozen=True)

    zone: Zone
    display_name: str
    role: str
    boptest_suffix: str
    camera_id: str


ZONES: dict[Zone, ZoneInfo] = {
    Zone.COR: ZoneInfo(
        zone=Zone.COR,
        display_name="Core",
        role="Open-plan workspace",
        boptest_suffix="Cor",
        camera_id="cam-cor",
    ),
    Zone.NOR: ZoneInfo(
        zone=Zone.NOR,
        display_name="North",
        role="Team area",
        boptest_suffix="Nor",
        camera_id="cam-nor",
    ),
    Zone.SOU: ZoneInfo(
        zone=Zone.SOU,
        display_name="South",
        role="Meeting room",
        boptest_suffix="Sou",
        camera_id="cam-sou",
    ),
    Zone.EAS: ZoneInfo(
        zone=Zone.EAS,
        display_name="East",
        role="Team area",
        boptest_suffix="Eas",
        camera_id="cam-eas",
    ),
    Zone.WES: ZoneInfo(
        zone=Zone.WES,
        display_name="West",
        role="Reception and entrance",
        boptest_suffix="Wes",
        camera_id="cam-wes",
    ),
}
