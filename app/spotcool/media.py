"""Audio transcription (Muse Voice Transcribe), the decision overlay, and the
privacy-safe scene render (Muse Image)."""

import asyncio
import base64
import io
import json
import logging
import os
import time
import urllib.error
import urllib.request
import uuid
import wave
from typing import Any, Optional, Sequence

import cv2
import numpy as np
from openai import AsyncOpenAI

from . import config

LOGGER = logging.getLogger("spotcool.media")

COMFORT_KEYWORDS = [
    "hot", "cold", "freezing", "warm", "stuffy", "humid", "sweating", "draft",
    "air conditioning", "AC", "temperature", "fan",
]


def extract_audio_wav(video_path: str, start_s: float, end_s: float, rate: int = 16000) -> Optional[bytes]:
    """Decode [start, end) of the first audio track to 16-bit mono WAV; None if there is no audio."""
    import av  # PyAV bundles FFmpeg, so no system ffmpeg is needed.

    chunks: list[np.ndarray] = []
    with av.open(video_path) as container:
        if not container.streams.audio:
            return None
        stream = container.streams.audio[0]
        if stream.time_base is not None:
            container.seek(int(start_s / stream.time_base), stream=stream, backward=True, any_frame=False)
        resampler = av.AudioResampler(format="s16", layout="mono", rate=rate)
        for frame in container.decode(stream):
            if frame.time is None:
                continue
            if frame.time + frame.samples / frame.sample_rate < start_s:
                continue
            if frame.time >= end_s:
                break
            for out in resampler.resample(frame):
                chunks.append(out.to_ndarray().reshape(-1))
        for out in resampler.resample(None):
            chunks.append(out.to_ndarray().reshape(-1))
    if not chunks:
        return None
    pcm = np.concatenate(chunks).astype(np.int16)[: int((end_s - start_s) * rate)]
    buffer = io.BytesIO()
    with wave.open(buffer, "wb") as wav:
        wav.setnchannels(1)
        wav.setsampwidth(2)
        wav.setframerate(rate)
        wav.writeframes(pcm.tobytes())
    return buffer.getvalue()


def _post_transcription(wav: bytes, timeout_s: float) -> dict[str, Any]:
    api_key = os.getenv("META_API_KEY")
    if not api_key:
        raise RuntimeError("META_API_KEY must be set to call the Meta AI API.")
    base_url = os.getenv("META_API_BASE_URL", "https://api.meta.ai/v1").rstrip("/")
    settings = json.dumps({
        "model": os.getenv("VOICE_MODEL", "muse-voice-transcribe-1.0"),
        "mode": "DIARIZATION",
        "audioEncoding": "WAV",
        "keywords": COMFORT_KEYWORDS,
    })
    boundary = uuid.uuid4().hex
    body = (
        f"--{boundary}\r\nContent-Disposition: form-data; name=\"request\"\r\n"
        f"Content-Type: application/json\r\n\r\n{settings}\r\n"
        f"--{boundary}\r\nContent-Disposition: form-data; name=\"audio\"; filename=\"clip.wav\"\r\n"
        "Content-Type: audio/wav\r\n\r\n"
    ).encode("utf-8") + wav + f"\r\n--{boundary}--\r\n".encode("utf-8")
    request = urllib.request.Request(
        f"{base_url}/asr/transcribe",
        data=body,
        method="POST",
        headers={
            "Authorization": f"Bearer {api_key}",
            "Content-Type": f"multipart/form-data; boundary={boundary}",
            "Accept": "application/json",
        },
    )
    try:
        with urllib.request.urlopen(request, timeout=timeout_s) as response:
            return json.loads(response.read())
    except urllib.error.HTTPError as exc:
        raise RuntimeError(f"Transcription failed ({exc.code}): {exc.read()[:300]!r}") from exc


async def transcribe_clip(video_path: str, start_s: float, end_s: float, ledger: list[dict[str, Any]], timeout_s: float) -> dict[str, Any]:
    if not config.bool_from_env("VOICE_TRANSCRIPTION_ENABLED", True):
        return {"status": "disabled"}
    try:
        wav = await asyncio.to_thread(extract_audio_wav, video_path, start_s, end_s)
    except ImportError:
        return {"status": "skipped", "reason": "PyAV (av) is not installed"}
    except Exception as exc:
        return {"status": "skipped", "reason": f"audio decode failed: {exc}"}
    if wav is None:
        return {"status": "skipped", "reason": "clip has no audio track"}
    model = os.getenv("VOICE_MODEL", "muse-voice-transcribe-1.0")
    started = time.monotonic()
    try:
        result = await asyncio.to_thread(_post_transcription, wav, timeout_s)
    except Exception as exc:
        config.record_model_call(ledger, "perception", model, "clip audio transcription", started, ok=False, detail=str(exc)[:200])
        return {"status": "error", "reason": str(exc)[:300]}
    config.record_model_call(ledger, "perception", model, "clip audio transcription", started, ok=True)
    turns = [
        {
            "speaker": turn.get("speaker"),
            "start_s": round(start_s + float(turn.get("startMs", 0)) / 1000.0, 2),
            "end_s": round(start_s + float(turn.get("endMs", 0)) / 1000.0, 2),
            "text": turn.get("transcript", ""),
        }
        for turn in result.get("turns", [])
        if turn.get("transcript")
    ]
    return {
        "status": "ok",
        "transcript": result.get("transcript", ""),
        "turns": turns,
        "audio_seconds": round(float(result.get("audioDurationMs", 0)) / 1000.0, 2),
    }


def transcript_lines(voice: dict[str, Any], clip_start_s: float) -> list[str]:
    if voice.get("status") != "ok":
        return []
    lines = [
        f"[{turn.get('speaker') or '?'} +{turn['start_s'] - clip_start_s:.1f}s] {turn['text']}"
        for turn in voice.get("turns", [])
    ]
    if not lines and voice.get("transcript"):
        lines = [voice["transcript"]]
    return lines[:40]


def _pmv_colour(pmv: float) -> tuple[int, int, int]:
    if pmv > 1.5:
        return (40, 40, 230)
    if pmv > 0.5:
        return (0, 140, 255)
    if pmv < -0.5:
        return (230, 160, 40)
    return (80, 200, 80)


def draw_overlay(
    frame: np.ndarray,
    occupants: Sequence[dict[str, Any]],
    aim_pixel: Optional[tuple[int, int]],
    swing: bool,
    banner_lines: Sequence[str],
) -> bytes:
    out = frame.copy()
    height, width = out.shape[:2]
    for occupant in occupants:
        x1, y1, x2, y2 = occupant["box"]
        p1, p2 = (int(x1 * width), int(y1 * height)), (int(x2 * width), int(y2 * height))
        colour = _pmv_colour(float(occupant.get("pmv", 0.0)))
        cv2.rectangle(out, p1, p2, colour, 2)
        label = f"#{occupant['id']} PMV {occupant.get('pmv', 0):+.1f} | {occupant.get('distance_m', 0):.1f} m"
        (text_w, text_h), _ = cv2.getTextSize(label, cv2.FONT_HERSHEY_SIMPLEX, 0.5, 1)
        top = max(text_h + 6, p1[1])
        cv2.rectangle(out, (p1[0], top - text_h - 6), (p1[0] + text_w + 6, top), (0, 0, 0), -1)
        cv2.putText(out, label, (p1[0] + 3, top - 4), cv2.FONT_HERSHEY_SIMPLEX, 0.5, colour, 1, cv2.LINE_AA)
    if aim_pixel and 0 <= aim_pixel[0] < width and 0 <= aim_pixel[1] < height:
        cv2.circle(out, aim_pixel, 26, (255, 255, 0), 2)
        cv2.drawMarker(out, aim_pixel, (255, 255, 0), cv2.MARKER_CROSS, 30, 2)
        cv2.putText(out, "SWING" if swing else "SPOT JET", (aim_pixel[0] + 30, aim_pixel[1]), cv2.FONT_HERSHEY_SIMPLEX, 0.6, (255, 255, 0), 2, cv2.LINE_AA)
    bar_height = 24 * len(banner_lines) + 12
    overlay = out.copy()
    cv2.rectangle(overlay, (0, 0), (width, bar_height), (0, 0, 0), -1)
    out = cv2.addWeighted(overlay, 0.6, out, 0.4, 0)
    for index, line in enumerate(banner_lines):
        cv2.putText(out, line, (10, 26 + 24 * index), cv2.FONT_HERSHEY_SIMPLEX, 0.6, (255, 255, 255), 1, cv2.LINE_AA)
    ok, encoded = cv2.imencode(".jpg", out, [cv2.IMWRITE_JPEG_QUALITY, 88])
    if not ok:
        raise RuntimeError("Could not encode decision overlay.")
    return encoded.tobytes()


PRIVACY_RENDER_PROMPT = (
    "Re-render this CCTV scene as a clean, flat isometric illustration for a building operations dashboard. "
    "Keep the room layout, furniture, doors, windows and the position, posture and clothing colours of every person. "
    "People must be faceless, featureless figures. Do not add or remove people or objects and do not add any text."
)


async def render_privacy_snapshot(client: AsyncOpenAI, frame: np.ndarray, ledger: list[dict[str, Any]]) -> bytes:
    model = os.getenv("IMAGE_MODEL", "muse-image-1.0")
    height, width = frame.shape[:2]
    ok, encoded = cv2.imencode(".jpg", frame, [cv2.IMWRITE_JPEG_QUALITY, 90])
    if not ok:
        raise RuntimeError("Could not encode frame for the privacy render.")
    started = time.monotonic()
    try:
        response = await client.images.edit(
            model=model,
            image=("frame.jpg", encoded.tobytes(), "image/jpeg"),
            prompt=PRIVACY_RENDER_PROMPT,
            n=1,
            size=f"{width}x{height}",
        )
        data = response.data[0] if response.data else None
        if data is None or not data.b64_json:
            raise RuntimeError("Muse Image returned no image data.")
        image = base64.b64decode(data.b64_json)
    except Exception as exc:
        config.record_model_call(ledger, "artifacts", model, "privacy-safe scene render", started, ok=False, detail=str(exc)[:200])
        raise
    config.record_model_call(ledger, "artifacts", model, "privacy-safe scene render", started, ok=True)
    return image
