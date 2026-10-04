import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

from shadow.assistant import ShadowAssistant
from shadow.config import Settings


def settings():
    return Settings(
        None, "", "", "test-key", "gpt-5-mini", frozenset(), False,
        "mentions", 12, 3800, "", "",
    )


class ModelSelectionTests(unittest.IsolatedAsyncioTestCase):
    async def test_everyday_chat_uses_only_the_default_model(self):
        with patch("shadow.assistant.AsyncOpenAI") as client:
            client.return_value.responses.create = AsyncMock(
                return_value=SimpleNamespace(output=[], output_text="Salom!")
            )
            assistant = ShadowAssistant(settings())
            answer, files = await assistant.reply_with_files(
                chat_title="Chat", history="", message="Salom, qalesan?",
                directory=Path(tempfile.gettempdir()),
            )
            request = client.return_value.responses.create.call_args.kwargs
            self.assertEqual(answer, "Salom!")
            self.assertEqual(files, [])
            self.assertEqual(request["model"], "gpt-5-mini")
            self.assertNotIn("reasoning", request)
            self.assertEqual(assistant.last_model, "gpt-5-mini")

    async def test_complex_message_uses_only_configured_reasoning_model_at_medium(self):
        with patch("shadow.assistant.AsyncOpenAI") as client:
            client.return_value.responses.create = AsyncMock(
                return_value=SimpleNamespace(output=[], output_text="Qadamlar bilan yechim.")
            )
            assistant = ShadowAssistant(settings())
            await assistant.reply_with_files(
                chat_title="Chat", history="", message="Bu tenglamani qadam-baqadam yech",
                directory=Path(tempfile.gettempdir()),
            )
            request = client.return_value.responses.create.call_args.kwargs
            self.assertEqual(request["model"], "gpt-6-luna")
            self.assertEqual(request["reasoning"], {"effort": "medium"})
            self.assertEqual(assistant.last_model, "gpt-6-luna")

    async def test_document_always_uses_advanced_model(self):
        with patch("shadow.assistant.AsyncOpenAI") as client:
            client.return_value.responses.create = AsyncMock(
                return_value=SimpleNamespace(output=[], output_text="Tahlil tayyor.")
            )
            assistant = ShadowAssistant(settings())
            await assistant.reply_with_files(
                chat_title="Chat", history="", message="Nimalar bor?",
                directory=Path(tempfile.gettempdir()), document_preview="Sheet: Budget",
            )
            request = client.return_value.responses.create.call_args.kwargs
            self.assertEqual(request["model"], "gpt-6-luna")
            self.assertEqual(request["reasoning"], {"effort": "medium"})


if __name__ == "__main__":
    unittest.main()
