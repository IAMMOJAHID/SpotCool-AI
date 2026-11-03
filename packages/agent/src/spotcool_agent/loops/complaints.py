"""ComplaintLoop: each complaint heard, understood, acted on in the meeting room, and answered.

The server answers ``/chat`` and ``/voice`` 202 at once and queues the complaint; this loop
takes them one at a time:

1. **Heard.** A voice message is transcribed by the ``STTService`` within ``STT_TIMEOUT_S``;
   without a transcript the occupant is asked to type it. The ``meta`` text, the script line,
   is never used as what was said; its ``room_hint`` and ``source`` are.
2. **Understood.** Keywords first, then the language model for anything they do not settle
   (``processors/intents.py``).
3. **Acted on, in the meeting room only.** A complaint is about the meeting room when its
   ``room_hint`` names it, or when a real occupant (the dashboard) gives no room. One about
   another zone, or with no intent, changes nothing. With the VRF loop on, the intent acts on
   the meeting room's unit (``VrfLoop.act``); the BMS zones are never written from here.
4. **Answered.** The language model writes the reply from the live state (Person A's
   ``state_card`` idea: the zone counts of the last perception, the unit's room temperature,
   setpoint, fan and vanes, and the action just taken), in at most two sentences within
   ``REPLY_TIMEOUT_S``; otherwise, or without a model, a template does
   ("Thanks, I've raised the meeting room's setpoint to 25.0 degC.").

Each complaint gets one ``chat`` reply and one ``explanation`` of what was understood and done;
the writes themselves are the VRF actuator's ``decision`` events. The run log carries only the
complaint's length, its intent and the action, never what was said.
"""

import asyncio
import json
import re
from collections.abc import Callable, Mapping

from loguru import logger

from spotcool_agent.actuator import Publish
from spotcool_agent.domain.events import Chat, Explanation
from spotcool_agent.loops.vrf import Action, VrfLoop
from spotcool_agent.processors.intents import CLASSIFY_TIMEOUT_S, Intent, classify
from spotcool_agent.services.llm import LLMError, LLMService
from spotcool_agent.services.stt import STTError, STTService
from spotcool_agent.transports.server import Complaint
from spotcool_contracts import ZONES, Zone
from spotcool_contracts.calendar import ROOMS
from spotcool_contracts.events import EventType

STT_TIMEOUT_S = 6.0
"""The longest a transcription may take."""
REPLY_TIMEOUT_S = 8.0
"""The longest the language model may take to write a reply."""
MAX_REPLY_CHARS = 400
"""A model's reply is cut to two sentences, and to this many characters."""
MEETING_ROOM = next(room for room, zone in ROOMS.items() if zone is Zone.SOU)
"""The room the VRF unit serves: ``meeting-south``."""
UNHEARD = "Sorry, I couldn't hear that; please type it."
SORRY = "Sorry, something went wrong with your message. Could you try again?"
NOT_HERE = (
    "noted it; I only control the meeting room's unit, so I haven't changed anything for your area"
)
NOT_ON = (
    "noted it, but the meeting room's unit isn't under my control right now, so nothing changed"
)
UNCLEAR = "left the AC as it is, as I couldn't tell what you'd like changed"


def template(action: Action) -> str:
    """The reply without a language model, such as ``Thanks, I've raised the setpoint.``."""
    return f"Thanks, I've {action.summary}."


def reply_prompt(text: str, intent: Intent, action: Action, facts: Mapping[str, object]) -> str:
    """The prompt for a reply grounded on the live state (A's ``state_card`` idea)."""
    card = {"complaint": text, "understood_as": intent, "action_taken": action.summary, **facts}
    return (
        "You are SpotCool, the AI that runs this office's air conditioning. An occupant has "
        "complained. Reply to them in at most 2 short sentences, in plain words, without "
        "markdown. Say what was done, using only these facts, and promise nothing else.\n"
        f"Facts: {json.dumps(card)}"
    )


def trimmed(answer: str) -> str:
    """A model's reply cut to two sentences and ``MAX_REPLY_CHARS``; empty if it said nothing."""
    sentences = re.split(r"(?<=[.!?])\s+", " ".join(answer.split()))
    return " ".join(sentences[:2])[:MAX_REPLY_CHARS].strip()


class ComplaintLoop:
    """Takes the server's complaints and answers each, until cancelled (see the module docstring).

    Args:
        queue: The server's complaint queue.
        publish: Where the ``chat`` replies and explanations go.
        stt: The transcriber; without one a voice message is not understood.
        llm: The language model for intents and replies; ``None`` for keywords and templates.
        vrf: The meeting room's loop, which the intents act on; ``None`` changes nothing.
        counts: The zone counts of the last perception, for the reply's facts.
        stt_timeout_s: The longest a transcription may take.
        classify_timeout_s: The longest the model may take to classify.
        reply_timeout_s: The longest the model may take to reply.
    """

    def __init__(
        self,
        queue: "asyncio.Queue[Complaint]",
        publish: Publish,
        *,
        stt: STTService | None = None,
        llm: LLMService | None = None,
        vrf: VrfLoop | None = None,
        counts: Callable[[], Mapping[Zone, int]] | None = None,
        stt_timeout_s: float = STT_TIMEOUT_S,
        classify_timeout_s: float = CLASSIFY_TIMEOUT_S,
        reply_timeout_s: float = REPLY_TIMEOUT_S,
    ) -> None:
        self._queue = queue
        self._publish = publish
        self._stt = stt
        self._llm = llm
        self._vrf = vrf
        self._counts = counts
        self._stt_timeout_s = stt_timeout_s
        self._classify_timeout_s = classify_timeout_s
        self._reply_timeout_s = reply_timeout_s
        self.answered = 0
        """Complaints answered."""

    async def run(self) -> None:
        """Answer complaints as they come, until cancelled; one bad complaint never ends it."""
        while True:
            complaint = await self._queue.get()
            said = complaint.complaint
            try:
                text = await self.handle(complaint)
            except Exception as exc:
                logger.error(f"complaint failed: {type(exc).__name__}: {exc}")
                text = SORRY
            self._publish(EventType.CHAT, Chat(text=text), said.sim_time_s if said else None)
            self.answered += 1

    async def handle(self, complaint: Complaint) -> str:
        """Hear, understand and act on one complaint; return the reply (steps 1-4)."""
        said = complaint.complaint
        t_s = said.sim_time_s if said else None
        room = said.room_hint if said else None
        if complaint.channel == "voice":
            text = await self._transcribe(complaint)
            if text is None:
                return UNHEARD
        else:
            text = said.text if said else ""
        here = room == MEETING_ROOM or (
            room is None and (said is None or said.source == "occupant")
        )
        intent, how = await classify(text, self._llm, self._classify_timeout_s)
        if not here:
            action = Action(intent, NOT_HERE, changed=False)
        elif intent == "none":
            action = Action(intent, UNCLEAR, changed=False)
        elif self._vrf is None:
            action = Action(intent, NOT_ON, changed=False)
        else:
            action = await self._vrf.act(intent)
        where = room or ("the meeting room" if here else "no room given")
        logger.info(
            f"complaint by {complaint.channel} ({where}): {len(text)} characters, intent "
            f"{intent} by {how} -> {action.summary}"
        )
        explanation = (
            f"Complaint by {complaint.channel} ({where}): {intent} -> I've {action.summary}"
        )
        self._publish(EventType.EXPLANATION, Explanation(text=explanation), t_s)
        return await self._reply(text, intent, action)

    async def _transcribe(self, complaint: Complaint) -> str | None:
        """The voice message's text, or ``None`` if it could not be heard."""
        if self._stt is None or not complaint.audio:
            return None
        try:
            text = await self._stt.transcribe(complaint.audio, self._stt_timeout_s)
        except STTError as exc:
            logger.warning(f"complaint by voice: {self._stt.name} gave no text: {exc}")
            return None
        return text or None

    async def _reply(self, text: str, intent: Intent, action: Action) -> str:
        """The language model's reply on the live state, or the template."""
        if self._llm is None:
            return template(action)
        facts: dict[str, object] = {}
        if self._counts is not None:
            facts["people_seen_per_zone"] = {
                ZONES[zone].display_name: n for zone, n in self._counts().items()
            }
        if self._vrf is not None:
            facts["meeting_room_unit"] = self._vrf.state()  # last known: never waits
        prompt = reply_prompt(text, intent, action, facts)
        try:
            answer = await self._llm.complete(prompt, self._reply_timeout_s, purpose="reply")
        except LLMError as exc:
            logger.info(f"complaint: {self._llm.name} gave no reply, the template answers: {exc}")
            return template(action)
        return trimmed(answer) or template(action)
