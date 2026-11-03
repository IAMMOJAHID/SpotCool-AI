"""Occupancy: a step's people counts as occupied, empty or unknown per zone (pure)."""

from collections.abc import Mapping
from enum import StrEnum

from spotcool_contracts import Zone


class Occupancy(StrEnum):
    """What the camera says about a zone at a step's close."""

    OCCUPIED = "occupied"
    EMPTY = "empty"
    UNKNOWN = "unknown"
    """No count: the snapshot or the detection failed, or timed out."""


def occupancy(counts: Mapping[Zone, int | None]) -> dict[Zone, Occupancy]:
    """Each zone's occupancy from its people count; a zone missing from ``counts`` is unknown.

    Args:
        counts: People counted per zone; ``None`` when no count came back.
    """
    found: dict[Zone, Occupancy] = {}
    for zone in Zone:
        count = counts.get(zone)
        if count is None:
            found[zone] = Occupancy.UNKNOWN
        else:
            found[zone] = Occupancy.OCCUPIED if count > 0 else Occupancy.EMPTY
    return found
