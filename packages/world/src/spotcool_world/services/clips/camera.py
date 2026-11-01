"""ZoneCamera: a zone's CCTV camera, showing the scene the scenario asks for."""

import asyncio
import threading
import time
from collections.abc import Sequence
from dataclasses import dataclass
from pathlib import Path

import cv2
import numpy as np
import numpy.typing as npt
from loguru import logger

from spotcool_contracts.cameras import CameraSource
from spotcool_contracts.zones import ZONES, Zone
from spotcool_world.services.camera_service import CameraService, CameraSpec, Snapshot
from spotcool_world.services.clips.synthetic import HEIGHT, WIDTH, synthetic_frame
from spotcool_world.services.clips.video import VideoLoop
from spotcool_world.services.datasets import DatasetIndex

JPEG_QUALITY = 80
"""Quality of the encoded snapshots."""


@dataclass(frozen=True)
class _Footage:
    """Where a scene's frames come from: a video segment, or synthetic frames without a path."""

    source: CameraSource
    label: str
    path: Path | None = None
    start_s: float = 0.0
    end_s: float | None = None


_SYNTHETIC = _Footage(CameraSource.SCENARIO, "synthetic frames")


class ZoneCamera(CameraService):
    """A zone's camera (``ZONES[zone].camera_id``), showing the scenario's scene for the zone.

    For each scene, the frames come from the first source that opens:

    1. our clip ``clips_dir/<zone>_<scene>.mp4``;
    2. the scene's segment for this camera in a dataset index, trying the indexes in order;
    3. synthetic frames, which draw the scenario's people count.

    The catalog's ``source`` is ``dataset`` for a dataset segment, else ``scenario``. The source
    is logged once per scene, and again if it fails and synthetic frames take over.

    The scene follows simulation time (``set_scene``), the frames follow wall time, as a real
    camera does while the simulation runs faster. A new scene starts at its first frame, then
    plays in real time and loops. Clips are not time-compressed: at ``speed=300`` a 15-minute
    step lasts 3 s, so a scene shows only its clip's first seconds. A run that must show a
    clip's action (the 14:15 complaint) runs at ``speed=60`` or slower.

    ``run()`` makes ``snapshot_fps`` frames a wall second in a worker thread, because OpenCV
    blocks: it skips the clip's frames in between with ``grab()``, encodes the frame shown as a
    1280x720 JPEG (quality 80) and keeps only the latest. No frame exists until the first
    scene is set.

    Args:
        zone: The zone the camera watches.
        clips_dir: Our clips (``footage_dir/clips``), or ``None``.
        datasets: Dataset indexes, in the order they are tried.
        snapshot_fps: Frames made per wall second, the catalog's ``fps_hint``.

    Raises:
        ValueError: If ``snapshot_fps`` is not positive.
    """

    def __init__(
        self,
        zone: Zone,
        *,
        clips_dir: Path | None = None,
        datasets: Sequence[DatasetIndex] = (),
        snapshot_fps: float = 5.0,
    ) -> None:
        if not snapshot_fps > 0:
            raise ValueError(f"snapshot_fps must be positive, got {snapshot_fps}")
        info = ZONES[zone]
        self._zone = zone
        self._id = info.camera_id
        self._name = f"{info.display_name}: {info.role}"
        self._clips_dir = clips_dir
        self._datasets = tuple(datasets)
        self._fps = snapshot_fps
        self._wanted: tuple[str, int, float] | None = None  # scene, people, when the scene began
        self._latest: Snapshot | None = None
        self._source = CameraSource.SCENARIO
        # The render thread's state; the lock keeps closing from overlapping a render.
        self._lock = threading.Lock()
        self._footage: dict[str, _Footage] = {}
        self._showing: str | None = None
        self._video: VideoLoop | None = None
        self._closed = False  # set by _close: a render that ends late opens no video

    @property
    def spec(self) -> CameraSpec:
        """The camera's catalog entry; ``source`` is that of the scene shown."""
        return CameraSpec(self._id, self._name, self._zone, WIDTH, HEIGHT, self._fps, self._source)

    def latest(self) -> Snapshot | None:
        """The newest frame, or ``None`` before the first scene's first frame."""
        return self._latest

    def set_scene(self, scene: str, people: int) -> None:
        """Show ``scene`` from the next frame on.

        A new scene starts at its first frame; a new people count in the same scene changes the
        synthetic frames only, and the playback goes on.

        Args:
            scene: The scene (``scenarios.schema.Scene``).
            people: How many people the scenario puts in it.
        """
        wanted = self._wanted
        if wanted is None or wanted[0] != scene:
            self._wanted = (scene, people, time.perf_counter())
        elif wanted[1] != people:
            self._wanted = (scene, people, wanted[2])

    async def run(self) -> None:
        """Make frames until cancelled, then release the video."""
        period = 1.0 / self._fps
        failing = False
        self._closed = False
        try:
            while True:
                now = time.perf_counter()
                if (wanted := self._wanted) is not None:
                    try:
                        jpeg = await asyncio.to_thread(self._render, *wanted, now)
                    except Exception as exc:  # no traceback: frames are in scope (Law 6)
                        if not failing:
                            logger.warning(
                                f"camera {self._id}: no frame: {type(exc).__name__}: {exc}"
                            )
                        failing = True
                    else:
                        self._latest = Snapshot(jpeg, now)
                        failing = False
                await asyncio.sleep(max(0.0, period - (time.perf_counter() - now)))
        finally:
            await asyncio.to_thread(self._close)

    def _render(self, scene: str, people: int, began_s: float, now_s: float) -> bytes:
        """The frame for wall time ``now_s``, as JPEG bytes. Runs in a worker thread."""
        with self._lock:
            if scene != self._showing and not self._closed:
                self._show(scene)
            image = self._frame(scene, people, now_s - began_s)
        if image.shape[:2] != (HEIGHT, WIDTH):
            image = cv2.resize(image, (WIDTH, HEIGHT), interpolation=cv2.INTER_AREA)
        ok, jpeg = cv2.imencode(".jpg", image, [cv2.IMWRITE_JPEG_QUALITY, JPEG_QUALITY])
        if not ok:
            raise RuntimeError("JPEG encoding failed")
        return jpeg.tobytes()

    def _frame(self, scene: str, people: int, elapsed_s: float) -> npt.NDArray[np.uint8]:
        if self._video is not None:
            try:
                return self._video.frame(int(elapsed_s * self._video.fps))
            except (OSError, cv2.error) as exc:
                label = self._footage[scene].label
                logger.warning(
                    f"camera {self._id}: {label} failed ({type(exc).__name__}: {exc}); "
                    f"scene {scene} from synthetic frames"
                )
                self._close_video()
                self._footage[scene] = _SYNTHETIC
                self._source = _SYNTHETIC.source
        return synthetic_frame(self._id, scene, people, int(elapsed_s * self._fps))

    def _show(self, scene: str) -> None:
        """Switch to a scene: open its known source, or the first of its candidates that opens."""
        self._close_video()
        self._showing = scene
        known = self._footage.get(scene)
        chosen = _SYNTHETIC
        for footage in (known,) if known else self._candidates(scene):
            if footage.path is None:
                break
            try:
                self._video = VideoLoop(footage.path, footage.start_s, footage.end_s)
            except (OSError, cv2.error) as exc:
                logger.warning(f"camera {self._id}: cannot play {footage.label}: {exc}")
                continue
            chosen = footage
            break
        if chosen != known:
            logger.info(f"camera {self._id}: scene {scene} from {chosen.label}")
        self._footage[scene] = chosen
        self._source = chosen.source

    def _candidates(self, scene: str) -> list[_Footage]:
        """A scene's sources in the order they are tried; synthetic frames come last."""
        found: list[_Footage] = []
        if self._clips_dir is not None:
            clip = self._clips_dir / f"{self._zone.value}_{scene}.mp4"
            if clip.parent == self._clips_dir and clip.is_file():
                found.append(_Footage(CameraSource.SCENARIO, f"clip {clip}", clip))
        for index in self._datasets:
            if (segment := index.segment(self._id, scene)) is not None:
                where = f"{segment.path.name} {segment.start_s:g}-{segment.end_s:g} s"
                found.append(
                    _Footage(
                        CameraSource.DATASET,
                        f"dataset {index.name} {where}",
                        segment.path,
                        segment.start_s,
                        segment.end_s,
                    )
                )
        return [*found, _SYNTHETIC]

    def _close(self) -> None:
        with self._lock:
            self._close_video()
            self._showing = None
            self._closed = True

    def _close_video(self) -> None:
        if self._video is not None:
            self._video.close()
            self._video = None
