import asyncio
import json
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest import mock

import httpx

from shadow.development import DevelopmentError, DevelopmentStudio
from shadow.guardian import AI_INTERVAL, Guardian


class GuardianTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        root = Path(self.tmp.name) / "repo"
        (root / "shadow").mkdir(parents=True)
        for name in ("app.py", "development.py", "guardian.py", "assistant.py", "development.js",
                     "dashboard.html", "dashboard-theme.css", "dashboard-redesign.css"):
            (root / "shadow" / name).write_text("value = 1\n" if name.endswith(".py") else "preview")
        self.prompts = []
        async def generate(settings, messages, tokens):
            self.prompts.append(messages)
            if tokens == 2000:
                return {"files": ["shadow/guardian.py"]}
            return {"title": "Self-check", "summary": "Source reviewed", "findings": [], "changes": []}
        self.studio = DevelopmentStudio(root, Path(self.tmp.name) / "jobs", generate)
        self.timestamp = 100_000.0
        self.runtime = {"connected": True, "reply_enabled": False, "reply_listener_registered": False,
                        "last_reply_error": "private-secret-never-export", "reply_count": 0}
        self.agent = SimpleNamespace(account_id=101, settings=SimpleNamespace(ai_ready=True),
                                     _setup_lock=asyncio.Lock(), status=lambda: self.runtime.copy())
        self.repairs = 0
        async def repair():
            self.repairs += 1
            return {"fixed": [{"title": "listener"}]}
        self.agent.run_repair_agent = repair
        self.guardian = Guardian(self.agent, self.studio, clock=lambda: self.timestamp)

    async def asyncTearDown(self):
        await self.guardian.close()
        await self.studio.close()
        self.tmp.cleanup()

    async def wait_for_cycle(self, guardian=None):
        guardian = guardian or self.guardian
        async def wait():
            while guardian.snapshot()["cycles"] < 1:
                await asyncio.sleep(.01)
        await asyncio.wait_for(wait(), 2)

    async def test_runs_without_browser_and_stops_cleanly(self):
        self.guardian.start()
        await self.wait_for_cycle()
        view = self.guardian.snapshot()
        self.assertTrue(view["running"])
        self.assertEqual(view["repairs"], 1)
        self.assertEqual(len(view["checks"]), 5)
        self.assertNotIn("private-secret", json.dumps(view))
        await self.guardian.close()
        self.assertFalse(self.guardian.snapshot()["running"])

    async def test_local_model_check_reports_loaded_model_and_missing_model(self):
        self.agent.settings = SimpleNamespace(ai_ready=True, local_ai_base_url="http://ollama:11434/v1",
                                              local_ai_api_key="ollama-local", local_ai_model="qwen2.5-coder:7b")
        real_client = httpx.AsyncClient
        models = ["qwen2.5-coder:7b"]
        def transport(request):
            self.assertEqual(request.headers.get("authorization"), "Bearer ollama-local")
            return httpx.Response(200, json={"data": [{"id": name} for name in models]})
        with mock.patch("shadow.guardian.httpx.AsyncClient", side_effect=lambda **kwargs: real_client(
                transport=httpx.MockTransport(transport), **kwargs)):
            self.assertEqual((await self.guardian._local_ai_check())["state"], "ok")
            models.clear()
            self.assertEqual((await self.guardian._local_ai_check())["state"], "warning")

    async def test_pause_and_preferences_survive_restart(self):
        self.guardian.configure(enabled=False, interval_seconds=300, ai_review=False)
        resumed = Guardian(self.agent, self.studio, clock=lambda: self.timestamp)
        resumed.start()
        try:
            await asyncio.sleep(.02)
            self.assertEqual(resumed.snapshot()["cycles"], 0)
            self.assertEqual(resumed.snapshot()["interval_seconds"], 300)
            with self.assertRaises(DevelopmentError):
                resumed.request_check()
            resumed.configure(enabled=True)
            await self.wait_for_cycle(resumed)
            self.assertEqual(len(self.studio.jobs), 0)
        finally:
            await resumed.close()

    async def test_review_budget_survives_restart_and_respects_busy_studio(self):
        await self.guardian.check_once()
        await self.studio.task
        self.assertEqual(len(self.studio.jobs), 1)
        resumed = Guardian(self.agent, self.studio, clock=lambda: self.timestamp)
        await resumed.check_once()
        self.assertEqual(len(self.studio.jobs), 1)
        self.timestamp += AI_INTERVAL + 1
        self.studio.task = asyncio.create_task(asyncio.Event().wait())
        await resumed.check_once()
        self.assertEqual(len(self.studio.jobs), 1)
        await self.studio.close()
        await resumed.check_once()
        await self.studio.task
        self.assertEqual(len(self.studio.jobs), 2)

    async def test_account_switch_discards_inflight_results(self):
        started, finish = asyncio.Event(), asyncio.Event()
        async def repair():
            started.set()
            await finish.wait()
            return {"fixed": [{"title": "old account repair"}]}
        self.agent.run_repair_agent = repair
        pending = asyncio.create_task(self.guardian.check_once())
        await started.wait()
        self.agent.account_id = 202
        finish.set()
        await pending
        view = self.guardian.snapshot()
        self.assertEqual(view["cycles"], 0)
        self.assertEqual(view["repairs"], 0)
        self.assertEqual(view["events"], [])
        self.assertEqual(len(self.studio.jobs), 0)

    async def test_automatic_audits_are_account_scoped(self):
        await self.guardian.check_once()
        await self.studio.task
        job_id = self.guardian.snapshot()["last_ai_job"]
        self.studio.feedback(job_id, "accepted", "private-account-101-note")
        self.agent.account_id = 202
        self.assertIsNone(self.guardian.snapshot()["last_ai_job"])
        self.assertEqual(self.studio.status(self.agent.settings, self.guardian.scope_key())["jobs"], [])
        with self.assertRaises(DevelopmentError):
            self.studio.get(job_id, self.guardian.scope_key())
        self.assertNotIn("private-secret", json.dumps(self.prompts))
        await self.guardian.check_once()
        await self.studio.task
        self.assertNotIn("private-account-101-note", json.dumps(self.prompts[-1]))

    async def test_failed_recovery_does_not_leak_secrets_or_stop_checks(self):
        self.guardian.configure(ai_review=False)
        self.agent.run_repair_agent = mock.AsyncMock(side_effect=[RuntimeError("secret-key"), {"fixed": []}])
        await self.guardian.check_once()
        view = self.guardian.snapshot()
        self.assertIn("recovery", [item["id"] for item in view["checks"]])
        self.assertNotIn("secret-key", json.dumps(view))
        await self.guardian.check_once()
        self.assertEqual(self.guardian.snapshot()["cycles"], 2)

    async def test_history_retention_preserves_manual_work(self):
        manual = await self.studio.start(self.agent.settings, "Review code manually", "audit")
        await self.studio.task
        for _ in range(7):
            self.timestamp += AI_INTERVAL + 1
            await self.guardian.check_once()
            await self.studio.task
        self.assertEqual(len(self.studio.jobs), 6)
        self.assertIn(manual["id"], self.studio.jobs)

    async def test_source_failure_and_unwritable_storage_are_visible(self):
        (self.studio.root / "shadow/guardian.py").write_text("def broken(\n")
        self.guardian.configure(ai_review=False)
        blocker = Path(self.tmp.name) / "not-a-directory"
        blocker.write_text("file")
        self.guardian.state_file = blocker / "state.json"
        await self.guardian.check_once()
        result = self.guardian.snapshot()
        self.assertFalse(result["storage_ok"])
        source = next(c for c in result["checks"] if c["id"] == "source")
        self.assertEqual(source["state"], "error")
        self.assertIn("guardian.py", source["detail"])

    async def test_fastapi_lifespan_starts_and_closes_worker(self):
        import importlib
        module = importlib.import_module("shadow.app")
        self.agent.start = mock.AsyncMock()
        self.agent.stop = mock.AsyncMock()
        with mock.patch.object(module, "guardian", self.guardian), mock.patch.object(module, "development", self.studio), \
             mock.patch.object(module, "agent", self.agent), mock.patch.object(module, "keepalive", None):
            async with module.lifespan(module.app):
                await self.wait_for_cycle()
                self.assertTrue(self.guardian.snapshot()["running"])
            self.assertFalse(self.guardian.snapshot()["running"])
            self.agent.stop.assert_awaited_once()

    async def test_api_auth_controls_and_account_isolation(self):
        import importlib
        module = importlib.import_module("shadow.app")
        with mock.patch.object(module, "guardian", self.guardian), mock.patch.object(module, "development", self.studio), \
             mock.patch.object(module, "agent", self.agent), \
             mock.patch.object(module, "settings", SimpleNamespace(admin_token="test", setup_token="")):
            async with httpx.AsyncClient(transport=httpx.ASGITransport(app=module.app), base_url="http://test") as client:
                path = "/dashboard/api/development/guardian"
                for method, suffix in [("GET", ""), ("PATCH", ""), ("POST", "/check")]:
                    response = await client.request(method, path+suffix, json={})
                    self.assertEqual(response.status_code, 401)
                client.headers["Authorization"] = "Bearer test"
                self.assertEqual((await client.patch(path, json={"interval_seconds": 1})).status_code, 422)
                self.guardian.start()
                await self.wait_for_cycle()
                await self.studio.task
                job = self.guardian.snapshot()["last_ai_job"]
                response = await client.get(path)
                self.assertEqual(response.headers["cache-control"], "no-store")
                self.assertTrue(response.json()["running"])
                self.assertEqual((await client.post(path+"/check")).status_code, 202)
                self.agent.account_id = 202
                self.assertEqual((await client.get("/dashboard/api/development/"+job)).status_code, 400)
                response = await client.patch(path, json={"enabled": False})
                self.assertFalse(response.json()["enabled"])


if __name__ == "__main__":
    unittest.main()
