"""Declarative, non-secret registry for Shadow's external services.

This registry powers the dashboard catalog. Registering a descriptor does not
create an API client or imply that the service is connected.
"""
from __future__ import annotations

from dataclasses import dataclass
import re
from typing import Literal, TypedDict


ServiceStage = Literal["builtin", "planned"]
ConnectionState = Literal["connected", "disconnected", "planned"]


@dataclass(frozen=True, slots=True)
class ServiceDefinition:
    key: str
    name: str
    category: str
    description: str
    auth: str
    capabilities: tuple[str, ...]
    stage: ServiceStage


class CatalogItem(TypedDict):
    key: str
    name: str
    category: str
    description: str
    state: ConnectionState
    auth: str
    capabilities: list[str]
    account: str


class ServiceRegistry:
    """Small validated registry; service-specific API code belongs in adapters."""

    def __init__(self, definitions: tuple[ServiceDefinition, ...] = ()) -> None:
        self._services: dict[str, ServiceDefinition] = {}
        for definition in definitions:
            self.register(definition)

    def register(self, definition: ServiceDefinition) -> None:
        if not re.fullmatch(r"[a-z][a-z0-9_-]{1,39}", definition.key):
            raise ValueError("Integration key must be a stable lowercase identifier")
        if definition.key in self._services:
            raise ValueError(f"Integration already registered: {definition.key}")
        if not definition.name.strip() or not definition.auth.strip():
            raise ValueError("Integration name and authentication description are required")
        self._services[definition.key] = definition

    def definitions(self) -> tuple[ServiceDefinition, ...]:
        return tuple(self._services.values())


SERVICES = ServiceRegistry((
    ServiceDefinition(
        key="telegram",
        name="Telegram",
        category="Xabar almashish",
        description="Shaxsiy suhbatlar, guruhlar, kanallar va akkaunt boshqaruvi.",
        auth="Telegram sessiyasi",
        capabilities=("Suhbatlar", "Guruhlar", "Kanallar", "Avtojavob"),
        stage="builtin",
    ),
    ServiceDefinition(
        key="instagram",
        name="Instagram",
        category="Ijtimoiy tarmoq",
        description="Rasmiy Meta API imkoniyatlari va kerakli akkaunt turini aniqlashdan so‘ng ulash rejalashtirilgan.",
        auth="Meta OAuth",
        capabilities=("Profil", "Media", "Statistika"),
        stage="planned",
    ),
    ServiceDefinition(
        key="youtube",
        name="YouTube",
        category="Video platforma",
        description="Avval kanal va videolarni o‘qish; nashr qilish imkoniyatlari keyingi bosqichda alohida ko‘rib chiqiladi.",
        auth="Google OAuth",
        capabilities=("Kanal", "Videolar", "Tahlil"),
        stage="planned",
    ),
))


def catalog(*, telegram_connected: bool, telegram_account: str | None) -> list[CatalogItem]:
    """Return safe dashboard metadata; never include tokens or session material."""
    items: list[CatalogItem] = []
    for service in SERVICES.definitions():
        if service.key == "telegram":
            state: ConnectionState = "connected" if telegram_connected else "disconnected"
            account = telegram_account or ""
        else:
            state = "planned"
            account = ""
        items.append({
            "key": service.key,
            "name": service.name,
            "category": service.category,
            "description": service.description,
            "state": state,
            "auth": service.auth,
            "capabilities": list(service.capabilities),
            "account": account,
        })
    return items
