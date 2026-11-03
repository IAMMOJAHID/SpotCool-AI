"""The camera system over HTTP, shaped like a video recorder's API (the ``cameras`` extra).

- ``GET /cameras``: the ``CameraCatalog``. Each ``snapshot_url`` is absolute and built from the
  request, so it is right for whoever asked.
- ``GET /cameras/{camera_id}/snapshot.jpg``: the camera's latest frame as ``image/jpeg`` with
  ``Cache-Control: no-store``, the same idea as ONVIF ``GetSnapshotUri``. An unknown camera
  answers 404, a camera without its first frame 503. A frame 5 s old or older carries
  ``X-Camera-Stale: 1``.
- ``GET /cameras/{camera_id}/mjpeg``: ``multipart/x-mixed-replace`` for the dashboard's tiles:
  each new frame once, at most 5 a second. It waits for the first frame, and ends when the
  client disconnects or the server stops (``app.state.closing``, see ``create_http_app``). It
  holds one frame at a time, so a slow client never makes it buffer more.

A camera id is lower-case letters, digits and dashes; any other id answers 422.
"""

import asyncio
from collections.abc import AsyncIterator
from dataclasses import asdict
from typing import Annotated

from fastapi import APIRouter, HTTPException, Path, Request, Response
from fastapi.responses import StreamingResponse

from spotcool_contracts.cameras import CATALOG_PATH, SNAPSHOT_PATH, CameraCatalog, CameraInfo
from spotcool_world.services.camera_service import STALE_AFTER_S, CameraSystem, Snapshot

MJPEG_PATH = "/cameras/{camera_id}/mjpeg"
"""Path of a camera's MJPEG stream."""
MJPEG_FPS = 5.0
"""The most frames a second an MJPEG stream sends."""
MJPEG_BOUNDARY = "frame"
"""The multipart boundary between MJPEG parts."""

CameraId = Annotated[str, Path(pattern=r"^[a-z0-9][a-z0-9-]{0,63}$")]


def cameras_router(cameras: CameraSystem) -> APIRouter:
    """The camera routes, serving ``cameras``.

    Args:
        cameras: The camera system, the one the ``CameraSceneProcessor`` sets scenes on.
    """
    router = APIRouter()

    @router.get(CATALOG_PATH)
    async def catalog(request: Request) -> CameraCatalog:
        """Every camera, with URLs for the asker."""
        return CameraCatalog(
            cameras=[
                CameraInfo(
                    **asdict(spec),
                    snapshot_url=str(request.url_for("camera_snapshot", camera_id=spec.id)),
                )
                for spec in cameras.cameras
            ]
        )

    @router.get(
        SNAPSHOT_PATH,
        name="camera_snapshot",
        response_class=Response,
        responses={200: {"content": {"image/jpeg": {}}, "description": "The latest frame."}},
    )
    async def snapshot(camera_id: CameraId) -> Response:
        """The camera's latest frame."""
        frame = _latest(cameras, camera_id)
        if frame is None:
            raise HTTPException(503, f"camera {camera_id} has no frame yet")
        headers = {"Cache-Control": "no-store"}
        if frame.age_s >= STALE_AFTER_S:
            headers["X-Camera-Stale"] = "1"
        return Response(frame.jpeg, media_type="image/jpeg", headers=headers)

    @router.get(MJPEG_PATH, name="camera_mjpeg", response_class=StreamingResponse)
    async def mjpeg(camera_id: CameraId, request: Request) -> StreamingResponse:
        """The camera's frames as a multipart MJPEG stream."""
        _latest(cameras, camera_id)  # an unknown camera answers 404 before the stream starts
        closing = getattr(request.app.state, "closing", None)
        return StreamingResponse(
            _parts(cameras, camera_id, closing),
            media_type=f"multipart/x-mixed-replace; boundary={MJPEG_BOUNDARY}",
            headers={"Cache-Control": "no-store"},
        )

    return router


def _latest(cameras: CameraSystem, camera_id: str) -> Snapshot | None:
    """The camera's latest frame; an unknown camera raises a 404."""
    try:
        return cameras.snapshot(camera_id)
    except KeyError:
        known = ", ".join(spec.id for spec in cameras.cameras)
        raise HTTPException(404, f"unknown camera {camera_id!r}; cameras: {known}") from None


async def _parts(
    cameras: CameraSystem, camera_id: str, closing: asyncio.Event | None
) -> AsyncIterator[bytes]:
    """Each new frame of the camera as one multipart part, until ``closing`` is set."""
    sent: Snapshot | None = None
    while closing is None or not closing.is_set():
        frame = cameras.snapshot(camera_id)
        if frame is not None and frame is not sent:
            sent = frame
            head = (
                f"--{MJPEG_BOUNDARY}\r\nContent-Type: image/jpeg\r\n"
                f"Content-Length: {len(frame.jpeg)}\r\n\r\n"
            )
            yield head.encode() + frame.jpeg + b"\r\n"
        await asyncio.sleep(1 / MJPEG_FPS)
