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


def typing_delay(answer: str) -> float:
    """Seconds to keep the typing indicator on after the answer is ready (0.4-3.5s)."""
    return max(0.4, min(len(answer) * 0.02, 3.5))


GROUP_REPLY_MODES = ("mentions", "all")


def parse_group_reply_update(body: object) -> tuple[bool | None, str | None]:
    """Validate the panel's group-reply request: an `enabled` switch, a `mode`, or both."""
    if not isinstance(body, dict):
        raise ValueError("So‘rov noto‘g‘ri")
    enabled = body.get("enabled")
    mode = body.get("mode")
    if enabled is None and mode is None:
        raise ValueError("enabled yoki mode kerak")
    if enabled is not None and not isinstance(enabled, bool):
        raise ValueError("enabled qiymati true yoki false bo‘lishi kerak")
    if mode is not None and mode not in GROUP_REPLY_MODES:
        raise ValueError("mode faqat mentions yoki all bo‘lishi mumkin")
    return enabled, mode
