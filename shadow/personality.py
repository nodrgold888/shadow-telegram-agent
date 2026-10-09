"""Small, meaning-safe style adjustments for casual Uzbek Telegram replies."""
from __future__ import annotations

import random

from .humanize import phone_text

# Only exact, short acknowledgements are randomized; arbitrary answers never get
# a mood word, emoji, or extra promise appended to them.
_SHORT_REPLY_VARIANTS = {
    "mayli": ("mayli", "xo'p", "bo'pti"),
    "xop": ("xo'p", "mayli", "bo'pti"),
    "bopti": ("bo'pti", "mayli", "xo'p"),
    "arzimaydi": ("arzimaydi", "hechqisi yo'q"),
    "hechqisi yoq": ("hechqisi yo'q", "arzimaydi"),
    "ha, shunaqa": ("ha, shunaqa", "ha, xuddi shunaqa"),
}


def normalize_uz_text(text: str) -> str:
    """Compatibility alias for the shared phone-text normalizer."""
    return phone_text(text)


def personality_tweak(text: str) -> str:
    """Normalize phone text and occasionally vary an exact interchangeable acknowledgement."""
    text = normalize_uz_text(text)
    if not text or random.random() >= 0.2:
        return text
    variants = _SHORT_REPLY_VARIANTS.get(text.casefold())
    return normalize_uz_text(random.choice(variants)) if variants else text
