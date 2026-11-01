import asyncio

import httpx
import pytest
from fastapi import FastAPI

from spotcool_contracts.calendar import DateTimeTimeZone, ScheduleInformation, ScheduleItem
from spotcool_contracts.events import CalendarData, ComplaintCueData
from spotcool_contracts.zones import Zone
from spotcool_world.bus import InProcessBus
from spotcool_world.frames import CalendarFrame, ComplaintCueFrame
from spotcool_world.processors import WORLD_FRAMES_TOPIC
from spotcool_world.scenarios import compile_scenario, load_scenario
from spotcool_world.transports.http.calendar import CalendarStore, calendar_router

TUESDAY = compile_scenario(load_scenario("hybrid_tuesday").scenario, step_s=900.0)
SCHEDULE = "/calendar/rooms/meeting-south/schedule"
ICS = "/calendar/rooms/meeting-south.ics"
REVIEW = {
    "status": "busy",
    "subject": "Quarterly review",
    "location": "meeting-south",
    "start": {"dateTime": "2026-07-19T14:00:00", "timeZone": "UTC"},
    "end": {"dateTime": "2026-07-19T15:00:00", "timeZone": "UTC"},
    "isPrivate": False,
}


def at(clock: str, day: int = 199) -> float:
    hours, minutes = clock.split(":")
    return day * 86_400 + int(hours) * 3600 + int(minutes) * 60


def store_at(t_s: float) -> CalendarStore:
    """The store as the ScenarioProcessor's CalendarFrames leave it by ``t_s``."""
    store = CalendarStore()
    for change_s in (TUESDAY.start_s, *TUESDAY.booking_change_times):
        if change_s <= t_s:
            for room, items in TUESDAY.bookings_at(change_s).items():
                data = CalendarData(room=room, items=items)
                store.update(CalendarFrame(data=data, sim_time_s=change_s))
    return store


async def get(store: CalendarStore, url: str) -> httpx.Response:
    app = FastAPI()
    app.include_router(calendar_router(store))
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://world") as http:
        return await http.get(url)


async def schedule_items(store: CalendarStore, query: str = "") -> list[object]:
    response = await get(store, f"{SCHEDULE}?{query}" if query else SCHEDULE)
    assert response.status_code == 200
    return response.json()["scheduleItems"]


@pytest.mark.asyncio
async def test_the_schedule_is_graph_shaped() -> None:
    response = await get(store_at(at("14:00")), SCHEDULE)
    assert response.status_code == 200
    assert response.json() == {"scheduleId": "meeting-south", "scheduleItems": [REVIEW]}
    ScheduleInformation.model_validate(response.json())


@pytest.mark.asyncio
async def test_the_booking_appears_at_13_45() -> None:
    assert await schedule_items(store_at(at("13:30"))) == []
    assert await schedule_items(store_at(at("13:45"))) == [REVIEW]


@pytest.mark.parametrize(
    ("query", "count"),
    [
        ("start=2026-07-19T14:30:00&end=2026-07-19T16:00:00", 1),
        ("start=2026-07-19T13:00:00&end=2026-07-19T14:00:00", 0),  # ends as the meeting starts
        ("start=2026-07-19T15:00:00&end=2026-07-19T16:00:00", 0),
        ("start=2026-07-19T14:59:00Z&end=2026-07-19T15:30:00Z", 1),
        ("start=2026-07-19T16:30:00%2B02:00&end=2026-07-19T17:30:00%2B02:00", 1),  # 14:30 UTC
    ],
)
@pytest.mark.asyncio
async def test_the_window_picks_overlapping_bookings(query: str, count: int) -> None:
    assert len(await schedule_items(store_at(at("13:45")), query)) == count


@pytest.mark.asyncio
async def test_without_a_window_it_is_the_current_simulation_day() -> None:
    store = store_at(at("13:45"))
    later = CalendarData(room="meeting-south", items=store.items("meeting-south"))
    store.update(CalendarFrame(data=later, sim_time_s=at("08:00", day=200)))
    assert await schedule_items(store) == []  # the meeting was yesterday


@pytest.mark.asyncio
async def test_before_any_frame_the_schedule_is_empty() -> None:
    assert CalendarStore().now_s is None
    response = await get(CalendarStore(), SCHEDULE)
    assert response.json() == {"scheduleId": "meeting-south", "scheduleItems": []}


@pytest.mark.parametrize(
    ("query", "status"),
    [
        ("start=2026-07-19T14:00:00", 400),
        ("end=2026-07-19T14:00:00", 400),
        ("start=2026-07-19T15:00:00&end=2026-07-19T14:00:00", 400),
        ("start=2026-07-19T15:00:00&end=2026-07-19T15:00:00", 400),
        ("start=tomorrow&end=2026-07-19T14:00:00", 422),
    ],
)
@pytest.mark.asyncio
async def test_a_bad_window_is_refused(query: str, status: int) -> None:
    response = await get(store_at(at("13:45")), f"{SCHEDULE}?{query}")
    assert response.status_code == status


@pytest.mark.parametrize(
    "url", ["/calendar/rooms/boardroom/schedule", "/calendar/rooms/boardroom.ics"]
)
@pytest.mark.asyncio
async def test_an_unknown_room_is_404(url: str) -> None:
    response = await get(store_at(at("13:45")), url)
    assert response.status_code == 404
    assert "unknown room 'boardroom'" in response.json()["detail"]


@pytest.mark.asyncio
async def test_the_ics_feed_is_well_formed() -> None:
    response = await get(store_at(at("13:45")), ICS)
    assert response.status_code == 200
    assert response.headers["content-type"].startswith("text/calendar")
    text = response.text
    assert text.endswith("\r\n") and "\n" not in text.replace("\r\n", "")
    lines = text.split("\r\n")[:-1]
    stack: list[str] = []
    for line in lines:  # every BEGIN has its END, properly nested
        if line.startswith("BEGIN:"):
            stack.append(line.removeprefix("BEGIN:"))
        elif line.startswith("END:"):
            assert stack.pop() == line.removeprefix("END:")
    assert stack == []
    assert lines[:3] == ["BEGIN:VCALENDAR", "VERSION:2.0", "PRODID:-//SpotCool//SpotCool World//EN"]
    event = lines[lines.index("BEGIN:VEVENT") : lines.index("END:VEVENT")]
    assert "DTSTART:20260719T140000Z" in event
    assert "DTEND:20260719T150000Z" in event
    assert "DTSTAMP:20260719T134500Z" in event
    assert "SUMMARY:Quarterly review" in event
    assert any(line.startswith("UID:meeting-south-") for line in event)


@pytest.mark.asyncio
async def test_the_ics_feed_of_an_empty_calendar_has_no_events() -> None:
    lines = (await get(store_at(at("13:30")), ICS)).text.split("\r\n")
    assert "BEGIN:VCALENDAR" in lines and "BEGIN:VEVENT" not in lines


@pytest.mark.asyncio
async def test_ics_text_is_escaped() -> None:
    item = ScheduleItem(
        status="busy",
        subject="Review; Q3, all\\hands\nday two",
        start=DateTimeTimeZone(dateTime="2026-07-19T09:00:00"),
        end=DateTimeTimeZone(dateTime="2026-07-19T10:00:00"),
    )
    store = CalendarStore()
    store.update(CalendarFrame(data=CalendarData(room="meeting-south", items=[item])))
    lines = (await get(store, ICS)).text.split("\r\n")
    assert "SUMMARY:Review\\; Q3\\, all\\\\hands\\nday two" in lines
    assert "LOCATION:meeting-south" in lines  # no location given: the room
    assert "DTSTAMP:20260101T000000Z" in lines  # no simulation time yet


@pytest.mark.asyncio
async def test_the_store_follows_calendar_frames_on_the_world_topic() -> None:
    bus = InProcessBus()
    subscription = bus.subscribe(WORLD_FRAMES_TOPIC)
    store = CalendarStore()
    items = TUESDAY.bookings_at(at("13:45"))["meeting-south"]
    cue = ComplaintCueData(zone=Zone.SOU, text="Cold.", channel="chat", delivered=False)

    async def publish() -> None:
        await bus.publish(WORLD_FRAMES_TOPIC, ComplaintCueFrame(data=cue))  # not for the store
        data = CalendarData(room="meeting-south", items=items)
        await bus.publish(WORLD_FRAMES_TOPIC, CalendarFrame(data=data, sim_time_s=at("13:45")))
        await asyncio.sleep(0.01)
        subscription.close()

    await asyncio.wait_for(asyncio.gather(store.follow(subscription), publish()), timeout=2.0)
    assert store.items("meeting-south") == items
    assert store.now_s == at("13:45")
    assert store.items("elsewhere") == []
