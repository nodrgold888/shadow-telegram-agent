from __future__ import annotations

import os
import re
from dataclasses import replace

from .config import MAX_BACKUP_PROVIDERS, AIProvider, Settings, _ai_base_url

_NAME_RE = re.compile(r"[^A-Za-z0-9 ._-]")
_MODEL_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:/@+-]{0,99}$")


def slot_env_names(slot: int) -> dict[str, str]:
    suffix = "" if slot == 1 else f"_{slot}"
    return {part: f"AI_{part}{suffix}" for part in ("BASE_URL", "API_KEY", "MODEL", "NAME")}


def free_slot(env: dict[str, str] | None = None, settings: Settings | None = None) -> int | None:
    """First backup slot (1..8) whose URL, key and model are not all set."""
    if settings is not None:
        occupied = {provider.slot for provider in settings.backup_providers}
        return next((slot for slot in range(1, MAX_BACKUP_PROVIDERS + 1) if slot not in occupied), None)
    env = os.environ if env is None else env
    for slot in range(1, MAX_BACKUP_PROVIDERS + 1):
        names = slot_env_names(slot)
        if not all(env.get(names[part], "").strip() for part in ("BASE_URL", "API_KEY", "MODEL")):
            return slot
    return None


def parse_provider(body: object) -> AIProvider:
    """Validate the dashboard form. Raises ValueError with an Uzbek message."""
    if not isinstance(body, dict):
        raise ValueError("Forma noto‘g‘ri")
    fields = {key: body.get(key, "") for key in ("name", "base_url", "api_key", "model")}
    if any(not isinstance(value, str) for value in fields.values()):
        raise ValueError("Forma noto‘g‘ri")
    try:
        base_url = _ai_base_url(fields["base_url"])
    except ValueError:
        raise ValueError("Base URL https:// bilan boshlanishi kerak") from None
    if not base_url:
        raise ValueError("Base URL kiriting")
    api_key = fields["api_key"].strip()
    if not 8 <= len(api_key) <= 500 or re.search(r"\s", api_key):
        raise ValueError("API kalitni tekshiring")
    model = fields["model"].strip()
    if not _MODEL_RE.match(model):
        raise ValueError("Model nomini tekshiring")
    name = _NAME_RE.sub("", fields["name"]).strip()[:30] or model.split("/")[-1][:30]
    return AIProvider(name, base_url, api_key, model)


def apply_provider(settings: Settings, slot: int, provider: AIProvider) -> Settings:
    """Return settings with the provider added in memory (slot 1 or an extra slot)."""
    if slot == 1:
        return replace(
            settings,
            ai_base_url=provider.base_url,
            ai_api_key=provider.api_key,
            ai_model=provider.model,
            ai_name=provider.name,
        )
    return replace(settings, ai_extra_providers=settings.ai_extra_providers + (replace(provider, slot=slot),))


def provider_env(slot: int, provider: AIProvider) -> dict[str, str]:
    names = slot_env_names(slot)
    return {
        names["BASE_URL"]: provider.base_url,
        names["API_KEY"]: provider.api_key,
        names["MODEL"]: provider.model,
        names["NAME"]: provider.name,
    }


def remove_slot(settings: Settings, slot: int, env: dict[str, str] | None = None) -> Settings:
    """Drop one provider slot from the env mapping (default: os.environ) and from the settings."""
    env = os.environ if env is None else env
    for name in slot_env_names(slot).values():
        env.pop(name, None)
    if settings.ai_first_slot == slot:
        env.pop("AI_FIRST_SLOT", None)
        settings = replace(settings, ai_first_slot=0)
    if slot == 1:
        return replace(settings, ai_base_url="", ai_api_key="", ai_model="", ai_name="zaxira")
    return replace(settings, ai_extra_providers=tuple(p for p in settings.ai_extra_providers if p.slot != slot))


def set_first(settings: Settings, slot: int) -> tuple[Settings, dict[str, str]]:
    """Make the backup provider in `slot` the first AI tried (slot 0 = OpenAI first again).

    Returns the new settings and the environment variables to persist. Raises ValueError for a slot
    that holds no provider."""
    if slot == 0:
        return replace(settings, ai_primary=False, ai_first_slot=0), {"AI_PRIMARY": "false", "AI_FIRST_SLOT": "0"}
    if slot not in {p.slot for p in settings.backup_providers}:
        raise ValueError("Bunday AI topilmadi")
    return (
        replace(settings, ai_primary=True, ai_first_slot=slot),
        {"AI_PRIMARY": "true", "AI_FIRST_SLOT": str(slot)},
    )
