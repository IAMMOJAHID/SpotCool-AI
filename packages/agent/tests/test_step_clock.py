import asyncio

import pytest
from loguru import logger

from spotcool_agent.transports.bacnet import (
    DEFAULT_STEP_WALL_S,
    SILENT_WARN_S,
    ClockReading,
    NoAnswer,
    StepClock,
)

T0 = 199 * 86_400.0 + 7 * 3600  # 07:00
STEP_S = 900.0


class Wall:
    def __init__(self) -> None:
        self.now = 100.0

    def __call__(self) -> float:
        return self.now


async def no_reading() -> ClockReading | None:
    return None


def clock() -> tuple[StepClock, Wall]:
    wall = Wall()
    return StepClock(no_reading, clock=wall), wall


def test_the_initial_state_is_no_steps_end_and_the_first_change_is_step_0() -> None:
    steps, wall = clock()
    steps.observe(None)  # the BMS has no measurement yet
    steps.observe(ClockReading(T0, initial=True))
    assert steps.latest is None
    wall.now += 0.02  # the first step is not paced
    steps.observe(ClockReading(T0 + STEP_S))
    assert steps.latest is not None
    assert (steps.latest.start_s, steps.latest.end_s) == (T0, T0 + STEP_S)
    assert steps.step_wall_s == DEFAULT_STEP_WALL_S  # not measured from the unpaced step


def test_a_missed_initial_state_still_stamps_step_0() -> None:
    steps, _ = clock()
    steps.observe(ClockReading(T0 + STEP_S))  # 07:15 seen first
    assert steps.latest is not None
    assert steps.latest.start_s == T0


def test_the_wall_time_needs_three_intervals_so_one_late_reading_cannot_set_it() -> None:
    steps, wall = clock()
    steps.observe(ClockReading(T0 + STEP_S))
    wall.now += 0.8  # 07:15 was seen 2.2 s late
    steps.observe(ClockReading(T0 + 2 * STEP_S))
    assert steps.step_wall_s == DEFAULT_STEP_WALL_S
    for k in (3, 4):
        wall.now += 3.0
        steps.observe(ClockReading(T0 + k * STEP_S))
    assert steps.step_wall_s == pytest.approx(3.0)


def test_the_step_and_its_wall_time_are_measured() -> None:
    steps, wall = clock()
    for k in range(1, 5):
        steps.observe(ClockReading(T0 + k * STEP_S))
        wall.now += 3.0
        steps.observe(ClockReading(T0 + k * STEP_S))  # the same value again: nothing new
    assert steps.step_s == STEP_S
    assert steps.step_wall_s == pytest.approx(3.0)
    wall.now += 3.0  # a missed value: two steps in 6 s
    steps.observe(ClockReading(T0 + 6 * STEP_S))
    assert steps.step_wall_s == pytest.approx(3.0)
    assert steps.latest is not None and steps.latest.start_s == T0 + 5 * STEP_S


def test_a_32_bit_value_is_rounded_to_whole_seconds() -> None:
    steps, _ = clock()
    steps.observe(ClockReading(T0 + STEP_S + 0.4))
    assert steps.latest is not None and steps.latest.end_s == T0 + STEP_S


@pytest.mark.asyncio
async def test_next_step_hands_out_each_newest_step_once() -> None:
    steps, _ = clock()
    steps.observe(ClockReading(T0 + STEP_S))
    first = await steps.next_step()
    waiting = asyncio.create_task(steps.next_step())
    await asyncio.sleep(0)
    assert not waiting.done()
    steps.observe(ClockReading(T0 + 2 * STEP_S))
    steps.observe(ClockReading(T0 + 3 * STEP_S))
    newest = await asyncio.wait_for(waiting, 1.0)
    assert (first.end_s, newest.end_s) == (T0 + STEP_S, T0 + 3 * STEP_S)


@pytest.mark.asyncio
async def test_run_polls_the_reader() -> None:
    readings = [None, ClockReading(T0, initial=True), ClockReading(T0 + STEP_S)]

    async def read() -> ClockReading | None:
        return readings.pop(0) if readings else ClockReading(T0 + STEP_S)

    steps = StepClock(read, poll_s=0.01)
    polling = asyncio.create_task(steps.run())
    try:
        step = await asyncio.wait_for(steps.next_step(), 1.0)
    finally:
        polling.cancel()
        await asyncio.gather(polling, return_exceptions=True)
    assert step.start_s == T0


def test_a_clock_going_back_or_a_new_initial_state_is_a_new_run() -> None:
    steps, wall = clock()
    for k in range(1, 5):
        wall.now += 3.0
        steps.observe(ClockReading(T0 + k * STEP_S))
    assert steps.latest is not None and steps.latest.run == 0
    assert steps.step_wall_s == pytest.approx(3.0)
    steps.observe(ClockReading(T0, initial=True))  # the world started its run again
    wall.now += 0.1
    steps.observe(ClockReading(T0 + STEP_S))
    assert steps.latest is not None and steps.latest.run == 1
    assert steps.latest.start_s == T0
    assert steps.step_wall_s == DEFAULT_STEP_WALL_S  # measured afresh
    wall.now += 3.0
    steps.observe(ClockReading(T0 + 2 * STEP_S))
    steps.observe(ClockReading(T0 + STEP_S))  # backwards, the initial state missed
    assert steps.latest.run == 2


@pytest.mark.asyncio
async def test_a_silent_bms_is_warned_about_once_after_10_s() -> None:
    wall = Wall()
    answered = asyncio.Event()

    async def read() -> ClockReading | None:
        wall.now += 1.0
        if wall.now < 100.0 + 2 * SILENT_WARN_S:
            raise NoAnswer("127.0.0.1:47808: no-response")
        answered.set()
        return None

    said: list[str] = []
    sink = logger.add(lambda message: said.append(str(message)), level="INFO")
    steps = StepClock(read, poll_s=0.0, clock=wall)
    polling = asyncio.create_task(steps.run())
    try:
        await asyncio.wait_for(answered.wait(), 5)
    finally:
        polling.cancel()
        await asyncio.gather(polling, return_exceptions=True)
        logger.remove(sink)
    warned = [line for line in said if "SIM-CLOCK unanswered for 10 s" in line]
    assert len(warned) == 1 and "WARNING" in warned[0]
    assert any("SIM-CLOCK answers again" in line for line in said)
