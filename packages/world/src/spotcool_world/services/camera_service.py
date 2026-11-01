"""CameraService: produces CCTV snapshots for the camera catalog."""

from abc import ABC, abstractmethod


class CameraService(ABC):
    """Base of every camera provider (scenario clips, public datasets, live devices).

    Frames live in memory only and are never written to disk.
    """

    @abstractmethod
    async def snapshot(self, camera_id: str, sim_time_s: float) -> bytes:
        """Return one JPEG frame from a camera.

        The scene follows simulation time; the frame within a clip may follow wall time so
        the feed looks live.

        Args:
            camera_id: A camera id from the catalog.
            sim_time_s: Current simulation time.

        Returns:
            JPEG bytes.

        Raises:
            KeyError: If the camera is unknown.
        """
