import json
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

from shadow.assistant import ShadowAssistant, SKILL_PROMPT
from shadow.humanize import MAX_PARTS, human_typing_delay, read_delay, split_parts
from tests.test_ai_fallback import make_settings


def tool_call(call_id, name, arguments):
    return SimpleNamespace(id=call_id, function=SimpleNamespace(name=name, arguments=json.dumps(arguments)))


def completion(content=None, calls=None):
    return SimpleNamespace(choices=[SimpleNamespace(message=SimpleNamespace(content=content, tool_calls=calls))])


def build(responses):
    compat = SimpleNamespace(chat=SimpleNamespace(completions=SimpleNamespace(create=AsyncMock(side_effect=responses))))
    openai_client = SimpleNamespace(responses=SimpleNamespace(create=AsyncMock()))
    factory = lambda **kw: compat if "base_url" in kw else openai_client
    with patch("shadow.assistant.AsyncOpenAI", side_effect=factory):
        assistant = ShadowAssistant(make_settings(ai_primary=True))
    return assistant, compat


class CompatToolTests(unittest.IsolatedAsyncioTestCase):
    async def test_calculator_works_through_the_backup_provider(self):
        assistant, compat = build([
            completion(calls=[tool_call("c1", "calculate", {"expression": "12*8"})]),
            completion("12 marta 8 — 96."),
        ])
        answer, files = await assistant.reply_with_files(chat_title="Chat", history="", message="12*8?", directory=Path("."))
        self.assertEqual((answer, files), ("12 marta 8 — 96.", []))
        second = compat.chat.completions.create.call_args_list[1].kwargs["messages"]
        self.assertEqual(second[-1]["role"], "tool")
        self.assertIn("96", second[-1]["content"])
        self.assertEqual(second[-2]["tool_calls"][0]["function"]["name"], "calculate")

    async def test_word_file_is_created_through_the_backup_provider(self):
        args = {"title": "Hisobot", "sections": [{"heading": "Kirish", "paragraphs": ["Salom"], "table": []}]}
        assistant, _ = build([completion(calls=[tool_call("c1", "create_word", args)]), completion("Tayyor.")])
        with tempfile.TemporaryDirectory() as directory:
            answer, files = await assistant.reply_with_files(
                chat_title="Chat", history="", message="Word hujjat yarat", directory=Path(directory))
            self.assertEqual(answer, "Tayyor.")
            self.assertEqual(len(files), 1)
            self.assertTrue(files[0].exists() and files[0].suffix == ".docx")

    async def test_excel_file_is_created_through_the_backup_provider(self):
        args = {"sheets": [{"name": "Jadval", "rows": [["Nom", "Narx"], ["A", 10]]}]}
        assistant, _ = build([completion(calls=[tool_call("c1", "create_excel", args)]), completion("Excel tayyor.")])
        with tempfile.TemporaryDirectory() as directory:
            _, files = await assistant.reply_with_files(
                chat_title="Chat", history="", message="Excel yarat", directory=Path(directory))
            self.assertEqual([f.suffix for f in files], [".xlsx"])

    async def test_provider_without_tool_support_still_answers_as_text(self):
        class NoTools(Exception):
            status_code = 400
        assistant, compat = build([NoTools(), completion("Oddiy matnli javob.")])
        answer, files = await assistant.reply_with_files(chat_title="Chat", history="", message="Salom", directory=Path("."))
        self.assertEqual((answer, files), ("Oddiy matnli javob.", []))
        retry = compat.chat.completions.create.call_args_list[1].kwargs
        self.assertNotIn("tools", retry)

    async def test_bad_tool_arguments_are_returned_to_the_model_not_raised(self):
        assistant, compat = build([
            completion(calls=[tool_call("c1", "calculate", {"wrong": 1})]),
            completion("Hisoblab bo‘lmadi."),
        ])
        answer, _ = await assistant.reply_with_files(chat_title="Chat", history="", message="?", directory=Path("."))
        self.assertEqual(answer, "Hisoblab bo‘lmadi.")
        tool_message = compat.chat.completions.create.call_args_list[1].kwargs["messages"][-1]
        self.assertIn("error", tool_message["content"])


class HumanizeTests(unittest.TestCase):
    def test_split_parts(self):
        self.assertEqual(split_parts("Va alaykum assalom! 😊 ||\nNima gap?"), ["Va alaykum assalom! 😊", "Nima gap?"])
        self.assertEqual(split_parts("bitta xabar"), ["bitta xabar"])
        self.assertEqual(split_parts(""), [])
        self.assertEqual(split_parts("a || || b"), ["a", "b"])

    def test_at_most_three_messages(self):
        parts = split_parts("1 || 2 || 3 || 4 || 5")
        self.assertEqual(len(parts), MAX_PARTS)
        self.assertEqual(parts[-1], "3\n\n4\n\n5")

    def test_delays_are_short_and_bounded(self):
        for _ in range(100):
            self.assertTrue(0.3 <= read_delay() <= 1.0)
            self.assertTrue(0.3 <= human_typing_delay("salom") <= 4.5)

    def test_skill_keeps_the_honesty_rule_and_the_split_protocol(self):
        self.assertIn("say plainly that you are Shadow AI", SKILL_PROMPT)
        self.assertIn("Do not deny it", SKILL_PROMPT)
        self.assertIn("line containing only `||`", SKILL_PROMPT)


if __name__ == "__main__":
    unittest.main()
