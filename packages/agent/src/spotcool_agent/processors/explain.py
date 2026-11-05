"""Explanations: one template sentence per command or back-off, for the agent panel (pure).

The sentences are the stub agent's, word for word, so the dashboard and the video read the same
story from either. The scene sentences (Muse Spark's notes and the comfort setpoint) are the
agent's own.
"""

from collections import Counter
from collections.abc import Sequence

from spotcool_agent.domain.events import Value
from spotcool_agent.domain.scene import PersonScene, Scene
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


CLOTHING_WORDS: dict[str, str] = {
    "shorts_short_sleeve": "shorts and short sleeves",
    "skirt_or_dress_light": "a light skirt or dress",
    "trousers_short_sleeve": "trousers and short sleeves",
    "trousers_long_sleeve": "typical office clothing",
    "suit_jacket": "suits or jackets",
    "sweater_or_layers": "sweaters or layers",
    "winter_outerwear": "coats",
}
"""Each ASHRAE 55 ensemble in plain words."""


def _most(values: Sequence[str]) -> str:
    return Counter(values).most_common(1)[0][0]


def people_words(people: Sequence[PersonScene]) -> str:
    """``1 person, typical office clothing``: the count and what most of them wear."""
    if not people:
        return "nobody"
    count = "1 person" if len(people) == 1 else f"{len(people)} people"
    return f"{count}, {CLOTHING_WORDS[_most([p.clothing_ensemble for p in people])]}"


def comfort(people: Sequence[PersonScene], clo: float, met: float, setpoint_c: float) -> str:
    """The sentence for the meeting room's comfort setpoint, from Muse Spark's scene.

    ``Muse Spark: 2 people, seated, typical office clothing (0.71 clo, 1.1 met) -> comfort
    setpoint 24.5 degC (PMV 0)``.
    """
    count = "1 person" if len(people) == 1 else f"{len(people)} people"
    posture = _most([p.posture for p in people])
    worn = CLOTHING_WORDS[_most([p.clothing_ensemble for p in people])]
    return (
        f"Muse Spark: {count}, {posture}, {worn} ({clo:.2f} clo, {met:.1f} met) -> comfort "
        f"setpoint {setpoint_c:.1f} degC (PMV 0)"
    )


def scene_note(zone: Zone, scene: Scene) -> str:
    """A zone's scene note: ``Muse Spark, West: 1 person, typical office clothing; a door is open``."""
    parts = [people_words(scene.people)]
    for n, thing in ((scene.doors_open, "door"), (scene.windows_open, "window")):
        if n:
            parts.append(f"a {thing} is open" if n == 1 else f"{n} {thing}s are open")
    cues = sorted({p.discomfort_cue.replace("_", " ") for p in scene.people} - {"none"})
    if cues:
        parts.append(f"visible discomfort: {', '.join(cues)}")
    return f"Muse Spark, {ZONES[zone].display_name}: {'; '.join(parts)}"


def counts_disagree(sam: int, spark: int) -> bool:
    """The MVP's cross-check: the two counts differ by more than 2, or by 30 % of the larger."""
    return abs(spark - sam) > max(2.0, 0.3 * max(sam, spark))


def disagreement(zone: Zone, sam: int, spark: int) -> str:
    """The sentence for counts that disagree: the policy keeps SAM's."""
    where = ZONES[zone].display_name
    return f"{where}: SAM 3.1 saw {sam}, Muse Spark {spark}: the policy keeps SAM's"
