"""Public, non-secret capability catalog for the dashboard.

The catalog describes what Shadow supports today and what is only planned. It does
not imply that a planned provider is connected or that its APIs are configured.
"""
from __future__ import annotations

from typing import TypedDict


class CatalogItem(TypedDict):
    key: str
    name: str
    category: str
    description: str
    state: str
    auth: str
    capabilities: list[str]
    account: str


_PLANNED: tuple[CatalogItem, ...] = (
    {
        "key": "instagram",
        "name": "Instagram",
        "category": "Ijtimoiy tarmoq",
        "description": "Rasmiy Meta API imkoniyatlari va kerakli akkaunt turini aniqlashdan so‘ng ulash rejalashtirilgan.",
        "state": "planned",
        "auth": "Meta OAuth",
        "capabilities": ["Profil", "Media", "Statistika"],
        "account": "",
    },
    {
        "key": "youtube",
        "name": "YouTube",
        "category": "Video platforma",
        "description": "Avval kanal va videolarni o‘qish; nashr qilish imkoniyatlari keyingi bosqichda alohida ko‘rib chiqiladi.",
        "state": "planned",
        "auth": "Google OAuth",
        "capabilities": ["Kanal", "Videolar", "Tahlil"],
        "account": "",
    },
)


def catalog(*, telegram_connected: bool, telegram_account: str | None) -> list[CatalogItem]:
    """Return safe dashboard metadata; never include tokens or session material."""
    telegram: CatalogItem = {
        "key": "telegram",
        "name": "Telegram",
        "category": "Xabar almashish",
        "description": "Shaxsiy suhbatlar, guruhlar, kanallar va akkaunt boshqaruvi.",
        "state": "connected" if telegram_connected else "disconnected",
        "auth": "Telegram sessiyasi",
        "capabilities": ["Suhbatlar", "Guruhlar", "Kanallar", "Avtojavob"],
        "account": telegram_account or "",
    }
    return [telegram, *(dict(item) for item in _PLANNED)]
