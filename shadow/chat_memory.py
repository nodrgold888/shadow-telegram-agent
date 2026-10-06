from __future__ import annotations

import json
import re
from typing import Any

from .agents import AGENT_IDS

PROFILE_LIMITS = {
    "style": 400,
    "memory": 1400,
    "notes": 1400,
    "routines": 900,
    "agent_instructions": 1200,
}
MAX_CHAT_PROFILES = 250
MAX_CHAT_PROFILE_BYTES = 64 * 1024
_CHAT_ID = re.compile(r"-?\d{1,20}\Z")


def normalize_chat_profile(value: Any) -> dict[str, str]:
    if not isinstance(value, dict):
        raise ValueError("Chat xotirasi noto‘g‘ri formatda")
    profile: dict[str, str] = {}
    for field, limit in PROFILE_LIMITS.items():
        text = value.get(field, "")
        if not isinstance(text, str):
            raise ValueError(f"{field} matn ko‘rinishida bo‘lishi kerak")
        text = text.replace("\x00", "").replace("\r\n", "\n").strip()
        if len(text) > limit:
            raise ValueError(f"{field} {limit} belgidan oshmasin")
        profile[field] = text
    agent = value.get("agent", "")
    if not isinstance(agent, str) or agent.strip() not in AGENT_IDS:
        raise ValueError("Agent turi noto‘g‘ri")
    profile["agent"] = agent.strip()
    return profile


def normalize_chat_profiles(value: Any) -> dict[str, dict[str, str]]:
    if not isinstance(value, dict) or len(value) > MAX_CHAT_PROFILES:
        raise ValueError("Chat xotiralari ro‘yxati noto‘g‘ri yoki limitdan oshgan")
    profiles: dict[str, dict[str, str]] = {}
    for chat_id, raw_profile in value.items():
        if not isinstance(chat_id, str) or not _CHAT_ID.fullmatch(chat_id):
            raise ValueError("Chat ID noto‘g‘ri")
        profile = normalize_chat_profile(raw_profile)
        if any(profile.values()):
            profiles[chat_id] = profile
    if len(json.dumps(profiles, ensure_ascii=False).encode("utf-8")) > MAX_CHAT_PROFILE_BYTES:
        raise ValueError("Chat xotiralarining jami hajmi 64 KB dan oshmasin")
    return profiles
