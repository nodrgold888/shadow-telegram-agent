"""Conservative task triage: reserve the reasoning model for work that benefits from it."""
from __future__ import annotations

import os
import re
from typing import TYPE_CHECKING
from .provider_catalog import XKIRO_BASE_URL, XKIRO_MODELS, OPENROUTER_BASE_URL, LOCAL_AI_SLOT
from .free_catalog import AUTO_MODELS, free_chat_model_ids

if TYPE_CHECKING:
    from .config import AIProvider, Settings

WORK_MODES = frozenset({"free", "professional", "economy", "manual"})
_QWEN, _SONNET, _GPT, _OPUS = (model for model, _ in XKIRO_MODELS)
_TASK_MODELS = {
    "selection": (_QWEN, _SONNET, _GPT, _OPUS),
    "development": (_SONNET, _OPUS, _GPT, _QWEN),
    "analysis": (_OPUS, _GPT, _SONNET, _QWEN),
    "review": (_GPT, _OPUS, _SONNET, _QWEN),
    "reasoning": (_GPT, _SONNET, _OPUS, _QWEN),
}


def free_gateway_base_url() -> str:
    """Operator-approved FreeLLMAPI endpoint; its enabled upstreams must be free tiers."""
    return os.getenv("SHADOW_FREE_GATEWAY_BASE_URL", "").strip().rstrip("/")


def free_gateway_headers(base_url: str) -> dict[str, str]:
    """Send the private edge key only to the explicitly configured gateway."""
    access_key = os.getenv("SHADOW_FREE_GATEWAY_ACCESS_KEY", "").strip()
    if access_key and base_url.rstrip("/") == free_gateway_base_url():
        return {"X-Shadow-Gateway-Key": access_key}
    return {}


def is_free_provider(provider: AIProvider) -> bool:
    """Only explicit free cloud routes and the configured local host qualify.

    A name containing 'free', or an arbitrary endpoint's model suffix, is not
    evidence of zero-cost routing. Other providers remain opt-in in paid modes.
    """
    gateway = free_gateway_base_url()
    return (provider.slot == LOCAL_AI_SLOT or
            (bool(gateway) and provider.base_url == gateway and
             (provider.model in AUTO_MODELS or provider.model in free_chat_model_ids())) or
            (provider.base_url in {XKIRO_BASE_URL, OPENROUTER_BASE_URL}
             and provider.model.endswith(":free")) or
            (provider.base_url == OPENROUTER_BASE_URL and provider.model == "openrouter/free"))


def ordered_backup_providers(settings: Settings, purpose: str = "chat") -> tuple[AIProvider, ...]:
    """Route only the known xKiro models; preserve other services and manual chat priority."""
    providers = list(settings.backup_providers)
    mode = settings.ai_work_mode
    if mode == "free":
        return tuple(p for p in providers if is_free_provider(p))
    preferred = (_QWEN, _SONNET, _GPT, _OPUS) if mode == "economy" else _TASK_MODELS.get(purpose)
    if mode == "manual" or not preferred:
        return tuple(providers)
    positions = [i for i, p in enumerate(providers)
                 if p.base_url == XKIRO_BASE_URL and p.model in preferred]
    ranked = sorted((providers[i] for i in positions), key=lambda p: preferred.index(p.model))
    for i, provider in zip(positions, ranked):
        providers[i] = provider
    return tuple(providers)

_COMPLEX_PATTERNS = (
    r"murakkab", r"chuqur tahlil", r"tahlil qil", r"tahlil qilib",
    r"solishtir", r"taqqosla", r"qadam(?:ma)?-?qadam", r"bosqichma",
    r"isbot(?:la|lang)?", r"tenglama", r"tizimni yech", r"masalani yech",
    r"ehtimollik", r"integral", r"hosila", r"statistik", r"prognoz",
    r"strategiya", r"reja tuz", r"tahlil", r"batafsil tahlil", r"xatolarini top",
    r"jadvalni", r"hisobot", r"excel", r"word hujjat",
    r"analy[sz]e", r"compare", r"step[- ]by[- ]step", r"prove",
    r"equation", r"system of equations", r"probability", r"integral",
    r"derivative", r"statistics", r"forecast", r"strategy",
    r"spreadsheet", r"word document", r"xlsx", r"docx", r"debug", r"troubleshoot",
    r"проанализ", r"сравни", r"докажи", r"уравнен", r"вероятност",
    r"интеграл", r"производн", r"статистик", r"прогноз",
    r"стратег", r"подробно", r"таблиц", r"документ",
)
_COMPLEX_RE = re.compile("|".join(f"(?:{pattern})" for pattern in _COMPLEX_PATTERNS), re.IGNORECASE)


def needs_reasoning_model(message: str, *, has_document: bool = False) -> bool:
    """Use the advanced model for explicit multi-step analysis and Office-file work."""
    if has_document:
        return True
    normalized = " ".join((message or "").split())
    if len(normalized) >= 1200:
        return True
    return bool(_COMPLEX_RE.search(normalized))
