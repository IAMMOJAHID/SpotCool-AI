"""Zone cameras (the ``cameras`` extra): our clips, dataset segments or synthetic frames.

``ZoneCamera`` shows the scenario's scene for its zone; ``VideoLoop`` plays a video segment in
a loop and ``synthetic_frame`` draws the fallback frames.
"""

from spotcool_world.services.clips.camera import JPEG_QUALITY, ZoneCamera
from spotcool_world.services.clips.synthetic import HEIGHT, WIDTH, synthetic_frame
from spotcool_world.services.clips.video import VideoLoop

__all__ = ["HEIGHT", "JPEG_QUALITY", "WIDTH", "VideoLoop", "ZoneCamera", "synthetic_frame"]
