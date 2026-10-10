"""Conservative task triage: reserve the reasoning model for work that benefits from it."""
from __future__ import annotations

import re
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from .config import AIProvider, Settings

WORK_MODES = frozenset({"professional", "economy", "manual"})
_QWEN = "qwen/qwen3.8-max:free"
_SONNET = "anthropic/claude-sonnet-5"
_GPT = "openai/gpt-6.1-sol"
_OPUS = "anthropic/claude-opus-5.5"
_TASK_MODELS = {
    "selection": (_QWEN, _SONNET, _GPT, _OPUS),
    "development": (_SONNET, _OPUS, _GPT, _QWEN),
    "analysis": (_OPUS, _GPT, _SONNET, _QWEN),
    "review": (_GPT, _OPUS, _SONNET, _QWEN),
    "reasoning": (_GPT, _SONNET, _OPUS, _QWEN),
}


def ordered_backup_providers(settings: Settings, purpose: str = "chat") -> tuple[AIProvider, ...]:
    """Route only the known xKiro models; preserve other services and manual chat priority."""
    providers = list(settings.backup_providers)
    mode = settings.ai_work_mode
    preferred = (_QWEN, _SONNET, _GPT, _OPUS) if mode == "economy" else _TASK_MODELS.get(purpose)
    if mode == "manual" or not preferred:
        return tuple(providers)
    positions = [i for i, p in enumerate(providers)
                 if p.base_url == "https://api.xkiro.com/v1" and p.model in preferred]
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
