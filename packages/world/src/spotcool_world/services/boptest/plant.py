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
"""

import asyncio
import time
from collections.abc import Mapping

from loguru import logger

from spotcool_world.services.boptest.catalog import MANAGED_INPUTS, load_catalog
from spotcool_world.services.boptest.client import BoptestClient, BoptestError
from spotcool_world.services.plant_service import PlantService, PlantStartParams
from spotcool_world.utils.task_manager import TaskManager

KEEPALIVE_S = 300.0
"""Wall seconds without a call before the plant pings its test. BOPTEST stops a test after 15
idle minutes, for example while the other run's test is still queued."""


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
    """

    def __init__(
        self,
        client: BoptestClient,
        testcase: str = "multizone_office_simple_air",
        *,
        keepalive_s: float = KEEPALIVE_S,
    ) -> None:
        self._catalog = load_catalog()
        if testcase != self._catalog.testcase:
            raise ValueError(f"no input catalog for {testcase!r}, only {self._catalog.testcase!r}")
        self._client = client
        self._testcase = testcase
        self._keepalive_s = keepalive_s
        self._tasks = TaskManager()
        self._testid: str | None = None
        self._step_s: float | None = None
        self._managed: set[str] = set(MANAGED_INPUTS)

    @property
    def client(self) -> BoptestClient:
        """The BOPTEST service this plant calls."""
        return self._client

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
        fields = " ".join(f"{name}={seconds:.2f}" for name, seconds in latencies.items())
        logger.bind(testid=self._testid, **latencies).info(
            f"BOPTEST start: test={self._testid} {fields}"
        )
        return state

    async def step(self, overrides: Mapping[str, float | None]) -> dict[str, float]:
        """Advance one step, sending every managed input explicitly.

        Args:
            overrides: Input name (``..._u``) to value; ``None`` or leaving a managed input out
                releases it.

        Returns:
            The measurements at the end of the step.

        Raises:
            ValueError: If an override is not an input of the test case (nothing is sent).
            BoptestError: If BOPTEST fails, or the scenario period is over.
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
        state = await self._client.advance(testid, inputs)
        if not state:
            raise BoptestError("advance: the BOPTEST scenario period is over", status=200)
        return state

    async def kpis(self) -> dict[str, float | None]:
        """BOPTEST's own KPIs since the last initialization."""
        return await self._client.kpis(self._require_testid())

    async def stop(self) -> None:
        """Stop the test (freeing its worker) and close the client. Safe to call twice.

        The stop request is shielded, so cancelling this call (a second Ctrl+C) does not abort
        it while the event loop keeps running.
        """
        await self._tasks.cancel_all()
        self._step_s = None
        testid, self._testid = self._testid, None
        if testid is not None:
            # Shielded: a second Ctrl+C must not abort freeing a worker of a shared service.
            await asyncio.shield(self._client.stop(testid))
        await self._client.aclose()

    def _require_testid(self) -> str:
        if self._testid is None:
            raise RuntimeError("the plant has not been started, or has been stopped")
        return self._testid

    async def _keep_alive(self) -> None:
        while True:
            await asyncio.sleep(self._keepalive_s)
            testid = self._testid
            if testid is not None and self._client.idle_s >= self._keepalive_s:
                try:
                    await self._client.keep_alive(testid)
                except BoptestError as exc:
                    logger.warning(f"BOPTEST: keep-alive of test {testid} failed: {exc}")
