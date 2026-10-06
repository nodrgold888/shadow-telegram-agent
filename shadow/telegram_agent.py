from __future__ import annotations

import asyncio
import io
import logging
from pathlib import Path
from tempfile import TemporaryDirectory
from contextlib import asynccontextmanager
from dataclasses import replace
from datetime import datetime, timezone

from telethon import TelegramClient, events
from telethon.errors import SessionPasswordNeededError
from telethon.sessions import StringSession

from .agents import profile_agent_ids
from .assistant import ShadowAssistant
from .chat_memory import normalize_chat_profile
from .office_files import OfficeFileError, MAX_UPLOAD_BYTES, inspect_office

_WORK_SLOTS = asyncio.Semaphore(2)
_VIDEO_SLOTS = asyncio.Semaphore(2)
from .video_download import VideoDownloadError, downloaded_video, find_video_url
from .config import AVAILABLE_MODELS, Settings
from .persist import persistence_available, load_greet_unknown, save_greet_unknown, load_video_unknown, save_video_unknown, load_stranger_flags, save_stranger_flag, load_friend_chats, save_friend_chats, format_friend_chats, FRIEND_CATEGORIES, DEFAULT_FRIEND_CATEGORY, load_chat_profiles, save_chat_profiles, save_session, save_approved_chats, save_reply_enabled
from .diagnostics import safe_error_detail
from .image_gen import ImageGenError, gemini_api_key, generate_image, parse_image_command
from .humanize import human_typing_delay, read_delay, split_parts
from .presence import OnlinePresence
from .greeting import GreetingState, NoteState
from .public_bank import DISCLOSURE, PublicBankState, is_stop_request
from .policy import chat_is_approved, group_message_needs_reply, split_telegram_message

log = logging.getLogger("shadow.telegram")


class TelegramAgent:
    def __init__(self, settings: Settings) -> None:
        self.settings = settings
        self.client: TelegramClient | None = None
        self.assistant: ShadowAssistant | None = None
        self.connected = False
        self.reply_enabled = settings.reply_enabled
        self.account_label: str | None = None
        self.account_id: int | None = None
        self.last_error: str | None = None
        self.last_reply_at: str | None = None
        self.last_reply_error: str | None = None
        self.last_reply_error_detail: str | None = None
        self.reply_count = 0
        self._locks: dict[int, asyncio.Lock] = {}
        self._me_id: int | None = None
        self._image_lock = asyncio.Lock()
        self._login_client: TelegramClient | None = None
        self._login_phone: str | None = None
        self._login_hash: str | None = None
        self._setup_lock = asyncio.Lock()
        self._approval_lock = asyncio.Lock()
        self._profile_lock = asyncio.Lock()
        self.chat_profiles = load_chat_profiles()
        self.friend_ids: dict[int, str] = load_friend_chats()
        self.session_persisted = bool(settings.telegram_session)
        self._watchdog_task: asyncio.Task | None = None
        self.presence = OnlinePresence(settings.always_online)
        self.public_bank = PublicBankState()
        self.greeting = GreetingState()
        self.greet_unknown = load_greet_unknown()
        self.video_unknown = load_video_unknown()
        _flags = load_stranger_flags()
        self.voice_unknown = _flags["voice_unknown"]
        self.notify_unknown = _flags["notify_unknown"]
        self.voice_state = GreetingState(max_per_chat=5, global_per_hour=20)
        self.notes = NoteState()
        self.session_revoked = False

    async def start(self) -> None:
        if not self.settings.telegram_api_ready or not self.settings.telegram_session:
            self.last_error = "configuration_required"
            log.warning("Shadow is waiting for Telegram session setup")
            return

        await self._connect_saved_session()
        self._watchdog_task = asyncio.create_task(self._watchdog())

    async def _connect_saved_session(self) -> None:
        try:
            client = TelegramClient(
                StringSession(self.settings.telegram_session),
                self.settings.telegram_api_id,
                self.settings.telegram_api_hash,
                auto_reconnect=True,
                connection_retries=None,
                retry_delay=3,
            )
            await client.connect()
            if not await client.is_user_authorized():
                await client.disconnect()
                self.session_revoked = True
                raise RuntimeError("The Telegram session is not authorized")
            await self._activate_client(client)
        except Exception as exc:
            self.connected = False
            self.last_error = type(exc).__name__
            log.exception("Telegram connection failed")

    async def _watchdog(self) -> None:
        """Keep the saved session connected without manual re-setup.

        Telethon already reconnects dropped sockets; this covers a failed boot
        (Telegram/network briefly unreachable) and a client left disconnected.
        A revoked session cannot be fixed automatically, so we stop retrying it.
        """
        while True:
            await asyncio.sleep(60)
            if self.session_revoked:
                return
            try:
                if self.client is None:
                    await self._connect_saved_session()
                elif not self.client.is_connected():
                    await self.client.connect()
                    self.connected = self.client.is_connected()
                    if self.connected:
                        self.last_error = None
                        log.info("Telegram connection restored")
            except Exception as exc:
                self.connected = False
                self.last_error = type(exc).__name__
                log.warning("Telegram reconnect attempt failed: %s", type(exc).__name__)

    async def _persist_login(self, client: TelegramClient) -> None:
        session = client.session.save()
        self.session_persisted = await save_session(session)
        # The watchdog and later reconnects must use the account that was just
        # authenticated, even before Render restarts the service with new env.
        self.settings = replace(self.settings, telegram_session=session)

    async def stop(self) -> None:
        self.presence.stop()
        if self._watchdog_task:
            self._watchdog_task.cancel()
        if self.client:
            await self.client.disconnect()
        if self._login_client and self._login_client is not self.client:
            await self._login_client.disconnect()
        self.connected = False

    async def _activate_client(self, client: TelegramClient) -> None:
        me = await client.get_me()
        previous = self.client if self.client is not client else None
        # Authenticate the new account before detaching the working connection.
        # Then remove its listener before switching to avoid duplicate replies.
        if previous is not None:
            previous.remove_event_handler(self._on_message)
            previous.remove_event_handler(self._on_owner_command)
        if self.reply_enabled:
            self.assistant = ShadowAssistant(self.settings)
        self.client = client
        self.session_revoked = False
        self._me_id = me.id
        self.account_id = me.id
        self.account_label = f"@{me.username}" if me.username else str(me.id)
        if self.reply_enabled:
            client.add_event_handler(self._on_message, events.NewMessage(incoming=True))
        # Owner commands (/rasm) work whether or not auto-replies are on.
        client.add_event_handler(self._on_owner_command, events.NewMessage(outgoing=True))
        self.connected = True
        self.last_error = None
        log.info("Shadow connected to Telegram account %s", self.account_label)
        self.presence.start(lambda: self.client)
        if previous is not None:
            try:
                await previous.disconnect()
            except Exception as exc:
                log.warning("Could not disconnect previous account: %s", type(exc).__name__)

    async def request_login_code(self, phone: str) -> None:
        if not self.settings.telegram_api_ready:
            raise RuntimeError("Telegram API credentials are missing")
        async with self._setup_lock:
            # Never drop the live account just because a new login was started.
            if self._login_client and self._login_client is not self.client:
                await self._login_client.disconnect()
            self._login_client = None
            self._login_phone = None
            self._login_hash = None
            client = TelegramClient(
                StringSession(),
                self.settings.telegram_api_id,
                self.settings.telegram_api_hash,
            )
            await client.connect()
            sent = await client.send_code_request(phone)
            self._login_client = client
            self._login_phone = phone
            self._login_hash = sent.phone_code_hash

    async def complete_login(self, code: str) -> str:
        async with self._setup_lock:
            if not self._login_client or not self._login_phone or not self._login_hash:
                raise RuntimeError("Login code was not requested")
            try:
                await self._login_client.sign_in(
                    phone=self._login_phone,
                    code=code,
                    phone_code_hash=self._login_hash,
                )
            except SessionPasswordNeededError:
                return "password_required"
            await self._activate_client(self._login_client)
            await self._persist_login(self._login_client)
            return "connected"

    async def complete_password(self, password: str) -> None:
        async with self._setup_lock:
            if not self._login_client:
                raise RuntimeError("Login session is not ready")
            await self._login_client.sign_in(password=password)
            await self._activate_client(self._login_client)
            await self._persist_login(self._login_client)

    def setup_result(self) -> dict[str, object]:
        """Return safe account and persistence details after private login."""
        return {
            "account": self.account_label,
            "account_id": self.account_id,
            "session_persisted": self.session_persisted,
        }

    async def _on_owner_command(self, event: events.NewMessage.Event) -> None:
        """'/rasm <tavsif>' written by the owner in Saved Messages: generate an image and send it back.

        Only the owner can write to their own Saved Messages, so nobody else can trigger (or pay for) it."""
        if self._me_id is None or event.chat_id != self._me_id or not self.client:
            return
        if getattr(event.message, "media", None):
            return  # Shadow's own image (its caption is the prompt) must never re-trigger the command
        prompt = parse_image_command(event.raw_text or "")
        if prompt is None:
            return
        chat_id = event.chat_id
        if self._image_lock.locked():
            await self.client.send_message(chat_id, "Oldingi rasm hali tayyorlanmoqda, biroz kuting.")
            return
        async with self._image_lock:
            try:
                api_key = gemini_api_key(self.settings)
                async with self.client.action(chat_id, "photo"):
                    data, mime = await generate_image(api_key, prompt)
                buffer = io.BytesIO(data)
                buffer.name = "shadow.jpg" if "jpeg" in mime or "jpg" in mime else "shadow.png"
                await self.client.send_file(chat_id, buffer, caption=prompt[:200], reply_to=event.id)
            except ImageGenError as exc:
                await self.client.send_message(chat_id, str(exc))
            except Exception as exc:
                log.warning("Image command failed (%s)", type(exc).__name__)
                await self.client.send_message(chat_id, f"Rasm yuborilmadi ({type(exc).__name__}).")

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
        if (
            not self.reply_enabled or not self.connected or not self.client or not self.assistant
            or event.client is not self.client
        ):
            return
        chat_id = event.chat_id
        if chat_id is None or chat_id in self.friend_ids:
            return
        if not chat_is_approved(chat_id, self.settings.approved_chat_ids):
            await self._notify_owner(event)
            if await self._maybe_unknown_video(event):
                return
            if await self._maybe_unknown_voice(event):
                return
            if not await self._maybe_public_bank_reply(event):
                await self._maybe_greet_unknown(event)
            return
        text = (event.raw_text or "").strip()
        is_voice = bool(getattr(event.message, "voice", None))
        if is_voice and not text:
            text = "Ovozli xabar"
        attachment = getattr(event, "file", None)
        extension = Path(getattr(attachment, "name", "") or "").suffix.lower()
        if not extension:
            extension = (getattr(attachment, "ext", "") or "").lower()
        office_attachment = extension in {".xlsx", ".docx", ".xls", ".doc", ".xlsm", ".docm"}
        if office_attachment and not text:
            text = "Faylni o‘qib, uning mazmunini qisqacha tahlil qilib ber."
        if not text and not is_voice:
            return

        sender = await event.get_sender()
        if getattr(sender, "bot", False):
            return
        video_url = None if office_attachment else find_video_url(text)
        replying_to_shadow = await self._is_reply_to_shadow(event)
        if not video_url and not group_message_needs_reply(
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
            if not self.reply_enabled or not self.assistant or not chat_is_approved(chat_id, self.settings.approved_chat_ids):
                return
            try:
                chat = await event.get_chat()
                title = getattr(chat, "title", None) or getattr(chat, "first_name", None) or str(chat_id)

                if video_url:
                    await self._send_video(event, chat_id, video_url)
                    return
                async with _WORK_SLOTS:
                    async with asyncio.timeout(180):
                        if is_voice:
                            await self._work_voice_reply(event, title)
                        else:
                            await self._work_reply(event, title, text, extension if office_attachment else "")
                self.reply_count += 1
                self.last_reply_at = datetime.now(timezone.utc).isoformat()
                self.last_reply_error = None
                self.last_reply_error_detail = None
                await self.client.send_read_acknowledge(chat_id)
                log.info("Replied in approved chat %s", chat_id)
            except Exception as exc:
                self.last_error = type(exc).__name__
                self.last_reply_error = type(exc).__name__
                self.last_reply_error_detail = safe_error_detail(exc)
                if getattr(exc, "status_code", None) == 401:
                    self.last_reply_error = "openai_authentication_failed"
                elif getattr(exc, "status_code", None) == 429:
                    self.last_reply_error = "openai_quota_or_rate_limit"
                elif str(exc) == "empty_ai_reply":
                    self.last_reply_error = "empty_ai_reply"
                log.exception("Failed to reply in chat %s", chat_id)

    def _video_allowed(self, chat_id: int) -> bool:
        """Videos go to approved chats, and to any private non-friend chat while the owner's switch is on."""
        if not self.reply_enabled or chat_id in self.friend_ids:
            return False
        return chat_is_approved(chat_id, self.settings.approved_chat_ids) or self.video_unknown

    async def _send_video(self, event, chat_id: int, video_url: str) -> None:
        try:
            async with downloaded_video(video_url) as video:
                if not self._video_allowed(chat_id):
                    return
                async with self.client.action(chat_id, "video"):
                    await self.client.send_file(
                        chat_id, str(video), caption="Video tayyor.",
                        reply_to=event.id, supports_streaming=True,
                    )
            self.reply_count += 1
            self.last_reply_at = datetime.now(timezone.utc).isoformat()
            self.last_reply_error = None
            await self.client.send_read_acknowledge(chat_id)
        except VideoDownloadError as exc:
            self.last_reply_error = "video_download_failed"
            if self._video_allowed(chat_id):
                await event.reply(str(exc))

    async def _maybe_unknown_video(self, event) -> bool:
        """Opt-in (dashboard switch): send the video for a public Instagram/TikTok link written by
        an unapproved private chat. No rate limit, but at most two downloads run at the same time.
        Returns True when the message was a video link handled (or skipped) here."""
        if not self.video_unknown or not event.is_private or not self.client:
            return False
        video_url = find_video_url((event.raw_text or "").strip())
        if not video_url:
            return False
        sender = await event.get_sender()
        if sender is None or getattr(sender, "bot", False) or getattr(sender, "deleted", False) or sender.id in {777000, self._me_id}:
            return True
        chat_id = event.chat_id
        lock = self._locks.setdefault(chat_id, asyncio.Lock())
        async with lock:
            if not self._video_allowed(chat_id):
                return True
            try:
                async with _VIDEO_SLOTS:
                    await self._send_video(event, chat_id, video_url)
            except Exception as exc:
                self.last_reply_error = type(exc).__name__
                self.last_reply_error_detail = safe_error_detail(exc)
                log.exception("Video for an unapproved chat failed in chat %s", chat_id)
        return True

    async def _stranger_sender(self, event):
        """The sender of a private message from an unapproved chat, or None for bots, deleted users and ourselves."""
        sender = await event.get_sender()
        if sender is None or getattr(sender, "bot", False) or getattr(sender, "deleted", False) or sender.id in {777000, self._me_id}:
            return None
        return sender

    async def _notify_owner(self, event) -> None:
        """Opt-in: tell the owner (Saved Messages) that someone not approved wrote, at most once per chat
        every few minutes. Never raises."""
        try:
            if not self.notify_unknown or not event.is_private or not self.client:
                return
            sender = await self._stranger_sender(event)
            if sender is None or not self.notes.allow(event.chat_id):
                return
            name = " ".join(part for part in (getattr(sender, "first_name", None), getattr(sender, "last_name", None)) if part) or "Noma’lum"
            username = getattr(sender, "username", None)
            text = (event.raw_text or "").strip()
            if getattr(event.message, "voice", None):
                body = "🎤 ovozli xabar"
            elif text:
                body = text[:400] + ("…" if len(text) > 400 else "")
            else:
                body = "📎 fayl yoki rasm"
            who = f"{name} (@{username})" if username else name
            await self.client.send_message(
                "me", f"📩 Notanish yozdi\nKim: {who}\nChat ID: {event.chat_id}\nXabar: {body}")
        except Exception as exc:
            log.warning("Could not send the owner note (%s)", type(exc).__name__)

    async def _maybe_unknown_voice(self, event) -> bool:
        """Opt-in: transcribe a short voice message from an unapproved private chat and answer it briefly
        (greeting-style: learn why they wrote). Daily per-chat and hourly overall limits. Returns True when
        the message was a voice message handled or skipped here."""
        if not self.voice_unknown or not event.is_private or not self.client or not self.assistant:
            return False
        voice = getattr(event.message, "voice", None)
        if not voice:
            return False
        sender = await self._stranger_sender(event)
        if sender is None:
            return True
        chat_id = event.chat_id
        duration = getattr(voice, "duration", None)
        size = getattr(getattr(event, "file", None), "size", None)
        if (duration is not None and duration > 60) or (size is not None and size > 5 * 1024 * 1024):
            return True
        lock = self._locks.setdefault(chat_id, asyncio.Lock())
        async with lock:
            if not self.reply_enabled or not self.voice_unknown or not self.voice_state.allow(chat_id, "ovozli xabar"):
                return True
            try:
                with TemporaryDirectory(prefix="shadow-voice-") as temporary:
                    source = Path(temporary) / "voice.ogg"
                    downloaded = await self.client.download_media(event.message, file=str(source))
                    if not downloaded or not source.exists():
                        return True
                    transcript = await self.assistant.transcribe_audio(source)
                if not transcript:
                    return True
                history = await self._history(chat_id)
                async with self.client.action(chat_id, "typing"):
                    answer = await self.assistant.reply_greeting(history=history, message=transcript)
                    if answer:
                        await asyncio.sleep(human_typing_delay(answer))
                if not answer:
                    return True
                for number, part in enumerate(split_parts(answer)):
                    if number > 0:
                        async with self.client.action(chat_id, "typing"):
                            await asyncio.sleep(human_typing_delay(part))
                    for chunk in split_telegram_message(part, self.settings.max_reply_chars):
                        await self.client.send_message(chat_id, chunk)
                self.voice_state.reply_count += 1
                self.reply_count += 1
                self.last_reply_at = datetime.now(timezone.utc).isoformat()
                self.last_reply_error = None
                await self.client.send_read_acknowledge(chat_id)
            except Exception as exc:
                self.last_reply_error = type(exc).__name__
                self.last_reply_error_detail = safe_error_detail(exc)
                log.exception("Voice reply for an unapproved chat failed in chat %s", chat_id)
        return True

    async def set_stranger_flag(self, name: str, enabled: bool) -> dict[str, object]:
        if name not in {"voice_unknown", "notify_unknown"}:
            raise ValueError("Noma’lum sozlama")
        setattr(self, name, enabled)
        return {name: enabled, "persisted": await save_stranger_flag(name, enabled)}

    async def set_video_unknown(self, enabled: bool) -> dict[str, object]:
        self.video_unknown = enabled
        return {"video_unknown": enabled, "persisted": await save_video_unknown(enabled)}

    async def _maybe_public_bank_reply(self, event) -> bool:
        """Opt-in (PUBLIC_BANK_REPLY): answer bank questions from people in private chats
        that are not approved. Private chats only, text only, bank topics only, rate limited,
        and the first reply says it is an AI. Never runs for groups, channels, bots or files.
        Returns True when this chat is in a bank conversation (so the greeting must stay quiet)."""
        if not self.settings.public_bank_reply or not event.is_private or not self.assistant:
            return False
        chat_id = event.chat_id
        text = (event.raw_text or "").strip()
        if not text or getattr(event.message, "media", None):
            return False
        sender = await event.get_sender()
        if sender is None or getattr(sender, "bot", False) or getattr(sender, "deleted", False) or sender.id in {777000, self._me_id}:
            return False
        state = self.public_bank
        if chat_id in state.muted:
            return True
        lock = self._locks.setdefault(chat_id, asyncio.Lock())
        async with lock:
            if not self.reply_enabled or not self.client or not self.assistant:
                return False
            if is_stop_request(text):
                # Only acknowledge a stop inside a conversation we started answering.
                if state.in_session(chat_id):
                    state.mute(chat_id)
                    await self.client.send_message(chat_id, "Xo‘p, boshqa yozmayman.")
                    return True
                return False
            if not state.should_answer(chat_id, text):
                return False
            if not state.allow(chat_id):
                return True
            try:
                history = await self._history(chat_id)
                async with self.client.action(chat_id, "typing"):
                    answer = await self.assistant.reply_public_bank(history=history, message=text)
                    if answer:
                        await asyncio.sleep(human_typing_delay(answer))
                if not answer:
                    return True
                parts = split_parts(answer)
                if state.needs_disclosure(chat_id) and parts:
                    parts[0] = DISCLOSURE + parts[0]
                for number, part in enumerate(parts):
                    if number > 0:
                        async with self.client.action(chat_id, "typing"):
                            await asyncio.sleep(human_typing_delay(part))
                    for chunk in split_telegram_message(part, self.settings.max_reply_chars):
                        await self.client.send_message(chat_id, chunk)
                state.mark_replied(chat_id)
                self.reply_count += 1
                self.last_reply_at = datetime.now(timezone.utc).isoformat()
                self.last_reply_error = None
                await self.client.send_read_acknowledge(chat_id)
                log.info("Answered a public bank question in chat %s", chat_id)
            except Exception as exc:
                self.last_reply_error = type(exc).__name__
                self.last_reply_error_detail = safe_error_detail(exc)
                log.exception("Public bank reply failed in chat %s", chat_id)
            return True

    async def _maybe_greet_unknown(self, event) -> None:
        """Opt-in (dashboard switch): a short greeting that asks why an unapproved person wrote.
        Private chats only, text only, no bots, friends never reach this code, and a few replies
        per chat per day."""
        if not self.greet_unknown or not event.is_private or not self.assistant:
            return
        chat_id = event.chat_id
        text = (event.raw_text or "").strip()
        if not text or getattr(event.message, "media", None):
            return
        sender = await event.get_sender()
        if sender is None or getattr(sender, "bot", False) or getattr(sender, "deleted", False) or sender.id in {777000, self._me_id}:
            return
        state = self.greeting
        if chat_id in state.muted:
            return
        lock = self._locks.setdefault(chat_id, asyncio.Lock())
        async with lock:
            if not self.reply_enabled or not self.client or not self.assistant or not self.greet_unknown:
                return
            if is_stop_request(text):
                if state.replies_in_chat(chat_id):
                    state.mute(chat_id)
                    await self.client.send_message(chat_id, "Xo‘p, boshqa yozmayman.")
                return
            if not state.allow(chat_id, text):
                return
            try:
                history = await self._history(chat_id)
                async with self.client.action(chat_id, "typing"):
                    answer = await self.assistant.reply_greeting(history=history, message=text)
                    if answer:
                        await asyncio.sleep(human_typing_delay(answer))
                if not answer:
                    return
                for number, part in enumerate(split_parts(answer)):
                    if number > 0:
                        async with self.client.action(chat_id, "typing"):
                            await asyncio.sleep(human_typing_delay(part))
                    for chunk in split_telegram_message(part, self.settings.max_reply_chars):
                        await self.client.send_message(chat_id, chunk)
                state.reply_count += 1
                self.reply_count += 1
                self.last_reply_at = datetime.now(timezone.utc).isoformat()
                self.last_reply_error = None
                await self.client.send_read_acknowledge(chat_id)
                log.info("Greeted an unapproved chat %s", chat_id)
            except Exception as exc:
                self.last_reply_error = type(exc).__name__
                self.last_reply_error_detail = safe_error_detail(exc)
                log.exception("Greeting failed in chat %s", chat_id)

    async def set_greet_unknown(self, enabled: bool) -> dict[str, object]:
        self.greet_unknown = enabled
        return {"greet_unknown": enabled, "persisted": await save_greet_unknown(enabled)}

    async def ai_check(self) -> dict[str, object]:
        """One tiny OpenAI request so the owner can see why replies fail. Never raises."""
        if not self.settings.ai_ready:
            return {"ok": False, "error": "openai_key_missing", "detail": "OPENAI_API_KEY o‘rnatilmagan"}
        assistant = self.assistant or ShadowAssistant(self.settings)
        try:
            result = await assistant.check()
        except Exception as exc:
            return {"ok": False, "error": type(exc).__name__, "detail": safe_error_detail(exc),
                    "model": self.settings.openai_model}
        models = result["models"]
        chat_test = await assistant.chat_test()
        if any(m["replied"] for m in models):
            return {"ok": True, "models": models, "chat_test": chat_test}
        first = next((m for m in models if m.get("error")), {})
        return {"ok": False, "error": first.get("error", "empty_reply"), "detail": first.get("detail", "Bo‘sh javob"),
                "models": models, "chat_test": chat_test}

    def _can_reply(self, chat_id: int) -> bool:
        return bool(self.reply_enabled and chat_id not in self.friend_ids
                    and chat_is_approved(chat_id, self.settings.approved_chat_ids))

    async def _work_voice_reply(self, event, title: str) -> None:
        assert self.client is not None and self.assistant is not None
        chat_id = event.chat_id
        voice = getattr(event.message, "voice", None)
        duration = getattr(voice, "duration", None)
        attachment = getattr(event, "file", None)
        size = getattr(attachment, "size", None)
        if (duration is not None and duration > 180) or (size is not None and size > MAX_UPLOAD_BYTES):
            if self._can_reply(chat_id):
                await event.reply("Ovozli xabar 3 daqiqadan yoki 10 MB dan oshmasin.")
            return
        with TemporaryDirectory(prefix="shadow-voice-") as temporary:
            source = Path(temporary) / "voice.ogg"
            downloaded = await self.client.download_media(event.message, file=str(source))
            if not downloaded or not source.exists() or source.stat().st_size > MAX_UPLOAD_BYTES:
                if self._can_reply(chat_id):
                    await event.reply("Ovozli xabarni yuklab bo‘lmadi yoki hajmi 10 MB dan oshdi.")
                return
            transcript = await self.assistant.transcribe_audio(source)
            if not transcript:
                if self._can_reply(chat_id):
                    await event.reply("Ovozli xabarni tushunib bo‘lmadi. Iltimos, yana bir bor yuboring.")
                return
            await self._work_reply(event, title, transcript, "", voice_reply=True)

    async def _work_reply(self, event, title: str, text: str, extension: str, *, voice_reply: bool = False) -> None:
        assert self.client is not None and self.assistant is not None
        chat_id = event.chat_id
        with TemporaryDirectory(prefix="shadow-work-") as temporary:
            directory = Path(temporary)
            preview = ""
            if extension:
                try:
                    if extension not in {".xlsx", ".docx"}:
                        raise OfficeFileError("Faylni .xlsx yoki .docx formatida yuboring; eski va makrosli formatlar qo‘llanmaydi.")
                    size = getattr(event.file, "size", None)
                    if size is None or size > MAX_UPLOAD_BYTES:
                        raise OfficeFileError("Fayl hajmi 10 MB dan oshmasin.")
                    async def progress(current, total):
                        if current > MAX_UPLOAD_BYTES or total > MAX_UPLOAD_BYTES:
                            raise OfficeFileError("Fayl hajmi 10 MB dan oshmasin.")
                    source = directory / ("input" + extension)
                    downloaded = await self.client.download_media(event.message, file=str(source), progress_callback=progress)
                    if not downloaded:
                        raise OfficeFileError("Faylni yuklab bo‘lmadi. Qayta yuboring.")
                    preview = await asyncio.to_thread(inspect_office, source)
                except OfficeFileError as exc:
                    if self._can_reply(chat_id):
                        await event.reply(str(exc))
                    return
            if not self._can_reply(chat_id):
                return
            history = await self._history(chat_id)
            if not voice_reply:
                await asyncio.sleep(read_delay())
            async with self.client.action(chat_id, "typing"):
                answer, files = await self.assistant.reply_with_files(
                    chat_title=title, history=history, message=text,
                    directory=directory, document_preview=preview,
                    chat_profile=self.chat_profiles.get(str(chat_id), {}),
                )
                if answer and not voice_reply:
                    # Keep "typing…" visible for a moment so replies arrive at a human pace.
                    await asyncio.sleep(human_typing_delay((split_parts(answer) or [answer])[0]))
            if not answer and not files:
                raise RuntimeError("empty_ai_reply")
            parts = split_parts(answer)
            answer = " ".join(parts)  # voice, caption and length checks use the message without separators
            speech_path = None
            if voice_reply and answer and len(answer) <= 4000:
                speech_path = directory / "reply.ogg"
                try:
                    await self.assistant.synthesize_speech(answer, speech_path)
                except Exception:
                    speech_path = None
                    log.exception("Voice synthesis failed; sending text reply instead")
            if speech_path and speech_path.exists() and self._can_reply(chat_id):
                caption = "Shadow AI ovozida (sun’iy yaratilgan):\n" + answer
                await self.client.send_file(
                    chat_id, str(speech_path), caption=caption[:1024],
                    reply_to=event.id, voice_note=True,
                )
            else:
                sent = 0
                for number, part in enumerate(parts):
                    if number > 0:
                        # Natural texting: the next message follows after a short typing pause.
                        async with self.client.action(chat_id, "typing"):
                            await asyncio.sleep(human_typing_delay(part))
                    for chunk in split_telegram_message(part, self.settings.max_reply_chars):
                        if not self._can_reply(chat_id):
                            return
                        if event.is_private or sent > 0:
                            await self.client.send_message(chat_id, chunk)
                        else:
                            await event.reply(chunk)
                        sent += 1
            for path in files:
                if not self._can_reply(chat_id):
                    return
                await self.client.send_file(chat_id, str(path), reply_to=event.id, force_document=True)

    async def send_to_self(self, text: str) -> None:
        """Send a message to the connected account's own Saved Messages (never to other chats)."""
        if not self.connected or not self.client:
            raise RuntimeError("Telegram is not connected")
        await self.client.send_message("me", text)

    def status(self) -> dict[str, object]:
        approved = self.settings.approved_chat_ids
        return {
            "configured": self.settings.configured or self.connected,
            "connected": self.connected,
            "account": self.account_label,
            "account_id": self.account_id,
            "approved_chat_count": "all" if approved == "*" else len(approved),
            "reply_enabled": self.reply_enabled,
            "openai_configured": self.settings.ai_ready,
            "reply_ready": bool(self.connected and self.client and self.settings.ai_ready and approved),
            "group_reply_mode": self.settings.group_reply_mode,
            "last_ai_model": self.assistant.last_model if self.assistant else None,
            "openai_model": self.settings.openai_model,
            "ai_backup": {"configured": self.settings.compat_ai_ready, "model": self.settings.ai_model or None, "primary": self.settings.ai_primary, "last_provider": self.assistant.last_provider if self.assistant else None, "providers": [{"name": p.name, "model": p.model, "slot": p.slot} for p in self.settings.backup_providers]},
            "supported_models": [{"id": model_id, "label": label} for model_id, label in AVAILABLE_MODELS],
            "complex_openai_model": self.settings.complex_openai_model,
            "last_error": self.last_error,
            "last_reply_at": self.last_reply_at,
            "last_reply_error": self.last_reply_error,
            "last_reply_error_detail": self.last_reply_error_detail,
            "reply_count": self.reply_count,
            "session_persisted": self.session_persisted,
            "session_revoked": self.session_revoked,
            "can_persist_session": persistence_available(),
            "online_presence": self.presence.status(),
            "video_unknown": {"enabled": self.video_unknown},
            "voice_unknown": {"enabled": self.voice_unknown, "replies": self.voice_state.reply_count},
            "notify_unknown": {"enabled": self.notify_unknown, "notes": self.notes.note_count},
            "greet_unknown": {"enabled": self.greet_unknown, "replies": self.greeting.reply_count},
            "public_bank_reply": {"enabled": self.settings.public_bank_reply, "replies": self.public_bank.reply_count, "muted_chats": len(self.public_bank.muted)},
        }

    def set_reply_enabled(self, enabled: bool) -> None:
        if enabled and not (self.settings.ai_ready and self.settings.approved_chat_ids):
            raise ValueError("Avtomatik javobni yoqish uchun OpenAI kaliti va ruxsatli chat ID kerak")
        if enabled and (not self.connected or not self.client):
            raise ValueError("Avtomatik javobni yoqish uchun Telegram ulangan bo‘lishi kerak")
        if self.reply_enabled == enabled:
            return
        self.reply_enabled = enabled
        if not self.client:
            return
        if enabled:
            self.assistant = ShadowAssistant(self.settings)
            self.client.add_event_handler(self._on_message, events.NewMessage(incoming=True))
        else:
            self.client.remove_event_handler(self._on_message)
            self.assistant = None


    def chat_profile_for(self, chat_id: int) -> dict[str, str]:
        if not chat_is_approved(chat_id, self.settings.approved_chat_ids):
            raise ValueError("Chat avtojavob uchun ruxsat etilmagan")
        return dict(self.chat_profiles.get(str(chat_id), {
            "style": "", "memory": "", "notes": "", "routines": "", "agent": "", "agents": "", "agent_instructions": "",
        }))

    async def update_chat_profile(self, chat_id: int, value: dict[str, object]) -> dict[str, object]:
        if not chat_is_approved(chat_id, self.settings.approved_chat_ids):
            raise ValueError("Avval ushbu chatga avtojavob ruxsatini bering")
        profile = normalize_chat_profile(value)
        key = str(chat_id)
        async with self._profile_lock:
            if not chat_is_approved(chat_id, self.settings.approved_chat_ids):
                raise ValueError("Avval ushbu chatga avtojavob ruxsatini bering")
            updated = dict(self.chat_profiles)
            if any(profile.values()):
                updated[key] = profile
            else:
                updated.pop(key, None)
            self.chat_profiles = updated
            persisted = await save_chat_profiles(updated)
        return {"profile": profile, "has_profile": any(profile.values()), "persisted": persisted}

    async def clear_chat_profile(self, chat_id: int) -> dict[str, object]:
        return await self.update_chat_profile(chat_id, {})

    async def update_chat_approval(self, chat_id: int, approved: bool) -> dict[str, object]:
        async with self._approval_lock:
            current = self.settings.approved_chat_ids
            if current == "*":
                raise ValueError("Avval barcha ruxsatlarni olib tashlang, so‘ng kerakli chatlarni tanlang")
            if approved:
                dialogs = await self.dialogs()
                if not any(dialog["id"] == chat_id for dialog in dialogs):
                    raise ValueError("Chat arxivlanmagan suhbatlar ro‘yxatida topilmadi")
            selected = set(current)
            if approved:
                selected.add(chat_id)
            else:
                selected.discard(chat_id)
            return await self._apply_chat_approvals(frozenset(selected))

    async def update_chat_friend(self, chat_id: int, friend: bool, category: str = DEFAULT_FRIEND_CATEGORY) -> dict[str, object]:
        """Friends are chats Shadow never writes to, whatever the approval list says."""
        if category not in FRIEND_CATEGORIES:
            raise ValueError("Do‘st toifasi noto‘g‘ri")
        async with self._approval_lock:
            if friend:
                dialogs = await self.dialogs()
                if not any(dialog["id"] == chat_id for dialog in dialogs):
                    raise ValueError("Chat arxivlanmagan suhbatlar ro‘yxatida topilmadi")
            selected = dict(self.friend_ids)
            if friend:
                selected[chat_id] = category
            else:
                selected.pop(chat_id, None)
            self.friend_ids = selected
            persisted = await save_friend_chats(format_friend_chats(selected))
        return {"ok": True, "persisted": persisted, "friend_count": len(selected)}

    async def clear_chat_approvals(self) -> dict[str, object]:
        async with self._approval_lock:
            return await self._apply_chat_approvals(frozenset())

    async def _apply_chat_approvals(self, selected: frozenset[int]) -> dict[str, object]:
        self.settings = replace(self.settings, approved_chat_ids=selected)
        if not selected:
            self.set_reply_enabled(False)
            await save_reply_enabled(False)
        persisted = await save_approved_chats(",".join(str(i) for i in sorted(selected)))
        allowed = {str(chat_id) for chat_id in selected}
        async with self._profile_lock:
            retained = {key: value for key, value in self.chat_profiles.items() if key in allowed}
            profiles_persisted = True
            if retained != self.chat_profiles:
                self.chat_profiles = retained
                profiles_persisted = await save_chat_profiles(retained)
        return {
            "ok": True, "persisted": persisted,
            "approved_chat_count": len(selected),
            "chat_profiles_persisted": profiles_persisted,
        }

    async def dialogs(self, limit: int | None = None) -> list[dict[str, object]]:
        """Return all non-archived chats, including chats in custom folders."""
        if not self.connected or not self.client:
            return []
        approved = self.settings.approved_chat_ids
        result: list[dict[str, object]] = []
        async for dialog in self.client.iter_dialogs(limit=limit, archived=False):
            entity = dialog.entity
            is_group = bool(dialog.is_group or getattr(entity, "megagroup", False) or getattr(entity, "gigagroup", False))
            is_channel = bool(dialog.is_channel or getattr(entity, "broadcast", False)) and not is_group
            result.append({
                "id": dialog.id,
                "title": dialog.name or "Telegram chat",
                "kind": "Kanal" if is_channel else "Guruh" if is_group else "Shaxsiy chat",
                "approved": approved == "*" or dialog.id in approved,
                "friend": dialog.id in self.friend_ids,
                "friend_category": self.friend_ids.get(dialog.id, ""),
                "has_profile": (approved == "*" or dialog.id in approved) and str(dialog.id) in self.chat_profiles,
                "agents": profile_agent_ids(self.chat_profiles.get(str(dialog.id))) if (approved == "*" or dialog.id in approved) else [],
                "unread_count": dialog.unread_count,
            })
        return result

@asynccontextmanager
async def telegram_lifespan(agent: TelegramAgent):
    await agent.start()
    try:
        yield
    finally:
        await agent.stop()
