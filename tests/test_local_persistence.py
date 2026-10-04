import asyncio
import json
import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from shadow.config import Settings
from shadow.persist import load_local_settings, save_approved_chats, save_chat_profiles, save_model_selection, save_reply_enabled, save_session


class LocalPersistenceTests(unittest.IsolatedAsyncioTestCase):
    async def test_concurrent_settings_survive_reload(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "private" / "state.json"
            with patch.dict(os.environ, {"SHADOW_STATE_FILE": str(path)}):
                results = await asyncio.gather(
                    save_session("test-session"),
                    save_approved_chats("123,456"),
                    save_reply_enabled(False),
                    save_model_selection("gpt-6-luna", "gpt-5-mini"),
                    save_chat_profiles({"101": {"style": "do‘stona", "memory": "Futbolni yaxshi ko‘radi", "notes": "", "routines": ""}}),
                )
                self.assertEqual(results, [True, True, True, True, True])
                saved = load_local_settings()
                self.assertEqual(saved["TELEGRAM_SESSION"], "test-session")
                self.assertEqual(saved["APPROVED_CHAT_IDS"], "123,456")
                self.assertEqual(saved["REPLY_ENABLED"], "false")
                self.assertEqual(
                    json.loads(saved["SHADOW_MODEL_SELECTION"]),
                    {"openai_model": "gpt-6-luna", "complex_openai_model": "gpt-5-mini"},
                )
                self.assertEqual(
                    json.loads(saved["SHADOW_CHAT_PROFILES"]),
                    {"101": {"style": "do‘stona", "memory": "Futbolni yaxshi ko‘radi", "notes": "", "routines": ""}},
                )
                current = Settings.from_env()
                self.assertEqual(current.openai_model, "gpt-6-luna")
                self.assertEqual(current.complex_openai_model, "gpt-5-mini")
                if os.name != "nt":
                    self.assertEqual(path.stat().st_mode & 0o777, 0o600)

    async def test_malformed_state_is_not_overwritten(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "state.json"
            path.write_text(json.dumps({"REPLY_ENABLED": True}), encoding="utf-8")
            with patch.dict(os.environ, {"SHADOW_STATE_FILE": str(path)}):
                with self.assertRaises(ValueError):
                    load_local_settings()
                self.assertFalse(await save_reply_enabled(True))
                self.assertEqual(json.loads(path.read_text()), {"REPLY_ENABLED": True})
