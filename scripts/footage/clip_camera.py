"""Serve a recorded clip as a phone camera, for the meeting room's live camera.

    uv run python scripts/footage/clip_camera.py CLIP.mp4 [--port 8099]

The world polls ``SPOTCOOL_WORLD_LIVE_CAMERA_URL`` for one JPEG at a time, the way it polls the
IP Webcam app on a phone (``services/live/phone.py``). This plays CLIP in a loop on the wall
clock and answers ``GET /shot.jpg`` with the frame it is on. Point the world at it with
``SPOTCOOL_WORLD_LIVE_CAMERA_URL=http://127.0.0.1:8099/shot.jpg``.

SAM 3.1 and the louvers still run live on these frames. Only the camera's source is a
recording, so the video says so. Frames stay in memory, and nothing is written (Law 6).
Needs the world's extras (OpenCV).
"""

import argparse
import os
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

import cv2

JPEG_QUALITY = 85


class ClipPlayer:
    """A clip played in a loop on the wall clock; ``jpeg()`` is the current frame."""

    def __init__(self, path: Path, clock=time.monotonic) -> None:
        self._path, self._clock = str(path), clock
        self._lock = threading.Lock()
        self._capture = cv2.VideoCapture(self._path)
        if not self._capture.isOpened():
            raise SystemExit(f"cannot open {path}")
        self._fps = self._capture.get(cv2.CAP_PROP_FPS) or 30.0
        self._frames = int(self._capture.get(cv2.CAP_PROP_FRAME_COUNT)) or 1
        self._next = 0  # the index of the frame read() returns next
        self._last: bytes | None = None
        self._started = clock()

    def jpeg(self) -> bytes:
        """The frame the clip is on now, as a JPEG."""
        with self._lock:
            want = int((self._clock() - self._started) * self._fps) % self._frames
            if want < self._next - 1:
                self._capture.set(cv2.CAP_PROP_POS_FRAMES, 0)  # looped: back to the start
                self._next = 0
            frame = None
            while self._next <= want:
                ok, frame = self._capture.read()
                self._next += 1
                if not ok:  # the count overstated the clip: loop from here
                    self._frames = max(self._next - 1, 1)
                    self._capture.set(cv2.CAP_PROP_POS_FRAMES, 0)
                    self._next = 0
                    break
            if frame is not None:
                ok, encoded = cv2.imencode(".jpg", frame, [cv2.IMWRITE_JPEG_QUALITY, JPEG_QUALITY])
                if ok:
                    self._last = encoded.tobytes()
            if self._last is None:
                raise RuntimeError("no frame decoded yet")
            return self._last


def serve(player: ClipPlayer, port: int) -> ThreadingHTTPServer:
    """An HTTP server on 127.0.0.1:port that answers ``/shot.jpg`` with the player's frame."""

    class Handler(BaseHTTPRequestHandler):
        def do_GET(self) -> None:  # noqa: N802 (http.server's name)
            if self.path.split("?")[0] != "/shot.jpg":
                self.send_error(404)
                return
            body = player.jpeg()
            self.send_response(200)
            self.send_header("Content-Type", "image/jpeg")
            self.send_header("Content-Length", str(len(body)))
            self.send_header("Cache-Control", "no-store")
            self.end_headers()
            self.wfile.write(body)

        def log_message(self, *args: object) -> None:  # quiet: the world polls twice a second
            pass

    class Server(ThreadingHTTPServer):
        # On Windows, SO_REUSEADDR lets a second camera share a port while the first keeps
        # answering, so a forgotten one silently plays the wrong clip. Refuse to start instead.
        allow_reuse_address = os.name != "nt"

    return Server(("127.0.0.1", port), Handler)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("clip", type=Path, help="the recorded clip (any video OpenCV reads)")
    parser.add_argument("--port", type=int, default=8099)
    args = parser.parse_args()
    server = serve(ClipPlayer(args.clip), args.port)
    print(
        f"clip camera: http://127.0.0.1:{args.port}/shot.jpg plays {args.clip.name} (Ctrl+C stops)"
    )
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()


if __name__ == "__main__":
    main()
