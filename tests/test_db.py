from __future__ import annotations

import json

from shadow import db


def test_kv_round_trip(tmp_path, monkeypatch):
    monkeypatch.setenv("SHADOW_DB_PATH", str(tmp_path / "shadow.db"))
    db.reset_for_tests()
    assert db.get_kv("TELEGRAM_SESSION") is None
    db.set_kv("TELEGRAM_SESSION", "abc123")
    assert db.get_kv("TELEGRAM_SESSION") == "abc123"
    db.set_kv("TELEGRAM_SESSION", "updated")
    assert db.get_kv("TELEGRAM_SESSION") == "updated"


def test_chat_profiles_full_replace(tmp_path, monkeypatch):
    monkeypatch.setenv("SHADOW_DB_PATH", str(tmp_path / "shadow.db"))
    db.reset_for_tests()
    db.replace_all_chat_profiles({"123": {"style": "do‘stona", "memory": "", "notes": "", "routines": ""}})
    assert db.list_chat_profiles() == {"123": {"style": "do‘stona", "memory": "", "notes": "", "routines": ""}}
    db.replace_all_chat_profiles({"456": {"style": "rasmiy", "memory": "", "notes": "", "routines": ""}})
    # Full replace: the old chat_id 123 is gone, only 456 remains.
    assert db.list_chat_profiles() == {"456": {"style": "rasmiy", "memory": "", "notes": "", "routines": ""}}


def test_fresh_db_migrates_from_legacy_env_vars_once(tmp_path, monkeypatch):
    monkeypatch.setenv("SHADOW_DB_PATH", str(tmp_path / "shadow.db"))
    monkeypatch.setenv("TELEGRAM_SESSION", "legacy-session-string")
    monkeypatch.setenv("APPROVED_CHAT_IDS", "1,2,3")
    monkeypatch.setenv(
        "SHADOW_CHAT_PROFILES",
        json.dumps({"1": {"style": "qisqa", "memory": "", "notes": "", "routines": ""}}),
    )
    db.reset_for_tests()
    assert db.get_kv("TELEGRAM_SESSION") == "legacy-session-string"
    assert db.get_kv("APPROVED_CHAT_IDS") == "1,2,3"
    assert db.list_chat_profiles() == {"1": {"style": "qisqa", "memory": "", "notes": "", "routines": ""}}

    # Simulate a later boot where the env vars are still set (as they would
    # be, left over in Render's dashboard) but the DB is no longer fresh --
    # migration must not run again and clobber a value changed since.
    db.set_kv("TELEGRAM_SESSION", "rotated-by-app")
    db.reset_for_tests()  # closes the connection; path stays the same, file persists
    assert db.get_kv("TELEGRAM_SESSION") == "rotated-by-app"
