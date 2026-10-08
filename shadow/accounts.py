"""Saved Telegram accounts: switch between logins without typing a code again.

The registry maps a Telegram user id to {"label", "session"}. The session string is a
full login, so it never leaves the server: public_view() is the only thing the panel gets."""
from __future__ import annotations

import json

MAX_ACCOUNTS = 8


def parse_accounts(raw: str) -> dict[str, dict[str, str]]:
    """Read the saved registry. Anything unreadable is ignored, never fatal for boot."""
    if not raw or not raw.strip():
        return {}
    try:
        data = json.loads(raw)
    except json.JSONDecodeError:
        return {}
    if not isinstance(data, dict):
        return {}
    accounts: dict[str, dict[str, str]] = {}
    for key, value in data.items():
        if not isinstance(value, dict):
            continue
        session, label = value.get("session"), value.get("label")
        if str(key).isdigit() and isinstance(session, str) and session.strip() and isinstance(label, str):
            accounts[str(key)] = {"label": label.strip()[:64] or str(key), "session": session.strip()}
    return accounts


def dump_accounts(accounts: dict[str, dict[str, str]]) -> str:
    return json.dumps(accounts, ensure_ascii=False, separators=(",", ":"))


def remember(accounts: dict[str, dict[str, str]], account_id: int | str, label: str, session: str) -> dict[str, dict[str, str]]:
    """Add the account or refresh its session. The oldest account makes room when the list is full."""
    key = str(account_id)
    updated = {k: v for k, v in accounts.items() if k != key}
    while len(updated) >= MAX_ACCOUNTS:
        updated.pop(next(iter(updated)))
    updated[key] = {"label": (label or key).strip()[:64] or key, "session": session}
    return updated


def forget(accounts: dict[str, dict[str, str]], account_id: str, active_id: int | str | None) -> dict[str, dict[str, str]]:
    key = str(account_id)
    if key not in accounts:
        raise ValueError("Bunday akkaunt saqlanmagan")
    if active_id is not None and key == str(active_id):
        raise ValueError("Faol akkauntni o‘chirib bo‘lmaydi. Avval boshqasiga o‘ting.")
    return {k: v for k, v in accounts.items() if k != key}


def public_view(accounts: dict[str, dict[str, str]], active_id: int | str | None) -> list[dict[str, object]]:
    """What the dashboard may see: id, label and which one is active. Never the session."""
    return [{"id": key, "label": value["label"], "active": active_id is not None and key == str(active_id)}
            for key, value in accounts.items()]


def mask_label(label: str | None) -> str | None:
    """A recognisable but not searchable form of the account name, for the sign-in screen: @no•••k."""
    text = (label or "").strip()
    if not text:
        return None
    if text.isdigit():
        return "ID •••" + text[-4:]
    name = text.removeprefix("@")
    if len(name) <= 3:
        return "@" + name[:1] + "•••"
    return "@" + name[:2] + "•••" + name[-1:]
