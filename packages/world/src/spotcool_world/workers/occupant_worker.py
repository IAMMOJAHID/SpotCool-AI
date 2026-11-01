"""OccupantWorker: the scripted occupants who complain to the agent (the ``occupants`` extra).

It runs beside the pipeline. ``ComplaintCueFrame``s arrive on ``WORLD_FRAMES_TOPIC`` (a
``FramePublisher`` puts them there) and each one goes to ``WorldSettings.agent_base_url``:

- chat: ``POST /chat`` with a ``ComplaintText`` JSON body;
- voice: ``POST /voice`` as ``multipart/form-data``, with the recording as ``audio``
  (``audio/wav``, checked against ``VOICE_FORMAT`` first) and the ``ComplaintText`` as a JSON
  string in ``meta``. A recording that is missing, unreadable or in another format falls back
  to chat, and the result's ``detail`` says why.

Each request has a 10 s timeout and is retried twice after a connection error or a 5xx answer.
Every delivery's result goes to the dashboard as a ``complaint_cue`` event on the ``events``
topic, with ``delivered`` and ``detail``. A failed delivery is logged and reported, never fatal.

Audio bytes are read, sent and dropped: never copied, cached or logged (AGENTS.md Law 6). So the
worker never logs a traceback: loguru's tracebacks show local variables, the bytes among them.
"""

import asyncio
import io
import wave
from typing import Any

import httpx
from loguru import logger

from spotcool_contracts.calendar import ROOMS
from spotcool_contracts.events import ComplaintCueData, EventEnvelope, EventSource, EventType
from spotcool_contracts.occupants import (
    AGENT_CHAT_PATH,
    AGENT_VOICE_PATH,
    VOICE_FORMAT,
    ComplaintText,
)
from spotcool_world.bus import Bus, Subscription
from spotcool_world.frames import ComplaintCueFrame
from spotcool_world.processors.frame_publisher import WORLD_FRAMES_TOPIC
from spotcool_world.services.settings import WorldSettings
from spotcool_world.workers.base_worker import BaseWorker, WorkerState

TIMEOUT_S = 10.0
"""Timeout of one request to the agent."""
RETRIES = 2
"""Retries of a request after a connection error or a 5xx answer."""
MAX_AUDIO_BYTES = 10_000_000
"""Largest recording sent, about 5 minutes in ``VOICE_FORMAT``; a larger one falls back to chat."""
NOT_SENT = "not sent: the world stopped"
"""The ``detail`` of a cue still waiting when the worker stopped."""
_SAMPLE_WIDTH = 2  # bytes per sample of VOICE_FORMAT's s16le


class OccupantWorker(BaseWorker):
    """Delivers every ``ComplaintCueFrame`` on the bus to the agent, by chat or by voice.

    ``run()`` subscribes at once, so start it with the pipeline under one ``WorkerRunner``. It
    needs the runner's bus. It runs until ``stop()`` and never ends on its own: WP11 stops it
    from the pipeline worker's ``on_pipeline_finished`` event, which fires after the run's
    ``EndFrame`` or ``CancelFrame``.

    Args:
        settings: Where the agent is (``agent_base_url``) and the recordings are
            (``footage_dir/audio/``).
        topic: Topic the ``ComplaintCueFrame``s arrive on.
        events_topic: Topic the results go to, as dashboard events (``BusPublisher``'s topic).
        backoff_s: Wait before the first retry; it doubles with each retry.
        transport: httpx transport; tests pass an ``httpx.MockTransport``.
        name: Unique worker name.

    Raises:
        ValueError: If ``agent_base_url`` is not an http or https URL with a host.
    """

    def __init__(
        self,
        settings: WorldSettings,
        *,
        topic: str = WORLD_FRAMES_TOPIC,
        events_topic: str = "events",
        backoff_s: float = 0.5,
        transport: httpx.AsyncBaseTransport | None = None,
        name: str | None = None,
    ) -> None:
        super().__init__(name=name)
        self._base_url = _agent_url(settings.agent_base_url)
        self._audio_dir = settings.footage_dir / "audio" if settings.footage_dir else None
        self._topic = topic
        self._events_topic = events_topic
        self._backoff_s = backoff_s
        self._transport = transport
        self._subscription: Subscription | None = None
        self._stopping = False

    async def run(self) -> None:
        """Deliver complaints until ``stop()`` is called; the state is then ``finished``.

        Raises:
            RuntimeError: If the worker has already run, or has no bus.
        """
        if self._state is not WorkerState.IDLE:
            raise RuntimeError(f"{self.name} has already run")
        bus = self.bus
        if bus is None:
            raise RuntimeError(f"{self.name} needs a bus: run it with a WorkerRunner")
        self._state = WorkerState.RUNNING
        subscription = self._subscription = bus.subscribe(self._topic)
        if self._stopping:
            subscription.close()
        try:
            async with httpx.AsyncClient(
                base_url=self._base_url, timeout=TIMEOUT_S, transport=self._transport
            ) as http:
                async for message in subscription:
                    if isinstance(message, ComplaintCueFrame):
                        if self._stopping:  # taken just as stop() closed the subscription
                            data = self._not_sent(message)
                        else:
                            data = await self._deliver(http, message)
                        await self._publish(bus, message, data)
            self._state = WorkerState.FINISHED
        except asyncio.CancelledError:
            self._state = WorkerState.CANCELLED
            raise
        except Exception:
            self._state = WorkerState.FAILED
            raise
        finally:
            subscription.close()
            await self.cleanup()

    async def stop(self, reason: str = "") -> None:
        """Stop taking complaints. ``run()`` returns once a delivery in progress has ended.

        Cues already waiting on the subscription are not sent: each gets a ``complaint_cue``
        result with ``delivered=False`` and the detail ``NOT_SENT``. Cues published after
        ``stop()`` are not received.

        Args:
            reason: Why it is being stopped (logged).
        """
        logger.info(f"{self.name}: stopping{f' ({reason})' if reason else ''}")
        self._stopping = True
        subscription, bus = self._subscription, self.bus
        if subscription is None or bus is None:
            return  # run() has not subscribed yet; it sees _stopping and ends at once
        for message in await _waiting(subscription):
            if isinstance(message, ComplaintCueFrame):
                await self._publish(bus, message, self._not_sent(message))
        subscription.close()

    async def _deliver(self, http: httpx.AsyncClient, cue: ComplaintCueFrame) -> ComplaintCueData:
        """Deliver one complaint and return its result; never raises."""
        data = cue.data
        try:
            delivered, detail = await self._send(http, cue)
        except Exception as exc:  # a surprise; reported without a traceback (Law 6)
            delivered, detail = False, f"{type(exc).__name__}: {exc}"
            logger.error(f"{self.name}: complaint from {data.zone} failed: {detail}")
        else:
            if delivered:
                logger.info(f"{self.name}: {data.channel} complaint from {data.zone} delivered")
            else:
                logger.warning(f"{self.name}: complaint from {data.zone} not delivered: {detail}")
        return data.model_copy(update={"delivered": delivered, "detail": detail})

    def _not_sent(self, cue: ComplaintCueFrame) -> ComplaintCueData:
        logger.warning(f"{self.name}: complaint from {cue.data.zone} {NOT_SENT}")
        return cue.data.model_copy(update={"delivered": False, "detail": NOT_SENT})

    async def _publish(self, bus: Bus, cue: ComplaintCueFrame, data: ComplaintCueData) -> None:
        """Put a cue's result on the dashboard topic as a ``complaint_cue`` event."""
        event = EventEnvelope.build(
            EventType.COMPLAINT_CUE, EventSource.WORLD, data, cue.sim_time_s
        )
        await bus.publish(self._events_topic, event)

    async def _send(
        self, http: httpx.AsyncClient, cue: ComplaintCueFrame
    ) -> tuple[bool, str | None]:
        """Send by voice, or by chat; return whether it arrived and what to report."""
        data = cue.data
        complaint = ComplaintText(
            text=data.text,
            source="occupant-sim",
            room_hint=next((room for room, zone in ROOMS.items() if zone is data.zone), None),
            sim_time_s=cue.sim_time_s,
        )
        fallback = None
        if data.channel == "voice":
            try:
                audio = self._recording(cue.audio)
            except ValueError as exc:
                fallback = f"voice fell back to chat: {exc}"
            else:
                files = {"audio": (cue.audio, audio, "audio/wav")}
                meta = {"meta": complaint.model_dump_json()}
                failure = await self._post(http, AGENT_VOICE_PATH, files=files, data=meta)
                return failure is None, failure
        failure = await self._post(http, AGENT_CHAT_PATH, json=complaint.model_dump(mode="json"))
        return failure is None, "; ".join(filter(None, (fallback, failure))) or None

    def _recording(self, name: str | None) -> bytes:
        """A voice cue's WAV file, read and checked against ``VOICE_FORMAT``.

        Raises:
            ValueError: Why the recording cannot be sent.
        """
        if name is None:
            raise ValueError("the cue has no audio file")
        if self._audio_dir is None:
            raise ValueError("footage_dir is not set")
        path = self._audio_dir / name
        if path.parent != self._audio_dir:
            raise ValueError(f"{name!r} is not a file name in {self._audio_dir}")
        try:
            if path.stat().st_size > MAX_AUDIO_BYTES:
                raise ValueError(f"{path} is larger than {MAX_AUDIO_BYTES} bytes")
            audio = path.read_bytes()
        except FileNotFoundError:
            raise ValueError(f"{path} is missing") from None
        except OSError as exc:
            raise ValueError(f"{path} is unreadable: {exc}") from None
        try:
            with wave.open(io.BytesIO(audio)) as wav:
                rate, channels = wav.getframerate(), wav.getnchannels()
                width, frames = wav.getsampwidth(), wav.getnframes()
        except (wave.Error, EOFError) as exc:
            raise ValueError(f"{path.name} is not a WAV file ({exc})") from None
        wanted = (VOICE_FORMAT.sample_rate_hz, VOICE_FORMAT.channels, _SAMPLE_WIDTH)
        if (rate, channels, width) != wanted:
            found = f"{rate} Hz, {channels} channel(s), {8 * width}-bit"
            raise ValueError(f"{path.name} is {found}, not {wanted[0]} Hz mono 16-bit")
        if frames == 0:
            raise ValueError(f"{path.name} holds no audio")
        return audio

    async def _post(self, http: httpx.AsyncClient, path: str, **request: Any) -> str | None:
        """POST to the agent with retries; ``None`` if it was accepted, else why not."""
        failure = ""
        for attempt in range(RETRIES + 1):
            if attempt:
                await asyncio.sleep(self._backoff_s * 2 ** (attempt - 1))
            try:
                response = await http.post(path, **request)
            except (httpx.ConnectError, httpx.ConnectTimeout) as exc:
                failure = f"POST {path}: {type(exc).__name__}: {exc}"
                continue  # the request never reached the agent, so it is safe to send again
            except httpx.HTTPError as exc:
                return f"POST {path}: {type(exc).__name__}: {exc}"
            if response.is_success:
                return None
            failure = f"POST {path}: HTTP {response.status_code}"
            if response.status_code < 500:
                return f"{failure}: {response.text[:200]}"
        return f"{failure} (after {RETRIES + 1} attempts)"


def _agent_url(text: str) -> httpx.URL:
    """The agent's base URL, checked: an http or https scheme and a host.

    Raises:
        ValueError: If it is not such a URL.
    """
    try:
        url = httpx.URL(text)
    except httpx.InvalidURL as exc:
        raise ValueError(f"agent_base_url {text!r} is not a URL: {exc}") from None
    if url.scheme not in ("http", "https") or not url.host:
        raise ValueError(f"agent_base_url {text!r} needs an http:// or https:// scheme and a host")
    return url


async def _waiting(subscription: Subscription) -> list[Any]:
    """The messages already waiting on a subscription, taken without waiting for more."""
    messages: list[Any] = []
    while True:
        try:
            async with asyncio.timeout(0):  # a waiting message returns before the timeout fires
                messages.append(await anext(subscription))
        except (TimeoutError, StopAsyncIteration):
            return messages
