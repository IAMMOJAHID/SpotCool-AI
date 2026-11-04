"""Narrator: Muse Wonder sums up a building step's decisions in one plain sentence.

After a step whose writes changed what the agent holds, ``narrate()`` starts one background
call to the language model (``TIMEOUT_S``, on the language services' budget), grounded on the
step's decisions only: each zone written, its reason and its count. The answer, cut to one
sentence, goes out as an ``explanation`` marked "Muse Wonder:". One call at a time: a step
while one is under way is not narrated. A call that fails says nothing; the template
explanations already cover the step. The narrator never writes, and the step never waits.

A sentence with a percentage, or a number the step's decisions don't hold, is dropped: the
dashboard shows no figure the model made up (AGENTS.md, Honesty). At most ``MAX_CALLS`` per
process, so the narration can't use up the complaints' language budget.
"""

import asyncio
import re
from collections.abc import Sequence

from loguru import logger

from spotcool_agent.actuator import Publish
from spotcool_agent.domain.events import Explanation
from spotcool_agent.loops.complaints import trimmed
from spotcool_agent.services.llm import LLMError, LLMService
from spotcool_contracts.events import EventType

TIMEOUT_S = 8.0
"""The longest a narration may take: the language model's reply budget."""
MAX_CALLS = 60
"""The most narrations per process: the language budget is 200, shared with complaints."""
NUMBER = re.compile(r"\d+(?:[.,]\d+)?")
MARK = "Muse Wonder:"
"""What every narration starts with, so the panel shows who wrote it."""


def narration_prompt(decisions: Sequence[str]) -> str:
    """The prompt: the step's decisions as facts, and the rules for the sentence."""
    facts = "\n".join(f"- {line}" for line in decisions)
    return (
        "You narrate an office building's HVAC agent for the facility manager's dashboard. "
        "In one short, plain-language sentence, say what the agent just did and why. Use only "
        "these facts; add no numbers, savings or claims of your own.\n"
        f"Decisions this step:\n{facts}"
    )


def grounded(sentence: str, decisions: Sequence[str]) -> bool:
    """Whether every number in the sentence is one of the decisions', and it has no percentage."""
    if "%" in sentence or "percent" in sentence.lower():
        return False
    known = set(NUMBER.findall(" ".join(decisions)))
    return all(number in known for number in NUMBER.findall(sentence))


class Narrator:
    """Narrates decisions in the background (see the module docstring).

    Args:
        llm: The language model.
        publish: Where the narration goes.
    """

    def __init__(self, llm: LLMService, publish: Publish) -> None:
        self._llm = llm
        self._publish = publish
        self._talking: asyncio.Task[None] | None = None
        self._calls = 0

    def narrate(self, decisions: Sequence[str], t_s: float) -> None:
        """Start one narration of a step's decisions, unless one is under way; never waits.

        Args:
            decisions: One line per zone written: the zone, its count and the reason.
            t_s: The stamp of the explanation: the step's start.
        """
        if not decisions or self._talking is not None or self._calls >= MAX_CALLS:
            return
        self._calls += 1
        self._talking = asyncio.create_task(self._say(decisions, t_s))

    async def stop(self) -> None:
        """Cancel a narration under way. Safe to call twice."""
        talking = self._talking
        if talking is not None:
            talking.cancel()
            await asyncio.gather(talking, return_exceptions=True)

    async def _say(self, decisions: Sequence[str], t_s: float) -> None:
        try:
            answer = await self._llm.complete(
                narration_prompt(decisions), TIMEOUT_S, purpose="narrate"
            )
        except LLMError as exc:
            logger.info(f"narration: none this step ({exc})")
            return
        finally:
            self._talking = None
        sentence = trimmed(answer, sentences=1)
        if sentence and not grounded(sentence, decisions):
            logger.info("narration: dropped, it named a figure the decisions don't hold")
        elif sentence:
            self._publish(EventType.EXPLANATION, Explanation(text=f"{MARK} {sentence}"), t_s)
