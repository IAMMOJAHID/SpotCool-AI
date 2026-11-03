"""MetaLLM: intents and replies from ``muse-wonder-1.0`` on the Meta AI API.

Each prompt is one ``responses.create`` through the process's ``MetaClient``, so it shares the
limiter, the ledger and the run's call cap with SAM 3.1. No retries: a caller has a template or
keywords to fall back on.
"""

import asyncio

import openai

from spotcool_agent.services.llm import LLMError, LLMService
from spotcool_agent.services.meta.client import MetaClient
from spotcool_agent.services.vision import CallCapReached

LLM_MODEL = "muse-wonder-1.0"
TIMEOUT_S = 8.0
"""The longest a reply may take (a short prompt took about 2.2 s)."""


class MetaLLM(LLMService):
    """Answers with ``muse-wonder-1.0`` (see the module docstring).

    Args:
        client: The process's Meta client.
        model: The model.
    """

    def __init__(self, client: MetaClient, *, model: str = LLM_MODEL) -> None:
        super().__init__(name=f"MetaLLM({model})")
        self._client = client
        self._model = model

    async def complete(self, prompt: str, timeout_s: float = TIMEOUT_S, *, purpose: str) -> str:
        """The model's answer (see ``LLMService.complete``)."""
        if timeout_s <= 0:
            raise LLMError("no time left for the call")
        content = [{"type": "input_text", "text": prompt}]
        try:
            async with asyncio.timeout(timeout_s):
                return await self._client.respond(
                    model=self._model, purpose=purpose, timeout_s=timeout_s, content=content
                )
        except TimeoutError:
            raise LLMError(f"no answer within {timeout_s:.1f} s") from None
        except CallCapReached as exc:
            raise LLMError(str(exc)) from None
        except openai.APIStatusError as exc:
            raise LLMError(f"{type(exc).__name__} (HTTP {exc.status_code})") from None
        except openai.OpenAIError as exc:  # connection failures, the client's own timeout
            raise LLMError(type(exc).__name__) from None
        except (AttributeError, TypeError, ValueError) as exc:  # an answer of another shape
            raise LLMError(f"unreadable answer: {type(exc).__name__}") from None
