"""The VRF worker on a fake wall clock: its 20 Hz ticks, the 5 Hz limit and the 5 s keep-alive.

The gateway is never started: no sockets, and no test waits in real time.
"""

from typing import Any

import pytest

from spotcool_contracts import load_vrf_profiles
from spotcool_contracts.events import EventSource, EventType
from spotcool_world.bus import InProcessBus
from spotcool_world.clocks import SimClock
from spotcool_world.transports.bacnet.vrf import BacnetVrfGateway
from spotcool_world.workers import WorkerState
from spotcool_world.workers.vrf_worker import VrfWorker

T0 = 198 * 86_400 + 10 * 3600  # the worker's simulation time (conftest)


def gateway(vendor: str = "mitsubishi") -> BacnetVrfGateway:
    return BacnetVrfGateway(load_vrf_profiles()[vendor], InProcessBus(), SimClock(T0, 900.0))


@pytest.mark.asyncio
async def test_it_ticks_at_20_hz_and_moves_the_unit_by_the_wall_time(run_worker: Any) -> None:
    unit = gateway()
    unit.unit.vane_v = "Pos5"
    run = await run_worker(unit, 10.0)
    assert len(run.ticks) == 200
    assert run.ticks == pytest.approx([0.05 * k for k in range(1, 201)])
    assert unit.unit.vane_v_angle_deg == -60.0  # 0 to -60 deg in 4 of the 10 s


@pytest.mark.asyncio
async def test_a_changing_state_is_published_at_most_5_times_a_second(run_worker: Any) -> None:
    unit = gateway()
    unit.unit.vane_v = "Swing"  # it changes on every tick
    run = await run_worker(unit, 10.0)
    times = [t for t, _ in run.published]
    assert times[0] == 0.0  # the state at the start, at once
    assert len(times) == 51  # then one every 0.2 s
    gaps = [b - a for a, b in zip(times, times[1:], strict=False)]
    assert min(gaps) == pytest.approx(0.2) and max(gaps) == pytest.approx(0.2)
    assert all(e.type is EventType.VRF_STATE for e in run.events)
    assert {(e.source, e.sim_time_s) for e in run.events} == {(EventSource.WORLD, T0)}


@pytest.mark.asyncio
async def test_a_steady_state_is_published_every_5_s(run_worker: Any) -> None:
    unit = gateway()
    unit.unit.power_on = False  # at ambient, louvers held, no power: nothing changes
    run = await run_worker(unit, 12.0)
    assert [t for t, _ in run.published] == pytest.approx([0.0, 5.0, 10.0])


@pytest.mark.asyncio
async def test_a_change_is_compared_after_its_rounding(run_worker: Any) -> None:
    unit = gateway()
    unit.unit.power_on = False

    async def nudge(time: Any) -> None:
        unit.unit.room_c = 30.0 + (0.004 if time.elapsed > 1.0 else 0.0)  # under 0.01 degC

    run = await run_worker(unit, 4.0, nudge)
    assert [t for t, _ in run.published] == [0.0]


@pytest.mark.asyncio
async def test_a_late_tick_skips_the_ticks_it_missed(run_worker: Any) -> None:
    unit = gateway()
    unit.unit.vane_v = "Pos5"
    stalled = False

    async def stall(time: Any) -> None:
        nonlocal stalled
        if time.elapsed >= 1.0 and not stalled:
            stalled = True
            time.now += 0.5  # the loop was busy for half a second

    run = await run_worker(unit, 3.0, stall)
    assert len(run.ticks) == 60 - 10  # ten ticks skipped, none made up in a burst
    gaps = [b - a for a, b in zip(run.ticks, run.ticks[1:], strict=False)]
    assert sorted(gaps)[0] == pytest.approx(0.05) and sorted(gaps)[-2] == pytest.approx(0.05)
    assert max(gaps) == pytest.approx(0.55)
    # The late tick moves the louver 0.1 s worth (MAX_DT_S), never the 0.55 s it waited.
    assert unit.unit.vane_v_angle_deg == pytest.approx(-15.0 * (3.0 - 0.45))


@pytest.mark.asyncio
async def test_it_needs_a_bus() -> None:
    worker = VrfWorker(gateway(), SimClock(T0, 900.0))
    with pytest.raises(RuntimeError, match="needs a bus"):
        await worker.run()
    assert worker.state is WorkerState.IDLE
