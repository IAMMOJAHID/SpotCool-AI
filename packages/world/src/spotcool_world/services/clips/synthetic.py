"""Synthetic camera frames: a plain room with one filled silhouette per person.

They stand in for footage in tests, CI and an emergency demo. Every person has a distinct,
saturated colour on an unsaturated room, so a test counts people as colour blobs; a person
detector is not expected to see the silhouettes as people.
"""

import math

import cv2
import numpy as np
import numpy.typing as npt

WIDTH, HEIGHT = 1280, 720
"""Size of every zone camera's frames, the size WP8 normalizes clips to."""

_HUES = (0, 120, 60, 150, 30, 90, 15, 135, 45, 105, 165, 75)
"""OpenCV hues (0-179) of the people's colours, ordered so neighbours differ the most."""
_WALL = (200, 200, 200)
_FLOOR = (150, 150, 150)
_BAR = (40, 40, 40)
_TEXT = (255, 255, 255)
_BAR_HEIGHT = 56
_COLUMNS = 6
_SWAY_FRAMES = 25  # one sway period, 5 s at 5 fps


def _colour(k: int) -> tuple[int, int, int]:
    """The BGR colour of the ``k``-th person; colours repeat after twelve people."""
    hsv = np.array([[[_HUES[k % len(_HUES)], 255, 230]]], np.uint8)
    blue, green, red = cv2.cvtColor(hsv, cv2.COLOR_HSV2BGR)[0, 0]
    return int(blue), int(green), int(red)


def synthetic_frame(camera_id: str, scene: str, people: int, index: int) -> npt.NDArray[np.uint8]:
    """One BGR frame of a room with ``people`` silhouettes and a caption.

    People stand in rows of up to six and sway a little from frame to frame, so the feed looks
    live. The same arguments always give the same pixels.

    Args:
        camera_id: Shown in the caption.
        scene: Shown in the caption.
        people: How many silhouettes to draw.
        index: Frame number since the scene began; it sets the sway.
    """
    image = np.empty((HEIGHT, WIDTH, 3), np.uint8)
    image[:] = _WALL
    image[int(HEIGHT * 0.62) :] = _FLOOR
    if people > 0:
        columns = min(people, _COLUMNS)
        rows = math.ceil(people / columns)
        cell_w, cell_h = WIDTH / columns, (HEIGHT - _BAR_HEIGHT - 30) / rows
        size = min(cell_w / 2.2, cell_h / 2.8)
        for k in range(people):
            row, column = divmod(k, columns)
            phase = 2 * math.pi * index / _SWAY_FRAMES + k
            x = int(cell_w * (column + 0.5) + size * 0.08 * math.sin(phase))
            top = int(_BAR_HEIGHT + 15 + cell_h * row + (cell_h - 2.6 * size) / 2)
            head = int(size * 0.4)
            colour = _colour(k)
            cv2.circle(image, (x, top + head), head, colour, -1, cv2.LINE_AA)
            body = (x, top + int(2 * head + size * 0.9))
            cv2.ellipse(
                image, body, (int(size * 0.75), int(size)), 0, 0, 360, colour, -1, cv2.LINE_AA
            )
    cv2.rectangle(image, (0, 0), (WIDTH, _BAR_HEIGHT), _BAR, -1)
    caption = f"{camera_id}  {scene}  (synthetic)"
    cv2.putText(image, caption, (16, 38), cv2.FONT_HERSHEY_SIMPLEX, 1.0, _TEXT, 2, cv2.LINE_AA)
    return image
