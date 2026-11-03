import cv2
import pytest

from spotcool_agent.services.fake.blob import BlobVision
from spotcool_agent.services.fake.scripted import ScriptedVision
from spotcool_agent.services.vision import VisionError
from spotcool_world.services.clips.camera import JPEG_QUALITY
from spotcool_world.services.clips.synthetic import synthetic_frame


def frame(people: int, index: int = 0) -> bytes:
    image = synthetic_frame("cam-sou", "meeting", people, index)
    ok, jpeg = cv2.imencode(".jpg", image, [cv2.IMWRITE_JPEG_QUALITY, JPEG_QUALITY])
    assert ok
    return jpeg.tobytes()


@pytest.mark.asyncio
@pytest.mark.parametrize("people", range(13))
async def test_the_blob_counter_is_exact_on_the_worlds_synthetic_frames(people: int) -> None:
    vision = BlobVision()
    for index in (0, 7, 13):  # the silhouettes sway from frame to frame
        found = await vision.detect(frame(people, index), "humans", 5.0)
        assert len(found.boxes) == people
    assert (found.width, found.height) == (1280, 720)
    for polygon in found.polygons():
        assert len(polygon) == 4 and all(0 <= x <= 1280 and 0 <= y <= 720 for x, y in polygon)


@pytest.mark.asyncio
async def test_the_blob_counter_refuses_what_is_not_an_image_or_has_no_time() -> None:
    with pytest.raises(VisionError, match="not an image"):
        await BlobVision().detect(b"nope", "humans", 5.0)
    with pytest.raises(VisionError, match="no time left"):
        await BlobVision().detect(frame(1), "humans", 0.0)


@pytest.mark.asyncio
async def test_scripted_counts_are_per_camera_and_none_fails() -> None:
    vision = ScriptedVision({"cam-sou": 3, "cam-cor": None}, default=1)
    assert len((await vision.detect(b"", "humans", 1.0, camera_id="cam-sou")).boxes) == 3
    assert len((await vision.detect(b"", "humans", 1.0, camera_id="cam-nor")).boxes) == 1
    with pytest.raises(VisionError):
        await vision.detect(b"", "humans", 1.0, camera_id="cam-cor")
