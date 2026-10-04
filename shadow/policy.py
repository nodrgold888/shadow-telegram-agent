from __future__ import annotations

from collections.abc import Collection


def chat_is_approved(chat_id: int, approved: Collection[int] | str) -> bool:
    return approved == "*" or chat_id in approved


def group_message_needs_reply(
    *,
    is_private: bool,
    mode: str,
    mentioned: bool,
    replying_to_shadow: bool,
) -> bool:
    if is_private:
        return True
    if mode == "all":
        return True
    return mentioned or replying_to_shadow


def split_telegram_message(text: str, limit: int = 3800) -> list[str]:
    cleaned = text.strip()
    if not cleaned:
        return []
    chunks: list[str] = []
    while len(cleaned) > limit:
        cut = cleaned.rfind("\n", 0, limit)
        if cut < limit // 2:
            cut = cleaned.rfind(" ", 0, limit)
        if cut < limit // 2:
            cut = limit
        chunks.append(cleaned[:cut].strip())
        cleaned = cleaned[cut:].strip()
    if cleaned:
        chunks.append(cleaned)
    return chunks
