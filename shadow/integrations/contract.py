"""Shared contract for future service adapters.

Adapters receive a reference to server-side credentials, never a token from the
browser. Sync results retain their source connection so the app can enforce
account isolation and build owner-selected cross-service views safely.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Protocol

from .catalog import ServiceDefinition


@dataclass(frozen=True, slots=True)
class IntegrationContext:
    owner_id: str
    connection_id: str
    credential_ref: str


@dataclass(frozen=True, slots=True)
class ExternalIdentity:
    service_account_id: str
    display_name: str
    username: str | None = None
    avatar_url: str | None = None


@dataclass(frozen=True, slots=True)
class ActivityItem:
    item_id: str
    kind: str
    title: str
    url: str | None
    occurred_at: datetime | None
    owner_id: str
    connection_id: str


@dataclass(frozen=True, slots=True)
class SyncPage:
    items: tuple[ActivityItem, ...]
    next_cursor: str | None
    has_more: bool


class IntegrationAdapter(Protocol):
    """Contract implemented by each concrete Instagram/YouTube/etc. adapter."""

    @property
    def definition(self) -> ServiceDefinition: ...

    async def verify(self, context: IntegrationContext) -> ExternalIdentity: ...

    async def sync(
        self,
        context: IntegrationContext,
        *,
        cursor: str | None,
        limit: int,
    ) -> SyncPage: ...

    async def disconnect(self, context: IntegrationContext) -> None: ...


def validate_sync_page(context: IntegrationContext, page: SyncPage, *, limit: int) -> None:
    """Reject adapter output that exceeds its page bound or crosses account scope."""
    if not 1 <= limit <= 200 or len(page.items) > limit:
        raise ValueError("Integration returned an invalid page size")
    if any(
        item.owner_id != context.owner_id or item.connection_id != context.connection_id
        for item in page.items
    ):
        raise ValueError("Integration returned data outside its connection scope")
