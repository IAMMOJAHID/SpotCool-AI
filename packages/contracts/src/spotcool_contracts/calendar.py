"""Room-booking calendar served by the world.

Field names mirror Microsoft Graph's ``getSchedule`` response (camelCase on purpose), so an
agent written against this contract can switch to a real Microsoft 365 tenant by changing only
the base URL and adding authentication.
"""

from typing import Literal

from pydantic import BaseModel, ConfigDict

from spotcool_contracts.zones import Zone

SCHEDULE_PATH = "/calendar/rooms/{room}/schedule"
ICS_PATH = "/calendar/rooms/{room}.ics"

ROOMS: dict[str, Zone] = {"meeting-south": Zone.SOU}
"""Bookable rooms and the zone each one sits in."""


class DateTimeTimeZone(BaseModel):
    """A local date-time with its time zone, as in Microsoft Graph."""

    model_config = ConfigDict(extra="forbid")

    dateTime: str
    timeZone: str = "UTC"


class ScheduleItem(BaseModel):
    """One booking.

    Parameters:
        status: Free/busy status of the slot.
        subject: Meeting title, if not private.
        location: Room name.
        start: Start time.
        end: End time.
        isPrivate: Whether the subject is hidden.
    """

    model_config = ConfigDict(extra="forbid")

    status: Literal["free", "tentative", "busy", "oof", "workingElsewhere", "unknown"]
    subject: str | None = None
    location: str | None = None
    start: DateTimeTimeZone
    end: DateTimeTimeZone
    isPrivate: bool = False


class ScheduleInformation(BaseModel):
    """Response body of ``GET /calendar/rooms/{room}/schedule``.

    Parameters:
        scheduleId: Room identifier (a key of ``ROOMS``).
        scheduleItems: Bookings overlapping the requested window, in start order.
    """

    model_config = ConfigDict(extra="forbid")

    scheduleId: str
    scheduleItems: list[ScheduleItem]
