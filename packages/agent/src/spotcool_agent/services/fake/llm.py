"""FakeLLM: scripted answers, for tests and offline rehearsals.

``answers`` maps a purpose (``classify`` or ``reply``) to its answer; a purpose it does not
name gets ``default``. ``None`` fails like a call that timed out; ``delay_s`` makes it slow. It
keeps each prompt it was asked, for tests.
"""

import asyncio
from collections.abc import Mapping

from spotcool_agent.services.llm import LLMError, LLMService

REHEARSAL_REPLY = "Thanks, I'm on it."
"""What it answers by default: a reply, and no intent (``--llm fake``)."""


class FakeLLM(LLMService):
    """Scripted answers by purpose (see the module docstring).

    Args:
        answers: Each purpose's answer; ``None`` fails.
        default: The answer to any other purpose; ``None`` fails.
        delay_s: Wall seconds each answer takes.
    """

    def __init__(
        self,
        answers: Mapping[str, str | None] | None = None,
        *,
        default: str | None = REHEARSAL_REPLY,
        delay_s: float = 0.0,
    ) -> None:
        super().__init__(name="FakeLLM")
        self.answers = dict(answers or {})
        self.default = default
        self.delay_s = delay_s
        self.prompts: list[tuple[str, str]] = []
        """Each call's purpose and prompt."""

    async def complete(self, prompt: str, timeout_s: float, *, purpose: str) -> str:
        """The scripted answer (see ``LLMService.complete``)."""
        self.prompts.append((purpose, prompt))
        try:
            async with asyncio.timeout(timeout_s):
                await asyncio.sleep(self.delay_s)
        except TimeoutError:
            raise LLMError(f"no answer within {timeout_s:.1f} s") from None
        answer = self.answers.get(purpose, self.default)
        if answer is None:
            raise LLMError("scripted failure")
        return answer
