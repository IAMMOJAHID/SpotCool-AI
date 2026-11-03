import asyncio
import math
import time

import pytest

from spotcool_world.clocks import SimClock, WallPacer
from spotcool_world.frames import EndFrame, TickFrame
from spotcool_world.processors import TickSource
from spotcool_world.testing import run_test

DAY = 86_400


def test_day_199_at_0830() -> None:
    t = 198 * DAY + 8.5 * 3600
    assert SimClock.day_of_year(t) == 199
    assert SimClock.clock_text(t) == "08:30"


@pytest.mark.parametrize(
    ("t", "day", "text"),
    [
        (0.0, 1, "00:00"),
        (364 * DAY + 23 * 3600 + 59 * 60, 365, "23:59"),
        (365 * DAY, 1, "00:00"),  # BOPTEST years are non-leap; day numbers wrap
        (198 * DAY + 8.5 * 3600 - 0.4, 199, "08:30"),  # rounded to the second first
    ],
)
def test_day_and_clock_text(t: float, day: int, text: str) -> None:
    assert SimClock.day_of_year(t) == day
    assert SimClock.clock_text(t) == text


def test_advance_counts_steps_without_drift() -> None:
    clock = SimClock(start_time_s=198 * DAY + 7 * 3600, step_s=0.1)
    for _ in range(36_000):
        clock.advance()
    assert clock.index == 36_000
    assert math.isclose(clock.now_s, 198 * DAY + 8 * 3600, abs_tol=1e-6)
    assert clock.clock_text(clock.now_s) == "08:00"


@pytest.mark.parametrize(("start", "step"), [(0.0, 0.0), (0.0, -900.0), (-1.0, 900.0)])
def test_clock_rejects_bad_arguments(start: float, step: float) -> None:
    with pytest.raises(ValueError):
        SimClock(start, step)


def test_pacer_rejects_non_positive_speed() -> None:
    with pytest.raises(ValueError, match="speed"):
        WallPacer(0)


@pytest.mark.asyncio
async def test_pacer_never_sleeps_without_speed(monkeypatch: pytest.MonkeyPatch) -> None:
    async def forbidden(delay: float) -> None:
        raise AssertionError(f"slept {delay} s")

    monkeypatch.setattr("spotcool_world.clocks.sim_clock.asyncio.sleep", forbidden)
    pacer = WallPacer(None)
    for _ in range(1000):
        await pacer.pace(900.0)


@pytest.mark.asyncio
async def test_pacer_fills_each_slot() -> None:
    pacer = WallPacer(speed=900.0 / 0.05)  # one 900 s step every 0.05 s
    began = time.perf_counter()
    for _ in range(3):
        await pacer.pace(900.0)
    # Windows asyncio timers may fire one 15.6 ms clock tick early.
    assert 0.125 <= time.perf_counter() - began < 0.5


@pytest.mark.asyncio
async def test_pacer_does_not_burst_after_an_overrun() -> None:
    pacer = WallPacer(speed=900.0 / 0.06)
    await pacer.pace(900.0)
    await asyncio.sleep(0.25)  # a slow step: four slots late
    began = time.perf_counter()
    await pacer.pace(900.0)  # late: returns at once and restarts the schedule
    await pacer.pace(900.0)  # so this one waits a full slot instead of catching up
    assert time.perf_counter() - began >= 0.04


@pytest.mark.asyncio
async def test_tick_source_ticks_until_end_time() -> None:
    clock = SimClock(start_time_s=7 * 3600, step_s=900.0)
    source = TickSource(clock, WallPacer(None), end_time_s=8 * 3600)
    down, _ = await run_test(source, frames_to_send=[], send_end=False)
    ticks = [f for f in down if isinstance(f, TickFrame)]
    assert [t.index for t in ticks] == [0, 1, 2, 3]
    assert [clock.clock_text(t.sim_time_s or 0) for t in ticks] == [
        "07:00",
        "07:15",
        "07:30",
        "07:45",
    ]
    assert isinstance(down[-1], EndFrame)
    assert down[-1].sim_time_s == 8 * 3600
    assert len(down) == 5


@pytest.mark.asyncio
async def test_tick_source_stops_on_end_frame_from_upstream() -> None:
    source = TickSource(SimClock(0.0, 900.0), WallPacer(900.0 / 0.05), end_time_s=math.inf)
    down, _ = await asyncio.wait_for(run_test(source, frames_to_send=[]), timeout=2.0)
    assert all(isinstance(f, TickFrame) for f in down)
    assert len(down) <= 1
