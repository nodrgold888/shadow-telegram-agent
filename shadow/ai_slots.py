from __future__ import annotations

import os
import re
from dataclasses import replace

import httpx

from .anthropic_compat import ANTHROPIC_BASE_URL
from .config import MAX_BACKUP_PROVIDERS, AIProvider, Settings, _ai_base_url
from .provider_catalog import XKIRO_BASE_URL, XKIRO_MODELS, XKIRO_DEFAULTS_VERSION, OPENROUTER_BASE_URL, AI_COST_POLICY_VERSION
from .model_routing import is_free_provider, free_gateway_base_url, free_gateway_headers

_NAME_RE = re.compile(r"[^A-Za-z0-9 ._-]")
_MODEL_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:/@+-]{0,99}$")


class ProviderSlotsFull(ValueError):
    pass


def prepare_xkiro_bundle(settings: Settings, api_key: str, primary: str) -> tuple[Settings, dict[str, str], list[AIProvider]]:
    """Prepare all four models before any writes; reuse existing matching slots on retries."""
    if not isinstance(primary, str) or primary not in {model for model, _ in XKIRO_MODELS}:
        raise ValueError("Birinchi AI modelini tekshiring")
    existing = {(p.base_url, p.model): p.slot for p in settings.backup_providers if p.slot <= MAX_BACKUP_PROVIDERS}
    free = [slot for slot in range(1, MAX_BACKUP_PROVIDERS + 1)
            if slot not in {p.slot for p in settings.backup_providers}]
    needed = sum((XKIRO_BASE_URL, model) not in existing for model, _ in XKIRO_MODELS)
    if needed > len(free):
        raise ProviderSlotsFull(f"xKiro uchun {needed} ta bosh AI joyi kerak; hozir {len(free)} ta bosh")
    updated, values, providers = settings, {}, []
    for model, name in XKIRO_MODELS:
        provider = parse_provider({"name": name, "base_url": XKIRO_BASE_URL, "model": model, "api_key": api_key})
        slot = existing.get((XKIRO_BASE_URL, model))
        if slot is None:
            slot = free.pop(0)
        provider = replace(provider, slot=slot)
        # Replacing an extra slot must not append another copy of that slot.
        updated = replace(updated, ai_extra_providers=tuple(p for p in updated.ai_extra_providers if p.slot != slot))
        updated = apply_provider(updated, slot, provider)
        values.update(provider_env(slot, provider))
        providers.append(provider)
    updated, order = set_first(updated, next(p.slot for p in providers if p.model == primary))
    values.update(order)
    values["XKIRO_DEFAULTS_VERSION"] = XKIRO_DEFAULTS_VERSION
    return updated, values, providers


def prepare_xkiro_defaults(settings: Settings) -> tuple[Settings, dict[str, str]]:
    """Complete older single-model setups without changing existing keys or other providers."""
    configured = [p for p in settings.backup_providers if p.base_url == XKIRO_BASE_URL]
    if not configured:
        return settings, {}
    existing = {p.model for p in configured}
    missing = [(model, name) for model, name in XKIRO_MODELS if model not in existing]
    free = [slot for slot in range(1, MAX_BACKUP_PROVIDERS + 1)
            if slot not in {p.slot for p in settings.backup_providers}]
    if len(missing) > len(free):
        raise ProviderSlotsFull("Tortta xKiro modeli uchun AI joylari yetarli emas. Boshqa AI lar saqlandi.")
    updated, values = settings, {}
    for slot, (model, name) in zip(free, missing):
        provider = parse_provider({"name": name, "base_url": XKIRO_BASE_URL,
                                   "model": model, "api_key": configured[0].api_key})
        updated = apply_provider(updated, slot, provider)
        values.update(provider_env(slot, provider))
    if not settings.ai_first_slot and settings.ai_work_mode != "manual":
        qwen = next(p for p in updated.backup_providers if p.base_url == XKIRO_BASE_URL
                    and p.model == XKIRO_MODELS[0][0])
        updated, order = set_first(updated, qwen.slot)
        values.update(order)
    values["XKIRO_DEFAULTS_VERSION"] = XKIRO_DEFAULTS_VERSION
    return updated, values


def prepare_free_defaults(settings: Settings) -> tuple[Settings, dict[str, str]]:
    """Enable no-credit routing and reuse this account's OpenRouter key once.

    Never overwrite a slot or borrow another account's credentials. A full
    provider list still receives the free-only policy without adding a slot.
    """
    updated = replace(settings, ai_work_mode="free")
    values = {"AI_WORK_MODE": "free", "AI_COST_POLICY_VERSION": AI_COST_POLICY_VERSION}
    router = [p for p in settings.backup_providers if p.base_url == OPENROUTER_BASE_URL]
    if router and not any(is_free_provider(p) for p in router):
        available = next((slot for slot in range(1, MAX_BACKUP_PROVIDERS + 1)
                          if slot not in {p.slot for p in settings.backup_providers}), None)
        if available is not None:
            provider = parse_provider({"name": "OpenRouter Free", "base_url": OPENROUTER_BASE_URL,
                                       "api_key": router[0].api_key, "model": "openrouter/free"})
            updated = apply_provider(updated, available, provider)
            values.update(provider_env(available, provider))
    return updated, values


MODEL_CATALOG_BASE_URLS = frozenset({
    "http://ollama:11434/v1",
    "https://api.openai.com/v1",
    ANTHROPIC_BASE_URL,
    "https://openrouter.ai/api/v1",
    "https://api.xkiro.com/v1",
    "https://generativelanguage.googleapis.com/v1beta/openai",
    "https://api.groq.com/openai/v1",
    "https://api.deepseek.com",
    "https://api.mistral.ai/v1",
    "https://api.together.xyz/v1",
    "https://api.cerebras.ai/v1",
    "https://api.fireworks.ai/inference/v1",
    "https://api.sambanova.ai/v1",
    "https://integrate.api.nvidia.com/v1",
    "https://api.x.ai/v1",
    "https://api.deepinfra.com/v1/openai",
    "https://api.siliconflow.com/v1",
    "https://api.novita.ai/v3/openai",
    "https://api.perplexity.ai",
    "https://api.cohere.com/compatibility/v1",
    "https://api.studio.nebius.ai/v1",
    "https://api.hyperbolic.xyz/v1",
})


async def fetch_provider_models(base_url: str, api_key: str) -> list[str]:
    """Fetch the authenticated model catalog from one of the dashboard's fixed providers."""
    if not isinstance(base_url, str):
        raise ValueError("Provayder manzili noto‘g‘ri")
    try:
        base_url = _ai_base_url(base_url)
    except ValueError:
        raise ValueError("Provayder manzili noto‘g‘ri") from None
    if base_url not in MODEL_CATALOG_BASE_URLS and base_url != free_gateway_base_url():
        raise ValueError("Bu provayder modeli ro‘yxatini avtomatik bermaydi")
    api_key = api_key.strip() if isinstance(api_key, str) else ""
    if not 8 <= len(api_key) <= 500 or re.search(r"\s", api_key):
        raise ValueError("API kalitni tekshiring")
    try:
        async with httpx.AsyncClient(timeout=12, follow_redirects=False) as client:
            headers = ({"x-api-key": api_key, "anthropic-version": "2023-06-01"}
                       if base_url == ANTHROPIC_BASE_URL else {"Authorization": "Bearer " + api_key})
            headers.update(free_gateway_headers(base_url))
            response = await client.get(base_url + "/models", headers=headers)
    except httpx.TimeoutException:
        raise ValueError("Provayder javob bermadi. Qayta urinib ko‘ring") from None
    except httpx.HTTPError:
        raise ValueError("Provayderga ulanib bo‘lmadi") from None
    if response.status_code in {401, 403}:
        raise ValueError("API kalit qabul qilinmadi")
    if response.status_code in {404, 405, 501}:
        raise ValueError("Bu provayder model ro‘yxatini API orqali bermaydi")
    if response.status_code >= 400:
        raise ValueError(f"Provayder model ro‘yxatini bermadi (HTTP {response.status_code})")
    try:
        payload = response.json()
    except ValueError:
        raise ValueError("Provayder model ro‘yxati noto‘g‘ri formatda") from None
    items = payload.get("data") if isinstance(payload, dict) else None
    if not isinstance(items, list):
        raise ValueError("Provayder mos model ro‘yxatini qaytarmadi")
    models = sorted({item["id"].strip() for item in items if isinstance(item, dict)
                     and isinstance(item.get("id"), str) and _MODEL_RE.fullmatch(item["id"].strip())}, key=str.casefold)
    if not models:
        raise ValueError("Ushbu API kalit uchun model topilmadi")
    return models[:2000]


def slot_env_names(slot: int) -> dict[str, str]:
    suffix = "" if slot == 1 else f"_{slot}"
    return {part: f"AI_{part}{suffix}" for part in ("BASE_URL", "API_KEY", "MODEL", "NAME")}


def free_slot(env: dict[str, str] | None = None, settings: Settings | None = None) -> int | None:
    """First user slot whose URL, key and model are not all set."""
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
    if slot > MAX_BACKUP_PROVIDERS:
        raise ValueError("Serverda sozlangan lokal AI tartibi bu panelda o‘zgarmaydi")
    if slot not in {p.slot for p in settings.backup_providers}:
        raise ValueError("Bunday AI topilmadi")
    return (
        replace(settings, ai_primary=True, ai_first_slot=slot),
        {"AI_PRIMARY": "true", "AI_FIRST_SLOT": str(slot)},
    )
