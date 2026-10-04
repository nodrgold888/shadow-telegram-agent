import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

from shadow.assistant import ShadowAssistant
from shadow.config import Settings


class OfficeAssistantTests(unittest.IsolatedAsyncioTestCase):
    async def test_tool_output_creates_actual_file_then_returns_answer(self):
        settings = Settings(None, "", "", "test", "test", frozenset({123}), True,
                            "mentions", 12, 3800, "", "")
        with patch("shadow.assistant.AsyncOpenAI") as client:
            call = SimpleNamespace(
                type="function_call", name="create_word", call_id="call1",
                arguments='{"title":"Test","sections":[]}',
            )
            client.return_value.responses.create = AsyncMock(side_effect=[
                SimpleNamespace(output=[call], output_text=""),
                SimpleNamespace(output=[], output_text="Fayl tayyor."),
            ])
            assistant = ShadowAssistant(settings)
            with tempfile.TemporaryDirectory() as temporary:
                answer, files = await assistant.reply_with_files(
                    chat_title="Test", history="", message="Word yarat",
                    directory=Path(temporary),
                )
                self.assertEqual(answer, "Fayl tayyor.")
                self.assertEqual(len(files), 1)
                self.assertTrue(files[0].exists())
                second = client.return_value.responses.create.call_args
                tool_result = second.kwargs["input"][-1]
                self.assertEqual(tool_result["type"], "function_call_output")
                self.assertEqual(tool_result["call_id"], "call1")
                self.assertTrue(second.kwargs["store"] is False)

    async def test_permissions_revoked_during_generation_prevent_sending(self):
        from shadow.telegram_agent import TelegramAgent
        settings = Settings(None, "", "", "test", "test", frozenset({123}), True,
                            "mentions", 12, 3800, "", "")
        agent = TelegramAgent(settings)
        agent.client = SimpleNamespace(send_message=AsyncMock(), send_file=AsyncMock())
        # Telethon action context replaced by a minimal async context manager.
        from contextlib import asynccontextmanager
        @asynccontextmanager
        async def action(*args):
            yield
        agent.client.action = action
        agent._history = AsyncMock(return_value="")
        async def generate(**kwargs):
            agent.reply_enabled = False
            path = kwargs["directory"] / "result.docx"
            path.write_text("test")
            return "ready", [path]
        agent.assistant = SimpleNamespace(reply_with_files=generate)
        event = SimpleNamespace(chat_id=123, is_private=True, id=1)
        await agent._work_reply(event, "Test", "create Word", "")
        agent.client.send_message.assert_not_awaited()
        agent.client.send_file.assert_not_awaited()
