"""Small, bounded web-search client for the assistant's allow-listed tool."""
from __future__ import annotations

import os
from urllib.parse import urlparse

import httpx


class WebSearchError(ValueError):
    """A safe, user-displayable web search configuration or request error."""


def _configured_providers() -> list[str]:
    preference = os.getenv("WEB_SEARCH_PROVIDER", "auto").strip().lower()
    if preference not in {"auto", "google", "bing"}:
        raise WebSearchError("WEB_SEARCH_PROVIDER qiymati auto, google yoki bing bo‘lishi kerak.")

    google_ready = bool(os.getenv("GOOGLE_CSE_API_KEY", "").strip() and os.getenv("GOOGLE_CSE_CX", "").strip())
    bing_ready = bool(os.getenv("BING_SEARCH_API_KEY", "").strip())
    configured = {"google": google_ready, "bing": bing_ready}
    if preference != "auto":
        if not configured[preference]:
            key_name = "GOOGLE_CSE_API_KEY va GOOGLE_CSE_CX" if preference == "google" else "BING_SEARCH_API_KEY"
            raise WebSearchError(f"Web qidiruv sozlanmagan. Render Environment’da {key_name} ni kiriting.")
        return [preference]

    providers = [name for name in ("google", "bing") if configured[name]]
    if not providers:
        raise WebSearchError(
            "Web qidiruv sozlanmagan. Google uchun GOOGLE_CSE_API_KEY va GOOGLE_CSE_CX, "
            "yoki Bing uchun BING_SEARCH_API_KEY kiriting."
        )
    return providers


def _safe_url(value: object) -> str:
    if not isinstance(value, str) or len(value) > 2000:
        return ""
    parsed = urlparse(value)
    return value if parsed.scheme in {"http", "https"} and parsed.netloc else ""


async def _google_search(client: httpx.AsyncClient, query: str, limit: int) -> list[dict[str, str]]:
    response = await client.get(
        "https://www.googleapis.com/customsearch/v1",
        params={
            "key": os.getenv("GOOGLE_CSE_API_KEY", "").strip(),
            "cx": os.getenv("GOOGLE_CSE_CX", "").strip(),
            "q": query,
            "num": limit,
        },
    )
    if response.status_code in {401, 403}:
        raise WebSearchError("Google qidiruv ruxsat bermadi. API kaliti, Custom Search Engine va kvotani tekshiring.")
    response.raise_for_status()
    payload = response.json()
    items = payload.get("items", []) if isinstance(payload, dict) else []
    return [
        {"title": str(item.get("title", ""))[:300], "url": _safe_url(item.get("link")),
         "snippet": str(item.get("snippet", ""))[:1200]}
        for item in items[:limit] if isinstance(item, dict) and _safe_url(item.get("link"))
    ]


async def _bing_search(client: httpx.AsyncClient, query: str, limit: int) -> list[dict[str, str]]:
    response = await client.get(
        "https://api.bing.microsoft.com/v7.0/search",
        headers={"Ocp-Apim-Subscription-Key": os.getenv("BING_SEARCH_API_KEY", "").strip()},
        params={"q": query, "count": limit, "responseFilter": "Webpages", "safeSearch": "Moderate"},
    )
    if response.status_code in {401, 403}:
        raise WebSearchError("Bing qidiruv ruxsat bermadi. API kaliti va xizmat kvotasini tekshiring.")
    response.raise_for_status()
    payload = response.json()
    pages = payload.get("webPages", {}).get("value", []) if isinstance(payload, dict) else []
    return [
        {"title": str(item.get("name", ""))[:300], "url": _safe_url(item.get("url")),
         "snippet": str(item.get("snippet", ""))[:1200]}
        for item in pages[:limit] if isinstance(item, dict) and _safe_url(item.get("url"))
    ]


async def search_web(query: str, limit: int = 5) -> dict[str, object]:
    """Search configured Google Custom Search or Bing APIs; return bounded source snippets."""
    if not isinstance(query, str) or not 2 <= len(query.strip()) <= 300:
        raise WebSearchError("Qidiruv so‘rovi 2–300 belgi bo‘lishi kerak.")
    if type(limit) is not int or not 1 <= limit <= 5:
        raise WebSearchError("Natijalar soni 1–5 oralig‘ida bo‘lishi kerak.")

    providers = _configured_providers()
    errors: list[str] = []
    async with httpx.AsyncClient(timeout=httpx.Timeout(12.0, connect=5.0), follow_redirects=False) as client:
        for provider in providers:
            try:
                results = await (_google_search(client, query.strip(), limit) if provider == "google"
                                else _bing_search(client, query.strip(), limit))
                return {"provider": provider, "query": query.strip(), "results": results}
            except WebSearchError as exc:
                # An explicitly selected provider has no fallback; in auto mode use the next configured API.
                if os.getenv("WEB_SEARCH_PROVIDER", "auto").strip().lower() != "auto":
                    raise
                errors.append(str(exc))
            except (httpx.HTTPError, ValueError):
                errors.append(f"{provider} qidiruv xizmati vaqtincha javob bermadi.")
    raise WebSearchError(errors[-1] if errors else "Web qidiruv bajarilmadi.")
