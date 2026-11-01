"""PlantProcessor: steps one building plant (the SpotCool run or the baseline) every tick."""

import time

from loguru import logger

from spotcool_contracts.events import KpiData, MetricsData, Run
from spotcool_world.frames import (
    CancelFrame,
    EndFrame,
    Frame,
    KpiFrame,
    MetricsFrame,
    PlantMeasurementsFrame,
    SetpointCommandFrame,
    StartFrame,
    TickFrame,
)
from spotcool_world.processors.frame_processor import FrameDirection, FrameProcessor
from spotcool_world.services.boptest.catalog import load_catalog
from spotcool_world.services.plant_service import PlantService, PlantStartParams

DEFAULT_WARMUP_S = 3 * 86_400.0
"""Simulated warm-up before the first tick. BOPTEST's zones need about three days to settle
from its initial state: after one, they are still at 22-23 degC at 10:00 on the peak cooling
day. It costs about a minute on BOPTEST and well under a second on the RC plant."""


class PlantProcessor(FrameProcessor):
    """Owns one plant run and steps it in lockstep with the ticks.

    - ``StartFrame``: starts the plant at the run's start time and step.
    - ``SetpointCommandFrame`` for this run: merged into the overrides for the next steps
      (``None`` releases an input). Names the catalog does not know are rejected as a whole
      command with a non-fatal error.
    - ``TickFrame``: steps the plant (the baseline always with no overrides), then pushes a
      ``PlantMeasurementsFrame`` stamped with the end of the step (tick time plus ``step_s``,
      the time the values describe), a ``MetricsFrame`` stamped with the tick's time (step
      latency, simulated seconds per wall second), then the tick.
    - ``EndFrame`` or ``CancelFrame``: pushes the plant's final ``KpiFrame``, then stops it.

    A plant that fails to start or step is a fatal error: the run cannot go on without it.
    The plant is always stopped, at the end of the run or in ``cleanup()``.

    Args:
        service: The plant provider.
        run: Which run this processor steps.
        time_period: BOPTEST scenario time period passed to the plant's ``start``.
        warmup_s: Simulated seconds the plant runs before the first tick.
        name: Display name.
    """

    def __init__(
        self,
        service: PlantService,
        run: Run,
        *,
        time_period: str | None = None,
        warmup_s: float = DEFAULT_WARMUP_S,
        name: str | None = None,
    ) -> None:
        super().__init__(name=name or f"PlantProcessor({run})")
        self._service = service
        self._run: Run = run
        self._time_period = time_period
        self._warmup_s = warmup_s
        self._overrides: dict[str, float | None] = {}
        self._started = False
        self._stopped = False

    async def process_frame(self, frame: Frame, direction: FrameDirection) -> None:
        """Start, command, step and stop the plant as frames arrive; pass every frame on."""
        await super().process_frame(frame, direction)
        if direction is FrameDirection.DOWNSTREAM:
            if isinstance(frame, StartFrame):
                await self._start(frame)
            elif isinstance(frame, SetpointCommandFrame) and frame.run == self._run:
                await self._command(frame)
            elif isinstance(frame, TickFrame):
                await self._step(frame)
            elif isinstance(frame, EndFrame | CancelFrame):
                await self._finish(frame)
        await self.push_frame(frame, direction)

    async def cleanup(self) -> None:
        """Stop the plant if the run ended without an ``EndFrame`` or ``CancelFrame``."""
        await super().cleanup()
        await self._stop()

    async def _start(self, frame: StartFrame) -> None:
        params = PlantStartParams(
            start_time_s=frame.start_time_s,
            step_s=frame.step_s,
            warmup_s=self._warmup_s,
            time_period=self._time_period,
        )
        try:
            await self._service.start(params)
        except Exception as exc:
            await self.push_error(f"{self._run} plant failed to start: {exc}", fatal=True)
            return
        self._started = True

    async def _command(self, frame: SetpointCommandFrame) -> None:
        try:
            load_catalog().check_overrides(frame.overrides)
        except ValueError as exc:
            await self.push_error(f"{self._run} setpoint command rejected: {exc}")
            return
        self._overrides.update(frame.overrides)

    async def _step(self, frame: TickFrame) -> None:
        if not self._started:
            return
        overrides = dict(self._overrides) if self._run == "spotcool" else {}
        started = time.perf_counter()
        try:
            raw = await self._service.step(overrides)
        except Exception as exc:
            self._started = False
            await self.push_error(f"{self._run} plant step failed: {exc}", fatal=True)
            return
        latency_s = time.perf_counter() - started
        assert frame.sim_time_s is not None  # a TickFrame always has one
        end_s = frame.sim_time_s + frame.step_s  # the raw values describe the end of the step
        await self.push_frame(PlantMeasurementsFrame(run=self._run, raw=raw, sim_time_s=end_s))
        labels = {"run": self._run}
        samples = [MetricsData(name="plant_step_latency", value=latency_s, unit="s", labels=labels)]
        if latency_s > 0:
            ratio = frame.step_s / latency_s
            samples.append(
                MetricsData(name="plant_time_ratio", value=ratio, unit="1", labels=labels)
            )
        await self.push_frame(MetricsFrame(samples=samples, sim_time_s=frame.sim_time_s))

    async def _finish(self, frame: EndFrame | CancelFrame) -> None:
        if self._started:
            self._started = False
            try:
                kpis = await self._service.kpis()
            except Exception as exc:
                await self.push_error(f"{self._run} plant KPIs failed: {exc}")
            else:
                data = KpiData(run=self._run, boptest=kpis, provisional=False)
                await self.push_frame(KpiFrame(data=data, sim_time_s=frame.sim_time_s))
        await self._stop()

    async def _stop(self) -> None:
        if self._stopped:
            return
        self._stopped = True
        try:
            await self._service.stop()
        except Exception:
            # Logged, not pushed: this also runs in cleanup(), after the pipeline has stopped.
            logger.exception(f"{self.name}: {self._run} plant failed to stop")
