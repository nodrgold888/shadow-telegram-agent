from __future__ import annotations

import asyncio
import logging
from contextlib import asynccontextmanager

from telethon import TelegramClient, events
from telethon.sessions import StringSession

from .assistant import ShadowAssistant
from .config import Settings
from .policy import chat_is_approved, group_message_needs_reply, split_telegram_message

log = logging.getLogger("shadow.telegram")


class TelegramAgent:
    def __init__(self, settings: Settings) -> None:
        self.settings = settings
        self.client: TelegramClient | None = None
        self.assistant: ShadowAssistant | None = None
        self.connected = False
        self.account_label: str | None = None
        self.last_error: str | None = None
        self._locks: dict[int, asyncio.Lock] = {}
        self._me_id: int | None = None

    async def start(self) -> None:
        if not self.settings.configured:
            self.last_error = "configuration_required"
            log.warning("Shadow is waiting for required environment variables")
            return

        try:
            self.assistant = ShadowAssistant(self.settings)
            self.client = TelegramClient(
                StringSession(self.settings.telegram_session),
                self.settings.telegram_api_id,
                self.settings.telegram_api_hash,
                auto_reconnect=True,
                connection_retries=None,
                retry_delay=3,
            )
            await self.client.connect()
            if not await self.client.is_user_authorized():
                raise RuntimeError("The Telegram session is not authorized")

            me = await self.client.get_me()
            self._me_id = me.id
            self.account_label = f"@{me.username}" if me.username else str(me.id)
            self.client.add_event_handler(self._on_message, events.NewMessage(incoming=True))
            self.connected = True
            self.last_error = None
            log.info("Shadow connected to Telegram account %s", self.account_label)
        except Exception as exc:
            self.connected = False
            self.last_error = type(exc).__name__
            log.exception("Telegram connection failed")

    async def stop(self) -> None:
        if self.client:
            await self.client.disconnect()
        self.connected = False

    async def _is_reply_to_shadow(self, event: events.NewMessage.Event) -> bool:
        if not event.is_reply or self._me_id is None:
            return False
        reply = await event.get_reply_message()
        return bool(reply and reply.sender_id == self._me_id)

    async def _history(self, chat_id: int) -> str:
        assert self.client is not None
        lines: list[str] = []
        async for item in self.client.iter_messages(chat_id, limit=self.settings.context_messages):
            text = (item.raw_text or "").strip()
            if not text:
                continue
            sender = await item.get_sender()
            if sender and sender.id == self._me_id:
                name = "Shadow/men"
            else:
                name = getattr(sender, "first_name", None) or getattr(sender, "title", None) or "Suhbatdosh"
            lines.append(f"{name}: {text[:1200]}")
        return "\n".join(reversed(lines))[-9000:]

    async def _on_message(self, event: events.NewMessage.Event) -> None:
        if not self.connected or not self.client or not self.assistant:
            return
        chat_id = event.chat_id
        if chat_id is None or not chat_is_approved(chat_id, self.settings.approved_chat_ids):
            return
        text = (event.raw_text or "").strip()
        if not text:
            return

        sender = await event.get_sender()
        if getattr(sender, "bot", False):
            return
        replying_to_shadow = await self._is_reply_to_shadow(event)
        if not group_message_needs_reply(
            is_private=event.is_private,
            mode=self.settings.group_reply_mode,
            mentioned=bool(getattr(event.message, "mentioned", False)),
            replying_to_shadow=replying_to_shadow,
        ):
            return

        lock = self._locks.setdefault(chat_id, asyncio.Lock())
        if lock.locked():
            log.info("A reply is already running for chat %s", chat_id)
        async with lock:
            try:
                chat = await event.get_chat()
                title = getattr(chat, "title", None) or getattr(chat, "first_name", None) or str(chat_id)
                history = await self._history(chat_id)
                async with self.client.action(chat_id, "typing"):
                    answer = await self.assistant.reply(chat_title=title, history=history, message=text)
                chunks = split_telegram_message(answer, self.settings.max_reply_chars)
                for index, chunk in enumerate(chunks):
                    if event.is_private or index > 0:
                        await self.client.send_message(chat_id, chunk)
                    else:
                        await event.reply(chunk)
                await self.client.send_read_acknowledge(chat_id)
                log.info("Replied in approved chat %s", chat_id)
            except Exception as exc:
                self.last_error = type(exc).__name__
                log.exception("Failed to reply in chat %s", chat_id)

    def status(self) -> dict[str, object]:
        approved = self.settings.approved_chat_ids
        return {
            "configured": self.settings.configured,
            "connected": self.connected,
            "account": self.account_label,
            "approved_chat_count": "all" if approved == "*" else len(approved),
            "group_reply_mode": self.settings.group_reply_mode,
            "last_error": self.last_error,
        }

@asynccontextmanager
async def telegram_lifespan(agent: TelegramAgent):
    await agent.start()
    try:
        yield
    finally:
        await agent.stop()
