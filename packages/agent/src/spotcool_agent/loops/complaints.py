"""ComplaintLoop: answers each complaint the server queued with a template ``chat`` reply.

The reply acknowledges the complaint and is logged; it does not act. A voice message is not
transcribed yet, so its reply names only its length; the ``meta`` text, the script line, is
logged and never used as what was said.
"""

import asyncio

from loguru import logger

from spotcool_agent.actuator import Publish
from spotcool_agent.domain.events import Chat
from spotcool_agent.transports.server import Complaint
from spotcool_contracts.events import EventType


def reply(complaint: Complaint) -> str:
    """The template reply to a complaint."""
    if complaint.channel == "voice":
        return f"Thanks, I got your voice message ({complaint.audio_s:.1f} s). I'm looking into it."
    text = complaint.complaint.text if complaint.complaint else ""
    return f'Thanks, I got your message: "{text}". I\'m looking into it.'


class ComplaintLoop:
    """Takes the server's complaints and publishes a reply to each, until cancelled.

    Args:
        queue: The server's complaint queue.
        publish: Where the ``chat`` replies go.
    """

    def __init__(self, queue: "asyncio.Queue[Complaint]", publish: Publish) -> None:
        self._queue = queue
        self._publish = publish
        self.answered = 0
        """Complaints answered."""

    async def run(self) -> None:
        """Answer complaints as they come, until cancelled."""
        while True:
            complaint = await self._queue.get()
            said = complaint.complaint
            room = said.room_hint if said and said.room_hint else "no room given"
            line = ascii(said.text) if said else "no text"
            logger.info(f"complaint by {complaint.channel} ({room}): {line}")
            text = reply(complaint)
            self._publish(EventType.CHAT, Chat(text=text), said.sim_time_s if said else None)
            self.answered += 1
