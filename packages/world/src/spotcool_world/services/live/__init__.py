"""The live camera (the ``cameras`` extra): a real phone polled over HTTP, ``cam-meeting-live``."""

from spotcool_world.services.live.phone import LIVE_CAMERA_ID, PhoneCamera

__all__ = ["LIVE_CAMERA_ID", "PhoneCamera"]
