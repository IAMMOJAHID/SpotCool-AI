"""STTService: the base every voice transcriber implements (Meta's, and a fake).

A provider takes one WAV, held in memory only, and returns what was said as text. Any failure
(an error, a timeout, an answer it cannot read) raises ``STTError``. A provider never logs what
was said, only its length.
"""

from abc import ABC, abstractmethod


class STTError(Exception):
    """A transcription that gave no text: it failed, timed out or could not be read."""


class STTService(ABC):
    """Turns one voice message into text.

    Args:
        name: Display name, for logs.
    """

    def __init__(self, name: str) -> None:
        self.name = name

    @abstractmethod
    async def transcribe(self, wav: bytes, timeout_s: float) -> str:
        """What was said in a WAV, within ``timeout_s`` wall seconds.

        Args:
            wav: The audio, WAV bytes in memory.
            timeout_s: Wall seconds the answer may take.

        Raises:
            STTError: If no text came in time, or the call failed.
        """

    async def close(self) -> None:  # noqa: B027 (a hook: a fake holds no connection)
        """Release the provider's connections. Safe to call twice."""
