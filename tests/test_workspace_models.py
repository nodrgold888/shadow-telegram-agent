import json
import os
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import httpx

from shadow.assistant import ShadowAssistant
from shadow.config import AIProvider
from shadow.free_catalog import catalog_snapshot
from shadow.workspace import ChatStore, Workspace, WorkspaceError, workspace_reply, validate_model_selection
from shadow.workspace_models import shortlist, account_shortlist, catalog_choice, check_catalog_choice
from tests.test_ai_fallback import make_settings, QuotaError
from tests.test_free_catalog import BASE, MODEL, gateway_settings


class ShortlistTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.env = patch.dict(os.environ, {'SHADOW_FREE_GATEWAY_BASE_URL': BASE})
        self.env.start()
        self.addCleanup(self.env.stop)

    def test_twelve_distinct_chat_recommendations_without_fake_connections(self):
        settings = make_settings(openai_api_key='', ai_api_key='')
        models = shortlist(settings)
        self.assertEqual(len(models), 12)
        self.assertEqual(len({m['label'].casefold() for m in models}), 12)
        self.assertFalse(any(m['enabled'] for m in models))
        chat_ids = {r['id'] for r in catalog_snapshot()['models'] if r['kind'] == 'chat' and r['enabled']}
        self.assertTrue(all(m['model'] in chat_ids for m in models))
        self.assertEqual(len(catalog_snapshot()['models']), 339)
        self.assertEqual([m['rank'] for m in models], sorted(m['rank'] for m in models))

    def test_saved_flagships_remain_and_weaker_reserves_are_hidden(self):
        settings = make_settings(ai_work_mode='free', ai_base_url='https://api.xkiro.com/v1', ai_model='qwen/qwen3.8-max:free',
            ai_extra_providers=(AIProvider('Sonnet', 'https://api.xkiro.com/v1', 'private-provider-secret', 'anthropic/claude-sonnet-5', 2),
                                AIProvider('reserve', 'https://openrouter.ai/api/v1', 'private-provider-secret', 'weak:free', 3)))
        models = shortlist(settings)
        self.assertEqual(len(models), 12)
        self.assertEqual(models[0]['id'], 'slot:1')
        self.assertTrue(models[0]['enabled'])
        self.assertFalse(models[1]['enabled'])
        self.assertNotIn('slot:3', {m['id'] for m in models})
        self.assertNotIn('private-provider-secret', json.dumps(models))

    async def test_account_keys_and_live_quota_determine_availability(self):
        rows = shortlist(gateway_settings())
        first, second = rows[:2]
        live = [{'id': first['model'], 'available': True, 'execution_status': 'ready'},
                {'id': second['model'], 'available': False, 'execution_status': 'exhausted'}]
        with patch('shadow.workspace_models.gateway_models', AsyncMock(return_value=live)) as fetch:
            one = await account_shortlist(gateway_settings('account-one-key'))
            two = await account_shortlist(gateway_settings('account-two-key'))
        self.assertEqual([call.args[1] for call in fetch.await_args_list], ['account-one-key', 'account-two-key'])
        self.assertTrue(next(m for m in one['models'] if m['model'] == first['model'])['enabled'])
        self.assertFalse(next(m for m in two['models'] if m['model'] == second['model'])['enabled'])
        self.assertNotIn('account-one-key', json.dumps(one))

    async def test_gateway_offline_disables_catalog_choices_and_reports_notice(self):
        with patch('shadow.workspace_models.gateway_models', AsyncMock(side_effect=ValueError('Gateway unavailable'))):
            result = await account_shortlist(gateway_settings())
        self.assertEqual(len(result['models']), 12)
        self.assertFalse(result['gateway_checked'])
        self.assertFalse(any(m['enabled'] for m in result['models']))
        self.assertEqual(result['gateway_notice'], 'Gateway unavailable')

    def test_arbitrary_media_and_other_account_connections_cannot_be_selected(self):
        for target in ['unknown', '@cf/black-forest-labs/flux-1-schnell']:
            with self.assertRaises(WorkspaceError):
                validate_model_selection(gateway_settings(), 'catalog:'+target)
        with self.assertRaises(ValueError):
            catalog_choice(make_settings(ai_api_key=''), 'catalog:'+MODEL)

    async def test_quota_or_missing_model_blocks_selection(self):
        with patch('shadow.workspace_models.gateway_models', AsyncMock(return_value=[])):
            with self.assertRaises(ValueError):
                await check_catalog_choice(gateway_settings(), 'catalog:'+MODEL)

    async def test_explicit_catalog_model_runs_then_falls_back_without_changing_settings(self):
        settings = gateway_settings()
        create = AsyncMock(return_value=SimpleNamespace(choices=[SimpleNamespace(message=SimpleNamespace(content='answer'))]))
        client = SimpleNamespace(chat=SimpleNamespace(completions=SimpleNamespace(create=create)))
        with patch('shadow.assistant.AsyncOpenAI', return_value=client):
            assistant = ShadowAssistant(settings)
        live = [{'id': MODEL, 'available': True, 'execution_status': 'ready'}]
        with patch('shadow.workspace_models.gateway_models', AsyncMock(return_value=live)):
            answer, chosen = await workspace_reply(assistant, [{'role': 'user', 'content': 'hello'}], 'chat', [], model='catalog:'+MODEL)
            self.assertEqual(create.await_args.kwargs['model'], MODEL)
            self.assertEqual(chosen['provider_id'], 'catalog:'+MODEL)
            self.assertFalse(chosen['fallback_used'])
            create.reset_mock()
            create.side_effect = [QuotaError(), SimpleNamespace(choices=[SimpleNamespace(message=SimpleNamespace(content='backup answer'))])]
            answer, chosen = await workspace_reply(assistant, [{'role': 'user', 'content': 'hello'}], 'chat', [], model='catalog:'+MODEL)
        self.assertEqual([c.kwargs['model'] for c in create.await_args_list], [MODEL, 'auto:smart'])
        self.assertTrue(chosen['fallback_used'])
        self.assertEqual(settings.ai_model, 'auto:smart')
        self.assertEqual(settings.ai_api_key, 'freellmapi-own-key')

    async def test_auto_reserve_is_usable_even_if_not_in_top_list(self):
        settings = make_settings(ai_work_mode='free', ai_base_url='https://openrouter.ai/api/v1', ai_model='weak:free')
        with tempfile.TemporaryDirectory() as tmp:
            result = await Workspace(ChatStore(Path(tmp)/'chat.sqlite3')).status(11, settings)
        self.assertEqual(result['recommended_count'], 12)
        self.assertEqual(result['connected_count'], 0)
        self.assertEqual(result['eligible_count'], 1)

    async def test_unavailable_catalog_request_does_not_write_any_history(self):
        from shadow import app as module
        settings = gateway_settings()
        with tempfile.TemporaryDirectory() as tmp:
            workspace = Workspace(ChatStore(Path(tmp)/'chat.sqlite3'))
            with patch.object(module, 'agent', SimpleNamespace(account_id=11, settings=settings)), \
                 patch.object(module, '_dashboard_allowed', return_value=True), \
                 patch.object(module, 'workspace_ai', workspace), \
                 patch('shadow.workspace_models.gateway_models', AsyncMock(return_value=[])):
                async with httpx.AsyncClient(transport=httpx.ASGITransport(app=module.app), base_url='http://test') as client:
                    response = await client.post('/dashboard/api/workspace/messages?account=11', json={'message': 'private', 'model': 'catalog:'+MODEL})
                self.assertEqual(response.status_code, 400)
                self.assertEqual(workspace.store.list(11), [])
