"""LLMService: the base every language model implements (Meta's ``muse-wonder-1.0``, a fake).

The agent asks it two things: the intent of a complaint the keywords did not settle (a JSON
answer), and a short reply to the occupant. Any failure (an error, a timeout, the call cap)
raises ``LLMError``, and the caller falls back to keywords or a template. A provider never logs
a prompt or an answer, only their lengths.
"""

from abc import ABC, abstractmethod


class LLMError(Exception):
    """A completion that gave no text: it failed, timed out or was refused."""


class LLMService(ABC):
    """Answers one prompt with text.

    Args:
        name: Display name, for logs.
    """

    def __init__(self, name: str) -> None:
        self.name = name

    @abstractmethod
    async def complete(self, prompt: str, timeout_s: float, *, purpose: str) -> str:
        """The model's answer to a prompt, within ``timeout_s`` wall seconds.

        Args:
            prompt: The whole prompt.
            timeout_s: Wall seconds the answer may take.
            purpose: What the call is for, for the ledger.

        Raises:
            LLMError: If no answer came in time, or the call failed or was refused.
        """
