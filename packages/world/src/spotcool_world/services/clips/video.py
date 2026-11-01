"""VideoLoop: a segment of a video file, read frame by frame and looped."""

from pathlib import Path

import cv2
import numpy as np
import numpy.typing as npt


class VideoLoop:
    """Plays ``[start_s, end_s)`` of a video file (all of it by default) in a loop.

    ``frame(index)`` decodes forward to the wanted frame, skipping the ones in between with
    ``grab()`` (no colour conversion), and goes back to the segment's start when the index wraps.
    Every call blocks (OpenCV): make them from a worker thread, one at a time.

    Args:
        path: The video file.
        start_s: Where the segment starts, in seconds from the start of the file.
        end_s: Where it ends; ``None`` plays to the end of the file.

    Raises:
        OSError: If the file cannot be opened, or the segment holds no frames.
    """

    def __init__(self, path: Path, start_s: float = 0.0, end_s: float | None = None) -> None:
        capture = cv2.VideoCapture(str(path), cv2.CAP_FFMPEG)
        if not capture.isOpened():
            capture.release()
            raise OSError(f"cannot open {path}")
        rate = capture.get(cv2.CAP_PROP_FPS)
        self.fps = rate if rate > 0 else 30.0
        """Frames per second of the file."""
        total = int(capture.get(cv2.CAP_PROP_FRAME_COUNT))
        self._first = round(start_s * self.fps)
        last = round(end_s * self.fps) if end_s is not None else total
        if total > 0:
            last = min(last, total)
        # None: the header gives no frame count, so the end is found by reading to it.
        self._length: int | None = last - self._first if last > 0 else None
        if self._length is not None and self._length <= 0:
            capture.release()
            raise OSError(f"{path} has no frames between {start_s} and {end_s} s")
        self._capture = capture
        self._next: int | None = None  # the segment frame read() returns next; None: unknown

    def frame(self, index: int) -> npt.NDArray[np.uint8]:
        """The segment's frame ``index``, counted from its start; indexes past its end wrap.

        Args:
            index: Frame number, 0 or more.

        Raises:
            OSError: If the video yields no frame.
        """
        while True:
            if self._length:
                index %= self._length
            if self._next is None or index < self._next:
                self._capture.set(cv2.CAP_PROP_POS_FRAMES, self._first)
                self._next = 0
            while self._next < index and self._capture.grab():
                self._next += 1
            if self._next == index:
                ok, image = self._capture.read()
                if ok:
                    self._next += 1
                    return np.asarray(image, np.uint8)
            # The file ended before frame `index`: the segment is as long as what was read.
            if self._next == 0:
                raise OSError("the video yields no frames")
            self._length = self._next

    def close(self) -> None:
        """Release the file."""
        self._capture.release()
