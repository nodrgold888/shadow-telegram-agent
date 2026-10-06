from __future__ import annotations

import time
from collections import deque
from dataclasses import dataclass, field

from .public_bank import MAX_MESSAGE_CHARS

# Short greeting chat with people who are not approved: learn who they are and why they wrote.
MAX_REPLIES_PER_CHAT = 3
CHAT_WINDOW_SECONDS = 24 * 3600
GLOBAL_PER_HOUR = 30


@dataclass
class GreetingState:
    """In-memory limits for greeting unapproved chats. Resets on restart.

    Each chat gets at most MAX_REPLIES_PER_CHAT replies per day, the whole account at most
    GLOBAL_PER_HOUR per hour, and a chat that said "stop" is never answered again.
    """

    max_per_chat: int = MAX_REPLIES_PER_CHAT
    global_per_hour: int = GLOBAL_PER_HOUR
    muted: set[int] = field(default_factory=set)
    reply_count: int = 0
    _chat_times: dict[int, deque] = field(default_factory=dict)
    _global_times: deque = field(default_factory=deque)

    def allow(self, chat_id: int, text: str, now: float | None = None) -> bool:
        """Checks the gates and records the reply when allowed."""
        now = time.time() if now is None else now
        if chat_id in self.muted or not text.strip() or len(text) > MAX_MESSAGE_CHARS:
            return False
        times = self._chat_times.setdefault(chat_id, deque())
        while times and times[0] < now - CHAT_WINDOW_SECONDS:
            times.popleft()
        while self._global_times and self._global_times[0] < now - 3600:
            self._global_times.popleft()
        if len(times) >= self.max_per_chat or len(self._global_times) >= self.global_per_hour:
            return False
        times.append(now)
        self._global_times.append(now)
        return True

    def replies_in_chat(self, chat_id: int) -> int:
        return len(self._chat_times.get(chat_id, ()))

    def mute(self, chat_id: int) -> None:
        self.muted.add(chat_id)
