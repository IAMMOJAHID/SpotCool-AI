"""MuseImageRender: a camera snapshot redrawn by ``muse-image-1.0`` as a faceless illustration.

The request is Person A's (``render_privacy_snapshot`` in the MVP's ``app/spotcool/media.py``
on branch ``third_level_improvement``): ``images.edit`` with the frame as ``frame.jpg``, A's
``PRIVACY_RENDER_PROMPT`` and ``n=1``. The lead's probe (Nov 4) took 24 s and answered with a
280 KB ``b64_json`` and no URL. The image that comes back is checked and kept as a JPEG,
in memory only.

One request, a timeout of ``TIMEOUT_S``, no retries: the next round renders again. Each call
takes a turn of the renders' own ``MetaClient`` (1 at a time, ``--render-call-cap``). The trace
gives the frame's size and the render's size, never the image or its base64.
"""

import asyncio
import base64
import binascii
import io

import openai
from PIL import Image

from spotcool_agent.services.meta.client import MetaClient
from spotcool_agent.services.render import RenderError, RenderService
from spotcool_agent.services.vision import VisionError, image_size

RENDER_MODEL = "muse-image-1.0"
TIMEOUT_S = 45.0
"""The longest one render may take (the lead's probe took 24 s)."""
PRIVACY_RENDER_PROMPT = (
    "Re-render this CCTV scene as a clean, flat isometric illustration for a building operations dashboard. "
    "Keep the room layout, furniture, doors, windows and the position, posture and clothing colours of every person. "
    "People must be faceless, featureless figures. Do not add or remove people or objects and do not add any text."
)  # fmt: skip
"""Person A's prompt, word for word."""


def as_jpeg(image: bytes) -> tuple[bytes, str]:
    """The render as JPEG bytes, and how it came back (format, size), for the trace.

    Raises:
        RenderError: If the bytes are not an image.
    """
    try:
        with Image.open(io.BytesIO(image)) as picture:
            came = f"{picture.format} {picture.width}x{picture.height}"
            if picture.format == "JPEG":
                return image, came
            buffer = io.BytesIO()
            picture.convert("RGB").save(buffer, format="JPEG", quality=85)
            return buffer.getvalue(), came
    except (OSError, ValueError) as exc:  # PIL's UnidentifiedImageError is an OSError
        raise RenderError(f"the answer is not an image: {type(exc).__name__}") from None


class MuseImageRender(RenderService):
    """Renders with ``muse-image-1.0`` (see the module docstring).

    Args:
        client: The renders' Meta client: its limiter, ledger and call cap.
        model: The image model.
    """

    def __init__(self, client: MetaClient, *, model: str = RENDER_MODEL) -> None:
        super().__init__(name=f"MuseImageRender({model})")
        self._client = client
        self._model = model

    async def render(self, jpeg: bytes, timeout_s: float = TIMEOUT_S, *, camera_id: str) -> bytes:
        """Ask Muse Image for the privacy render (see ``RenderService.render``)."""
        if timeout_s <= 0:
            raise RenderError("no time left for the call")
        try:
            width, height = image_size(jpeg)
        except VisionError as exc:
            raise RenderError(str(exc)) from None
        sent = (
            f"JPEG {width}x{height}, {round(len(jpeg) / 1024)} KB "
            f"+ prompt {len(PRIVACY_RENDER_PROMPT)} characters"
        )
        url = self._client.base_url.rstrip("/") + "/images/edits"
        try:
            async with (
                asyncio.timeout(timeout_s),
                self._client.call(
                    self._model,
                    f"privacy render of {camera_id}",
                    url=url,
                    sent=sent,
                    timeout_s=timeout_s,
                ) as trace,
            ):
                raw = await self._client.openai.images.with_raw_response.edit(
                    model=self._model,
                    image=("frame.jpg", jpeg, "image/jpeg"),
                    prompt=PRIVACY_RENDER_PROMPT,
                    n=1,
                    timeout=timeout_s,
                )
                trace.status = raw.status_code
                trace.read_headers(raw.headers)
                response = raw.parse()
                data = response.data[0] if response.data else None
                if data is None or not data.b64_json:
                    raise RenderError("the answer has no image")
                try:
                    image = base64.b64decode(data.b64_json, validate=True)
                except (binascii.Error, ValueError):
                    raise RenderError("the answer's image is not base64") from None
                render, came = await asyncio.to_thread(as_jpeg, image)
                trace.out = f"{came}, {round(len(image) / 1024)} KB"
                return render
        except TimeoutError:
            raise RenderError(f"no answer within {timeout_s:.1f} s") from None
        except openai.APIStatusError as exc:
            raise RenderError(f"{type(exc).__name__} (HTTP {exc.status_code})") from None
        except openai.OpenAIError as exc:  # connection failures, the client's own timeout
            raise RenderError(type(exc).__name__) from None
        except (AttributeError, TypeError, IndexError) as exc:  # an answer of another shape
            raise RenderError(f"unreadable answer: {type(exc).__name__}") from None

    async def close(self) -> None:
        """Close the Meta client (see ``MetaClient.close``)."""
        await self._client.close()
