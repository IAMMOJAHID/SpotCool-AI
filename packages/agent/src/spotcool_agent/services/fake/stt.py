"""FakeSTT: a fixed transcript for every voice message, for tests and offline rehearsals.

It never listens: ``text`` is "what was said", whatever the audio. By default it hears nothing
(``None``), as a call that failed, so an offline run (``--stt fake``) asks the occupant to type;
tests that need words pass them. ``delay_s`` makes it slow. It keeps only the audio's lengths.
"""

import asyncio

from spotcool_agent.services.stt import STTError, STTService


class FakeSTT(STTService):
    """A fixed transcript (see the module docstring).

    Args:
        text: What every message says; ``None`` fails.
        delay_s: Wall seconds each transcription takes.
    """

    def __init__(self, text: str | None = None, *, delay_s: float = 0.0) -> None:
        super().__init__(name="FakeSTT")
        self.text = text
        self.delay_s = delay_s
        self.heard: list[int] = []
        """The length in bytes of each message transcribed."""

    async def transcribe(self, wav: bytes, timeout_s: float) -> str:
        """The fixed text (see ``STTService.transcribe``)."""
        self.heard.append(len(wav))
        try:
            async with asyncio.timeout(timeout_s):
                await asyncio.sleep(self.delay_s)
        except TimeoutError:
            raise STTError(f"no answer within {timeout_s:.1f} s") from None
        if self.text is None:
            raise STTError("the fake transcriber hears nothing")
        return self.text
