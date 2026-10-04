"""Conservative task triage: reserve the reasoning model for work that benefits from it."""
from __future__ import annotations

import re

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
