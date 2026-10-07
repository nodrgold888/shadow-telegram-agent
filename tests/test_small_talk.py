import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

from shadow import assistant as assistant_module
from shadow.assistant import ShadowAssistant, is_small_talk
from tests.test_ai_fallback import make_settings


class DetectorTests(unittest.TestCase):
    def test_plain_chat_is_small_talk(self):
        for text in ("salom", "assalomu alaykum", "tuzukmisiz", "Yaxshi ishlavosmi", "nima gap", "rahmat", "ok", "kecha kinoga bordim"):
            self.assertTrue(is_small_talk(text), text)

    def test_tasks_numbers_links_documents_and_long_texts_are_not(self):
        for text in ("5 ta 12 mingdan nechpul", "excel jadval tuz", "kod xatosini top", "https://youtu.be/x", "kredit foizi qancha",
                     "o'zbekchadan ruschaga tarjima qil", "Bu tenglamani qadam-baqadam yech", "x" * 120, "", "bugun 3 ta uchrashuv bor"):
            self.assertFalse(is_small_talk(text), text)
        self.assertFalse(is_small_talk("salom", has_document=True))
        self.assertFalse(is_small_talk("salom", has_files=True))


def make_assistant():
    with patch("shadow.assistant.AsyncOpenAI") as openai:
        create = AsyncMock(return_value=SimpleNamespace(output_text="salom, tinchlikmi", output=[]))
        openai.return_value.responses.create = create
        assistant = ShadowAssistant(make_settings(openai_api_key="sk-test"))
    return assistant, create


class LeanPathTests(unittest.IsolatedAsyncioTestCase):
    async def ask(self, assistant, message, **kw):
        return await assistant.reply_with_files(chat_title="Aziz", history="Aziz: salom", message=message, directory=Path("."), **kw)

    async def test_small_talk_uses_the_lean_prompt_without_tools(self):
        assistant, create = make_assistant()
        answer, files = await self.ask(assistant, "salom, qalaysiz")
        self.assertEqual((answer, files), ("salom, tinchlikmi", []))
        request = create.await_args.kwargs
        self.assertNotIn("tools", request)
        self.assertIn("Real Uzbek texting", request["instructions"])
        self.assertNotIn("Davr Bank car loans", request["instructions"])
        self.assertLess(len(request["instructions"]), len(assistant_module.SYSTEM_PROMPT + assistant_module.SKILL_PROMPT_NO_BANK))
        self.assertIn("Chat: Aziz", request["input"][0]["content"])  # the normal prompt with history is still used

    async def test_the_chat_role_and_private_context_still_apply(self):
        assistant, create = make_assistant()
        await self.ask(assistant, "salom", chat_profile={"agent": "friend", "agents": "friend", "memory": "ismi Aziz"})
        request = create.await_args.kwargs
        self.assertIn("do‘stona suhbatdosh", request["instructions"])
        self.assertIn("ismi Aziz", request["input"][0]["content"])

    async def test_real_tasks_keep_the_full_tool_path(self):
        assistant, create = make_assistant()
        create.return_value = SimpleNamespace(output_text="3 960 000", output=[])
        await self.ask(assistant, "36 kvadrat 110 mingdan qancha")
        request = create.await_args.kwargs
        self.assertIn("tools", request)
        self.assertIn("calculate", str(request["tools"]))


if __name__ == "__main__":
    unittest.main()
