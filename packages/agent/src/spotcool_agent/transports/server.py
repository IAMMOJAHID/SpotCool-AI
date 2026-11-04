"""The agent's HTTP server: complaints on ``POST /chat`` and ``POST /voice`` (A-handoff section 9).

- ``POST /chat``: a ``ComplaintText`` JSON body.
- ``POST /voice``: ``multipart/form-data`` with an ``audio`` WAV in ``VOICE_FORMAT`` and an
  optional ``meta`` field holding a ``ComplaintText`` JSON string (its text is for logging only).
- ``GET /privacy/{camera_id}.jpg``: the camera's latest Muse Image render (``loops/render.py``),
  from memory, with ``Cache-Control: no-store``; 404 before the first. Display only.
- ``GET /healthz``.

A complaint is validated, queued and answered ``202`` at once, well inside the world's 10 s; the
complaint loop handles it later. A full queue answers ``503``, which the world retries. Audio
stays in memory: the body is capped at ``MAX_VOICE_BYTES``, below Starlette's spool size, so the
form parser never moves it to a temporary file. CORS lets the dashboard's page (the world's
dashboard port, and Vite's ``http://localhost:5173``) send ``POST`` with ``content-type`` and
``x-spotcool-client``, and ``GET`` the renders.

``AgentServer`` serves the app with uvicorn on the event loop it is started from, without
uvicorn's own signal handlers: the agent's runner owns Ctrl+C.
"""

import asyncio
import contextlib
import io
import socket
import sys
import wave
from collections.abc import Iterator, Mapping
from dataclasses import dataclass
from typing import Literal

import uvicorn
from fastapi import FastAPI, HTTPException, Request, Response
from fastapi.middleware.cors import CORSMiddleware
from loguru import logger
from pydantic import ValidationError
from starlette.datastructures import UploadFile

from spotcool_contracts.occupants import (
    AGENT_CHAT_PATH,
    AGENT_VOICE_PATH,
    VOICE_FORMAT,
    ComplaintText,
)

MAX_VOICE_BYTES = 1_000_000
"""The largest ``/voice`` body taken: about 30 s of 16 kHz mono audio, below the 1 MiB spool."""
QUEUE_SIZE = 32
"""Complaints waiting to be handled before new ones are refused (503)."""
STOP_TIMEOUT_S = 2
"""Seconds ``stop()`` lets open requests finish (uvicorn takes an int)."""


@dataclass(frozen=True)
class Complaint:
    """A complaint taken by the server, waiting for the complaint loop.

    Parameters:
        channel: How it came in.
        complaint: The body (chat) or the ``meta`` field (voice; its text is the script line,
            for logging only); ``None`` for a voice message without ``meta``.
        audio: The voice message's WAV bytes, in memory; ``None`` for chat.
        audio_s: The voice message's length in seconds; 0 for chat.
    """

    channel: Literal["chat", "voice"]
    complaint: ComplaintText | None
    audio: bytes | None = None
    audio_s: float = 0.0


def dashboard_origins(world_host: str, dashboard_port: int) -> list[str]:
    """The pages that may call ``/chat`` and ``/voice``: the world's dashboard and Vite's."""
    hosts = dict.fromkeys([world_host, "127.0.0.1", "localhost"])
    return [f"http://{host}:{dashboard_port}" for host in hosts] + ["http://localhost:5173"]


def create_app(
    queue: "asyncio.Queue[Complaint]",
    *,
    origins: list[str],
    renders: Mapping[str, bytes] | None = None,
) -> FastAPI:
    """The agent's complaint app.

    Args:
        queue: Where accepted complaints go.
        origins: The browser origins allowed to ``POST`` (``dashboard_origins``).
        renders: The latest privacy render of each camera (``RenderLoop.latest``); ``None``
            for none.
    """
    app = FastAPI(title="SpotCool agent", docs_url=None, redoc_url=None, openapi_url=None)
    app.add_middleware(
        CORSMiddleware,
        allow_origins=origins,
        allow_methods=["GET", "POST"],
        allow_headers=["content-type", "x-spotcool-client"],
    )

    def accept(complaint: Complaint) -> dict[str, bool]:
        try:
            queue.put_nowait(complaint)
        except asyncio.QueueFull:
            raise HTTPException(503, "the agent is busy; try again") from None
        return {"accepted": True}

    @app.post(AGENT_CHAT_PATH, status_code=202)
    async def chat(complaint: ComplaintText) -> dict[str, bool]:
        """Take a typed complaint."""
        return accept(Complaint("chat", complaint))

    @app.post(AGENT_VOICE_PATH, status_code=202)
    async def voice(request: Request) -> dict[str, bool]:
        """Take a voice complaint: a WAV in ``VOICE_FORMAT`` and an optional ``meta``."""
        length = request.headers.get("content-length")
        if length is None or not length.isdigit():
            raise HTTPException(411, "send the voice message with a Content-Length")
        if int(length) > MAX_VOICE_BYTES:
            raise HTTPException(413, f"a voice message is at most {MAX_VOICE_BYTES} bytes")
        form = await request.form(max_files=1, max_fields=2)
        upload, meta = form.get("audio"), form.get("meta")
        if not isinstance(upload, UploadFile):
            raise HTTPException(422, "the form needs an 'audio' file")
        audio = await upload.read()
        await upload.close()
        seconds = _wav_seconds(audio)
        complaint = None
        if meta is not None:
            if not isinstance(meta, str):
                raise HTTPException(422, "'meta' must be a ComplaintText JSON string")
            try:
                complaint = ComplaintText.model_validate_json(meta)
            except ValidationError:
                raise HTTPException(422, "'meta' is not a ComplaintText") from None
        return accept(Complaint("voice", complaint, audio, seconds))

    @app.get("/privacy/{camera_id}.jpg")
    async def privacy(camera_id: str) -> Response:
        """A camera's latest privacy render, or 404 before the first."""
        render = (renders or {}).get(camera_id)
        if render is None:
            raise HTTPException(404, "no render of this camera yet")
        return Response(render, media_type="image/jpeg", headers={"Cache-Control": "no-store"})

    @app.get("/healthz")
    async def healthz() -> dict[str, bool]:
        """Whether the server answers."""
        return {"ok": True}

    return app


def _wav_seconds(audio: bytes) -> float:
    """A WAV's length in seconds, checked against ``VOICE_FORMAT``.

    Raises:
        HTTPException: 422 if it is not a WAV in that format.
    """
    try:
        with wave.open(io.BytesIO(audio)) as wav:
            shape = (wav.getnchannels(), wav.getsampwidth(), wav.getframerate(), wav.getcomptype())
            frames = wav.getnframes()
    except (wave.Error, EOFError):
        raise HTTPException(422, "the audio is not a WAV file") from None
    wanted = (VOICE_FORMAT.channels, 2, VOICE_FORMAT.sample_rate_hz, "NONE")  # s16le: 2 bytes
    if shape != wanted:
        raise HTTPException(422, "the audio must be a 16 kHz mono 16-bit PCM WAV")
    return frames / VOICE_FORMAT.sample_rate_hz


class _Server(uvicorn.Server):
    """uvicorn without signal handlers of its own."""

    @contextlib.contextmanager
    def capture_signals(self) -> Iterator[None]:
        yield


class AgentServer:
    """Serves the complaint app on ``host:port`` until ``stop()``.

    Args:
        app: The app (``create_app``).
        host: The address to bind.
        port: The TCP port.
    """

    def __init__(self, app: FastAPI, host: str, port: int) -> None:
        self._app = app
        self.host = host
        self.port = port
        self._server: _Server | None = None
        self._task: asyncio.Task[None] | None = None

    async def start(self) -> None:
        """Bind the port and serve in a background task.

        Raises:
            OSError: If the port is taken.
        """
        sock = _listen(self.host, self.port)
        config = uvicorn.Config(
            self._app,
            log_config=None,
            access_log=False,
            timeout_graceful_shutdown=STOP_TIMEOUT_S,
        )
        server = self._server = _Server(config)
        self._task = asyncio.create_task(self._serve(server, sock), name="agent-http")
        while not server.started and not self._task.done():  # noqa: ASYNC110 (uvicorn: a flag)
            await asyncio.sleep(0.01)
        if self._task.done():
            await self._task  # raises what stopped it
        logger.info(f"agent: complaints on http://{self.host}:{self.port}")

    async def stop(self) -> None:
        """Ask uvicorn to exit and wait for it. Safe to call twice."""
        task, self._task = self._task, None
        if task is None or self._server is None:
            return
        self._server.should_exit = True
        await asyncio.gather(task, return_exceptions=True)

    @staticmethod
    async def _serve(server: _Server, sock: socket.socket) -> None:
        with sock:
            try:
                await server.serve(sockets=[sock])
            except SystemExit:  # uvicorn exits the process when the app's startup fails
                raise RuntimeError("the agent's HTTP app failed to start") from None


def _listen(host: str, port: int) -> socket.socket:
    """A listening TCP socket on ``host:port`` that no other socket shares.

    Raises:
        OSError: If the port is taken.
    """
    sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    try:
        if sys.platform == "win32":  # Windows would let another socket share the port
            sock.setsockopt(socket.SOL_SOCKET, socket.SO_EXCLUSIVEADDRUSE, 1)
        sock.bind((host, port))
        sock.listen()
    except BaseException:
        sock.close()
        raise
    return sock
