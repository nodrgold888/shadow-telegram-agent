from __future__ import annotations

import json
import re
from typing import Any

from .agents import AGENT_IDS, MAX_CHAT_AGENTS

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
    raw = value.get("agents")
    if raw is None:
        raw = value.get("agent", "")
    if isinstance(raw, str):
        items = [item.strip() for item in raw.split(",")]
    elif isinstance(raw, list) and all(isinstance(item, str) for item in raw):
        items = [item.strip() for item in raw]
    else:
        raise ValueError("Agent turi noto‘g‘ri")
    agents: list[str] = []
    for item in items:
        if not item:
            continue
        if item not in AGENT_IDS:
            raise ValueError("Agent turi noto‘g‘ri")
        if item not in agents:
            agents.append(item)
    if len(agents) > MAX_CHAT_AGENTS:
        raise ValueError(f"Bir chatga {MAX_CHAT_AGENTS} tagacha tur tanlash mumkin")
    profile["agent"] = agents[0] if agents else ""
    profile["agents"] = ",".join(agents)
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
