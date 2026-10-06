import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

from shadow.agents import AGENT_IDS, AGENTS, CUSTOM_RULES, agent_catalog, agent_role
from shadow.assistant import ShadowAssistant
from shadow.chat_memory import normalize_chat_profile, normalize_chat_profiles
from tests.test_ai_fallback import make_settings


class AgentCatalogTests(unittest.TestCase):
    def test_presets_have_unique_ids_and_a_default(self):
        ids = [agent.id for agent in AGENTS]
        self.assertEqual(len(ids), len(set(ids)))
        self.assertIn("", ids)
        for wanted in ("bank", "translator", "tutor", "friend", "work"):
            self.assertIn(wanted, AGENT_IDS)
        self.assertEqual([a["id"] for a in agent_catalog()], ids)

    def test_no_preset_claims_to_be_human(self):
        for agent in AGENTS:
            self.assertNotIn("inson", agent.instructions.lower())

    def test_role_text_for_default_and_empty_profiles_is_empty(self):
        self.assertEqual(agent_role(None), "")
        self.assertEqual(agent_role({}), "")
        self.assertEqual(agent_role({"agent": ""}), "")

    def test_preset_and_custom_instructions_are_combined_with_the_safety_note(self):
        role = agent_role({"agent": "translator", "agent_instructions": "Doim ruscha javob ber"})
        self.assertIn("tarjimon", role)
        self.assertIn("Doim ruscha javob ber", role)
        self.assertIn(CUSTOM_RULES, role)

    def test_custom_instructions_work_without_a_preset(self):
        role = agent_role({"agent": "", "agent_instructions": "Qisqa yoz"})
        self.assertIn("Qisqa yoz", role)


class AgentProfileTests(unittest.TestCase):
    def test_profile_accepts_known_agents_and_rejects_unknown(self):
        self.assertEqual(normalize_chat_profile({"agent": "bank"})["agent"], "bank")
        self.assertEqual(normalize_chat_profile({})["agent"], "")
        with self.assertRaises(ValueError):
            normalize_chat_profile({"agent": "hacker"})
        with self.assertRaises(ValueError):
            normalize_chat_profile({"agent": 5})

    def test_custom_instruction_length_is_bounded(self):
        normalize_chat_profile({"agent_instructions": "x" * 1200})
        with self.assertRaises(ValueError):
            normalize_chat_profile({"agent_instructions": "x" * 1201})

    def test_only_an_agent_choice_is_a_saved_profile_and_old_profiles_still_load(self):
        profiles = normalize_chat_profiles({"101": {"agent": "tutor"}, "102": {"notes": "eski qayd"}, "103": {}})
        self.assertEqual(set(profiles), {"101", "102"})
        self.assertEqual(profiles["102"]["agent"], "")


class AgentPromptTests(unittest.IsolatedAsyncioTestCase):
    async def test_each_chat_gets_only_its_own_agent_in_the_system_prompt(self):
        create = AsyncMock(return_value=SimpleNamespace(output=[], output_text="ok"))
        with patch("shadow.assistant.AsyncOpenAI") as client:
            client.return_value.responses.create = create
            assistant = ShadowAssistant(make_settings(ai_base_url="", ai_api_key="", ai_model=""))
            for profile in ({"agent": "bank"}, {"agent": "translator"}, {}):
                await assistant.reply_with_files(
                    chat_title="C", history="", message="Salom", directory=Path("."), chat_profile=profile)
        bank, translator, default = (call.kwargs["instructions"] for call in create.call_args_list)
        self.assertIn("Davr Bank bo‘yicha maslahatchi", bank)
        self.assertNotIn("tarjimon", bank.split("Rol:")[-1])
        self.assertIn("Rol: tarjimon", translator)
        self.assertNotIn("Rol: ", default)
        self.assertNotIn("Egasi shu chat uchun yozgan qo‘shimcha ko‘rsatma", default)

    async def test_agent_role_is_also_used_by_the_backup_provider(self):
        completions = AsyncMock(return_value=SimpleNamespace(
            choices=[SimpleNamespace(message=SimpleNamespace(content="javob", tool_calls=None))]))
        compat = SimpleNamespace(chat=SimpleNamespace(completions=SimpleNamespace(create=completions)))
        factory = lambda **kw: compat if "base_url" in kw else SimpleNamespace(responses=SimpleNamespace(create=AsyncMock()))
        with patch("shadow.assistant.AsyncOpenAI", side_effect=factory):
            assistant = ShadowAssistant(make_settings(ai_primary=True))
            await assistant.reply_with_files(
                chat_title="C", history="", message="Salom", directory=Path("."),
                chat_profile={"agent": "tutor", "agent_instructions": "Ruscha tushuntir"})
        system = completions.call_args.kwargs["messages"][0]["content"]
        self.assertIn("sabrli o‘qituvchi", system)
        self.assertIn("Ruscha tushuntir", system)


if __name__ == "__main__":
    unittest.main()
