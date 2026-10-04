import asyncio
import json
import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from shadow.persist import load_local_settings, save_approved_chats, save_reply_enabled, save_session


class LocalPersistenceTests(unittest.IsolatedAsyncioTestCase):
    async def test_concurrent_settings_survive_reload(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "private" / "state.json"
            with patch.dict(os.environ, {"SHADOW_STATE_FILE": str(path)}):
                results = await asyncio.gather(
                    save_session("test-session"),
                    save_approved_chats("123,456"),
                    save_reply_enabled(False),
                )
                self.assertEqual(results, [True, True, True])
                self.assertEqual(load_local_settings(), {
                    "TELEGRAM_SESSION": "test-session",
                    "APPROVED_CHAT_IDS": "123,456",
                    "REPLY_ENABLED": "false",
                })
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
