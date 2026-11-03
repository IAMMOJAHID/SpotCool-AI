"""The world's CCTV cameras: one ``CameraService`` per camera, run together by ``CameraSystem``.

A ``CameraService`` is one camera's provider: ``services/clips/``'s ``ZoneCamera`` (filmed
clips, dataset segments or synthetic frames, picked by the scenario's scene) or
``services/live/``'s ``PhoneCamera`` (a real phone). ``CameraSystem`` runs the services from
``start()`` to ``stop()``; the HTTP router (``transports/http/cameras.py``) and the
``CameraSceneProcessor`` share one ``CameraSystem``.

Frames live in memory only: nothing here writes or logs them (AGENTS.md Law 6).
"""

import time
from abc import ABC, abstractmethod
from collections.abc import Sequence
from dataclasses import dataclass, field

from loguru import logger

from spotcool_contracts.cameras import CameraSource
from spotcool_contracts.zones import Zone
from spotcool_world.services.settings import WorldSettings
from spotcool_world.utils.base_object import BaseObject

STALE_AFTER_S = 5.0
"""Age (wall seconds) from which a snapshot is served with ``X-Camera-Stale: 1``."""


@dataclass(frozen=True)
class CameraSpec:
    """A camera's catalog entry without its URLs, which the HTTP router builds per request.

    Parameters:
        id: Camera id.
        name: Human-readable name.
        zone: The thermal zone it watches, if any.
        width: Width of the frames served, in pixels.
        height: Height of the frames served, in pixels.
        fps_hint: Rate at which polling makes sense, in frames per second.
        source: What feeds it now.
    """

    id: str
    name: str
    zone: Zone | None
    width: int
    height: int
    fps_hint: float
    source: CameraSource


@dataclass(frozen=True)
class Snapshot:
    """One encoded frame.

    Parameters:
        jpeg: The JPEG bytes, kept out of ``repr`` so that no log line can carry them.
        taken_s: When the frame was taken, ``time.perf_counter()``.
    """

    jpeg: bytes = field(repr=False)
    taken_s: float

    @property
    def age_s(self) -> float:
        """Wall seconds since the frame was taken."""
        return time.perf_counter() - self.taken_s


class CameraService(ABC):
    """Base of every camera provider (scenario clips, public datasets, live devices): one camera.

    Frames live in memory only and are never written to disk.
    """

    @property
    @abstractmethod
    def spec(self) -> CameraSpec:
        """The camera's catalog entry; its ``source`` may change with the scene."""

    @abstractmethod
    def latest(self) -> Snapshot | None:
        """The newest frame, or ``None`` before the first one."""

    @abstractmethod
    async def run(self) -> None:
        """Make frames until cancelled. A failing source is logged, never raised."""

    def set_scene(self, scene: str, people: int) -> None:
        """Show a scenario scene with ``people`` in it. Only zone cameras show scenes.

        Args:
            scene: The scene (``scenarios.schema.Scene``).
            people: How many people the scenario puts in it.

        Raises:
            TypeError: For a camera that shows no scenes (this default).
        """
        raise TypeError(f"camera {self.spec.id} shows no scenario scenes")


class CameraSystem(BaseObject):
    """The world's cameras, like a video recorder: one ``CameraService`` per camera.

    ``start()`` runs every camera as a background task and ``stop()`` cancels them and waits
    until each has released its source. A camera whose ``run()`` raises is logged without a
    traceback (frames may be in scope) and stays on its last frame.

    Args:
        cameras: The cameras, in catalog order.
        name: Display name.

    Raises:
        ValueError: If two cameras have the same id.
    """

    def __init__(self, cameras: Sequence[CameraService], *, name: str | None = None) -> None:
        super().__init__(name=name)
        self._cameras = {camera.spec.id: camera for camera in cameras}
        if len(self._cameras) != len(cameras):
            raise ValueError(f"{self.name}: camera ids must be unique")
        self._started = False

    @property
    def cameras(self) -> list[CameraSpec]:
        """Every camera's catalog entry, in catalog order."""
        return [camera.spec for camera in self._cameras.values()]

    def snapshot(self, camera_id: str) -> Snapshot | None:
        """A camera's newest frame, or ``None`` before its first one.

        Args:
            camera_id: The camera.

        Raises:
            KeyError: If there is no such camera.
        """
        return self._cameras[camera_id].latest()

    def set_scene(self, camera_id: str, scene: str, people: int) -> None:
        """Show a scenario scene on a zone camera (what a ``ScenarioStateFrame`` asks for).

        Args:
            camera_id: The camera.
            scene: The scene.
            people: How many people the scenario puts in it.

        Raises:
            KeyError: If there is no such camera.
            TypeError: If the camera shows no scenes.
        """
        self._cameras[camera_id].set_scene(scene, people)

    async def start(self) -> None:
        """Start every camera; does nothing if started. Call it before the run starts."""
        if self._started:
            return
        self._started = True
        for camera_id, camera in self._cameras.items():
            self.create_task(self._run(camera), camera_id)

    async def stop(self) -> None:
        """Stop every camera and wait for it to release its source. Safe to call twice."""
        self._started = False
        await self.cancel_tasks()

    async def _run(self, camera: CameraService) -> None:
        try:
            await camera.run()
        except Exception as exc:  # no traceback: frames may be in its locals (Law 6)
            logger.error(
                f"{self.name}: camera {camera.spec.id} stopped: {type(exc).__name__}: {exc}"
            )


def create_cameras(settings: WorldSettings) -> CameraSystem:
    """The world's cameras from the settings (the ``cameras`` extra).

    The five zone cameras play ``footage_dir/clips/`` and the dataset indexes in
    ``footage_dir/datasets/*/index.yaml``, or synthetic frames without ``footage_dir``. The live
    phone camera is added when ``live_camera_url`` is set. Providers are imported here, so the
    core runs without the extra.

    Args:
        settings: ``footage_dir`` and ``live_camera_url``.

    Raises:
        ValueError: If ``live_camera_url`` is not an http or https URL with a host.
    """
    from spotcool_world.services.clips import ZoneCamera
    from spotcool_world.services.datasets import load_indexes
    from spotcool_world.services.live import PhoneCamera

    footage = settings.footage_dir
    datasets = load_indexes(footage / "datasets") if footage else []
    clips = footage / "clips" if footage else None
    cameras: list[CameraService] = [
        ZoneCamera(zone, clips_dir=clips, datasets=datasets) for zone in Zone
    ]
    if settings.live_camera_url:
        cameras.append(PhoneCamera(settings.live_camera_url))
    return CameraSystem(cameras)
