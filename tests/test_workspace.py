import asyncio
import json
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import httpx

from shadow.assistant import ShadowAssistant
from shadow.config import AIProvider
from shadow.workspace import ChatStore, Workspace, WorkspaceError, workspace_reply
from tests.test_ai_fallback import build, make_settings, QuotaError

CODE = "```python\ndef hello(name):\n    return 'hi-' + name\n```\n\nozingiz-chi?"


class WorkspaceTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.store = ChatStore(Path(self.tmp.name) / 'private.sqlite3')
        self.workspace = Workspace(self.store)

    async def asyncTearDown(self):
        await self.workspace.close()
        self.tmp.cleanup()

    async def test_history_is_private_to_account_and_persists_exact_code(self):
        thread = self.store.create(11, 'My private project', 'code')
        thread['messages'] = [{'role': 'assistant', 'content': CODE}]
        self.store.save(11, thread)
        another = ChatStore(self.store.path)
        self.assertEqual(another.get(11, thread['id'])['messages'][0]['content'], CODE)
        self.assertEqual(another.list(22), [])
        with self.assertRaises(WorkspaceError):
            another.get(22, thread['id'])
        another.delete(22, thread['id'])
        self.assertEqual(len(another.list(11)), 1)
        self.assertEqual(self.store.path.stat().st_mode & 0o777, 0o600)

    async def test_raw_code_preserved_and_openai_credits_fall_back(self):
        assistant, primary, compat = build(make_settings(), openai_error=QuotaError())
        compat.chat.completions.create.return_value.choices[0].message.content = CODE
        answer, chosen = await workspace_reply(assistant, [{'role': 'user', 'content': "fix 'a-b'"}], 'code', [])
        self.assertEqual(answer, CODE)
        self.assertEqual(chosen['model'], 'backup-model')
        request = compat.chat.completions.create.call_args.kwargs
        self.assertEqual(request['messages'][-1]['content'], "fix 'a-b'")
        self.assertNotIn('telefon', request['messages'][0]['content'])
        primary.responses.create.assert_awaited_once()

    async def test_free_mode_never_constructs_paid_client_and_uses_fallback(self):
        settings = make_settings(ai_work_mode='free', ai_base_url='https://openrouter.ai/api/v1', ai_model='first:free',
                                ai_extra_providers=(AIProvider('second', 'https://openrouter.ai/api/v1', 'second-private', 'second:free', 2),
                                                    AIProvider('paid', 'https://paid.example/v1', 'paid-secret', 'paid', 3)))
        first = SimpleNamespace(chat=SimpleNamespace(completions=SimpleNamespace(create=AsyncMock(side_effect=QuotaError()))))
        second = SimpleNamespace(chat=SimpleNamespace(completions=SimpleNamespace(create=AsyncMock(return_value=SimpleNamespace(choices=[SimpleNamespace(message=SimpleNamespace(content=CODE))])))))
        with patch('shadow.assistant.AsyncOpenAI', side_effect=[first, second]) as factory:
            assistant = ShadowAssistant(settings)
            answer, chosen = await workspace_reply(assistant, [{'role': 'user', 'content': 'make code'}], 'code', [])
            self.assertEqual(factory.call_count, 2)
        self.assertIsNone(assistant.client)
        self.assertEqual(answer, CODE)
        self.assertEqual(chosen['model'], 'second:free')

    async def test_background_reply_cancellation_is_account_bound(self):
        assistant, _, _ = build(make_settings())
        async def wait(*args):
            await asyncio.Event().wait()
        with patch('shadow.workspace.workspace_reply', side_effect=wait):
            job = self.workspace.start(11, assistant, 'hello', 'chat')
            await asyncio.sleep(0)
            with self.assertRaises(WorkspaceError):
                await self.workspace.cancel(22, job['id'])
            with self.assertRaises(WorkspaceError):
                self.workspace.start(11, assistant, 'duplicate', 'chat')
            result = await self.workspace.cancel(11, job['id'])
            self.assertEqual(result['state'], 'cancelled')
        self.assertEqual(len(self.store.get(11, job['thread_id'])['messages']), 1)
        self.assertEqual(self.store.list(22), [])

    async def test_research_returns_only_real_search_results_and_model_metadata(self):
        assistant, _, _ = build(make_settings())
        sources = [{'title': 'Official source', 'url': 'https://example.org/source', 'snippet': 'Actual excerpt'}]
        with patch('shadow.workspace._configured_providers', return_value=['google']), \
             patch('shadow.workspace.search_web', AsyncMock(return_value={'results': sources})), \
             patch('shadow.workspace.workspace_reply', AsyncMock(return_value=('Result [1]', {'provider': 'test', 'model': 'model'}))) as reply:
            job = self.workspace.start(11, assistant, 'research topic', 'research')
            await self.workspace.jobs[job['id']]['task']
            self.assertEqual(reply.call_args.args[-1], sources)
        saved = self.store.get(11, job['thread_id'])['messages'][-1]
        self.assertEqual(saved['sources'], sources)
        self.assertEqual(saved['model'], 'model')
        self.assertEqual(self.workspace.job(11, job['id'])['state'], 'done')

    async def test_missing_search_or_ai_does_not_write_history(self):
        assistant, _, _ = build(make_settings())
        with patch('shadow.workspace._configured_providers', side_effect=ValueError('Not configured')):
            with self.assertRaises(ValueError):
                self.workspace.start(11, assistant, 'research', 'research')
        assistant.settings = make_settings(ai_work_mode='free')
        with self.assertRaises(WorkspaceError):
            self.workspace.start(11, assistant, 'hello', 'chat')
        self.assertEqual(self.store.list(11), [])

    async def test_modes_can_change_in_the_same_private_conversation(self):
        assistant, _, _ = build(make_settings())
        with patch('shadow.workspace.workspace_reply', AsyncMock(return_value=(CODE, {'provider': 'test', 'model': 'code-model'}))):
            first = self.workspace.start(11, assistant, 'hello', 'chat')
            await self.workspace.jobs[first['id']]['task']
            second = self.workspace.start(11, assistant, 'now write code', 'code', first['thread_id'])
            await self.workspace.jobs[second['id']]['task']
        thread = self.store.get(11, first['thread_id'])
        self.assertEqual(thread['mode'], 'code')
        self.assertEqual(len(thread['messages']), 4)
        self.assertEqual(self.store.list(11)[0]['mode'], 'code')

    async def test_errors_do_not_expose_provider_secrets(self):
        assistant, _, _ = build(make_settings())
        with patch('shadow.workspace.workspace_reply', AsyncMock(side_effect=RuntimeError('sk-private-key raw user data'))):
            job = self.workspace.start(11, assistant, 'hello', 'chat')
            await self.workspace.jobs[job['id']]['task']
        result = self.workspace.public(self.workspace.job(11, job['id']))
        self.assertEqual(result['state'], 'error')
        self.assertNotIn('sk-private', json.dumps(result))
        self.assertNotIn('raw user data', json.dumps(result))


class WorkspaceApiTests(unittest.IsolatedAsyncioTestCase):
    async def test_auth_and_expected_account_are_enforced(self):
        from shadow import app as module
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=module.app), base_url='http://test') as client:
            for method, path in [('GET', ''), ('POST', '/check'), ('GET', '/threads/private'), ('DELETE', '/threads/private'),
                                 ('GET', '/jobs/private'), ('POST', '/jobs/private/cancel'), ('POST', '/messages')]:
                r = await client.request(method, '/dashboard/api/workspace'+path, json={'message': 'hello'})
                self.assertEqual(r.status_code, 401)
        assistant, _, _ = build(make_settings())
        worker = SimpleNamespace(account_id=11, assistant=assistant, settings=assistant.settings)
        with patch.object(module, 'agent', worker), patch.object(module, '_dashboard_allowed', return_value=True):
            async with httpx.AsyncClient(transport=httpx.ASGITransport(app=module.app), base_url='http://test') as client:
                r = await client.post('/dashboard/api/workspace/messages?account=22', json={'message': 'hello'})
                self.assertEqual(r.status_code, 409)

    async def test_thread_and_job_from_other_account_are_inaccessible(self):
        from shadow import app as module
        with tempfile.TemporaryDirectory() as tmp:
            ws = Workspace(ChatStore(Path(tmp) / 'test.db'))
            thread = ws.store.create(11, 'Private', 'code')
            ws.jobs['private-job'] = {'account': '11'}
            assistant, _, _ = build(make_settings())
            with patch.object(module, 'workspace_ai', ws), patch.object(module, 'agent', SimpleNamespace(account_id=22, assistant=assistant, settings=assistant.settings)), \
                 patch.object(module, '_dashboard_allowed', return_value=True):
                async with httpx.AsyncClient(transport=httpx.ASGITransport(app=module.app), base_url='http://test') as client:
                    for path in ['/threads/'+thread['id'], '/jobs/private-job']:
                        result = await client.get('/dashboard/api/workspace'+path+'?account=22')
                        self.assertEqual(result.status_code, 404)
                    result = await client.get('/dashboard/api/workspace?account=22')
                    self.assertEqual(result.json()['threads'], [])
                    self.assertNotIn('backup-key', result.text)

    async def test_real_reply_check_is_independent_of_telegram_reply_switch(self):
        from shadow import app as module
        settings = make_settings(reply_enabled=False)
        with patch.object(module, 'agent', SimpleNamespace(account_id=11, settings=settings, assistant=None)), \
             patch.object(module, '_dashboard_allowed', return_value=True), \
             patch.object(module, 'workspace_reply', AsyncMock(return_value=('ok', {'provider': 'test', 'model': 'own-model'}))) as reply:
            async with httpx.AsyncClient(transport=httpx.ASGITransport(app=module.app), base_url='http://test') as client:
                result = await client.post('/dashboard/api/workspace/check?account=11')
                self.assertTrue(result.json()['ok'])
                self.assertEqual(result.json()['model'], 'own-model')
                self.assertNotIn('backup-key', result.text)
                self.assertEqual(reply.call_args.args[0].settings, settings)
                wrong = await client.post('/dashboard/api/workspace/check?account=22')
                self.assertEqual(wrong.status_code, 409)
