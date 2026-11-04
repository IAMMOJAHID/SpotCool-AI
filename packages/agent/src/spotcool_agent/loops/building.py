"""BuildingLoop: one bounded perceive-decide-act round per BMS step.

The world never waits for the agent, so each round fits in the step it starts in:

1. ``StepClock`` sees ``SIM-CLOCK`` change to S: the step [S - step, S) has just ended.
2. Wait ``settle_s`` from that moment, so every camera shows the step's scene; if ``SIM-CLOCK``
   moved on meanwhile, the newer step is taken instead.
3. Fetch the five zone snapshots at once, in memory.
4. One ``detect(jpeg, concept)`` per zone, all at once, each with a timeout of
   ``min(DETECT_TIMEOUT_S, deadline - now)``, where ``deadline`` is ``step_wall_s`` minus the
   margin after the change was seen (``deadline_after``: the margin is ``DEADLINE_MARGIN_S``,
   or ``MARGIN_SHARE`` of the step if that is more). A result after the deadline is discarded.
   A zone without a count is unknown, whatever went wrong with it.
5. The policy decides for the step that starts at S (bookings and 19:00 judged there), and the
   actuator writes what changed. The BMS takes the writes at its next tick, so they take
   effect from that step.
6. The ``perception``, ``decision`` and ``explanation`` events are stamped with the step's
   start, as the stub stamps its own with the state it acted on, so the record pairs each
   decision with the write the BMS took one step later.

7. Only then, and in the background: Muse Spark's scene notes on the occupied zones
   (``loops/scene.py``) and Muse Wonder's one-sentence narration of a step that wrote
   (``loops/narration.py``). They are explanations only: they get the step's counts after the
   policy has used them, never delay it, and nothing they learn flows back into the policy.

One bad step never ends the day: a step that raises is logged (once per kind of failure) and
the loop goes on; only a cancel stops it. A new run (``Step.run`` changed) starts afresh: the
actuator releases and forgets its points, the policy forgets its state, and the call cap and
the camera catalog start again.
"""

import asyncio
import time
from collections.abc import Callable, Mapping

from loguru import logger

from spotcool_agent.actuator import Actuator, Publish
from spotcool_agent.domain.events import Perception
from spotcool_agent.domain.geometry import Detections
from spotcool_agent.loops.narration import Narrator
from spotcool_agent.loops.scene import SceneNotes
from spotcool_agent.observers.metrics import LOG_EVERY_STEPS, StageMetrics
from spotcool_agent.processors.occupancy import occupancy
from spotcool_agent.processors.policy import AgentPolicy, clock_text
from spotcool_agent.services.vision import CallCapReached, VisionError, VisionService
from spotcool_agent.transports.bacnet import Step, StepClock
from spotcool_agent.transports.world_http import WorldHttp, WorldHttpError
from spotcool_contracts import ZONES, Zone
from spotcool_contracts.calendar import ROOMS
from spotcool_contracts.cameras import CameraInfo
from spotcool_contracts.events import EventType

DEADLINE_MARGIN_S = 2.5
"""Wall seconds left in a step after the detections, for the policy and the writes, at least."""
MARGIN_SHARE = 0.4
"""The share of a step left after the detections, when it is more than ``DEADLINE_MARGIN_S``."""
DETECT_TIMEOUT_S = 8.0
"""The longest any one detection may take."""
SNAPSHOT_TIMEOUT_S = 2.0
"""The longest any one snapshot may take."""
SETTLE_S = 1.0
"""Wall seconds from a ``SIM-CLOCK`` change to the snapshots, so the cameras show the step."""
CONCEPT = "humans"
"""What the vision provider counts (A's ``SAM_PERSON_CONCEPT``)."""


def deadline_after(seen_at: float, step_wall_s: float) -> float:
    """When a step's detections must be done: its change, plus the step, less the margin.

    Args:
        seen_at: When the step's change was seen (``time.perf_counter``).
        step_wall_s: Wall seconds per step.
    """
    return seen_at + step_wall_s - max(DEADLINE_MARGIN_S, MARGIN_SHARE * step_wall_s)


class BuildingLoop:
    """The per-step loop (see the module docstring); ``run()`` goes on until cancelled.

    Args:
        clock: The step clock (its ``run()`` polls in another task).
        world: The world's cameras and calendar.
        vision: The people counter.
        policy: The policy.
        actuator: The BMS writer.
        publish: Where the perception goes.
        metrics: Where each stage's latency goes.
        settle_s: Wall seconds from a step's change to its snapshots.
        concept: What to count.
        on_new_run: Called when a new run starts, such as the Meta client's ``new_run``.
        notes: Muse Spark's scene notes on the occupied zones; ``None`` for none.
        narrator: Muse Wonder's narration of the steps that wrote; ``None`` for none.
    """

    def __init__(
        self,
        *,
        clock: StepClock,
        world: WorldHttp,
        vision: VisionService,
        policy: AgentPolicy,
        actuator: Actuator,
        publish: Publish,
        metrics: StageMetrics,
        settle_s: float = SETTLE_S,
        concept: str = CONCEPT,
        on_new_run: Callable[[], None] | None = None,
        notes: SceneNotes | None = None,
        narrator: Narrator | None = None,
    ) -> None:
        self._clock = clock
        self._world = world
        self._vision = vision
        self._policy = policy
        self._actuator = actuator
        self._publish = publish
        self._metrics = metrics
        self._settle_s = settle_s
        self._concept = concept
        self._cameras: dict[Zone, CameraInfo] | None = None
        self._failing: dict[str, str] = {}  # camera or room -> its last failure, logged once
        self._cap_reached = False
        self._on_new_run = on_new_run
        self._notes = notes
        self._narrator = narrator
        self._run = 0
        self._step_errors: set[str] = set()  # kinds of step failure logged so far
        self.steps = 0
        """Steps handled."""
        self.counts: dict[Zone, int] = {}
        """The people counted in each zone at the last step (a zone without a count is out)."""

    async def run(self) -> None:
        """Handle every step as ``SIM-CLOCK`` changes, until cancelled."""
        try:
            while True:
                step = await self._clock.next_step()
                try:  # one bad step never ends the day; a cancel still does
                    if step.run != self._run:
                        await self.new_run(step.run)
                    wait = step.seen_at + self._settle_s - time.perf_counter()
                    if wait > 0:
                        await asyncio.sleep(wait)
                    if self._clock.latest is not step:
                        continue  # SIM-CLOCK moved on during the settle: take the newer step
                    await self.step(step)
                except Exception as exc:
                    kind = type(exc).__name__
                    if kind not in self._step_errors:
                        self._step_errors.add(kind)
                        when = clock_text(step.start_s)
                        logger.error(f"step {when} failed, the loop goes on: {kind}: {exc}")
        finally:
            await self.stop_background()

    async def stop_background(self) -> None:
        """Cancel the scene notes and the narration under way. Safe to call twice."""
        if self._notes is not None:
            await self._notes.stop()
        if self._narrator is not None:
            await self._narrator.stop()

    async def new_run(self, run: int) -> None:
        """Start a new run afresh (see the module docstring).

        Args:
            run: The new run's count, ``Step.run``.
        """
        logger.info(f"new run detected (run {run}): releasing, and starting afresh")
        await self.stop_background()  # an old run's note or narration must not land in this one
        self._run = run
        await self._actuator.reset()
        self._policy.reset()
        self._cap_reached = False
        self._cameras = None
        if self._on_new_run is not None:
            self._on_new_run()

    async def step(self, step: Step) -> None:
        """Perceive, decide and act on one step (stages 3 to 6 of the module docstring)."""
        deadline = deadline_after(step.seen_at, self._clock.step_wall_s)
        calendar = asyncio.create_task(self._calendar(), name="calendar")
        try:
            found, jpegs = await self._perceive(deadline)
            await calendar
        finally:
            calendar.cancel()  # done already, unless perceiving failed
            await asyncio.gather(calendar, return_exceptions=True)
        counts = {zone: len(d.boxes) for zone, d in found.items()}
        self.counts = counts
        cameras = self._cameras or {}
        masks = {cameras[zone].id: d.polygons() for zone, d in found.items() if zone in cameras}
        self._publish(EventType.PERCEPTION, Perception(people=counts, masks=masks), step.start_s)
        began = time.perf_counter()
        commands = self._policy.commands(
            occupancy(counts), counts, step.end_s, cap_reached=self._cap_reached
        )
        held = self._actuator.held if self._narrator is not None else {}
        await self._actuator.apply(commands, step.start_s)
        now = time.perf_counter()
        self._metrics.add("actuate", now - began)
        self._metrics.add("step", now - step.seen_at)
        if self._notes is not None:  # after the writes: explanations only
            ids = {zone: camera.id for zone, camera in cameras.items()}
            self._notes.offer(jpegs, counts, ids, step.start_s)
        if self._narrator is not None:
            written = written_zones(held, self._actuator.held)
            decisions = [
                f"{ZONES[zone].display_name}: {counts.get(zone, 'unknown')} people counted; "
                f"{commands[zone].reason}"
                for zone in Zone
                if zone in written and zone in commands
            ]
            self._narrator.narrate(decisions, step.start_s)
        self.steps += 1
        seen = " ".join(f"{z.value}={counts.get(z, '?')}" for z in Zone)
        logger.info(f"step {clock_text(step.start_s)}: people {seen}")
        if self.steps % LOG_EVERY_STEPS == 0:
            logger.info(f"latency: {self._metrics.summary()}")

    async def _perceive(self, deadline: float) -> tuple[dict[Zone, Detections], dict[Zone, bytes]]:
        """Each zone's detections that came back by the deadline, and the snapshots taken.

        A zone without detections is unknown. The snapshots stay in memory, for the scene notes.
        """
        cameras = await self._zone_cameras()
        began = time.perf_counter()
        snapshots = await asyncio.gather(
            *(self._snapshot(camera, deadline) for camera in cameras.values())
        )
        self._metrics.add("snapshot", time.perf_counter() - began)
        jpegs = {zone: jpeg for zone, jpeg in zip(cameras, snapshots, strict=True) if jpeg}
        began = time.perf_counter()
        results = await asyncio.gather(
            *(self._detect(cameras[zone], jpeg, deadline) for zone, jpeg in jpegs.items())
        )
        self._metrics.add("detect", time.perf_counter() - began)
        seen = {zone: found for zone, found in zip(jpegs, results, strict=True) if found}
        return seen, jpegs

    async def _zone_cameras(self) -> Mapping[Zone, CameraInfo]:
        """Each zone's camera, from the catalog (fetched until it answers, then kept)."""
        if self._cameras is None:
            try:
                catalog = await self._world.cameras()
            except WorldHttpError as exc:
                self._failed("catalog", str(exc))
                return {}
            self._recovered("catalog")
            chosen: dict[Zone, CameraInfo] = {}
            for zone in Zone:
                listed = catalog.for_zone(zone)
                named = [c for c in listed if c.id == ZONES[zone].camera_id]
                if named or listed:
                    chosen[zone] = (named or listed)[0]
            self._cameras = chosen
        return self._cameras

    async def _snapshot(self, camera: CameraInfo, deadline: float) -> bytes | None:
        left = deadline - time.perf_counter()
        if left <= 0:
            self._metrics.late += 1
            return None
        try:
            jpeg = await self._world.snapshot(camera.snapshot_url, min(SNAPSHOT_TIMEOUT_S, left))
        except WorldHttpError as exc:
            self._failed(camera.id, str(exc))
            return None
        return jpeg

    async def _detect(self, camera: CameraInfo, jpeg: bytes, deadline: float) -> Detections | None:
        left = deadline - time.perf_counter()
        if left <= 0:
            self._metrics.late += 1
            return None
        try:
            found = await self._vision.detect(
                jpeg, self._concept, min(DETECT_TIMEOUT_S, left), camera_id=camera.id
            )
        except CallCapReached as exc:
            self._cap_reached = True
            self._failed(camera.id, str(exc))
            return None
        except VisionError as exc:
            self._failed(camera.id, f"{self._vision.name}: {exc}")
            return None
        except Exception as exc:  # a provider's bug: this zone is unknown, the step goes on
            self._failed(camera.id, f"{self._vision.name}: {type(exc).__name__}: {exc}")
            return None
        if time.perf_counter() > deadline:
            self._metrics.late += 1  # too late for this step's writes
            return None
        self._recovered(camera.id)
        return found

    async def _calendar(self) -> None:
        """Take every room's bookings; a room that does not answer keeps the ones known."""
        for room in ROOMS:
            try:
                schedule = await self._world.schedule(room)
                self._policy.calendar(room, schedule.scheduleItems)
            except WorldHttpError as exc:
                self._failed(room, str(exc))
                continue
            except ValueError as exc:  # a booking whose date cannot be read
                self._failed(room, f"unreadable bookings: {exc}")
                continue
            self._recovered(room)

    def _failed(self, what: str, message: str) -> None:
        if self._failing.get(what) != message:
            logger.warning(f"{what}: {message}")
        self._failing[what] = message

    def _recovered(self, what: str) -> None:
        if self._failing.pop(what, None) is not None:
            logger.info(f"{what}: answering again")


def written_zones(before: Mapping[str, object], after: Mapping[str, object]) -> set[Zone]:
    """The zones with a point whose held value changed between two ``Actuator.held``."""
    changed = {name for name in before.keys() | after.keys() if before.get(name) != after.get(name)}
    return {
        zone for zone in Zone if any(n.startswith(f"ZN-{zone.value.upper()}-") for n in changed)
    }
