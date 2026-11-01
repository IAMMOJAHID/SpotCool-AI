"""Messages the world's simulated occupants send to the agent.

The agent exposes two endpoints. ``POST /chat`` takes a ``ComplaintText`` JSON body.
``POST /voice`` takes ``multipart/form-data`` with an ``audio`` file in ``VOICE_FORMAT`` and an
optional ``meta`` field holding a ``ComplaintText`` JSON string (its ``text`` is the script line,
for logging only; the agent must transcribe the audio itself).
"""

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

AGENT_CHAT_PATH = "/chat"
AGENT_VOICE_PATH = "/voice"


class AudioFormat(BaseModel):
    """Audio encoding for voice complaints."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    container: Literal["wav"] = "wav"
    sample_rate_hz: int = 16_000
    channels: int = 1
    sample_format: Literal["s16le"] = "s16le"


VOICE_FORMAT = AudioFormat()


class ComplaintText(BaseModel):
    """A comfort complaint in plain language.

    Parameters:
        text: What the occupant said or typed.
        source: ``occupant-sim`` for scripted occupants, ``occupant`` for a real person.
        room_hint: Where the occupant is, if the sender knows (the agent may ignore it).
        sim_time_s: Simulation time the complaint belongs to, when sent by the world.
    """

    model_config = ConfigDict(extra="forbid")

    text: str = Field(min_length=1, max_length=280)
    source: Literal["occupant-sim", "occupant"] = "occupant-sim"
    room_hint: str | None = None
    sim_time_s: float | None = None
