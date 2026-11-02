import asyncio
import base64
import json
import logging
import os
import re
import time
import urllib.error
import urllib.request
import uuid
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path
from typing import Any, Optional

from openai import AsyncOpenAI, BadRequestError

from .config import Settings
from .perception import strip_masks

LOGGER = logging.getLogger("spotcool.meta")


@lru_cache(maxsize=4)
def get_client(base_url: str, max_retries: int) -> AsyncOpenAI:
    api_key = os.getenv("META_API_KEY")
    if not api_key:
        raise RuntimeError("META_API_KEY must be set to call the Meta AI API.")
    return AsyncOpenAI(base_url=base_url, api_key=api_key, max_retries=max_retries)


@dataclass
class RawResult:
    text: str
    status: Optional[str] = "completed"
    response_id: Optional[str] = None


class CassetteMissing(RuntimeError):
    pass


class AsrError(RuntimeError):
    def __init__(self, status: int, message: str) -> None:
        super().__init__(f"[{status}] {message}")
        self.status = status


ASR_MAX_BYTES = 32 * 1024 * 1024
_RETRYABLE = {429, 500, 502, 503, 504}


def _multipart(parts: list[tuple[str, Optional[str], bytes, str]]) -> tuple[bytes, str]:
    boundary = uuid.uuid4().hex
    chunks = []
    for name, filename, data, content_type in parts:
        disposition = f'form-data; name="{name}"' + (f'; filename="{filename}"' if filename else "")
        header = f"--{boundary}\r\nContent-Disposition: {disposition}\r\nContent-Type: {content_type}\r\n\r\n"
        chunks.append(header.encode() + data + b"\r\n")
    chunks.append(f"--{boundary}--\r\n".encode())
    return b"".join(chunks), f"multipart/form-data; boundary={boundary}"


def _post_asr(url: str, request: dict[str, Any], filename: str, audio: bytes, timeout_s: float, retries: int) -> str:
    """Blocking multipart POST; the socket timeout bounds it even though to_thread cannot be cancelled."""
    api_key = os.getenv("META_API_KEY")
    if not api_key:
        raise RuntimeError("META_API_KEY must be set to call the Meta AI API.")
    body, content_type = _multipart(
        [("request", None, json.dumps(request).encode(), "application/json"), ("audio", filename, audio, "audio/wav")]
    )
    for attempt in range(retries + 1):
        req = urllib.request.Request(
            url, data=body, method="POST", headers={"Authorization": f"Bearer {api_key}", "Content-Type": content_type}
        )
        try:
            with urllib.request.urlopen(req, timeout=timeout_s) as response:
                return response.read().decode("utf-8")
        except urllib.error.HTTPError as exc:
            detail = exc.read().decode("utf-8", errors="replace")[:300]
            if exc.code in _RETRYABLE and attempt < retries:
                time.sleep(2**attempt)
                continue
            raise AsrError(exc.code, detail) from None
    raise AssertionError("unreachable")


def _slug(concept: str) -> str:
    return re.sub(r"[^a-z0-9]+", "_", concept.lower()).strip("_")


class MetaBackend:
    """The five Meta model calls. Subclasses: live API, mock responses, recorded replay."""

    name = "base"
    supports_policy = True

    def __init__(self) -> None:
        self.turn = 0
        self.n_frames = 0
        self.frame_size = (0, 0)

    def begin_turn(self, turn: int, n_frames: int, frame_size: tuple[int, int]) -> None:
        self.turn, self.n_frames, self.frame_size = turn, n_frames, frame_size

    async def upload_video(self, path: Path) -> str:
        raise NotImplementedError

    async def delete_file(self, file_id: str) -> None:
        return None

    async def sam_track(self, file_id: str, concept: str) -> RawResult:
        raise NotImplementedError

    async def spark_assess(self, image_jpeg: bytes, prompt: str, schema: dict[str, Any]) -> RawResult:
        raise NotImplementedError

    async def transcribe(self, wav_path: Path, index: int) -> RawResult:
        raise NotImplementedError

    async def wonder_decide(self, instructions: str, payload: str, schema: dict[str, Any]) -> RawResult:
        raise NotImplementedError

    async def image_edit(self, image_png: bytes, prompt: str) -> Optional[bytes]:
        return None


class LiveBackend(MetaBackend):
    name = "live"

    def __init__(self, settings: Settings, record_dir: Optional[Path] = None) -> None:
        super().__init__()
        self.settings = settings
        self.models = settings.models
        self.client = get_client(settings.base_url, settings.max_retries)
        self.record_dir = record_dir

    def _record(self, name: str, text: str) -> None:
        if self.record_dir is None:
            return
        path = self.record_dir / f"turn_{self.turn:03d}" / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text, encoding="utf-8")

    def _record_meta(self, name: str, result: RawResult) -> None:
        if self.record_dir is None:
            return
        path = self.record_dir / f"turn_{self.turn:03d}" / "meta.json"
        meta = json.loads(path.read_text(encoding="utf-8")) if path.exists() else {}
        meta[name] = {"status": result.status, "response_id": result.response_id}
        self._record("meta.json", json.dumps(meta, indent=2))

    async def upload_video(self, path: Path) -> str:
        upload = (path.name, path.read_bytes(), "video/mp4")
        try:
            created = await self.client.files.create(
                file=upload,
                purpose="user_data",
                expires_after={"anchor": "created_at", "seconds": self.settings.upload_expires_s},
            )
        except BadRequestError as exc:
            LOGGER.warning("files.create rejected expires_after (%s); uploading without it", exc)
            created = await self.client.files.create(file=upload, purpose="user_data")
        return created.id

    async def delete_file(self, file_id: str) -> None:
        try:
            await self.client.files.delete(file_id)
        except Exception as exc:  # the upload also expires on its own
            LOGGER.warning("Could not delete uploaded file %s: %s", file_id, exc)

    async def sam_track(self, file_id: str, concept: str) -> RawResult:
        content = [{"type": "input_text", "text": concept}, {"type": "input_video", "file_id": file_id}]
        kwargs: dict[str, Any] = {
            "model": self.models.sam,
            "input": [{"type": "message", "role": "user", "content": content}],
        }
        if self.settings.sam_include_confidence:
            kwargs["metadata"] = {"include_confidence": "true"}
        try:
            result = await self._stream_text(**kwargs)
        except BadRequestError:
            if "metadata" not in kwargs:
                raise
            LOGGER.warning("sam-3.1 rejected include_confidence; retrying without it")
            kwargs.pop("metadata")
            result = await self._stream_text(**kwargs)
        name = f"sam_{_slug(concept)}"
        self._record(f"{name}.txt", strip_masks(result.text))
        self._record_meta(name, result)
        return result

    async def _stream_text(self, **kwargs: Any) -> RawResult:
        chunks: list[str] = []
        status: Optional[str] = None
        response_id: Optional[str] = None
        error: Any = None
        async with self.client.responses.stream(**kwargs) as stream:
            async for event in stream:
                if event.type == "response.output_text.delta":
                    chunks.append(event.delta)
                elif event.type in ("response.created", "response.completed", "response.incomplete", "response.failed"):
                    response_id = getattr(event.response, "id", None) or response_id
                    if event.type != "response.created":
                        status = getattr(event.response, "status", None) or event.type.rsplit(".", 1)[-1]
                        error = getattr(event.response, "error", None)
        if status == "failed":
            raise RuntimeError(f"{kwargs['model']} response failed: {error}")
        return RawResult(text="".join(chunks), status=status or "unknown", response_id=response_id)

    async def _structured(self, name: str, schema: dict[str, Any], **kwargs: Any) -> RawResult:
        """Strict JSON schema, then non-strict, then plain text; the caller validates the text."""
        formats: list[Optional[dict[str, Any]]] = [
            {"type": "json_schema", "name": name, "schema": schema, "strict": True},
            {"type": "json_schema", "name": name, "schema": schema, "strict": False},
            None,
        ]
        last_error: Optional[Exception] = None
        for fmt in formats:
            try:
                extra = {"text": {"format": fmt}} if fmt else {}
                response = await self.client.responses.create(**kwargs, **extra)
            except BadRequestError as exc:
                label = "plain" if fmt is None else ("strict" if fmt["strict"] else "non-strict")
                LOGGER.warning("%s rejected %s output (%s)", name, label, exc)
                last_error = exc
                continue
            result = RawResult(text=response.output_text or "", status=response.status, response_id=response.id)
            self._record(f"{name}.json", result.text)
            self._record_meta(name, result)
            return result
        assert last_error is not None
        raise last_error

    async def spark_assess(self, image_jpeg: bytes, prompt: str, schema: dict[str, Any]) -> RawResult:
        image_url = "data:image/jpeg;base64," + base64.b64encode(image_jpeg).decode("ascii")
        content = [{"type": "input_text", "text": prompt}, {"type": "input_image", "image_url": image_url}]
        return await self._structured(
            "spark",
            schema,
            model=self.models.spark,
            input=[{"type": "message", "role": "user", "content": content}],
        )

    async def transcribe(self, wav_path: Path, index: int) -> RawResult:
        """Meta's one-shot ASR endpoint (not OpenAI's /audio/transcriptions, which returns 404).

        POST {base}/asr/transcribe, multipart "request" JSON + "audio" WAV, as in the
        meta-model-cookbook (06_muse_voice). DIARIZATION returns speaker-labelled turns.
        """
        audio = wav_path.read_bytes()
        if len(audio) > ASR_MAX_BYTES:
            raise RuntimeError(f"{wav_path.name} is {len(audio)} bytes; the ASR endpoint takes at most 32 MB")
        url = self.settings.base_url.rstrip("/") + "/asr/transcribe"
        last_error: Optional[Exception] = None
        for mode in ("DIARIZATION", "ENDPOINTING", "PUSH_TO_TALK"):
            request = {"model": self.models.transcribe, "mode": mode, "audioEncoding": "WAV"}
            try:
                text = await asyncio.to_thread(
                    _post_asr, url, request, wav_path.name, audio, self.settings.transcribe_timeout_s, self.settings.max_retries
                )
            except AsrError as exc:
                if exc.status != 400:
                    raise
                LOGGER.warning("ASR rejected mode %s (%s)", mode, exc)
                last_error = exc
                continue
            self._record(f"transcribe_{index}.json", text)
            return RawResult(text=text)
        assert last_error is not None
        raise last_error

    async def wonder_decide(self, instructions: str, payload: str, schema: dict[str, Any]) -> RawResult:
        return await self._structured(
            "wonder",
            schema,
            model=self.models.wonder,
            instructions=instructions,
            input=payload,
            reasoning={"effort": self.settings.wonder_reasoning_effort},
        )

    async def image_edit(self, image_png: bytes, prompt: str) -> Optional[bytes]:
        result = await self.client.images.edit(
            model=self.models.image, image=("frame.png", image_png, "image/png"), prompt=prompt
        )
        item = result.data[0] if result.data else None
        if item is not None and item.b64_json:
            return base64.b64decode(item.b64_json)
        if item is not None and item.url:
            return await asyncio.to_thread(lambda: urllib.request.urlopen(item.url, timeout=60).read())
        return None


class ReplayBackend(MetaBackend):
    """Replays recorded or hand-written responses from <cassette>/turn_NNN/."""

    name = "replay"

    def __init__(self, cassette_dir: Path) -> None:
        super().__init__()
        self.cassette_dir = Path(cassette_dir)

    def _read(self, name: str) -> str:
        path = self.cassette_dir / f"turn_{self.turn:03d}" / name
        if not path.is_file():
            raise CassetteMissing(f"no cassette file {path}")
        return path.read_text(encoding="utf-8")

    def _status(self, name: str) -> RawResult:
        path = self.cassette_dir / f"turn_{self.turn:03d}" / "meta.json"
        meta = json.loads(path.read_text(encoding="utf-8")).get(name, {}) if path.is_file() else {}
        return RawResult(text="", status=meta.get("status", "completed"), response_id=meta.get("response_id"))

    async def upload_video(self, path: Path) -> str:
        return "file-replay"

    async def sam_track(self, file_id: str, concept: str) -> RawResult:
        name = f"sam_{_slug(concept)}"
        result = self._status(name)
        result.text = self._read(f"{name}.txt")
        return result

    async def spark_assess(self, image_jpeg: bytes, prompt: str, schema: dict[str, Any]) -> RawResult:
        result = self._status("spark")
        result.text = self._read("spark.json")
        return result

    async def transcribe(self, wav_path: Path, index: int) -> RawResult:
        return RawResult(text=self._read(f"transcribe_{index}.json"))

    async def wonder_decide(self, instructions: str, payload: str, schema: dict[str, Any]) -> RawResult:
        result = self._status("wonder")
        result.text = self._read("wonder.json")
        return result


class MockBackend(MetaBackend):
    """Canned raw responses for any clip: one seated occupant, no door, no speech.
    The policy is the rule-based fallback, so a mock run is fully deterministic."""

    name = "mock"
    supports_policy = False

    async def upload_video(self, path: Path) -> str:
        return "file-mock"

    async def sam_track(self, file_id: str, concept: str) -> RawResult:
        if concept != "person":
            return RawResult(text="")
        width, height = self.frame_size
        lines = []
        for frame in range(self.n_frames):
            drift = 0.01 * ((frame % 6) - 3) / 3
            x1, x2 = int((0.56 + drift) * width), int((0.68 + drift) * width) - 1
            y1, y2 = int(0.42 * height), int(0.86 * height) - 1
            lines.append(
                f"<{frame}f>1<|box;x1={x1};y1={y1};x2={x2};y2={y2};w={width};h={height};c=0.93|>"
                "<|mask;x=0;y=0;data=4,4,~!!!!|>"
            )
        return RawResult(text="\n".join(lines))

    async def spark_assess(self, image_jpeg: bytes, prompt: str, schema: dict[str, Any]) -> RawResult:
        tiles = len(re.findall(r"#\d+ t=", prompt)) or 6
        occupants = []
        if "P1" in prompt:
            occupants.append(
                {
                    "person_id": "P1",
                    "clothing_clo": 0.6,
                    "activity_met": 1.1,
                    "visual_discomfort_cues": [],
                    "posture": "seated",
                    "posture_summary": "seated, typing",
                    "apparent_thermal_sensation": 0.0,
                }
            )
        payload = {"occupants": occupants, "door_open_by_frame": [False] * tiles, "scene_summary": "Mock scene."}
        return RawResult(text=json.dumps(payload))

    async def transcribe(self, wav_path: Path, index: int) -> RawResult:
        return RawResult(text=json.dumps({"text": "", "segments": []}))


def make_backend(settings: Settings, cassette_dir: Optional[Path] = None, record_dir: Optional[Path] = None) -> MetaBackend:
    if settings.mode == "mock":
        return MockBackend()
    if settings.mode == "replay":
        if cassette_dir is None:
            raise ValueError("--mode replay needs --cassette DIR")
        return ReplayBackend(cassette_dir)
    return LiveBackend(settings, record_dir)
