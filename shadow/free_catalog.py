"""Signed public catalog metadata with private, per-account gateway availability."""
from __future__ import annotations

from collections import Counter
from functools import lru_cache
from pathlib import Path
from typing import TYPE_CHECKING
import json
import re

import httpx

if TYPE_CHECKING:
    from .config import Settings

CATALOG_SOURCE = "https://api.freellmapi.co/v1/latest"
CATALOG_PAGE = "https://freellmapi.co/models"
MODEL_ID = re.compile(r"^[A-Za-z0-9@][A-Za-z0-9._:/@+-]{0,99}$")
AUTO_MODELS = frozenset({"auto", "auto:fast", "auto:smart"})


@lru_cache(maxsize=1)
def catalog_snapshot() -> dict:
    # The updater and CI verify the original bytes against upstream's pinned
    # Ed25519 key. No remote scripts or credentials are bundled with this data.
    raw = json.loads(Path(__file__).with_name("data").joinpath("freellmapi-catalog.json").read_text())
    platforms = {p["id"]: p["name"] for p in raw["platforms"]}
    rows = []
    for source, default_kind in (("models", "chat"), ("embeddings", "embedding"),
                                 ("transcriptionModels", "transcription"), ("videoModels", "video")):
        for model in raw.get(source, []):
            if not MODEL_ID.fullmatch(model.get("modelId", "")):
                continue
            kind = model.get("modality", default_kind)
            if kind not in {"chat", "image", "audio", "embedding", "transcription", "video"}:
                kind = default_kind
            limits = model.get("limits", {})
            quota = " · ".join(f"{limits[k]:,} {label}" for k, label in
                               (("rpm", "req/min"), ("rpd", "req/day"), ("tpm", "tokens/min"), ("tpd", "tokens/day"))
                               if isinstance(limits.get(k), int) and limits[k] > 0)
            rows.append({
                "uid": f'{kind}:{model["platform"]}:{model["modelId"]}',
                "id": model["modelId"], "name": model.get("displayName", model["modelId"]),
                "platform": model["platform"], "provider": platforms.get(model["platform"], model["platform"]),
                "kind": kind, "rank": model.get("intelligenceRank") if kind == "chat" else None,
                "context": model.get("contextWindow", model.get("maxInputTokens")),
                "tools": model.get("supportsTools", False), "vision": model.get("supportsVision", False),
                "enabled": model.get("enabled", True), "tier": model.get("sizeLabel", ""),
                "quota": model.get("quotaLabel") or quota or model.get("monthlyTokenBudget") or "",
                "note": model.get("mediaNote", ""), "dimensions": model.get("dimensions"),
            })
    return {"version": raw["version"], "generated_at": raw.get("generatedAt"),
            "source": CATALOG_PAGE, "models": rows,
            "counts": dict(Counter(m["kind"] for m in rows)),
            "providers": [{"id": p, "name": n} for p, n in sorted(platforms.items(), key=lambda item: item[1])]}


@lru_cache(maxsize=1)
def free_chat_model_ids() -> frozenset[str]:
    return frozenset(m["id"] for m in catalog_snapshot()["models"] if m["kind"] == "chat" and m["enabled"])


def gateway_provider(settings: Settings):
    from .model_routing import free_gateway_base_url
    from .provider_catalog import MAX_BACKUP_PROVIDERS
    base = free_gateway_base_url()
    return next((p for p in settings.backup_providers if base and p.base_url == base and p.slot <= MAX_BACKUP_PROVIDERS), None)


async def gateway_models(base_url: str, api_key: str) -> list[dict]:
    from .model_routing import free_gateway_base_url, free_gateway_headers
    if not base_url or base_url.rstrip("/") != free_gateway_base_url():
        raise ValueError("FreeLLMAPI manzilini Shadow Render Environment da sozlang.")
    if not isinstance(api_key, str) or not 8 <= len(api_key.strip()) <= 500 or re.search(r"\s", api_key):
        raise ValueError("FreeLLMAPI unified API kalitini kiriting.")
    headers = {"Authorization": "Bearer " + api_key.strip(), **free_gateway_headers(base_url)}
    try:
        async with httpx.AsyncClient(timeout=12, follow_redirects=False) as client:
            response = await client.get(base_url.rstrip("/") + "/models", headers=headers)
        if response.status_code in {401, 403}:
            raise ValueError("Gateway kirish kaliti yoki unified API kaliti qabul qilinmadi.")
        if response.status_code != 200:
            raise ValueError(f"Gateway model royxatini bermadi (HTTP {response.status_code}).")
        payload = response.json()
        items = payload.get("data") if isinstance(payload, dict) else None
        if not isinstance(items, list):
            raise ValueError("Gateway model royxati notogri formatda.")
        return [item for item in items[:5000] if isinstance(item, dict) and isinstance(item.get("id"), str)]
    except (httpx.HTTPError, json.JSONDecodeError):
        raise ValueError("Gateway bilan ulanishni tekshiring. Katalog saqlangan, holat tekshirilmadi.") from None


def model_status(item: dict | None) -> str:
    if item is None:
        return "not_listed"
    if item.get("available") is not True and item.get("available") != 1:
        return "disabled" if item.get("unavailable_reason") == "disabled" else "needsKey"
    # Never treat an absent/unknown execution status as a successful live probe.
    return item.get("execution_status") if item.get("execution_status") in {"ready", "needsKey", "exhausted"} else "connected"


async def catalog_view(settings: Settings, credentials: dict | None = None) -> dict:
    from .model_routing import free_gateway_base_url
    snapshot = catalog_snapshot()
    saved = gateway_provider(settings)
    base = free_gateway_base_url()
    key = saved.api_key if saved else ""
    if credentials is not None:
        base, key = credentials.get("base_url", ""), credentials.get("api_key", "")
        if not isinstance(base, str) or not isinstance(key, str):
            raise ValueError("Forma notogri.")
    checked, live, notice = False, {}, ""
    if base and key:
        try:
            live = {item["id"]: item for item in await gateway_models(base, key)}
            checked = True
            notice = "Holat gateway API orqali tekshirildi. Javobni tasdiqlash uchun model testini ishlating."
        except ValueError as exc:
            notice = str(exc)
    elif not base:
        notice = "Katalog tayyor. Gateway manzilini Shadow Render Environment da sozlang."
    else:
        notice = "Katalog tayyor. Shu Telegram akkauntiga FreeLLMAPI unified API kalitini qoshing."
    rows = []
    for original in snapshot["models"]:
        row = dict(original)
        # /v1/models describes chat availability only. Media entries remain
        # unverified until their dedicated APIs are integrated and checked.
        row["status"] = model_status(live.get(row["id"])) if checked and row["kind"] == "chat" else "unknown"
        row["selected"] = bool(saved and saved.model == row["id"])
        rows.append(row)
    strongest, seen = [], set()
    for row in sorted(rows, key=lambda m: (m["rank"] or 10000, m["status"] not in {"ready", "connected"}, m["name"].casefold())):
        family = re.sub(r"\s*\([^)]*\)", "", row["name"]).casefold().strip()
        if row["kind"] != "chat" or not row["enabled"] or family in seen:
            continue
        seen.add(family)
        strongest.append(row["uid"])
        if len(strongest) == 8:
            break
    return {**snapshot, "models": rows, "strongest": strongest,
            "gateway": {"base_url": free_gateway_base_url(), "has_key": bool(key), "checked": checked,
                        "saved": bool(saved), "selected": saved.model if saved else None, "notice": notice}}


def prepare_gateway_selection(settings: Settings, model: str, *, slot: int | None = None):
    """Pin one chat model and retain an automatic fallback without filling slots."""
    from dataclasses import replace
    from .ai_slots import apply_provider, provider_env, set_first, ProviderSlotsFull
    from .provider_catalog import MAX_BACKUP_PROVIDERS
    saved = gateway_provider(settings)
    if slot is not None and saved:
        saved = next((p for p in settings.backup_providers if p.slot == slot and p.base_url == saved.base_url), None)
    if saved is None:
        raise ValueError("Avval shu akkauntga FreeLLMAPI unified API kalitini saqlang.")
    if model not in free_chat_model_ids() and model not in AUTO_MODELS:
        raise ValueError("Faqat katalogdagi chat modeli yoki avto rejimni tanlang.")
    provider = replace(saved, model=model, name="FreeLLMAPI " + model.split("/")[-1][:17])
    updated = replace(settings, ai_extra_providers=tuple(p for p in settings.ai_extra_providers if p.slot != saved.slot))
    updated = apply_provider(updated, saved.slot, provider)
    values = provider_env(saved.slot, provider)
    if model not in AUTO_MODELS and not any(p.base_url == saved.base_url and p.model in AUTO_MODELS for p in updated.backup_providers):
        free = next((i for i in range(1, MAX_BACKUP_PROVIDERS + 1) if i not in {p.slot for p in updated.backup_providers}), None)
        if free is None:
            raise ProviderSlotsFull("Avto zaxira uchun bitta bosh AI joyi kerak. Eski sozlamalar saqlandi.")
        fallback = replace(saved, model="auto:smart", name="FreeLLMAPI Auto Smart", slot=free)
        updated = apply_provider(updated, free, fallback)
        values.update(provider_env(free, fallback))
    updated, order = set_first(updated, saved.slot)
    values.update(order)
    return updated, values
