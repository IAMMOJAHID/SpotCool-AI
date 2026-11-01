"""The room calendar over HTTP: Graph-style schedules and an ICS feed (the ``calendar`` extra).

``CalendarStore`` keeps the latest ``CalendarData`` per room from ``CalendarFrame``s, and
``calendar_router(store)`` serves it; WP4's HTTP server mounts the router.

- ``GET /calendar/rooms/{room}/schedule?start=&end=``: ``ScheduleInformation`` with the
  bookings that overlap the window. ``start`` and ``end`` are ISO date-times on the simulation
  calendar (``spotcool_world.scenarios.sim_datetime``); without them the window is the
  simulation day of the latest calendar update, which is the scenario's day.
- ``GET /calendar/rooms/{room}.ics``: the room's bookings as iCalendar (RFC 5545) text, in UTC
  (the label of the simulation's clock).

An unknown room answers 404.
"""

import re
from datetime import UTC, datetime, timedelta

from fastapi import APIRouter, HTTPException, Response

from spotcool_contracts.calendar import (
    ICS_PATH,
    ROOMS,
    SCHEDULE_PATH,
    ScheduleInformation,
    ScheduleItem,
)
from spotcool_contracts.events import CalendarData
from spotcool_world.bus import Subscription
from spotcool_world.clocks.sim_clock import SECONDS_PER_DAY
from spotcool_world.frames import CalendarFrame
from spotcool_world.scenarios.compiler import sim_datetime


class CalendarStore:
    """The latest bookings per room, and when they last changed, from ``CalendarFrame``s.

    Feed it frame by frame with ``update``, or with ``follow`` from a subscription to
    ``WORLD_FRAMES_TOPIC``, where a ``FramePublisher`` puts the pipeline's ``CalendarFrame``s.
    """

    def __init__(self) -> None:
        self._calendars: dict[str, CalendarData] = {}
        self._now_s: float | None = None

    @property
    def now_s(self) -> float | None:
        """The simulation time of the latest calendar update (the latest ``CalendarFrame``).

        Not the current simulation time: frames come only on a run's first tick and when
        bookings change. ``None`` before the first update.
        """
        return self._now_s

    def items(self, room: str) -> list[ScheduleItem]:
        """A room's latest bookings, in start order (empty before its first frame).

        Args:
            room: The room.
        """
        calendar = self._calendars.get(room)
        return list(calendar.items) if calendar else []

    def update(self, frame: CalendarFrame) -> None:
        """Take a room's bookings from a frame.

        Args:
            frame: The frame.
        """
        self._calendars[frame.data.room] = frame.data
        if frame.sim_time_s is not None:
            self._now_s = frame.sim_time_s

    async def follow(self, subscription: Subscription) -> None:
        """Take every ``CalendarFrame`` from a subscription; return when it is closed.

        Subscribe before the run starts (``bus.subscribe(WORLD_FRAMES_TOPIC)``) so the first
        tick's frames are not missed, and close the subscription to end this. Other messages
        are ignored.

        Args:
            subscription: The subscription.
        """
        async for message in subscription:
            if isinstance(message, CalendarFrame):
                self.update(message)


def calendar_router(store: CalendarStore) -> APIRouter:
    """The calendar's routes, serving ``store``.

    Args:
        store: Where the bookings come from.
    """
    router = APIRouter()

    @router.get(SCHEDULE_PATH)
    async def schedule(
        room: str, start: datetime | None = None, end: datetime | None = None
    ) -> ScheduleInformation:
        """The room's bookings that overlap ``[start, end)``, by default the scenario's day."""
        _check_room(room)
        items = store.items(room)
        window = _window(store.now_s, start, end)
        if window is not None:
            items = [item for item in items if _overlaps(item, *window)]
        return ScheduleInformation(scheduleId=room, scheduleItems=items)

    @router.get(ICS_PATH)
    async def ics(room: str) -> Response:
        """The room's bookings as an iCalendar feed."""
        _check_room(room)
        text = _ics(room, store.items(room), store.now_s or 0.0)
        return Response(content=text, media_type="text/calendar")

    return router


def _check_room(room: str) -> None:
    if room not in ROOMS:
        raise HTTPException(404, f"unknown room {room!r}; rooms: {', '.join(ROOMS)}")


def _window(
    now_s: float | None, start: datetime | None, end: datetime | None
) -> tuple[datetime, datetime] | None:
    """The requested window on the simulation calendar, or ``None`` to take every booking."""
    if start is None and end is None:
        if now_s is None:
            return None  # no update yet, so no bookings and no day either
        day = sim_datetime(now_s // SECONDS_PER_DAY * SECONDS_PER_DAY)
        return day, day + timedelta(days=1)
    if start is None or end is None:
        raise HTTPException(400, "give both start and end, or neither")
    start, end = _naive_utc(start), _naive_utc(end)
    if start >= end:
        raise HTTPException(400, "start must be before end")
    return start, end


def _naive_utc(moment: datetime) -> datetime:
    """A date-time on the simulation calendar, whose clock is labelled UTC."""
    return moment.astimezone(UTC).replace(tzinfo=None) if moment.tzinfo else moment


def _overlaps(item: ScheduleItem, start: datetime, end: datetime) -> bool:
    opens = datetime.fromisoformat(item.start.dateTime)
    closes = datetime.fromisoformat(item.end.dateTime)
    return opens < end and closes > start


def _ics(room: str, items: list[ScheduleItem], now_s: float) -> str:
    """The bookings as iCalendar (RFC 5545) text; DTSTAMP is the latest update's time."""
    stamp = _ics_time(sim_datetime(now_s).isoformat())
    lines = ["BEGIN:VCALENDAR", "VERSION:2.0", "PRODID:-//SpotCool//SpotCool World//EN"]
    lines.append(f"X-WR-CALNAME:{_ics_text(room)}")
    for index, item in enumerate(items):
        start, end = _ics_time(item.start.dateTime), _ics_time(item.end.dateTime)
        lines += [
            "BEGIN:VEVENT",
            f"UID:{_ics_text(room)}-{index}-{start}@spotcool-world",
            f"DTSTAMP:{stamp}",
            f"DTSTART:{start}",
            f"DTEND:{end}",
            f"SUMMARY:{_ics_text(item.subject or 'Busy')}",
            f"LOCATION:{_ics_text(item.location or room)}",
            "END:VEVENT",
        ]
    lines.append("END:VCALENDAR")
    return "".join(f"{_ics_fold(line)}\r\n" for line in lines)


def _ics_time(date_time: str) -> str:
    """``2026-07-19T14:00:00`` as ``20260719T140000Z``."""
    return datetime.fromisoformat(date_time).strftime("%Y%m%dT%H%M%SZ")


_NEWLINE = re.compile(r"\r\n|\r|\n")
_CONTROL = re.compile(r"[\x00-\x08\x0a-\x1f\x7f]")  # every control character but tab


def _ics_text(text: str) -> str:
    r"""Escape an iCalendar TEXT value: CR, LF and CRLF become ``\n``; other controls go."""
    text = text.replace("\\", "\\\\").replace(";", "\\;").replace(",", "\\,")
    return _CONTROL.sub("", _NEWLINE.sub(r"\\n", text))


def _ics_fold(line: str) -> str:
    """Fold a content line into lines of at most 75 octets, never inside a UTF-8 character."""
    folded, size = "", 0
    for char in line:
        octets = len(char.encode())
        if size + octets > 75:
            folded, size = f"{folded}\r\n ", 1  # a continuation line starts with one space
        folded += char
        size += octets
    return folded
