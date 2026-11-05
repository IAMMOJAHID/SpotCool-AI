"""MetaSTT: voice messages transcribed by ``muse-voice-transcribe-1.0`` on the Meta AI API.

The request is the MVP's (``_post_transcription`` in the MVP's ``media.py``), sent with httpx
instead of urllib: a multipart ``POST {base_url}/asr/transcribe`` with a ``request`` part (JSON:
the model, ``DIARIZATION``, ``WAV`` and the MVP's comfort keywords) and an ``audio`` part
(``clip.wav``, ``audio/wav``), and ``Authorization: Bearer <key>``. The answer, as the lead
measured it, is ``{sessionId, transcript, audioDurationMs, turns: [{..., transcript, ...}]}``;
the text is ``transcript``, or the turns' texts joined when it is missing.

One request, a timeout of ``TIMEOUT_S``, no retries: the occupant can say it again. The audio
stays in memory. Each call takes a turn of the language services' ``MetaClient`` (its limiter,
its call cap, its trace and a ``Call`` in its ledger, never the audio or the text: the trace
gives the audio's length and size, and the transcript's length).
"""

import asyncio
import io
import json
import wave
from typing import Any

import httpx
from pydantic import SecretStr

from spotcool_agent.services.meta.client import MetaClient, characters
from spotcool_agent.services.stt import STTError, STTService
from spotcool_agent.services.vision import CallCapReached

STT_MODEL = "muse-voice-transcribe-1.0"
ASR_PATH = "/asr/transcribe"
TIMEOUT_S = 6.0
"""The longest one transcription may take (5 s of audio took 4.4 s)."""
COMFORT_KEYWORDS = [
    "hot", "cold", "freezing", "warm", "stuffy", "humid", "sweating", "draft",
    "air conditioning", "AC", "temperature", "fan",
]  # fmt: skip
"""Words the model is told to expect (the MVP's ``COMFORT_KEYWORDS``)."""


def parse_transcript(body: bytes) -> str:
    """The text of a transcription answer (see the module docstring).

    Raises:
        STTError: If the answer is not JSON of that shape.
    """
    try:
        answer: Any = json.loads(body)
    except ValueError:
        raise STTError("the answer is not JSON") from None
    if not isinstance(answer, dict):
        raise STTError("the answer is not a JSON object")
    text = answer.get("transcript")
    if isinstance(text, str):
        return text.strip()
    turns = answer.get("turns")
    if isinstance(turns, list):
        said = [t.get("transcript") for t in turns if isinstance(t, dict)]
        return " ".join(s.strip() for s in said if isinstance(s, str) and s.strip())
    raise STTError("the answer has no transcript")


def describe_wav(wav: bytes) -> str:
    """The trace's ``in``: the audio's length and size, never its samples."""
    try:
        with wave.open(io.BytesIO(wav)) as audio:
            seconds = f"{audio.getnframes() / audio.getframerate():.1f} s, "
    except (wave.Error, EOFError, ZeroDivisionError):
        seconds = ""
    return f"WAV {seconds}{round(len(wav) / 1024)} KB (comfort keywords)"


class MetaSTT(STTService):
    """Transcribes with ``muse-voice-transcribe-1.0`` (see the module docstring).

    Args:
        client: The budget the calls count against (the language services' ``MetaClient``).
        api_key: The Meta API key; read only to build the header.
        base_url: The API's base URL.
        model: The transcription model.
        keywords: Words the model is told to expect.
        transport: httpx transport; tests pass an ``httpx.MockTransport``.
    """

    def __init__(
        self,
        client: MetaClient,
        api_key: SecretStr,
        *,
        base_url: str,
        model: str = STT_MODEL,
        keywords: list[str] | None = None,
        transport: httpx.AsyncBaseTransport | None = None,
    ) -> None:
        super().__init__(name=f"MetaSTT({model})")
        self._api_key = api_key
        self._url = base_url.rstrip("/") + ASR_PATH
        self._model = model
        self._keywords = COMFORT_KEYWORDS if keywords is None else keywords
        self._client = client
        self._transport = transport
        self._http: httpx.AsyncClient | None = None

    async def transcribe(self, wav: bytes, timeout_s: float = TIMEOUT_S) -> str:
        """What was said (see ``STTService.transcribe``)."""
        if timeout_s <= 0:
            raise STTError("no time left for the call")
        if self._http is None:
            self._http = httpx.AsyncClient(transport=self._transport)
        request = json.dumps(
            {
                "model": self._model,
                "mode": "DIARIZATION",
                "audioEncoding": "WAV",
                "keywords": self._keywords,
            }
        )
        files = {
            "request": (None, request, "application/json"),
            "audio": ("clip.wav", wav, "audio/wav"),
        }
        headers = {
            "Authorization": f"Bearer {self._api_key.get_secret_value()}",
            "Accept": "application/json",
        }
        try:
            async with (
                asyncio.timeout(timeout_s),
                self._client.call(
                    self._model,
                    "transcribe a voice complaint",
                    url=self._url,
                    sent=describe_wav(wav),
                    timeout_s=timeout_s,
                ) as trace,
            ):
                response = await self._http.post(
                    self._url, files=files, headers=headers, timeout=timeout_s
                )
                trace.status = response.status_code
                trace.read_headers(response.headers)
                if response.status_code != 200:
                    raise STTError(f"HTTP {response.status_code}")
                text = parse_transcript(response.content)
                trace.out = characters(text)
                return text
        except TimeoutError:
            raise STTError(f"no answer within {timeout_s:.1f} s") from None
        except CallCapReached as exc:
            raise STTError(str(exc)) from None
        except httpx.HTTPError as exc:  # connection failures, httpx's own timeout
            raise STTError(type(exc).__name__) from None

    async def close(self) -> None:
        """Close the HTTP client. Safe to call twice."""
        http, self._http = self._http, None
        if http is not None:
            await http.aclose()
