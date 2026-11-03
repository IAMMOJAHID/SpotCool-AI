"""MetaClient: one budget of calls to the Meta AI API (``api.meta.ai/v1``).

It wraps an ``AsyncOpenAI`` (Person A's MVP client, ``app/mvp_pipeline.py``), created on the
first call, inside the running event loop. Every request goes through its asyncio limiter,
counts against its call cap, and leaves a ``Call`` in its ledger: the model, the purpose, the
latency and whether it worked (``call()``, which ``respond()`` and the transcriber use). The
ledger never holds a request's content, so no pixels or audio. The client itself never
retries: each service decides its own retries.

The agent keeps three, so that no budget can starve another: the building's SAM calls (5 at
once, the concurrency the lead measured, ``--sam-call-cap``), the VRF loop's SAM calls (2,
``--vrf-call-cap``), and the transcriber's and language model's (2, ``--llm-call-cap``).
"""

import asyncio
import time
from collections import deque
from collections.abc import AsyncGenerator, Callable
from contextlib import asynccontextmanager
from dataclasses import dataclass
from typing import Any

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
    """

    model: str
    purpose: str
    latency_s: float
    ok: bool


class MetaClient:
    """The process's Meta AI API client (see the module docstring).

    Args:
        api_key: The Meta API key; it is read only to build the client.
        base_url: The API's base URL.
        max_concurrency: Requests in flight at once, across every service.
        call_cap: Requests the run may make in all; ``None`` for no cap.
        make_openai: Builds the underlying client; tests pass a fake.
    """

    def __init__(
        self,
        api_key: SecretStr,
        *,
        base_url: str,
        max_concurrency: int = 5,
        call_cap: int | None = None,
        make_openai: Callable[[], AsyncOpenAI] | None = None,
    ) -> None:
        self._api_key = api_key
        self._base_url = base_url
        self._max_concurrency = max_concurrency
        self._call_cap = call_cap
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
    ) -> str:
        """Send one ``responses.create`` request and return its ``output_text``.

        Args:
            model: The model, such as ``"sam-3.1"``.
            purpose: What the call is for, for the ledger.
            timeout_s: The request's own timeout, in wall seconds.
            content: The user message's content parts.
            metadata: The request's metadata, such as SAM's ``include_confidence``.

        Raises:
            CallCapReached: If the call cap is used up; nothing is sent.
            openai.OpenAIError: If the request failed.
        """
        async with self.call(model, purpose):
            if self._openai is None:
                self._openai = self._make_openai()
            response = await self._openai.responses.create(
                model=model,
                metadata=metadata,
                input=[{"type": "message", "role": "user", "content": content}],  # type: ignore[list-item]
                timeout=timeout_s,
            )
            return response.output_text or ""  # an answer without it is a failed call

    @asynccontextmanager
    async def call(self, model: str, purpose: str) -> AsyncGenerator[None, None]:
        """One request's turn: the limiter, the call cap, the count and its ledger line.

        The call is ``ok`` in the ledger when the block ends without an exception.

        Raises:
            CallCapReached: If the call cap is used up; the block does not run.
        """
        if self._limiter is None:
            self._limiter = asyncio.Semaphore(self._max_concurrency)
        async with self._limiter:
            if self.cap_reached:
                raise CallCapReached(f"the run's cap of {self._call_cap} Meta calls is reached")
            self._calls += 1
            started, ok = time.perf_counter(), False
            try:
                yield
                ok = True
            finally:
                latency_s = round(time.perf_counter() - started, 3)
                self.ledger.append(Call(model, purpose, latency_s, ok))

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
