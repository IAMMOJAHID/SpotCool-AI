"""MetaClient: one budget of calls to the Meta AI API (``api.meta.ai/v1``).

It wraps an ``AsyncOpenAI`` (the team's MVP client, ``app/mvp_pipeline.py`` on branch
``third_level_improvement``), created on the first call, inside the running event loop. Every
request goes through its asyncio limiter, counts against its call cap, and leaves a ``Call`` in
its ledger: the model, the purpose, the latency, whether it worked and the request id
(``call()``, which ``respond()`` and the transcriber use). The ledger never holds a request's
content, so no pixels or audio. The client itself never retries: each service decides its own
retries.

With ``trace`` on (``--no-meta-trace`` turns it off), every request also logs a ``META ->``
line when it is sent and a ``META <-`` (answered) or ``META x`` (failed) line when it ends, at
INFO, for the demo's terminal: the model, the endpoint, the request id, the rate limit left,
and what went in and came out as sizes, counts, boxes and labels, never content.

The agent keeps one per budget, so that no budget can starve another: the building's SAM calls
(5 at once, the concurrency the lead measured, ``--sam-call-cap``), the VRF loop's SAM calls
(2, ``--vrf-call-cap``), the transcriber's and language model's (2, ``--llm-call-cap``), Muse
Spark's scene readings (3, ``--scene-call-cap``) and Muse Image's renders (1,
``--render-call-cap``).
"""

import asyncio
import time
from collections import deque
from collections.abc import AsyncGenerator, Callable, Mapping
from contextlib import asynccontextmanager
from dataclasses import dataclass
from typing import Any

import openai
from loguru import logger
from openai import AsyncOpenAI
from pydantic import SecretStr

from spotcool_agent.services.vision import CallCapReached

LEDGER_SIZE = 10_000
"""Calls the ledger keeps; the oldest go first."""


@dataclass(frozen=True)
class Call:
    """One request to the Meta AI API, as the ledger keeps it.

    Parameters:
        model: The model asked.
        purpose: What for, such as ``"count humans on cam-sou"``.
        latency_s: Wall seconds from sending to the answer or the failure.
        ok: Whether an answer came back.
        request_id: The API's ``x-request-id`` for it, if it answered with one.
    """

    model: str
    purpose: str
    latency_s: float
    ok: bool
    request_id: str | None = None


@dataclass
class Trace:
    """What a request's block learns while it runs, for its ``META <-`` line and the ledger.

    Parameters:
        status: The HTTP status.
        request_id: The ``x-request-id`` header.
        response_id: The response's own id (``resp_...``).
        rate_limit: Such as ``"58/60 left"``, from the rate-limit headers.
        out: What came back, as sizes, counts, boxes or a label; never content.
    """

    status: int | None = None
    request_id: str | None = None
    response_id: str | None = None
    rate_limit: str | None = None
    out: str = ""

    def read_headers(self, headers: Mapping[str, str]) -> None:
        """Take the request id and the rate limit from an answer's headers."""
        self.request_id = headers.get("x-request-id")
        left = headers.get("x-ratelimit-remaining-requests")
        limit = headers.get("x-ratelimit-limit-requests")
        if left is not None and limit is not None:
            self.rate_limit = f"{left}/{limit} left"


def characters(text: str) -> str:
    """An answer described by its length alone."""
    return f"{len(text)} characters"


class MetaClient:
    """The process's Meta AI API client (see the module docstring).

    Args:
        api_key: The Meta API key; it is read only to build the client.
        base_url: The API's base URL.
        max_concurrency: Requests in flight at once, across every service.
        call_cap: Requests the run may make in all; ``None`` for no cap.
        trace: Log the ``META`` lines (see the module docstring).
        trace_every: Trace only every Nth call (the first, then every Nth); 1 traces all.
        make_openai: Builds the underlying client; tests pass a fake.
    """

    def __init__(
        self,
        api_key: SecretStr,
        *,
        base_url: str,
        max_concurrency: int = 5,
        call_cap: int | None = None,
        trace: bool = True,
        trace_every: int = 1,
        make_openai: Callable[[], AsyncOpenAI] | None = None,
    ) -> None:
        self._api_key = api_key
        self._base_url = base_url
        self._max_concurrency = max_concurrency
        self._call_cap = call_cap
        self._trace = trace
        self._trace_every = max(trace_every, 1)
        self._make_openai = make_openai or self._default_openai
        self._openai: AsyncOpenAI | None = None
        self._limiter: asyncio.Semaphore | None = None
        self._calls = 0
        self.ledger: deque[Call] = deque(maxlen=LEDGER_SIZE)

    @property
    def calls(self) -> int:
        """Requests sent so far."""
        return self._calls

    @property
    def cap_reached(self) -> bool:
        """Whether the run's call cap is used up."""
        return self._call_cap is not None and self._calls >= self._call_cap

    @property
    def base_url(self) -> str:
        """The API's base URL."""
        return self._base_url

    @property
    def openai(self) -> AsyncOpenAI:
        """The underlying client, made on first use, inside the running event loop."""
        if self._openai is None:
            self._openai = self._make_openai()
        return self._openai

    def new_run(self) -> None:
        """Start the call count, and so the cap, afresh for a new run; the ledger stays."""
        self._calls = 0

    async def respond(
        self,
        *,
        model: str,
        purpose: str,
        timeout_s: float,
        content: list[dict[str, Any]],
        metadata: dict[str, str] | None = None,
        sent: str = "",
        describe: Callable[[str], str] = characters,
    ) -> str:
        """Send one ``responses.create`` request and return its ``output_text``.

        Args:
            model: The model, such as ``"sam-3.1"``.
            purpose: What the call is for, for the ledger.
            timeout_s: The request's own timeout, in wall seconds.
            content: The user message's content parts.
            metadata: The request's metadata, such as SAM's ``include_confidence``.
            sent: What went in, for the trace, as sizes; never content.
            describe: What came back, for the trace, from the answer's text; never content.

        Raises:
            CallCapReached: If the call cap is used up; nothing is sent.
            openai.OpenAIError: If the request failed.
        """
        url = self._base_url.rstrip("/") + "/responses"
        async with self.call(model, purpose, url=url, sent=sent, timeout_s=timeout_s) as trace:
            raw = await self.openai.responses.with_raw_response.create(  # for its headers
                model=model,
                metadata=metadata,
                input=[{"type": "message", "role": "user", "content": content}],  # type: ignore[list-item]
                timeout=timeout_s,
            )
            response = raw.parse()
            text = response.output_text or ""  # an answer without it is a failed call
            trace.status, trace.response_id = raw.status_code, getattr(response, "id", None)
            trace.read_headers(raw.headers)
            if self._trace:
                try:
                    trace.out = describe(text)
                except (TypeError, ValueError):  # the service refuses the answer itself
                    trace.out = "unreadable answer"
            return text

    @asynccontextmanager
    async def call(
        self, model: str, purpose: str, *, url: str = "", sent: str = "", timeout_s: float = 0.0
    ) -> AsyncGenerator[Trace, None]:
        """One request's turn: the limiter, the call cap, the count, its trace and ledger line.

        The call is ``ok`` in the ledger when the block ends without an exception. The block
        fills the ``Trace`` it is given.

        Args:
            model: The model asked.
            purpose: What the call is for.
            url: The endpoint, for the trace.
            sent: What went in, for the trace, as sizes; never content.
            timeout_s: The caller's timeout, to tell a timeout from a stop in the trace.

        Raises:
            CallCapReached: If the call cap is used up; the block does not run.
        """
        entered = time.perf_counter()  # the caller's timeout runs from about here
        if self._limiter is None:
            self._limiter = asyncio.Semaphore(self._max_concurrency)
        async with self._limiter:
            if self.cap_reached:
                raise CallCapReached(f"the run's cap of {self._call_cap} Meta calls is reached")
            self._calls += 1
            trace = Trace()
            traced = self._trace and (self._calls - 1) % self._trace_every == 0
            shown = f" (1 in {self._trace_every} shown)" if self._trace_every > 1 else ""
            if traced:
                logger.info(f"META -> {model}{shown} | POST {url} | {purpose} | in: {sent or '-'}")
            started, ok = time.perf_counter(), False
            try:
                yield trace
                ok = True
            except BaseException as exc:
                trace.request_id = trace.request_id or getattr(exc, "request_id", None)
                if traced:
                    now = time.perf_counter()
                    why = failure(exc, now - started, now - entered, timeout_s, trace)
                    rid = f" | request-id {trace.request_id}" if trace.request_id else ""
                    logger.info(f"META x  {model} | {why}{rid} | {purpose}")
                raise
            finally:
                latency_s = round(time.perf_counter() - started, 3)
                self.ledger.append(Call(model, purpose, latency_s, ok, trace.request_id))
            if traced:
                logger.info(f"META <- {model} | {answered(trace, latency_s)}")

    async def close(self) -> None:
        """Close the underlying client. Safe to call twice."""
        client, self._openai = self._openai, None
        if client is not None:
            await client.close()

    def _default_openai(self) -> AsyncOpenAI:
        return AsyncOpenAI(
            base_url=self._base_url,
            api_key=self._api_key.get_secret_value(),
            max_retries=0,  # SamVision retries a 429 once itself; nothing else is retried
        )


def answered(trace: Trace, latency_s: float) -> str:
    """The ``META <-`` line after the model's name."""
    parts = [
        f"{trace.status or 'ok'} in {latency_s:.2f} s",
        f"request-id {trace.request_id or '-'}",
    ]
    if trace.response_id:
        parts.append(f"response {trace.response_id}")
    parts.append(f"out: {trace.out or '-'}")
    if trace.rate_limit:
        parts.append(f"rate limit {trace.rate_limit}")
    return " | ".join(parts)


def failure(
    exc: BaseException, latency_s: float, waited_s: float, timeout_s: float, trace: Trace
) -> str:
    """Why a request failed, for the ``META x`` line: a type and a status, never a message.

    ``asyncio.timeout`` ends a call with a cancel, so a cancel once the caller's timeout has
    passed is that timeout; an earlier one is a stop.
    """
    timed_out = isinstance(exc, (TimeoutError, openai.APITimeoutError))
    if timed_out or (isinstance(exc, asyncio.CancelledError) and waited_s >= timeout_s - 0.05):
        return f"timeout after {timeout_s or latency_s:.1f} s"
    if isinstance(exc, asyncio.CancelledError):
        return f"cancelled after {latency_s:.2f} s"
    status = getattr(exc, "status_code", None) or trace.status
    return f"{type(exc).__name__}{f' (HTTP {status})' if status else ''} after {latency_s:.2f} s"
