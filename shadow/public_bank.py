from __future__ import annotations

import re
import time
from collections import deque
from dataclasses import dataclass, field

# Substrings (after lowercasing and apostrophe normalisation) that mark a message
# as being about banking or payments. Uzbek (Latin/Cyrillic), Russian and English.
_BANK_WORDS = (
    "bank", "банк", "karta", "karti", "kartam", "карта", "карты", "карт ", "card",
    "uzcard", "humo", "хумо", "visa", "mastercard", "unionpay",
    "kredit", "кредит", "credit", "loan", "ipoteka", "ипотек", "mortgage", "mikrokredit", "микрокредит",
    "depozit", "deposit", "omonat", "omanat", "вклад", "депозит",
    "foiz", "foyiz", "процент", "stavka", "ставк", "interest",
    "tarif", "тариф", "komissiya", "комисси", "commission", "limit", "лимит",
    "hisob", "schyot", "счёт", "счет ", "account", "iban", "rekvizit", "реквизит",
    "otkazma", "o'tkazma", "perevod", "перевод", "transfer", "pul o'tka", "pul otka",
    "tolov", "to'lov", "платёж", "платеж", "payment", "payme", "click", "paynet", "apelsin",
    "valyuta", "валют", "kurs", "курс", "dollar", "доллар", "exchange", "konvertatsiya",
    "internet banking", "mobil banking", "mobile banking", "davr", "давр",
    "bloklash", "bloklan", "заблокир", "overdraft", "ovedraft", "avans", "plastik", "пластик",
    "pul yechish", "naqd", "bankomat", "atm", "terminal", "tranzaksiya", "транзакци",
    "qarz", "ssuda", "рассрочк", "muddatli tolov", "to'lov muddati", "installment",
)
_STOP_WORDS = {"stop", "/stop", "toxta", "to'xta", "to'xtat", "toxtat", "хватит", "стоп", "не пиши", "yozma", "yozmang"}

MAX_MESSAGE_CHARS = 1500
SESSION_WINDOW_SECONDS = 30 * 60
PER_CHAT_PER_HOUR = 6
GLOBAL_PER_HOUR = 60

DISCLOSURE = (
    "Salom! Men Shadow AI, akkaunt egasining avtomatik yordamchisiman (inson emasman). "
    "Bank va to‘lov masalalarida yordam bera olaman. To‘xtatish uchun “stop” deb yozing.\n\n"
)


def _normalize(text: str) -> str:
    text = text.lower().replace("ʻ", "'").replace("ʼ", "'").replace("’", "'").replace("`", "'").replace("‘", "'")
    return re.sub(r"\s+", " ", text).strip()


def is_bank_question(text: str) -> bool:
    normalized = " " + _normalize(text) + " "
    return any(word in normalized for word in _BANK_WORDS)


def is_stop_request(text: str) -> bool:
    normalized = _normalize(text).strip(" .!")
    return normalized in _STOP_WORDS


@dataclass
class PublicBankState:
    """In-memory policy state for answering people who are not approved chats.

    Limits are per chat and global so the feature cannot be used to flood the
    account or run up OpenAI costs. State resets on restart.
    """

    per_chat_per_hour: int = PER_CHAT_PER_HOUR
    global_per_hour: int = GLOBAL_PER_HOUR
    muted: set[int] = field(default_factory=set)
    _disclosed: set[int] = field(default_factory=set)
    _last_reply: dict[int, float] = field(default_factory=dict)
    _chat_times: dict[int, deque] = field(default_factory=dict)
    _global_times: deque = field(default_factory=deque)
    reply_count: int = 0

    def in_session(self, chat_id: int, now: float | None = None) -> bool:
        now = time.time() if now is None else now
        return now - self._last_reply.get(chat_id, float("-inf")) <= SESSION_WINDOW_SECONDS

    def should_answer(self, chat_id: int, text: str, now: float | None = None) -> bool:
        """Topic gate: bank keywords, or a follow-up inside an active bank conversation."""
        if chat_id in self.muted or not text.strip() or len(text) > MAX_MESSAGE_CHARS:
            return False
        return is_bank_question(text) or self.in_session(chat_id, now)

    def allow(self, chat_id: int, now: float | None = None) -> bool:
        """Rate limit check; records the reply when allowed."""
        now = time.time() if now is None else now
        cutoff = now - 3600
        times = self._chat_times.setdefault(chat_id, deque())
        while times and times[0] < cutoff:
            times.popleft()
        while self._global_times and self._global_times[0] < cutoff:
            self._global_times.popleft()
        if len(times) >= self.per_chat_per_hour or len(self._global_times) >= self.global_per_hour:
            return False
        times.append(now)
        self._global_times.append(now)
        return True

    def needs_disclosure(self, chat_id: int) -> bool:
        return chat_id not in self._disclosed

    def mark_replied(self, chat_id: int, now: float | None = None) -> None:
        self._disclosed.add(chat_id)
        self._last_reply[chat_id] = time.time() if now is None else now
        self.reply_count += 1

    def mute(self, chat_id: int) -> None:
        self.muted.add(chat_id)
