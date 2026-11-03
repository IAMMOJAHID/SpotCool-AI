"""VrfWorker: runs the meeting-room VRF unit in wall time and publishes its state (``bacnet`` extra).

The VRF lives outside the simulation pipeline: it moves at 15 deg/s whatever the run's speed.
"""

import asyncio
import time
from collections.abc import Awaitable, Callable

from loguru import logger

from spotcool_contracts.events import VrfStateData
from spotcool_world.bus import Bus
from spotcool_world.clocks import SimClock
from spotcool_world.frames import VrfStateFrame
from spotcool_world.processors.event_emitter import EVENTS_TOPIC, frame_events
from spotcool_world.transports.bacnet.vrf import BacnetVrfGateway
from spotcool_world.workers.base_worker import BaseWorker, WorkerState

TICK_S = 0.05
"""Wall seconds between two ticks of the unit: 20 Hz."""

PUBLISH_TICKS = 4
"""Fewest ticks between two ``vrf_state`` events while the state changes: at most 5 Hz."""

KEEPALIVE_TICKS = 100
"""Most ticks between two ``vrf_state`` events: one every 5 s, so a late dashboard catches up."""


class VrfWorker(BaseWorker):
    """Ticks the VRF unit at 20 Hz of wall time and publishes its ``vrf_state`` events.

    Each tick moves the unit on by the wall time since the last (``gateway.advance``, which
    also refreshes the gateway's analog inputs). The ticks follow a fixed schedule; one that
    runs late skips the ticks it missed, never bursting to catch up. A ``vrf_state`` event
    (``frame_events`` of a ``VrfStateFrame`` on ``EVENTS_TOPIC``, stamped with the simulation's
    time) goes out at the start, then at most every ``PUBLISH_TICKS`` ticks while the published
    state changes (compared after its rounding), and every ``KEEPALIVE_TICKS`` ticks otherwise.

    A tick that fails is logged, with the error's type and message, and the worker ticks on: a
    failure is logged again only when its message changes, and the first tick that succeeds
    after it is logged once. It needs the runner's bus and runs until ``stop()``.

    Args:
        gateway: The VRF gateway, whose unit it moves.
        sim_clock: The simulation clock whose time stamps the events.
        clock: Monotonic wall clock, in seconds; inject a fake one in tests.
        sleep: Sleeps for wall seconds; inject a fake one in tests.
        name: Unique worker name.
    """

    def __init__(
        self,
        gateway: BacnetVrfGateway,
        sim_clock: SimClock,
        *,
        clock: Callable[[], float] = time.perf_counter,
        sleep: Callable[[float], Awaitable[None]] = asyncio.sleep,
        name: str | None = None,
    ) -> None:
        super().__init__(name=name)
        self._gateway = gateway
        self._sim_clock = sim_clock
        self._clock = clock
        self._sleep = sleep
        self._stopping = False

    async def run(self) -> None:
        """Tick the unit until ``stop()``; the state is then ``finished``.

        Raises:
            RuntimeError: If the worker has already run, or has no bus.
        """
        if self._state is not WorkerState.IDLE:
            raise RuntimeError(f"{self.name} has already run")
        bus = self.bus
        if bus is None:
            raise RuntimeError(f"{self.name} needs a bus: run it with a WorkerRunner")
        self._state = WorkerState.RUNNING
        try:
            await self._tick(bus)
            self._state = WorkerState.FINISHED
        except asyncio.CancelledError:
            self._state = WorkerState.CANCELLED
            raise
        except Exception:
            self._state = WorkerState.FAILED
            raise
        finally:
            await self.cleanup()

    async def stop(self, reason: str = "") -> None:
        """Stop after the tick under way.

        Args:
            reason: Why it is being stopped (logged).
        """
        logger.info(f"{self.name}: stopping{f' ({reason})' if reason else ''}")
        self._stopping = True

    async def _tick(self, bus: Bus) -> None:
        start = last = self._clock()
        tick = published_tick = 0
        published: VrfStateData | None = None
        failure: str | None = None
        while True:
            now = self._clock()
            # Late: skip the missed ticks. The epsilon keeps 1.5 / 0.05 from flooring to 29.
            tick = max(tick, int((now - start) / TICK_S + 1e-9))
            dt_s, last = now - last, now
            try:
                self._gateway.advance(dt_s)
                state = self._gateway.unit.state()
                since = tick - published_tick
                changed = state != published and since >= PUBLISH_TICKS
                if published is None or changed or since >= KEEPALIVE_TICKS:
                    await self._publish(bus, state)
                    published, published_tick = state, tick
            except Exception as exc:
                text = f"{type(exc).__name__}: {exc}"
                if text != failure:  # once per error, not 20 times a second
                    logger.warning(f"{self.name}: a tick failed ({text}); ticking on")
                failure = text
            else:
                if failure is not None:
                    logger.info(f"{self.name}: ticks succeed again")
                failure = None
            if self._stopping:
                return
            tick += 1
            await self._sleep(max(0.0, start + tick * TICK_S - self._clock()))

    async def _publish(self, bus: Bus, state: VrfStateData) -> None:
        frame = VrfStateFrame(data=state, sim_time_s=self._sim_clock.now_s)
        for envelope in frame_events(frame):
            await bus.publish(EVENTS_TOPIC, envelope)
