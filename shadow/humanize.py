from __future__ import annotations

import random
import re

from .policy import typing_delay

# The model separates texts it would naturally send as separate messages with a line
# containing only "||". At most three are sent, one after another, like real texting.
MESSAGE_BREAK = re.compile(r"\s*\n?\s*\|\|\s*\n?\s*")
MAX_PARTS = 3


def split_parts(answer: str, max_parts: int = MAX_PARTS) -> list[str]:
    """Split an answer into the consecutive messages to send. Never returns empty parts."""
    parts = [part.strip() for part in MESSAGE_BREAK.split(answer or "") if part.strip()]
    if len(parts) > max_parts:
        parts = parts[: max_parts - 1] + ["\n\n".join(parts[max_parts - 1:])]
    return parts


def read_delay(rng: random.Random | None = None) -> float:
    """Short pause before "typing…" starts, as if the message was just read (0.5-1.6 s)."""
    return (rng or random).uniform(0.5, 1.6)


def human_typing_delay(text: str, rng: random.Random | None = None) -> float:
    """Typing pause for one message: proportional to its length, with natural variation."""
    return min(typing_delay(text) * (rng or random).uniform(0.85, 1.25), 4.5)
