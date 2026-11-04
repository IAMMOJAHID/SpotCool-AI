"""What a scene reader sees in one snapshot: people dressed and busy, and the room (pure).

Person A's enums (``app/spotcool/schemas.py`` on branch ``third_level_improvement``), cut to
what the agent uses: per person the ASHRAE 55 clothing ensemble, the activity, the posture and
a visible discomfort cue; for the room the open doors and windows, the sunlight and the space
type. The models forbid any other key, so an answer with a temperature in it is invalid.
"""

import json
import re
from collections import Counter
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

ClothingEnsemble = Literal[
    "shorts_short_sleeve",
    "skirt_or_dress_light",
    "trousers_short_sleeve",
    "trousers_long_sleeve",
    "suit_jacket",
    "sweater_or_layers",
    "winter_outerwear",
]
"""Person A's ASHRAE 55 ensembles (``app/spotcool/schemas.py``)."""
Activity = Literal[
    "seated_quiet",
    "seated_typing",
    "standing_relaxed",
    "standing_working",
    "walking",
    "carrying_or_lifting",
    "exercising",
]
"""Person A's activities, each with its met rate (``domain/comfort.py``)."""
Posture = Literal["seated", "standing", "walking", "lying"]
DiscomfortCue = Literal[
    "none",
    "fanning_self",
    "wiping_sweat",
    "rolled_up_sleeves",
    "hugging_arms_or_shivering",
    "adjusting_clothing",
]
Sunlight = Literal["none", "partial", "strong"]
SpaceType = Literal[
    "office", "meeting_room", "retail", "lobby", "classroom", "restaurant", "warehouse", "other"
]


class _Strict(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)  # a temperature is never accepted


class PersonScene(_Strict):
    """One person, as the scene reader saw them."""

    clothing_ensemble: ClothingEnsemble
    activity: Activity
    posture: Posture
    discomfort_cue: DiscomfortCue


class Scene(_Strict):
    """What the scene reader saw in one snapshot.

    Parameters:
        people: Everyone visible, one entry each.
        doors_open: Doors standing open.
        windows_open: Windows standing open.
        sunlight: How much direct sunlight falls into the room.
        space_type: What kind of space it is.
    """

    people: list[PersonScene]
    doors_open: int = Field(ge=0)
    windows_open: int = Field(ge=0)
    sunlight: Sunlight
    space_type: SpaceType


def parse_scene(text: str) -> Scene:
    """The scene in a model's answer: its outermost JSON object, validated.

    Raises:
        ValueError: If the answer holds no such object or it is not a valid ``Scene`` (pydantic's
            ``ValidationError`` is a ``ValueError``).
    """
    match = re.search(r"\{.*\}", text, re.DOTALL)  # an answer may wrap it in a code fence
    if match is None:
        raise ValueError("no JSON object in the answer")
    return Scene.model_validate(json.loads(match.group(0)))


def tally(scene: Scene) -> str:
    """The scene as counts and labels only, for the trace: never anything identifying."""
    worn = Counter(p.clothing_ensemble for p in scene.people)
    clothing = ", ".join(f"{name} {n}" for name, n in worn.most_common())
    return (
        f"{len(scene.people)} people ({clothing or '-'}), doors open {scene.doors_open}, "
        f"windows open {scene.windows_open}, sunlight {scene.sunlight}, {scene.space_type}"
    )
