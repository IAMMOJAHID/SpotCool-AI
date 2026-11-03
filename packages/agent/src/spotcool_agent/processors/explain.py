"""Explanations: one template sentence per command or back-off, for the agent panel (pure).

The sentences are the stub agent's, word for word, so the dashboard and the video read the same
story from either.
"""

from collections.abc import Sequence

from spotcool_agent.domain.events import Value
from spotcool_contracts import ZONES, Zone
from spotcool_contracts.priorities import AGENT_PRIORITY, OPERATOR_PRIORITY


def commanded(zone: Zone, reason: str, writes: Sequence[tuple[str, Value]]) -> str:
    """The sentence for a zone's writes: ``South: the zone is empty -> ZN-SOU-... at priority 10``.

    Args:
        zone: The zone commanded.
        reason: Why.
        writes: Each point written and its value; ``None`` is a release.
    """
    done = ", ".join(f"{name} {'released' if value is None else value}" for name, value in writes)
    return f"{ZONES[zone].display_name}: {reason} -> {done} at priority {AGENT_PRIORITY}"


def holder(priority: int) -> str:
    """Who holds a more urgent slot: ``an operator at priority 8``, else ``priority N``."""
    return (
        f"an operator at priority {priority}"
        if priority == OPERATOR_PRIORITY
        else f"priority {priority}"
    )


def backed_off(
    zone: Zone, point: str, held: str | float, priority: int, wanted: Value, *, retry: bool
) -> str:
    """The sentence for a back-off from a point a more urgent slot holds.

    Args:
        zone: The zone.
        point: The point held.
        held: The value it is held at.
        priority: The holding slot.
        wanted: What the agent wanted to write; ``None`` is a release.
        retry: Whether the agent tries again at the next step.
    """
    what = "releasing it" if wanted is None else f"writing {wanted}"
    again = "; trying again each step" if retry else ""
    who = holder(priority)
    return f"{ZONES[zone].display_name}: {point} is held at {held} by {who} -> backed off from {what}{again}"
