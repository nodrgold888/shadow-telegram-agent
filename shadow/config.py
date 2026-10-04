from __future__ import annotations

import os
from dataclasses import dataclass


def _integer(name: str, default: int | None = None) -> int | None:
    raw = os.getenv(name, "").strip()
    if not raw:
        return default
    try:
        return int(raw)
    except ValueError as exc:
        raise ValueError(f"{name} must be an integer") from exc


def _chat_ids(raw: str) -> frozenset[int] | str:
    value = raw.strip()
    if value == "*":
        return "*"
    if not value:
        return frozenset()
    try:
        return frozenset(int(item.strip()) for item in value.split(",") if item.strip())
    except ValueError as exc:
        raise ValueError("APPROVED_CHAT_IDS must contain numeric IDs separated by commas") from exc


def _boolean(name: str, default: bool = False) -> bool:
    raw = os.getenv(name, "").strip().lower()
    if not raw:
        return default
    if raw in {"1", "true", "yes", "on"}:
        return True
    if raw in {"0", "false", "no", "off"}:
        return False
    raise ValueError(f"{name} must be true or false")


@dataclass(frozen=True)
class Settings:
    telegram_api_id: int | None
    telegram_api_hash: str
    telegram_session: str
    openai_api_key: str
    openai_model: str
    approved_chat_ids: frozenset[int] | str
    reply_enabled: bool
    group_reply_mode: str
    context_messages: int
    max_reply_chars: int
    admin_token: str
    setup_token: str

    @classmethod
    def from_env(cls) -> "Settings":
        mode = os.getenv("GROUP_REPLY_MODE", "mentions").strip().lower()
        if mode not in {"mentions", "all"}:
            raise ValueError("GROUP_REPLY_MODE must be 'mentions' or 'all'")
        return cls(
            telegram_api_id=_integer("TELEGRAM_API_ID"),
            telegram_api_hash=os.getenv("TELEGRAM_API_HASH", "").strip(),
            telegram_session=os.getenv("TELEGRAM_SESSION", "").strip(),
            openai_api_key=os.getenv("OPENAI_API_KEY", "").strip(),
            openai_model=os.getenv("OPENAI_MODEL", "gpt-5-mini").strip(),
            approved_chat_ids=_chat_ids(os.getenv("APPROVED_CHAT_IDS", "")),
            reply_enabled=_boolean("REPLY_ENABLED", False),
            group_reply_mode=mode,
            context_messages=max(2, min(_integer("CONTEXT_MESSAGES", 12) or 12, 30)),
            max_reply_chars=max(500, min(_integer("MAX_REPLY_CHARS", 3800) or 3800, 4000)),
            admin_token=os.getenv("ADMIN_TOKEN", "").strip(),
            setup_token=os.getenv("SETUP_TOKEN", "").strip(),
        )

    @property
    def telegram_api_ready(self) -> bool:
        return bool(self.telegram_api_id and self.telegram_api_hash)

    @property
    def configured(self) -> bool:
        telegram_ready = bool(self.telegram_api_ready and self.telegram_session)
        reply_ready = bool(self.openai_api_key and self.approved_chat_ids)
        return bool(telegram_ready and (not self.reply_enabled or reply_ready))
