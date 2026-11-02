"""BoptestPlant: one BOPTEST test case as a ``PlantService``.

One plant holds one BOPTEST test (one ``testid``) from ``start()`` to ``stop()``.

BOPTEST inputs latch: an override stays active until ``<x>_activate = 0`` is sent. So every
step sends every managed input: the world's ten (``MANAGED_INPUTS``: each zone's cooling
setpoint and VAV damper) and any other input the plant has been given. An overridden input
gets ``<x>_u`` and ``<x>_activate = 1``; a released one (``None``, or left out of the step's
overrides) gets only ``<x>_activate = 0``.

Measurements keep BOPTEST's REST names. The REST API reports an overwritten signal under its
input's name ``<x>_u``, with the value actually applied (BOPTEST's own control when released,
the override when active); the point maps read those ``_u`` keys.

After a step, the seven ElectricPower meters are their means over the step, from BOPTEST's
stored results (``step_means``); every other value is the one at the end of the step. Power
can spike inside a step (a VAV box opening wide after its damper reopens), so a value at the
end of the step can be far from the step's mean.

A step whose answer is lost to a dropped connection is recovered, never guessed (``step``):
BOPTEST finishes an advance it got even when its client is gone, and stores every point at the
step's end, so its stored results tell whether the step ran and what it answered. An advance
is resent only when BOPTEST certainly never got it, so no step is applied twice.
"""

import asyncio
import itertools
import math
import time
from collections.abc import Awaitable, Callable, Mapping, Sequence

from loguru import logger

from spotcool_world.services.boptest.catalog import MANAGED_INPUTS, load_catalog
from spotcool_world.services.boptest.client import (
    ADVANCE_TIMEOUT_S,
    STOP_RETRY_S,
    BoptestClient,
    BoptestError,
    backoff_s,
)
from spotcool_world.services.plant_service import PlantService, PlantStartParams
from spotcool_world.utils.task_manager import TaskManager

KEEPALIVE_S = 300.0
"""Wall seconds without a call before the plant pings its test. BOPTEST stops a test after 15
idle minutes, for example while the other run's test is still queued."""
STOP_WAIT_S = STOP_RETRY_S + 5.0
"""Longest a ``stop()`` waits for a stop request already sent before it closes the client: the
request's whole retry budget, and a margin."""
RECOVERY_S = 300.0
"""Longest wall time a step waits for its state, from its advance's first failure."""
RECOVERY_BACKOFF_S = 1.0
"""Longest delay before a recovering step's second try; it doubles with each try, up to 30 s."""
_TUNNEL_STATUSES = frozenset({502, 503, 504})  # a proxy between us and BOPTEST lost it


class BoptestPlant(PlantService):
    """A BOPTEST test case behind ``PlantService``.

    ``start`` selects a test (once), then initializes it. When ``params.time_period`` is set
    and ``params.start_time_s`` is that period's own start, it uses BOPTEST's scenario API
    (the standard seven-day warm-up); otherwise it initializes at ``start_time_s`` after
    ``warmup_s``, with BOPTEST's default constant electricity price. Calling ``start`` again
    re-initializes the same test. ``stop`` stops the test and closes the client, so a stopped
    plant cannot be started again.

    Args:
        client: The BOPTEST service; the plant closes it in ``stop()``.
        testcase: Test case name. Only the office test case has a catalog to validate against.
        keepalive_s: Wall seconds without a call before the plant pings its test.
        clock: Wall clock in seconds for a step's recovery; tests inject a fake one.
        sleep: Waits between a recovering step's tries; tests inject a fake one.
    """

    def __init__(
        self,
        client: BoptestClient,
        testcase: str = "multizone_office_simple_air",
        *,
        keepalive_s: float = KEEPALIVE_S,
        clock: Callable[[], float] = time.perf_counter,
        sleep: Callable[[float], Awaitable[None]] = asyncio.sleep,
    ) -> None:
        self._catalog = load_catalog()
        if testcase != self._catalog.testcase:
            raise ValueError(f"no input catalog for {testcase!r}, only {self._catalog.testcase!r}")
        self._client = client
        self._testcase = testcase
        self._keepalive_s = keepalive_s
        self._clock = clock
        self._sleep = sleep
        self._tasks = TaskManager()
        self._testid: str | None = None
        self._step_s: float | None = None
        self._time_s: float | None = None  # the time of the last state BOPTEST gave
        self._managed: set[str] = set(MANAGED_INPUTS)
        self._stopping: asyncio.Task[None] | None = None  # the stop request, once sent
        self._power_fallbacks = 0
        self._recoveries = 0
        inputs = sorted(self._catalog.inputs)
        # What an advance answers: the outputs but the inputs' read-backs (BOPTEST reports
        # those under the inputs' _u names), the inputs and their _activate flags.
        self._state_names = [
            *(n for n in self._catalog.outputs if n.removesuffix("_y") + "_u" not in inputs),
            *inputs,
            *(name.removesuffix("_u") + "_activate" for name in inputs),
        ]

    @property
    def client(self) -> BoptestClient:
        """The BOPTEST service this plant calls."""
        return self._client

    @property
    def power_fallbacks(self) -> int:
        """Steps whose meters are their values at the end of the step, not their means.

        A step falls back when BOPTEST's results call fails; each one is logged as a warning.
        """
        return self._power_fallbacks

    @property
    def connection_recoveries(self) -> int:
        """Steps whose advance failed on the connection and were recovered (see ``step``).

        Each one is logged as a warning.
        """
        return self._recoveries

    @property
    def testid(self) -> str | None:
        """The test this plant holds, or ``None`` before ``start`` and after ``stop``."""
        return self._testid

    @property
    def step_s(self) -> float:
        """Simulation seconds each ``step()`` advances (set by ``start``)."""
        if self._step_s is None:
            raise RuntimeError("the plant has not been started, or has been stopped")
        return self._step_s

    async def start(self, params: PlantStartParams) -> dict[str, float]:
        """Select a test (first call only) and initialize it; on failure, stop the test.

        The latency of each call (select, scenario or initialize, step) goes to one INFO
        line, also bound as structured fields.

        Args:
            params: Start time, step, warm-up and BOPTEST time period.

        Returns:
            The measurements at ``params.start_time_s``.
        """
        period = params.time_period
        at_period_start = (
            period is not None and params.start_time_s == self._catalog.period_start_s(period)
        )
        latencies: dict[str, float] = {}
        try:
            if self._testid is None:
                started = time.perf_counter()
                self._testid = await self._client.select(self._testcase)
                latencies["select_s"] = time.perf_counter() - started
                self._tasks.create_task(self._keep_alive(), "boptest-keep-alive")
            started = time.perf_counter()
            if period is not None and at_period_start:
                state = await self._client.set_scenario(self._testid, time_period=period)
                latencies["scenario_s"] = time.perf_counter() - started
            else:
                state = await self._client.initialize(
                    self._testid, params.start_time_s, params.warmup_s
                )
                latencies["initialize_s"] = time.perf_counter() - started
            started = time.perf_counter()
            await self._client.set_step(self._testid, params.step_s)
            latencies["put_step_s"] = time.perf_counter() - started
        except BaseException:
            await self.stop()
            raise
        self._step_s = params.step_s
        self._time_s = state.get("time")
        fields = " ".join(f"{name}={seconds:.2f}" for name, seconds in latencies.items())
        logger.bind(testid=self._testid, **latencies).info(
            f"BOPTEST start: test={self._testid} {fields}"
        )
        return state

    async def step(self, overrides: Mapping[str, float | None]) -> dict[str, float]:
        """Advance one step, sending every managed input explicitly.

        An advance that fails on the connection (no answer, or HTTP 502, 503 or 504 from a
        proxy) is recovered within ``RECOVERY_S`` of the failure (no try starts later, and a
        resend's timeout ends by then; a read of the results under way takes at most
        ``QUICK_TIMEOUT_S`` more), and counted in ``connection_recoveries``. The step ends at
        ``t_next``, the last state's time plus the step:

        - An advance that never reached BOPTEST (no connection) is resent.
        - Any other may have reached it, and BOPTEST finishes an advance it got. If its stored
          results reach ``t_next``, the step ran: its state is read back from them
          (``BoptestClient.state_at``). If they do not, in an answer that arrives at least
          ``ADVANCE_TIMEOUT_S`` after the advance was sent, BOPTEST never got it, and it is
          resent; the same inputs make the same step, since inputs latch. Otherwise the
          results are read again, with backoff.

        Args:
            overrides: Input name (``..._u``) to value; ``None`` or leaving a managed input out
                releases it.

        Returns:
            The measurements at the end of the step, the seven meters' means over the step
            (their values at the end if BOPTEST's results call fails: see ``power_fallbacks``).

        Raises:
            ValueError: If an override is not an input of the test case (nothing is sent).
            BoptestError: If BOPTEST fails to advance (it refuses the step, or gives no state
                for it within ``RECOVERY_S``), or the scenario period is over.
        """
        testid = self._require_testid()
        self._catalog.check_overrides(overrides)
        self._managed.update(overrides)
        inputs: dict[str, float] = {}
        for name in sorted(self._managed):
            value = overrides.get(name)
            if value is not None:
                inputs[name] = value
            inputs[name.removesuffix("_u") + "_activate"] = 0.0 if value is None else 1.0
        state = await self._advance(testid, inputs)
        if not state:
            raise BoptestError("advance: the BOPTEST scenario period is over", status=200)
        self._time_s = state.get("time")
        await self._mean_power(testid, state)
        return state

    async def kpis(self) -> dict[str, float | None]:
        """BOPTEST's own KPIs since the last initialization."""
        return await self._client.kpis(self._require_testid())

    async def stop(self) -> None:
        """Stop the test (freeing its worker) and close the client. Safe to call twice.

        The stop request runs as a task kept on the plant. Cancelling this call (a second
        Ctrl+C) leaves the request running and the client open; the next ``stop()`` waits up to
        ``STOP_WAIT_S`` for the request before it closes the client, so it is never cut.
        """
        await self._tasks.cancel_all()
        self._step_s = None
        testid, self._testid = self._testid, None
        if testid is not None:
            # Not in _tasks: neither cancel_all() nor a cancelled caller may cut the request.
            self._stopping = asyncio.ensure_future(self._client.stop(testid))
        if self._stopping is not None:
            await asyncio.wait([self._stopping], timeout=STOP_WAIT_S)  # never cancels it
        await self._client.aclose()

    async def _advance(self, testid: str, inputs: Mapping[str, float]) -> dict[str, float]:
        """One advance, and its recovery if it fails on the connection (see ``step``)."""
        start_s, sent_s = self._time_s, self._clock()
        try:
            return await self._client.advance(testid, inputs)
        except BoptestError as exc:
            if start_s is None or not _connection_lost(exc):
                raise
            return await self._recover(testid, inputs, start_s, exc, sent_s)

    async def _recover(
        self,
        testid: str,
        inputs: Mapping[str, float],
        start_s: float,
        lost: BoptestError,
        sent_s: float,
    ) -> dict[str, float]:
        """Recover the step from ``start_s``: resend it, or read it back (see ``step``).

        Its advance was sent at ``sent_s`` and failed with ``lost``.
        """
        step_s = self.step_s
        final_s = start_s + step_s
        deadline = self._clock() + RECOVERY_S
        first, last = lost, lost
        reads = resends = tries = 0
        probe = self._catalog.electric_power[:1]  # a meter: a number at every stored time
        while True:
            try:
                resend = lost.unsent
                if not resend:
                    reads += 1
                    # Up to a step past this one's end, so a step applied twice would show.
                    stored = await self._client.results(testid, probe, start_s, final_s + step_s)
                    end_s = (stored.get("time") or [start_s])[-1]
                    if end_s > final_s + step_s / 2:
                        raise BoptestError(
                            f"test {testid} is at {end_s:.0f} s, past the step's end at "
                            f"{final_s:.0f} s: it advanced more than once",
                            status=200,
                        )
                    if end_s > start_s:  # the step ran: BOPTEST's stored state at its end
                        state = await self._client.state_at(testid, self._state_names, end_s)
                        how = "read back from BOPTEST's stored results"
                        return self._recovered(testid, final_s, first, state, how)
                    resend = self._clock() - sent_s >= ADVANCE_TIMEOUT_S
                if resend:
                    resends += 1
                    sent_s = self._clock()
                    timeout_s = min(ADVANCE_TIMEOUT_S, max(deadline - sent_s, 1.0))
                    try:
                        state = await self._client.advance(testid, inputs, timeout_s=timeout_s)
                    except BoptestError as exc:
                        lost = exc
                        raise
                    how = "resent, as BOPTEST never got it"
                    return self._recovered(testid, final_s, first, state, how)
            except BoptestError as exc:
                if not _connection_lost(exc):
                    raise
                last = exc
            tries += 1
            delay = backoff_s(tries, RECOVERY_BACKOFF_S)
            if self._clock() + delay > deadline:
                raise BoptestError(
                    f"advance of test {testid} to {final_s:.0f} s: no state for the step within "
                    f"{RECOVERY_S:.0f} s of its connection failing ({first}); tried {reads} "
                    f"reads of BOPTEST's stored results and {resends} resends; last: {last}"
                ) from last
            await self._sleep(delay)

    def _recovered(
        self, testid: str, final_s: float, lost: BoptestError, state: dict[str, float], how: str
    ) -> dict[str, float]:
        """Count and log a recovered step; return its state."""
        self._recoveries += 1
        logger.warning(
            f"BOPTEST: test {testid}'s step to {final_s:.0f} s lost its connection ({lost}); "
            f"{how} (connection recoveries so far: {self._recoveries})"
        )
        return state

    def _require_testid(self) -> str:
        if self._testid is None:
            raise RuntimeError("the plant has not been started, or has been stopped")
        return self._testid

    async def _mean_power(self, testid: str, state: dict[str, float]) -> None:
        """Replace the meters in ``state`` with their means over the step that ends at its time.

        On failure the meters keep their values at the end of the step; the step is logged and
        counted, never failed.
        """
        meters = self._catalog.electric_power
        try:
            final_s = state["time"]
            samples = await self._client.results(testid, meters, final_s - self.step_s, final_s)
            state.update(step_means(samples, meters))
        except (BoptestError, KeyError, ValueError) as exc:  # KeyError: a state without time
            self._power_fallbacks += 1
            logger.warning(
                f"BOPTEST: no power means for test {testid}'s step to {state.get('time')} s "
                f"({type(exc).__name__}: {exc}); its meters are the values at the end of the "
                f"step (power fallbacks so far: {self._power_fallbacks})"
            )

    async def _keep_alive(self) -> None:
        while True:
            await asyncio.sleep(self._keepalive_s)
            testid = self._testid
            if testid is not None and self._client.idle_s >= self._keepalive_s:
                try:
                    await self._client.keep_alive(testid)
                except BoptestError as exc:
                    logger.warning(f"BOPTEST: keep-alive of test {testid} failed: {exc}")


def _connection_lost(exc: BoptestError) -> bool:
    """Whether a call failed on the connection, not in BOPTEST: no answer, or a proxy's 5xx."""
    return exc.status is None or exc.status in _TUNNEL_STATUSES


def step_means(samples: Mapping[str, Sequence[float]], names: Sequence[str]) -> dict[str, float]:
    """Each point's time-weighted mean over its samples: the trapezoid rule over their span.

    Args:
        samples: Each point's samples and, under ``time``, their times, as
            ``BoptestClient.results`` returns them.
        names: The points to average.

    Raises:
        ValueError: If the times span no time or go backwards, a point lacks a sample for each
            time, or a mean is not finite.
    """
    times = samples.get("time", [])
    if len(times) < 2:
        raise ValueError(f"{len(times)} samples, too few to average")
    if any(t1 < t0 for t0, t1 in itertools.pairwise(times)):
        raise ValueError("the samples' times go backwards")
    span = times[-1] - times[0]
    if span <= 0:
        raise ValueError(f"the samples span no time: all at {times[0]} s")
    means: dict[str, float] = {}
    for name in names:
        values = samples.get(name, [])
        if len(values) != len(times):
            raise ValueError(f"{name}: {len(values)} samples for {len(times)} times")
        pairs = itertools.pairwise(zip(times, values, strict=True))
        mean = sum((y0 + y1) / 2 * (t1 - t0) for (t0, y0), (t1, y1) in pairs) / span
        if not math.isfinite(mean):
            raise ValueError(f"{name}: the mean is {mean}")
        means[name] = mean
    return means
