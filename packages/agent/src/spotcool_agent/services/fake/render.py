"""FakeRender: a small solid JPEG for every render, for tests and offline rehearsals.

It never looks at the image. It counts its calls by camera, never keeping the frame;
``delay_s`` makes it slow.
"""

import asyncio
import io

from PIL import Image

from spotcool_agent.services.render import RenderError, RenderService


def solid_jpeg(
    size: tuple[int, int] = (64, 36), colour: tuple[int, int, int] = (96, 140, 180)
) -> bytes:
    """A JPEG of one colour, in memory."""
    buffer = io.BytesIO()
    Image.new("RGB", size, colour).save(buffer, format="JPEG")
    return buffer.getvalue()


class FakeRender(RenderService):
    """A scripted render (see the module docstring).

    Args:
        delay_s: Wall seconds each render takes.
    """

    def __init__(self, *, delay_s: float = 0.0) -> None:
        super().__init__(name="FakeRender")
        self.delay_s = delay_s
        self.image = solid_jpeg()
        self.asked: list[str] = []
        """Each call's camera."""

    async def render(self, jpeg: bytes, timeout_s: float, *, camera_id: str) -> bytes:
        """The solid JPEG (see ``RenderService.render``)."""
        self.asked.append(camera_id)
        try:
            async with asyncio.timeout(timeout_s):
                await asyncio.sleep(self.delay_s)
        except TimeoutError:
            raise RenderError(f"no answer within {timeout_s:.1f} s") from None
        return self.image
