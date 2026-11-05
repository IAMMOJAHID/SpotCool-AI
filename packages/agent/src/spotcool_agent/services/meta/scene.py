"""SparkScene: the people and the room read by ``muse-spark-1.3`` on the Meta AI API.

The prompt is adapted from the MVP's ``SCENE_INSTRUCTIONS`` (``app/spotcool/perception.py`` on
branch ``third_level_improvement``): one frame, not the MVP's numbered boxes and storyboard, plus SAM
3.1's count as text, and a JSON-only answer of exactly the keys of ``Scene``. Each call is one
``responses.create`` through a ``MetaClient`` of the scene's own budget (``--scene-call-cap``).
No retries: a scene note or a comfort setpoint can wait for the next one. The trace gives the
frame's size, and the people, the clothing tally and the doors that came back; never the image
or anything identifying.
"""

import asyncio
import base64

import openai

from spotcool_agent.domain.scene import Scene, parse_scene, tally
from spotcool_agent.services.meta.client import MetaClient
from spotcool_agent.services.scene import SceneError, SceneService
from spotcool_agent.services.vision import CallCapReached, VisionError, image_size

SCENE_MODEL = "muse-spark-1.3"
TIMEOUT_S = 30.0
"""The longest one reading may take (the lead's probe took 10.4 s)."""

INSTRUCTIONS = """You are the perception module of an autonomous HVAC agent for a commercial building.
The image is the most recent CCTV frame of one room.
For every person visible return one entry with:
- clothing_ensemble: the closest ASHRAE 55 ensemble, one of shorts_short_sleeve, skirt_or_dress_light,
  trousers_short_sleeve, trousers_long_sleeve, suit_jacket, sweater_or_layers, winter_outerwear
  (winter_outerwear = coats/puffer jackets worn indoors).
- activity: one of seated_quiet, seated_typing, standing_relaxed, standing_working, walking,
  carrying_or_lifting, exercising.
- posture: one of seated, standing, walking, lying.
- discomfort_cue: a visible thermal-comfort behaviour, one of none, fanning_self, wiping_sweat,
  rolled_up_sleeves, hugging_arms_or_shivering, adjusting_clothing.
Also count the doors and the windows standing open, and give the sunlight (one of none, partial,
strong) and the space type (one of office, meeting_room, retail, lobby, classroom, restaurant,
warehouse, other).
Never guess temperatures or HVAC settings; physics models compute those.
Answer with JSON only, exactly these keys and no others:
{"people": [{"clothing_ensemble": "...", "activity": "...", "posture": "...", "discomfort_cue": "..."}],
 "doors_open": 0, "windows_open": 0, "sunlight": "...", "space_type": "..."}"""


def describe_scene(text: str) -> str:
    """The trace's ``out``: the scene's counts and labels (``tally``)."""
    return tally(parse_scene(text))


def prompt(sam_count: int | None) -> str:
    """The instructions, with SAM 3.1's count as a hint when there is one."""
    if sam_count is None:
        return INSTRUCTIONS
    return f"{INSTRUCTIONS}\n\nSAM 3.1 counted {sam_count} people in this frame."


class SparkScene(SceneService):
    """Reads scenes with ``muse-spark-1.3`` (see the module docstring).

    Args:
        client: The scene's Meta client: its limiter, ledger and call cap.
        model: The model.
    """

    def __init__(self, client: MetaClient, *, model: str = SCENE_MODEL) -> None:
        super().__init__(name=f"SparkScene({model})")
        self._client = client
        self._model = model

    async def assess(
        self, jpeg: bytes, sam_count: int | None, timeout_s: float = TIMEOUT_S, *, camera_id: str
    ) -> Scene:
        """Ask Muse Spark for the scene (see ``SceneService.assess``)."""
        if timeout_s <= 0:
            raise SceneError("no time left for the call")
        try:
            width, height = image_size(jpeg)
        except VisionError as exc:
            raise SceneError(str(exc)) from None
        text = prompt(sam_count)
        image_url = f"data:image/jpeg;base64,{base64.b64encode(jpeg).decode('ascii')}"
        content = [
            {"type": "input_text", "text": text},
            {"type": "input_image", "image_url": image_url},
        ]
        sent = (
            f"JPEG {width}x{height}, {round(len(jpeg) / 1024)} KB + prompt {len(text)} characters"
        )
        try:
            async with asyncio.timeout(timeout_s):
                answer = await self._client.respond(
                    model=self._model,
                    purpose=f"read the scene on {camera_id}",
                    timeout_s=timeout_s,
                    content=content,
                    sent=sent,
                    describe=describe_scene,
                )
        except TimeoutError:
            raise SceneError(f"no answer within {timeout_s:.1f} s") from None
        except CallCapReached as exc:
            raise SceneError(str(exc)) from None
        except openai.APIStatusError as exc:
            raise SceneError(f"{type(exc).__name__} (HTTP {exc.status_code})") from None
        except openai.OpenAIError as exc:  # connection failures, the client's own timeout
            raise SceneError(type(exc).__name__) from None
        except (AttributeError, TypeError, ValueError) as exc:  # an answer of another shape
            raise SceneError(f"unreadable answer: {type(exc).__name__}") from None
        try:
            return parse_scene(answer)
        except ValueError as exc:  # not the JSON asked for, or a key it must not have
            raise SceneError(f"invalid scene: {type(exc).__name__}") from None

    async def close(self) -> None:
        """Close the Meta client (see ``MetaClient.close``)."""
        await self._client.close()
