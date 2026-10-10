import os
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

from shadow.assistant import BACKUP_TIMEOUT, CHECK_MAX_TOKENS, OPENAI_TIMEOUT, ShadowAssistant, cooldown_seconds, is_transient
from shadow.config import AIProvider, Settings
from tests.test_ai_fallback import QuotaError, make_settings


_NO_RETRY_WAIT = patch("shadow.assistant.RETRY_DELAY", 0)


def setUpModule():
    _NO_RETRY_WAIT.start()


def tearDownModule():
    _NO_RETRY_WAIT.stop()


def reply(text):
    return SimpleNamespace(choices=[SimpleNamespace(message=SimpleNamespace(content=text, tool_calls=None))])


def build(settings, outcomes):
    """outcomes maps a backup provider's model name to a result or an exception."""
    clients = {}

    def factory(**kwargs):
        if "base_url" not in kwargs:
            return SimpleNamespace(responses=SimpleNamespace(create=AsyncMock(side_effect=QuotaError())))
        async def create(**request):
            outcome = outcomes[request["model"]]
            if isinstance(outcome, Exception):
                raise outcome
            return reply(outcome)
        client = SimpleNamespace(chat=SimpleNamespace(completions=SimpleNamespace(create=AsyncMock(side_effect=create))))
        clients[kwargs["base_url"]] = client
        return client
    with patch("shadow.assistant.AsyncOpenAI", side_effect=factory):
        return ShadowAssistant(settings), clients


def settings_with_two(**overrides):
    extra = (AIProvider("ikkinchi", "https://second.example/v1", "key-2", "model-2"),)
    return make_settings(ai_primary=True, ai_extra_providers=extra, **overrides)


class ProviderChainTests(unittest.IsolatedAsyncioTestCase):
    async def ask(self, assistant):
        return await assistant.reply_with_files(chat_title="C", history="", message="Salom", directory=Path("."))

    async def test_local_provider_answers_after_cloud_quota_error(self):
        settings = make_settings(local_ai_base_url="http://ollama:11434/v1",
                                 local_ai_api_key="ollama-local", local_ai_model="local-model")
        assistant, clients = build(settings, {"local-model": "salom", "backup-model": QuotaError()})
        answer, _files = await self.ask(assistant)
        self.assertEqual(answer, "salom")
        self.assertEqual(assistant.last_provider, "Ollama local")
        clients["https://api.backup.example/v1"].chat.completions.create.assert_not_awaited()

    async def test_first_provider_answers_and_second_is_untouched(self):
        assistant, clients = build(settings_with_two(), {"backup-model": "Birinchi", "model-2": "Ikkinchi"})
        answer, _ = await self.ask(assistant)
        self.assertEqual(answer, "Birinchi")
        self.assertEqual(assistant.last_provider, "zaxira")
        clients["https://second.example/v1"].chat.completions.create.assert_not_called()

    async def test_second_provider_takes_over_when_the_first_fails(self):
        assistant, _ = build(settings_with_two(), {"backup-model": QuotaError(), "model-2": "Ikkinchi javob"})
        answer, _ = await self.ask(assistant)
        self.assertEqual(answer, "Ikkinchi javob")
        self.assertEqual((assistant.last_provider, assistant.last_model), ("ikkinchi", "model-2"))

    async def test_empty_reply_falls_through_to_the_next_provider(self):
        assistant, _ = build(settings_with_two(), {"backup-model": "", "model-2": "Gemini javobi"})
        answer, _ = await self.ask(assistant)
        self.assertEqual(answer, "Gemini javobi")
        self.assertEqual(assistant.last_provider, "ikkinchi")

    async def test_all_empty_still_returns_empty(self):
        assistant, _ = build(settings_with_two(), {"backup-model": "", "model-2": "  "})
        answer, files = await self.ask(assistant)
        self.assertEqual((answer.strip(), files), ("", []))

    async def test_empty_then_failure_returns_empty_not_error(self):
        assistant, _ = build(settings_with_two(), {"backup-model": "", "model-2": QuotaError()})
        answer, _ = await self.ask(assistant)
        self.assertEqual(answer, "")

    async def test_all_failing_raises_the_last_error(self):
        class Last(Exception):
            status_code = 500
        assistant, _ = build(settings_with_two(), {"backup-model": QuotaError(), "model-2": Last()})
        with self.assertRaises(Last):
            await self.ask(assistant)

    async def test_check_reports_every_provider(self):
        assistant, _ = build(settings_with_two(), {"backup-model": QuotaError(), "model-2": "salom"})
        result = await assistant.check()
        by_model = {m["model"]: m for m in result["models"]}
        self.assertFalse(by_model["backup-model (zaxira)"]["replied"])
        self.assertTrue(by_model["model-2 (ikkinchi)"]["replied"])

    async def test_check_leaves_room_for_reasoning_models(self):
        assistant, clients = build(settings_with_two(), {"backup-model": "salom", "model-2": "salom"})
        await assistant.check()
        for client in clients.values():
            request = client.chat.completions.create.await_args.kwargs
            self.assertEqual(request["max_tokens"], CHECK_MAX_TOKENS)
        self.assertGreaterEqual(CHECK_MAX_TOKENS, 256)

    async def test_public_bank_reply_uses_the_chain(self):
        assistant, _ = build(settings_with_two(), {"backup-model": QuotaError(), "model-2": "Bank javobi"})
        self.assertEqual(await assistant.reply_public_bank(history="", message="kredit"), "Bank javobi")


class ProviderConfigTests(unittest.TestCase):
    def env(self, **values):
        return patch.dict(os.environ, {"SHADOW_STATE_FILE": "", **values})

    def test_numbered_slots_are_read_in_order(self):
        with self.env(AI_BASE_URL="https://a.example/v1", AI_API_KEY="k1", AI_MODEL="m1", AI_NAME="Groq",
                      AI_BASE_URL_2="https://b.example/v1/", AI_API_KEY_2="k2", AI_MODEL_2="m2", AI_NAME_2="Gemini <b>",
                      AI_BASE_URL_4="https://d.example/v1", AI_API_KEY_4="k4", AI_MODEL_4="m4"):
            providers = Settings.from_env().backup_providers
        self.assertEqual([(p.name, p.model) for p in providers], [("Groq", "m1"), ("Gemini b", "m2"), ("zaxira 4", "m4")])
        self.assertEqual(providers[1].base_url, "https://b.example/v1")

    def test_half_filled_slots_are_ignored(self):
        with self.env(AI_BASE_URL_2="https://b.example/v1", AI_API_KEY_2="", AI_MODEL_2="m2"):
            self.assertEqual(Settings.from_env().backup_providers, ())

    def test_insecure_slot_url_is_rejected(self):
        with self.env(AI_BASE_URL_3="http://evil.example/v1", AI_API_KEY_3="k", AI_MODEL_3="m"):
            with self.assertRaises(ValueError):
                Settings.from_env()

    def test_api_keys_are_not_in_repr(self):
        provider = AIProvider("x", "https://a.example/v1", "super-secret-key", "m")
        self.assertNotIn("super-secret-key", repr(provider))

    def test_only_extra_slots_still_count_as_ready(self):
        with self.env(AI_BASE_URL_2="https://b.example/v1", AI_API_KEY_2="k", AI_MODEL_2="m", OPENAI_API_KEY=""):
            self.assertTrue(Settings.from_env().ai_ready)


if __name__ == "__main__":
    unittest.main()


class RateLimited(Exception):
    status_code = 429


class PaidOnly(Exception):
    status_code = 403


class CooldownTests(unittest.IsolatedAsyncioTestCase):
    async def ask(self, assistant):
        return await assistant.reply_with_files(chat_title="C", history="", message="Salom", directory=Path("."))

    def test_cooldown_parses_the_providers_hint(self):
        self.assertEqual(cooldown_seconds(Exception("Please try again in 28m48s.")), 28 * 60 + 48)
        self.assertEqual(cooldown_seconds(Exception("try again in 2m")), 120)
        self.assertEqual(cooldown_seconds(Exception("try again in 6s")), 30)
        self.assertEqual(cooldown_seconds(Exception("try again in 5000h")), 3600)
        self.assertEqual(cooldown_seconds(Exception("no hint")), 300)

    async def test_rate_limited_provider_is_tried_last_next_time(self):
        assistant, clients = build(settings_with_two(), {"backup-model": RateLimited("try again in 10m"), "model-2": "Ikkinchi"})
        answer, _ = await self.ask(assistant)
        self.assertEqual(answer, "Ikkinchi")
        first = next(c for url, c in clients.items() if "second" not in url)
        calls_before = first.chat.completions.create.await_count
        answer, _ = await self.ask(assistant)
        self.assertEqual(answer, "Ikkinchi")
        self.assertEqual(first.chat.completions.create.await_count, calls_before)

    async def test_a_provider_refused_with_403_is_not_retried_on_every_message(self):
        assistant, clients = build(settings_with_two(), {"backup-model": PaidOnly("requires a paid plan"), "model-2": "Ikkinchi"})
        await self.ask(assistant)
        first = next(c for url, c in clients.items() if "second" not in url)
        calls_before = first.chat.completions.create.await_count
        for _ in range(2):
            answer, _ = await self.ask(assistant)
            self.assertEqual(answer, "Ikkinchi")
        self.assertEqual(first.chat.completions.create.await_count, calls_before)

    async def test_all_cooling_still_tries_the_providers(self):
        assistant, _ = build(settings_with_two(), {"backup-model": RateLimited("x"), "model-2": RateLimited("y")})
        with self.assertRaises(RateLimited):
            await self.ask(assistant)
        with self.assertRaises(RateLimited):
            await self.ask(assistant)

    async def test_client_errors_do_not_start_a_cooldown(self):
        class Boom(Exception):
            status_code = 400
        assistant, _ = build(settings_with_two(), {"backup-model": Boom(), "model-2": "Ikkinchi"})
        await self.ask(assistant)
        self.assertEqual(assistant._cooldowns, {})


class APITimeoutError(Exception):
    """Named like the openai SDK's timeout error."""


class DownProviderTests(unittest.IsolatedAsyncioTestCase):
    async def ask(self, assistant):
        return await assistant.reply_with_files(chat_title="C", history="", message="Salom", directory=Path("."))

    async def test_a_provider_that_times_out_or_answers_5xx_is_tried_last_next_time(self):
        from shadow.assistant import is_unhealthy
        self.assertTrue(is_unhealthy(APITimeoutError()))
        self.assertTrue(is_unhealthy(type("E", (Exception,), {"status_code": 503})()))
        self.assertFalse(is_unhealthy(type("E", (Exception,), {"status_code": 400})()))
        self.assertFalse(is_unhealthy(ValueError()))
        assistant, clients = build(settings_with_two(), {"backup-model": APITimeoutError(), "model-2": "Ikkinchi"})
        answer, _ = await self.ask(assistant)
        self.assertEqual(answer, "Ikkinchi")
        first = next(c for url, c in clients.items() if "second" not in url)
        calls_before = first.chat.completions.create.await_count
        answer, _ = await self.ask(assistant)
        self.assertEqual(answer, "Ikkinchi")
        self.assertEqual(first.chat.completions.create.await_count, calls_before)  # the dead one was not asked first

    async def test_the_dead_provider_is_still_tried_when_it_is_the_only_one_left(self):
        assistant, _ = build(settings_with_two(), {"backup-model": APITimeoutError(), "model-2": APITimeoutError()})
        with self.assertRaises(APITimeoutError):
            await self.ask(assistant)
        with self.assertRaises(APITimeoutError):
            await self.ask(assistant)  # both cooling, but they are still asked

    async def test_the_chain_gives_up_when_its_time_budget_is_used(self):
        assistant, clients = build(settings_with_two(), {"backup-model": APITimeoutError(), "model-2": "Ikkinchi"})
        with patch("shadow.assistant.CHAIN_BUDGET", -1.0):
            with self.assertRaises(TimeoutError):
                await self.ask(assistant)
        for client in clients.values():
            client.chat.completions.create.assert_not_awaited()


class Overloaded(Exception):
    status_code = 503


class RetryTests(unittest.IsolatedAsyncioTestCase):
    async def ask(self, assistant):
        return await assistant.reply_with_files(chat_title="C", history="", message="Salom", directory=Path("."))

    def test_which_errors_are_transient(self):
        self.assertTrue(is_transient(Overloaded()))
        self.assertFalse(is_transient(RateLimited()))
        self.assertFalse(is_transient(ValueError()))

    async def test_transient_error_is_retried_once_on_the_same_provider(self):
        results = [Overloaded(), "Qayta urinish ishladi"]
        settings = settings_with_two()
        assistant, clients = build(settings, {"backup-model": "x", "model-2": "Ikkinchi"})
        first = next(c for url, c in clients.items() if "second" not in url)

        async def flaky(**request):
            outcome = results.pop(0)
            if isinstance(outcome, Exception):
                raise outcome
            return reply(outcome)
        first.chat.completions.create = AsyncMock(side_effect=flaky)
        with patch("shadow.assistant.RETRY_DELAY", 0):
            answer, _ = await self.ask(assistant)
        self.assertEqual(answer, "Qayta urinish ishladi")
        self.assertEqual(first.chat.completions.create.await_count, 2)

    async def test_still_failing_moves_on_to_the_next_provider(self):
        assistant, clients = build(settings_with_two(), {"backup-model": Overloaded(), "model-2": "Ikkinchi"})
        with patch("shadow.assistant.RETRY_DELAY", 0):
            answer, _ = await self.ask(assistant)
        self.assertEqual(answer, "Ikkinchi")
        first = next(c for url, c in clients.items() if "second" not in url)
        self.assertEqual(first.chat.completions.create.await_count, 2)

    async def test_rate_limit_is_not_retried(self):
        assistant, clients = build(settings_with_two(), {"backup-model": RateLimited("x"), "model-2": "Ikkinchi"})
        with patch("shadow.assistant.RETRY_DELAY", 0):
            await self.ask(assistant)
        first = next(c for url, c in clients.items() if "second" not in url)
        self.assertEqual(first.chat.completions.create.await_count, 1)


class ClientLimitTests(unittest.TestCase):
    def build_kwargs(self, settings):
        seen = []

        def factory(**kwargs):
            seen.append(kwargs)
            return SimpleNamespace()
        with patch("shadow.assistant.AsyncOpenAI", side_effect=factory):
            ShadowAssistant(settings)
        return seen

    def test_backups_fail_fast_without_hidden_sdk_retries(self):
        seen = self.build_kwargs(settings_with_two())
        backups = [k for k in seen if "base_url" in k]
        self.assertEqual(len(backups), 2)
        for kwargs in backups:
            self.assertEqual((kwargs["timeout"], kwargs["max_retries"]), (BACKUP_TIMEOUT, 0))

    def test_openai_gets_a_short_timeout_only_when_a_backup_exists(self):
        with_backup = [k for k in self.build_kwargs(settings_with_two(openai_api_key="sk-test")) if "base_url" not in k]
        self.assertEqual((with_backup[0]["timeout"], with_backup[0]["max_retries"]), (OPENAI_TIMEOUT, 0))
        alone = [k for k in self.build_kwargs(make_settings(openai_api_key="sk-test", ai_base_url="", ai_api_key="", ai_model="")) if "base_url" not in k]
        self.assertNotIn("timeout", alone[0])
        self.assertNotIn("max_retries", alone[0])

    def test_timeouts_are_not_retried(self):
        class APITimeoutError(Exception):
            pass
        self.assertFalse(is_transient(APITimeoutError()))


class ChatTestTests(unittest.IsolatedAsyncioTestCase):
    async def test_success_reports_provider_time_and_preview(self):
        assistant, _ = build(settings_with_two(), {"backup-model": "Va alaykum assalom! Qalaysiz?", "model-2": "x"})
        result = await assistant.chat_test()
        self.assertTrue(result["ok"])
        self.assertEqual(result["provider"], "zaxira")
        self.assertTrue(result["preview"].startswith("Va alaykum"))
        self.assertIn("seconds", result)

    async def test_failure_reports_the_error_without_raising(self):
        class Last(Exception):
            status_code = 500
        assistant, _ = build(settings_with_two(), {"backup-model": Last("boom"), "model-2": Last("boom")})
        with patch("shadow.assistant.RETRY_DELAY", 0):
            result = await assistant.chat_test()
        self.assertFalse(result["ok"])
        self.assertEqual(result["error"], "Last")

    async def test_empty_answer_is_reported(self):
        assistant, _ = build(settings_with_two(), {"backup-model": "", "model-2": ""})
        result = await assistant.chat_test()
        self.assertEqual((result["ok"], result["error"]), (False, "empty_ai_reply"))

    async def test_slow_providers_hit_the_budget(self):
        import asyncio
        assistant, clients = build(settings_with_two(), {"backup-model": "x", "model-2": "x"})

        async def slow(**request):
            await asyncio.sleep(5)
        for client in clients.values():
            client.chat.completions.create = AsyncMock(side_effect=slow)
        result = await assistant.chat_test(budget=0.05)
        self.assertFalse(result["ok"])
        self.assertEqual(result["error"], "TimeoutError")


class ToolRoundTripTests(unittest.IsolatedAsyncioTestCase):
    def tool_response(self):
        call = SimpleNamespace(
            id="call-1", extra_content={"google": {"thought_signature": "SIG"}},
            function=SimpleNamespace(name="calculate", arguments='{"expression": "36 * 110000"}'),
        )
        return SimpleNamespace(choices=[SimpleNamespace(message=SimpleNamespace(content="", tool_calls=[call]))])

    async def ask(self, assistant):
        return await assistant.reply_with_files(chat_title="C", history="", message="36 m2 110 mln", directory=Path("."))

    def first_client(self, assistant):
        return assistant.compat_clients[0][1]

    async def test_thought_signature_is_echoed_with_the_tool_call(self):
        assistant, _ = build(settings_with_two(), {"backup-model": "x", "model-2": "x"})
        seen = []

        async def create(**request):
            seen.append(request)
            if len(seen) == 1:
                return self.tool_response()
            echoed = [m for m in request["messages"] if m.get("tool_calls")][0]["tool_calls"][0]
            if echoed.get("extra_content", {}).get("google", {}).get("thought_signature") != "SIG":
                raise Rejected("Function call is missing a thought_signature")
            return reply("Natija: 3 960 000 so‘m")
        self.first_client(assistant).chat.completions.create = AsyncMock(side_effect=create)
        answer, _ = await self.ask(assistant)
        self.assertEqual(answer, "Natija: 3 960 000 so‘m")
        self.assertEqual(len(seen), 2)

    async def test_rejected_tool_result_still_ends_with_a_plain_text_answer(self):
        assistant, _ = build(settings_with_two(), {"backup-model": "x", "model-2": "x"})
        seen = []

        async def create(**request):
            seen.append(request)
            if len(seen) == 1:
                return self.tool_response()
            if "tools" in request or any(m.get("role") == "tool" for m in request["messages"]):
                raise Rejected("bad tool round trip")
            return reply("3 960 000 so‘m bo‘ladi")
        self.first_client(assistant).chat.completions.create = AsyncMock(side_effect=create)
        answer, _ = await self.ask(assistant)
        self.assertEqual(answer, "3 960 000 so‘m bo‘ladi")
        last = seen[-1]["messages"][-1]["content"]
        self.assertIn("3960000", last.replace(" ", "").replace("\u00a0", ""))
        self.assertEqual(assistant.last_provider, "zaxira")


class Rejected(Exception):
    status_code = 400
