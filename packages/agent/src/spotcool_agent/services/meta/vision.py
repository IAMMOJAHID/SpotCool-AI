"""SamVision: people counted by SAM 3.1 on the Meta AI API.

The request is the MVP's (``_sam_image`` in the MVP's ``perception.py``): one user message
with the concept as text and the snapshot as a base64 JPEG data URL, and
``metadata={"include_confidence": "true"}``. The answer is parsed by ``parse_sam_output``; the
first frame's boxes below ``min_confidence`` are dropped. A 429 is retried once, after a short
pause, if time is left; nothing else is retried, because a step's deadline leaves no room for
it. The trace's lines give the frame's size and the boxes in pixels, never the image.
"""

import asyncio
import base64
import time
from collections.abc import Callable

import openai

from spotcool_agent.domain.geometry import Detection, Detections, parse_sam_output
from spotcool_agent.services.meta.client import MetaClient
from spotcool_agent.services.vision import VisionError, VisionService, image_size

SAM_MODEL = "sam-3.1"
MIN_CONFIDENCE = 0.3
"""Boxes SAM is less sure of than this are dropped (the MVP's ``SAM_MIN_CONFIDENCE``)."""
RETRY_429_S = 0.5
"""The pause before the one retry of a rate-limited (429) request."""
TRACE_BOXES = 6
"""Boxes the trace lists; the rest are counted."""


def first_frame(text: str) -> list[Detection]:
    """The boxes of the answer's first frame (frame 0, else the lowest).

    Raises:
        TypeError, ValueError: If the answer is unreadable.
    """
    frames = parse_sam_output(text)
    return frames.get(0) or (frames[min(frames)] if frames else [])


def describe_boxes(found: list[Detection], width: int, height: int, min_confidence: float) -> str:
    """The trace's ``out``: the boxes in pixels with their confidences, and how many were kept."""

    def box(d: Detection) -> str:
        c = "" if d.confidence is None else f" c={d.confidence:.2f}"
        x1, x2 = round(d.x1 * width), round(d.x2 * width)
        y1, y2 = round(d.y1 * height), round(d.y2 * height)
        return f"({x1},{y1})-({x2},{y2}){c}"

    listed = ", ".join(box(d) for d in found[:TRACE_BOXES])
    more = f", +{len(found) - TRACE_BOXES} more" if len(found) > TRACE_BOXES else ""
    kept = sum(d.confidence is None or d.confidence >= min_confidence for d in found)
    return f"{len(found)} boxes [{listed}{more}] -> kept {kept} (c >= {min_confidence:.2f})"


class SamVision(VisionService):
    """Counts with SAM 3.1 (see the module docstring).

    Args:
        client: The process's Meta client: its limiter, ledger and call cap.
        model: The SAM model.
        min_confidence: Boxes below this confidence are dropped.
        retry_429_s: The pause before retrying a 429 once.
    """

    def __init__(
        self,
        client: MetaClient,
        *,
        model: str = SAM_MODEL,
        min_confidence: float = MIN_CONFIDENCE,
        retry_429_s: float = RETRY_429_S,
    ) -> None:
        super().__init__(name=f"SamVision({model})")
        self._client = client
        self._model = model
        self._min_confidence = min_confidence
        self._retry_429_s = retry_429_s

    async def detect(
        self, jpeg: bytes, concept: str, timeout_s: float, *, camera_id: str | None = None
    ) -> Detections:
        """Ask SAM 3.1 for every ``concept`` in the image (see ``VisionService.detect``)."""
        if timeout_s <= 0:
            raise VisionError("no time left for the call")
        width, height = image_size(jpeg)
        image_url = f"data:image/jpeg;base64,{base64.b64encode(jpeg).decode('ascii')}"
        content = [
            {"type": "input_text", "text": concept},
            {"type": "input_image", "image_url": image_url},
        ]
        purpose = f"count {concept} on {camera_id or 'a camera'}"
        size_kb = round(len(jpeg) / 1024)
        sent = f'JPEG {width}x{height}, {size_kb} KB + text "{concept}" (include_confidence)'

        def describe(text: str) -> str:
            return describe_boxes(first_frame(text), width, height, self._min_confidence)

        deadline = time.perf_counter() + timeout_s
        try:
            async with asyncio.timeout(timeout_s):
                text = await self._ask(content, purpose, deadline, sent, describe)
        except TimeoutError:
            raise VisionError(f"no answer within {timeout_s:.1f} s") from None
        try:
            found = first_frame(text)
        except (TypeError, ValueError) as exc:  # such as a confidence of "0.5.1"
            raise VisionError(f"unreadable answer: {type(exc).__name__}") from None
        kept = tuple(
            d for d in found if d.confidence is None or d.confidence >= self._min_confidence
        )
        return Detections(kept, width, height)

    async def close(self) -> None:
        """Close the Meta client (see ``MetaClient.close``)."""
        await self._client.close()

    async def _ask(
        self,
        content: list[dict[str, str]],
        purpose: str,
        deadline: float,
        sent: str,
        describe: Callable[[str], str],
    ) -> str:
        """The answer's text: one request, and one more after a 429 if time is left."""
        for attempt in (1, 2):
            left = deadline - time.perf_counter()
            try:
                return await self._client.respond(
                    model=self._model,
                    purpose=purpose,
                    timeout_s=max(left, 0.1),
                    content=list(content),
                    metadata={"include_confidence": "true"},
                    sent=sent,
                    describe=describe,
                )
            except openai.RateLimitError:
                if attempt == 2 or left <= self._retry_429_s:
                    raise VisionError("rate limited (HTTP 429)") from None
                await asyncio.sleep(self._retry_429_s)
            except openai.APIStatusError as exc:
                raise VisionError(f"{type(exc).__name__} (HTTP {exc.status_code})") from None
            except openai.OpenAIError as exc:  # connection failures, the client's own timeout
                raise VisionError(type(exc).__name__) from None
            except (AttributeError, TypeError, ValueError) as exc:  # an answer of another shape
                raise VisionError(f"unreadable answer: {type(exc).__name__}") from None
        raise AssertionError("unreachable")
