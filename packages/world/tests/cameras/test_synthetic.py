from collections.abc import Callable

import numpy as np
import pytest

from spotcool_world.services.clips import HEIGHT, WIDTH, synthetic_frame


@pytest.mark.parametrize("people", [0, 1, 2, 4, 5, 9, 13])
def test_the_people_count_is_decodable_as_colour_blobs(
    people: int, encode: Callable[..., bytes], count_people: Callable[[bytes], int]
) -> None:
    image = synthetic_frame("cam-cor", "many", people, index=7)
    assert image.shape == (HEIGHT, WIDTH, 3) == (720, 1280, 3)
    assert count_people(encode(image)) == people


def test_frames_are_deterministic_and_sway() -> None:
    first = synthetic_frame("cam-sou", "meeting", 5, index=3)
    assert np.array_equal(first, synthetic_frame("cam-sou", "meeting", 5, index=3))
    assert not np.array_equal(first, synthetic_frame("cam-sou", "meeting", 5, index=9))
    assert not np.array_equal(first, synthetic_frame("cam-sou", "meeting", 4, index=3))
    assert not np.array_equal(first, synthetic_frame("cam-sou", "complaint", 5, index=3))
