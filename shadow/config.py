from __future__ import annotations

import json
import os
import re
from dataclasses import dataclass, field

from .persist import load_local_settings

# (API model ID, label shown in the dashboard). The two newest IDs are taken from the
# names shown in the OpenAI app ("GPT-5.6 Luna", "GPT-Reserve") and have not been
# confirmed against the API; the dashboard's "AI ni tekshirish" button verifies an ID.
MODEL_CATALOG = (
    ("gpt-5-mini", "GPT-5 mini"),
    ("gpt-6-luna", "GPT-6 Luna"),
    ("gpt-5.6-luna", "GPT-5.6 Luna"),
    ("gpt-reserve", "GPT-Reserve"),
)
_MODEL_ID = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:-]{0,63}$")


def _extra_models() -> tuple[tuple[str, str], ...]:
    """Extra API model IDs from EXTRA_OPENAI_MODELS (comma separated), so a new model
    can be offered in the dashboard without a code change."""
    known = {model_id for model_id, _ in MODEL_CATALOG}
    extras = []
    for raw in os.getenv("EXTRA_OPENAI_MODELS", "").split(","):
        model_id = raw.strip()
        if model_id and model_id not in known and _MODEL_ID.match(model_id):
            known.add(model_id)
            extras.append((model_id, model_id))
    return tuple(extras)


AVAILABLE_MODELS = MODEL_CATALOG + _extra_models()
SUPPORTED_OPENAI_MODELS = tuple(model_id for model_id, _ in AVAILABLE_MODELS)


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


def _boolean(name: str, default: bool = False, value: str | None = None) -> bool:
    raw = (os.getenv(name, "") if value is None else value).strip().lower()
    if not raw:
        return default
    if raw in {"1", "true", "yes", "on"}:
        return True
    if raw in {"0", "false", "no", "off"}:
        return False
    raise ValueError(f"{name} must be true or false")


def _ai_base_url(raw: str) -> str:
    """Base URL of an OpenAI-compatible API. https only (plain http just for localhost)."""
    value = raw.strip().rstrip("/")
    if not value:
        return ""
    if value.startswith("https://") or re.match(r"^http://(localhost|127\.0\.0\.1)(:\d+)?(/|$)", value):
        return value
    raise ValueError("AI_BASE_URL must start with https://")


MAX_BACKUP_PROVIDERS = 5


@dataclass(frozen=True)
class AIProvider:
    """One OpenAI-compatible (Chat Completions) backup provider."""

    name: str
    base_url: str
    api_key: str = field(repr=False)
    model: str


def _extra_providers() -> tuple[AIProvider, ...]:
    """Backup providers 2..5 from AI_BASE_URL_n / AI_API_KEY_n / AI_MODEL_n (+ optional AI_NAME_n).

    A slot with only some of its three values set is ignored, so a half-filled slot cannot
    break startup; an invalid URL still fails loudly like slot 1."""
    providers = []
    for number in range(2, MAX_BACKUP_PROVIDERS + 1):
        base = os.getenv(f"AI_BASE_URL_{number}", "").strip()
        key = os.getenv(f"AI_API_KEY_{number}", "").strip()
        model = os.getenv(f"AI_MODEL_{number}", "").strip()
        if not (base and key and model):
            continue
        name = re.sub(r"[^A-Za-z0-9 ._-]", "", os.getenv(f"AI_NAME_{number}", "")).strip()[:30]
        providers.append(AIProvider(name or f"zaxira {number}", _ai_base_url(base), key, model))
    return tuple(providers)


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
    complex_openai_model: str = "gpt-6-luna"
    always_online: bool = True
    public_bank_reply: bool = False
    ai_base_url: str = ""
    ai_api_key: str = ""
    ai_model: str = ""
    ai_primary: bool = False
    ai_name: str = "zaxira"
    ai_extra_providers: tuple["AIProvider", ...] = ()

    @classmethod
    def from_env(cls) -> "Settings":
        local = load_local_settings()
        mode = os.getenv("GROUP_REPLY_MODE", "mentions").strip().lower()
        raw_models = local.get("SHADOW_MODEL_SELECTION", os.getenv("SHADOW_MODEL_SELECTION", "")).strip()
        try:
            model_selection = json.loads(raw_models) if raw_models else {}
        except json.JSONDecodeError as exc:
            raise ValueError("SHADOW_MODEL_SELECTION is invalid") from exc
        if not isinstance(model_selection, dict) or any(
            key not in {"openai_model", "complex_openai_model"}
            or value not in SUPPORTED_OPENAI_MODELS
            for key, value in model_selection.items()
        ):
            raise ValueError("SHADOW_MODEL_SELECTION contains an unsupported model")
        if mode not in {"mentions", "all"}:
            raise ValueError("GROUP_REPLY_MODE must be 'mentions' or 'all'")
        return cls(
            telegram_api_id=_integer("TELEGRAM_API_ID"),
            telegram_api_hash=os.getenv("TELEGRAM_API_HASH", "").strip(),
            telegram_session=local.get("TELEGRAM_SESSION", os.getenv("TELEGRAM_SESSION", "")).strip(),
            openai_api_key=os.getenv("OPENAI_API_KEY", "").strip(),
            openai_model=model_selection.get("openai_model", local.get("OPENAI_MODEL", os.getenv("OPENAI_MODEL", "gpt-5-mini"))).strip(),
            complex_openai_model=model_selection.get("complex_openai_model", local.get("OPENAI_COMPLEX_MODEL", os.getenv("OPENAI_COMPLEX_MODEL", "gpt-6-luna"))).strip() or "gpt-6-luna",
            approved_chat_ids=_chat_ids(local.get("APPROVED_CHAT_IDS", os.getenv("APPROVED_CHAT_IDS", ""))),
            reply_enabled=_boolean("REPLY_ENABLED", False, local.get("REPLY_ENABLED")),
            group_reply_mode=mode,
            context_messages=max(2, min(_integer("CONTEXT_MESSAGES", 12) or 12, 30)),
            max_reply_chars=max(500, min(_integer("MAX_REPLY_CHARS", 3800) or 3800, 4000)),
            admin_token=os.getenv("ADMIN_TOKEN", "").strip(),
            setup_token=os.getenv("SETUP_TOKEN", "").strip(),
            always_online=_boolean("ALWAYS_ONLINE", True, local.get("ALWAYS_ONLINE")),
            public_bank_reply=_boolean("PUBLIC_BANK_REPLY", False, local.get("PUBLIC_BANK_REPLY")),
            ai_base_url=_ai_base_url(os.getenv("AI_BASE_URL", "")),
            ai_api_key=os.getenv("AI_API_KEY", "").strip(),
            ai_model=os.getenv("AI_MODEL", "").strip(),
            ai_primary=_boolean("AI_PRIMARY", False, local.get("AI_PRIMARY")),
            ai_name=re.sub(r"[^A-Za-z0-9 ._-]", "", os.getenv("AI_NAME", "")).strip()[:30] or "zaxira",
            ai_extra_providers=_extra_providers(),
        )

    @property
    def backup_providers(self) -> tuple[AIProvider, ...]:
        """Configured backup providers in the order they are tried (slot 1 first)."""
        first = (
            (AIProvider(self.ai_name, self.ai_base_url, self.ai_api_key, self.ai_model),)
            if self.ai_base_url and self.ai_api_key and self.ai_model else ()
        )
        return first + self.ai_extra_providers

    @property
    def compat_ai_ready(self) -> bool:
        """At least one OpenAI-compatible (Chat Completions) backup provider is fully configured."""
        return bool(self.backup_providers)

    @property
    def ai_ready(self) -> bool:
        return bool(self.openai_api_key or self.compat_ai_ready)

    @property
    def telegram_api_ready(self) -> bool:
        return bool(self.telegram_api_id and self.telegram_api_hash)

    @property
    def configured(self) -> bool:
        telegram_ready = bool(self.telegram_api_ready and self.telegram_session)
        reply_ready = bool(self.ai_ready and self.approved_chat_ids)
        return bool(telegram_ready and (not self.reply_enabled or reply_ready))
