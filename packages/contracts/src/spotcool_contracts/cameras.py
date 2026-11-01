"""Camera catalog served by the world's camera system.

The catalog plays the role of a video management system's camera list: each camera has an
HTTP snapshot URL (the same idea as ONVIF ``GetSnapshotUri``) and optionally an RTSP stream.
Real cameras plug into the agent by publishing the same catalog shape.
"""

from enum import StrEnum

from pydantic import BaseModel, ConfigDict, Field

from spotcool_contracts.zones import Zone

DEFAULT_CAMERA_PORT = 8081
CATALOG_PATH = "/cameras"
SNAPSHOT_PATH = "/cameras/{camera_id}/snapshot.jpg"


class CameraSource(StrEnum):
    """What feeds a camera."""

    SCENARIO = "scenario"
    """Recorded clips chosen by the scenario script."""
    DATASET = "dataset"
    """Public dataset footage (WiseNET, MEVA)."""
    LIVE = "live"
    """A real device, e.g. an Android phone running an IP-camera app."""


class CameraInfo(BaseModel):
    """One camera in the catalog.

    Parameters:
        id: Stable camera identifier.
        name: Human-readable camera name.
        zone: Thermal zone the camera covers, if any.
        snapshot_url: Absolute URL that returns one JPEG frame.
        stream_url: Optional RTSP URL for continuous video.
        width: Frame width in pixels.
        height: Frame height in pixels.
        fps_hint: Rate at which polling makes sense, in frames per second.
        source: What feeds the camera.
    """

    model_config = ConfigDict(extra="forbid")

    id: str = Field(min_length=1)
    name: str
    zone: Zone | None = None
    snapshot_url: str
    stream_url: str | None = None
    width: int = Field(gt=0)
    height: int = Field(gt=0)
    fps_hint: float = Field(gt=0)
    source: CameraSource


class CameraCatalog(BaseModel):
    """Response body of ``GET /cameras``.

    Parameters:
        cameras: Every camera the system exposes.
        privacy_notice: Shown to operators. Frames are processed in memory and never stored.
    """

    model_config = ConfigDict(extra="forbid")

    cameras: list[CameraInfo]
    privacy_notice: str = "Frames are processed in memory and never stored; only counts leave."

    def for_zone(self, zone: Zone) -> list[CameraInfo]:
        """Return the cameras covering a zone."""
        return [c for c in self.cameras if c.zone is zone]
