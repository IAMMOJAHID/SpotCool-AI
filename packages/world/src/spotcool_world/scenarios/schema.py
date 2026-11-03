"""Scenario DSL: the Pydantic models a scenario YAML file is validated against.

A scenario is the film script of one simulated day: what each zone's camera shows and how many
people are in it, the entrance door, room bookings, occupant complaints and clothing changes.
Times are ``HH:MM`` on day ``day``, BOPTEST's 0-based day index. These models check the shape of
a file; ``linter.check`` checks the rules that need the whole scenario (time order, the step
grid, overlaps).
"""

from functools import cache
from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, Field, ValidationError, field_validator

from spotcool_contracts import Zone, load_point_map
from spotcool_contracts.calendar import ROOMS
from spotcool_contracts.occupants import ComplaintText
from spotcool_world.services.settings import ClockText

Scene = Literal["empty", "few", "many", "meeting", "complaint", "door_open"]
"""What a zone's camera shows; its clip is ``<zone>_<scene>.mp4`` (WP8's naming)."""

Clothing = Literal["light", "medium", "heavy"]
"""What the occupants of a zone wear (``ZoneScene.clothing``)."""

AudioName = Annotated[str, Field(pattern=r"^[A-Za-z0-9][A-Za-z0-9_.-]*\.wav$")]
"""A WAV file name in ``footage_dir/audio/``, without folders."""


class _Model(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)


class ZoneEntry(_Model):
    """What a zone shows from ``at`` until its next entry.

    Parameters:
        at: When the entry takes effect.
        scene: The scene the zone's camera shows.
        people: Occupants visible in that zone's clip, the ground truth camera counting is
            scored against.
    """

    at: ClockText
    scene: Scene
    people: int = Field(ge=0)


class DoorOpenEvent(_Model):
    """A zone's door stands open from ``from`` until ``to``.

    Parameters:
        type: ``door_open``.
        zone: The zone; it needs a door contact in the BMS point map.
        from_: When the door opens (``from`` in the file).
        to: When it closes.
    """

    type: Literal["door_open"]
    zone: Zone
    from_: ClockText = Field(alias="from")
    to: ClockText


class BookingEvent(_Model):
    """A room booking, shown as busy in the room's calendar.

    Parameters:
        type: ``booking``.
        room: A bookable room (``spotcool_contracts.calendar.ROOMS``).
        from_: When the meeting starts (``from`` in the file).
        to: When it ends.
        subject: Meeting title.
        booked_at: When the booking appears in the calendar; ``None`` means the scenario's
            start.
    """

    type: Literal["booking"]
    room: str
    from_: ClockText = Field(alias="from")
    to: ClockText
    subject: str | None = None
    booked_at: ClockText | None = None

    @field_validator("room")
    @classmethod
    def _bookable(cls, room: str) -> str:
        if room not in ROOMS:
            raise ValueError(f"unknown room {room!r}; bookable rooms: {', '.join(ROOMS)}")
        return room


class ComplaintEvent(_Model):
    """An occupant complains to the agent, by chat or by voice.

    Parameters:
        type: ``complaint``.
        zone: Where the occupant is.
        at: When the complaint is sent.
        channel: ``chat`` sends the text; ``voice`` sends the recording.
        text: What the occupant says (the script line).
        audio: For a voice cue, the recording's file name in ``footage_dir/audio/``.
    """

    type: Literal["complaint"]
    zone: Zone
    at: ClockText
    channel: Literal["chat", "voice"]
    text: str
    audio: AudioName | None = None

    @field_validator("text")
    @classmethod
    def _agent_accepts(cls, text: str) -> str:
        try:
            ComplaintText(text=text)  # the agent's own limits on a complaint
        except ValidationError as exc:
            raise ValueError(exc.errors()[0]["msg"]) from None
        return text


class ClothingEvent(_Model):
    """The occupants of a zone change clothing.

    Parameters:
        type: ``clothing``.
        zone: The zone.
        at: When the change happens.
        clothing: What they wear from then on.
    """

    type: Literal["clothing"]
    zone: Zone
    at: ClockText
    clothing: Clothing


Event = Annotated[
    DoorOpenEvent | BookingEvent | ComplaintEvent | ClothingEvent, Field(discriminator="type")
]
"""Any scenario event, told apart by its ``type``."""


class Scenario(_Model):
    """One simulated day.

    Parameters:
        name: Scenario name (the library file's stem).
        description: What the day is about.
        day: BOPTEST's 0-based day index (``peak_cool_day``'s day is 199).
        start: When the run starts.
        end: When the run ends.
        zones: Every zone's entries, in time order.
        events: Doors, bookings, complaints and clothing changes, in any order.
    """

    name: str = Field(min_length=1)
    description: str = ""
    day: int = Field(ge=0, le=364)
    start: ClockText
    end: ClockText
    zones: dict[Zone, list[ZoneEntry]]
    events: list[Event] = Field(default_factory=list)


def clock_s(text: str) -> int:
    """Seconds after midnight of an ``HH:MM`` time.

    Args:
        text: The time, e.g. ``"14:15"``.
    """
    hours, minutes = text.split(":")
    return int(hours) * 3600 + int(minutes) * 60


@cache
def door_zones() -> frozenset[Zone]:
    """Zones with a door contact in the office BMS point map (West: ``WES-ENTRANCE-DOOR``)."""
    points = load_point_map("office_bms").points
    doors = (p for p in points if {"door", "contact"} <= set(p.haystack))
    return frozenset(p.zone for p in doors if p.zone is not None)
