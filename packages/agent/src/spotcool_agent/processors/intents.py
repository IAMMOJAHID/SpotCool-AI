"""Intents: what a complaint asks for, by keywords first, then by the language model.

The intents are ``cold``, ``hot``, ``draft`` and ``stuffy``, or ``none``. A complaint whose
words name exactly one intent is settled by its keywords (``keyword_intents``); one that names
none or several goes to the language model with a JSON-only prompt (``classify_prompt``), and an
answer that is late, fails or is not one of the intents counts as ``none``.
"""

import json
import re
from typing import Literal

from loguru import logger

from spotcool_agent.services.llm import LLMError, LLMService

Intent = Literal["cold", "hot", "draft", "stuffy", "none"]
INTENTS: tuple[Intent, ...] = ("cold", "hot", "draft", "stuffy")
CLASSIFY_TIMEOUT_S = 4.0
"""The longest the language model may take to classify."""

KEYWORDS: dict[Intent, tuple[str, ...]] = {
    "cold": ("cold", "colder", "freezing", "frozen", "chilly", "chilled", "icy", "shivering",
             "warmer"),
    "hot": ("hot", "hotter", "warm", "boiling", "roasting", "sweating", "sweaty", "sweltering",
            "stifling", "cooler"),
    "draft": ("draft", "drafty", "draught", "draughty", "breeze", "breezy", "windy", "blowing",
              "on my face", "in my face"),
    "stuffy": ("stuffy", "stale", "airless", "muggy", "humid", "no air", "fresh air"),
}  # fmt: skip
"""Words and simple synonyms per intent, matched whole and case-blind ("warmer" asks for heat)."""
_PATTERNS: dict[Intent, re.Pattern[str]] = {
    intent: re.compile(r"\b(?:" + "|".join(map(re.escape, words)) + r")\b", re.IGNORECASE)
    for intent, words in KEYWORDS.items()
}


def keyword_intents(text: str) -> set[Intent]:
    """Every intent whose keywords the text names."""
    return {intent for intent, pattern in _PATTERNS.items() if pattern.search(text)}


def classify_prompt(text: str) -> str:
    """The JSON-only prompt that asks the language model for a complaint's intent."""
    return (
        "Classify an office occupant's comfort complaint about the air conditioning. "
        'Answer with JSON only, exactly {"intent": "<intent>"}, where <intent> is one of: '
        '"cold" (they feel too cold), "hot" (too hot), "draft" (air is blowing on them), '
        '"stuffy" (the air feels stale or humid), "none" (anything else).\n'
        f"Complaint: {json.dumps(text)}"
    )


def parse_intent(answer: str) -> Intent:
    """The intent in the model's answer: its first JSON object's ``intent``, else ``none``."""
    match = re.search(r"\{.*?\}", answer, re.DOTALL)
    if match is None:
        return "none"
    try:
        found = json.loads(match.group(0))
    except ValueError:
        return "none"
    intent = found.get("intent") if isinstance(found, dict) else None
    return intent if intent in INTENTS else "none"


async def classify(
    text: str, llm: LLMService | None, timeout_s: float = CLASSIFY_TIMEOUT_S
) -> tuple[Intent, str]:
    """A complaint's intent, and how it was found: ``keywords``, ``llm`` or ``unsettled``.

    Args:
        text: What the occupant said or typed.
        llm: The language model for what the keywords do not settle; ``None`` for none.
        timeout_s: The longest the model may take.
    """
    named = keyword_intents(text)
    if len(named) == 1:
        return next(iter(named)), "keywords"
    if llm is None:
        return "none", "unsettled"
    try:
        answer = await llm.complete(classify_prompt(text), timeout_s, purpose="classify")
    except LLMError as exc:
        logger.info(f"complaint: {llm.name} gave no intent: {exc}")
        return "none", "unsettled"
    return parse_intent(answer), "llm"
