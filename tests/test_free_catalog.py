import asyncio
import json
import os
import unittest
from dataclasses import replace
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import httpx

from shadow.ai_slots import ProviderSlotsFull, parse_provider
from shadow.config import AIProvider
from shadow.free_catalog import (catalog_snapshot, catalog_view, free_chat_model_ids,
                                 gateway_models, model_status, prepare_gateway_selection)
from shadow.model_routing import is_free_provider
from tests.test_ai_fallback import make_settings

BASE = "https://gateway.example/v1"
MODEL = "gemini-3.8-flash"


def gateway_settings(key="freellmapi-own-key", **over):
    return make_settings(ai_work_mode="free", ai_base_url=BASE, ai_api_key=key, ai_model="auto:smart", **over)


class CatalogMetadataTests(unittest.TestCase):
    def test_every_signed_endpoint_is_included_and_media_is_not_chat(self):
        from pathlib import Path
        raw = json.loads(Path('shadow/data/freellmapi-catalog.json').read_text())
        snapshot = catalog_snapshot()
        expected = sum(len(raw[k]) for k in ('models', 'embeddings', 'transcriptionModels', 'videoModels'))
        self.assertEqual(len(snapshot['models']), expected)
        self.assertEqual(sum(snapshot['counts'].values()), expected)
        self.assertEqual(set(snapshot['counts']), {'chat', 'image', 'audio', 'embedding', 'transcription', 'video'})
        self.assertTrue(any(m['id'].startswith('@cf/') and m['kind'] == 'image' for m in snapshot['models']))
        self.assertNotIn('@cf/black-forest-labs/flux-1-schnell', free_chat_model_ids())

    def test_free_mode_approves_only_catalog_chat_ids_at_the_exact_gateway(self):
        with patch.dict(os.environ, {'SHADOW_FREE_GATEWAY_BASE_URL': BASE}):
            self.assertTrue(is_free_provider(AIProvider('Gemini', BASE, 'test', MODEL)))
            self.assertFalse(is_free_provider(AIProvider('Gemini', BASE+'-other', 'test', MODEL)))
            self.assertFalse(is_free_provider(AIProvider('paid', BASE, 'test', 'paid/custom-model')))
            self.assertFalse(is_free_provider(AIProvider('image', BASE, 'test', '@cf/black-forest-labs/flux-1-schnell')))
            self.assertEqual(parse_provider({'base_url': BASE, 'api_key': 'test-key-long', 'name': 'test',
                                             'model': '@cf/qwen/qwen3.8-27b'}).model, '@cf/qwen/qwen3.8-27b')

    def test_selection_preserves_keys_and_adds_one_automatic_fallback(self):
        original = gateway_settings()
        with patch.dict(os.environ, {'SHADOW_FREE_GATEWAY_BASE_URL': BASE}):
            pinned, values = prepare_gateway_selection(original, MODEL)
            self.assertEqual(pinned.ai_model, MODEL)
            self.assertEqual(pinned.ai_first_slot, 1)
            self.assertEqual(pinned.ai_api_key, original.ai_api_key)
            self.assertTrue(any(p.model == 'auto:smart' for p in pinned.backup_providers))
            again, values = prepare_gateway_selection(pinned, '@cf/qwen/qwen3.8-27b')
            self.assertEqual(len(again.backup_providers), 2)
            self.assertEqual(again.ai_model, '@cf/qwen/qwen3.8-27b')
            self.assertEqual(original.ai_model, 'auto:smart')

    def test_full_slots_fail_before_modifying_a_provider(self):
        original = gateway_settings(ai_extra_providers=tuple(AIProvider(str(i), 'https://other.example/v1', 'own-key', 'other', i) for i in range(2, 13)))
        with patch.dict(os.environ, {'SHADOW_FREE_GATEWAY_BASE_URL': BASE}):
            with self.assertRaises(ProviderSlotsFull):
                prepare_gateway_selection(original, MODEL)
        self.assertEqual(original.ai_model, 'auto:smart')

    def test_existing_fallback_and_other_providers_are_preserved(self):
        original = gateway_settings(ai_extra_providers=(AIProvider('Own fallback', BASE, 'other-own-key', 'auto:fast', 5),
                                                       AIProvider('Other', 'https://other.example/v1', 'private', 'other', 9)))
        with patch.dict(os.environ, {'SHADOW_FREE_GATEWAY_BASE_URL': BASE}):
            updated, values = prepare_gateway_selection(original, MODEL)
        self.assertEqual(updated.ai_extra_providers, original.ai_extra_providers)
        self.assertNotIn('AI_API_KEY_9', values)

    def test_media_and_unknown_ids_cannot_be_selected(self):
        with patch.dict(os.environ, {'SHADOW_FREE_GATEWAY_BASE_URL': BASE}):
            for model in ['paid/custom-model', '@cf/black-forest-labs/flux-1-schnell', 'whisper-large-v3-turbo']:
                with self.assertRaises(ValueError):
                    prepare_gateway_selection(gateway_settings(), model)

    def test_exhausted_and_unknown_availability_are_not_ready(self):
        self.assertEqual(model_status({'available': True, 'execution_status': 'exhausted'}), 'exhausted')
        self.assertEqual(model_status({'available': False}), 'needsKey')
        self.assertEqual(model_status({'available': False, 'unavailable_reason': 'disabled'}), 'disabled')
        self.assertEqual(model_status({'available': True}), 'connected')
        self.assertEqual(model_status(None), 'not_listed')


class CatalogLiveTests(unittest.IsolatedAsyncioTestCase):
    async def test_strong_model_quota_failure_uses_auto_fallback_without_paid_calls(self):
        from shadow.assistant import ShadowAssistant
        from tests.test_free_mode import NoCredits
        attempts, constructors = [], []
        class Client:
            def __init__(self, **options):
                constructors.append(options)
                async def create(**request):
                    attempts.append(request['model'])
                    if request['model'] == MODEL:
                        raise NoCredits()
                    return SimpleNamespace(choices=[SimpleNamespace(message=SimpleNamespace(content='tushunarli', tool_calls=[]))])
                self.chat = SimpleNamespace(completions=SimpleNamespace(create=create))
        with patch.dict(os.environ, {'SHADOW_FREE_GATEWAY_BASE_URL': BASE, 'SHADOW_FREE_GATEWAY_ACCESS_KEY': 'test-edge-key'}), \
             patch('shadow.assistant.AsyncOpenAI', Client):
            selected, _ = prepare_gateway_selection(gateway_settings(), MODEL)
            assistant = ShadowAssistant(selected)
            self.assertEqual(await assistant.reply(chat_title='Test', history='', message='salom'), 'tushunarli')
        self.assertIsNone(assistant.client)
        self.assertEqual(attempts, [MODEL, 'auto:smart'])
        self.assertTrue(all(c['base_url'] == BASE for c in constructors))
        for options in constructors:
            await options['http_client'].aclose()

    async def test_no_key_shows_the_whole_catalog_without_network_or_false_readiness(self):
        with patch.dict(os.environ, {'SHADOW_FREE_GATEWAY_BASE_URL': BASE}), patch('shadow.free_catalog.gateway_models') as fetch:
            result = await catalog_view(replace(gateway_settings(), ai_api_key=''))
        fetch.assert_not_called()
        self.assertEqual(len(result['models']), len(catalog_snapshot()['models']))
        self.assertFalse(result['gateway']['checked'])
        self.assertTrue(all(m['status'] == 'unknown' for m in result['models']))
        self.assertEqual(len(result['strongest']), 8)
        rows = {m['uid']: m for m in result['models']}
        ranks = [rows[uid]['rank'] for uid in result['strongest']]
        self.assertEqual(ranks, sorted(ranks))

    async def test_catalog_handles_live_ready_needs_key_and_exhausted_without_secrets(self):
        live = [{'id': MODEL, 'available': True, 'execution_status': 'ready', 'secret': 'leaked'},
                {'id': 'qwen/qwen3.8-27b', 'available': True, 'execution_status': 'exhausted'}]
        with patch.dict(os.environ, {'SHADOW_FREE_GATEWAY_BASE_URL': BASE}), patch('shadow.free_catalog.gateway_models', AsyncMock(return_value=live)) as fetch:
            result = await catalog_view(gateway_settings())
        fetch.assert_awaited_once_with(BASE, 'freellmapi-own-key')
        rows = {m['id']: m for m in result['models'] if m['kind'] == 'chat'}
        self.assertEqual(rows[MODEL]['status'], 'ready')
        self.assertEqual(rows['qwen/qwen3.8-27b']['status'], 'exhausted')
        self.assertTrue(all(m['status'] == 'unknown' for m in result['models'] if m['kind'] != 'chat'))
        self.assertNotIn('freellmapi-own-key', json.dumps(result))
        self.assertNotIn('leaked', json.dumps(result))

    async def test_offline_gateway_preserves_catalog_without_inventing_availability(self):
        with patch.dict(os.environ, {'SHADOW_FREE_GATEWAY_BASE_URL': BASE}), patch('shadow.free_catalog.gateway_models', AsyncMock(side_effect=ValueError('offline'))):
            result = await catalog_view(gateway_settings())
        self.assertFalse(result['gateway']['checked'])
        self.assertTrue(result['models'])
        self.assertTrue(all(m['status'] == 'unknown' for m in result['models']))

    async def test_unapproved_url_is_rejected_before_any_network_request(self):
        with patch.dict(os.environ, {'SHADOW_FREE_GATEWAY_BASE_URL': BASE}), patch('shadow.free_catalog.httpx.AsyncClient') as client:
            with self.assertRaises(ValueError):
                await gateway_models('https://attacker.example/v1', 'private-key')
        client.assert_not_called()

    async def test_each_account_uses_its_own_key_and_the_edge_key_stays_on_the_gateway(self):
        requests = []
        real = httpx.AsyncClient
        def handle(request):
            requests.append(request)
            return httpx.Response(200, json={'data': [{'id': MODEL, 'available': True, 'execution_status': 'ready'}]})
        with patch.dict(os.environ, {'SHADOW_FREE_GATEWAY_BASE_URL': BASE, 'SHADOW_FREE_GATEWAY_ACCESS_KEY': 'edge-secret'}), \
             patch('shadow.free_catalog.httpx.AsyncClient', side_effect=lambda **kw: real(transport=httpx.MockTransport(handle), **kw)):
            for key in ['account-one-key', 'account-two-key']:
                result = await catalog_view(gateway_settings(key))
                self.assertNotIn(key, json.dumps(result))
        self.assertEqual([r.headers['Authorization'] for r in requests], ['Bearer account-one-key', 'Bearer account-two-key'])
        self.assertTrue(all(r.headers['X-Shadow-Gateway-Key'] == 'edge-secret' for r in requests))


class CatalogApiTests(unittest.IsolatedAsyncioTestCase):
    async def test_catalog_and_selection_require_authentication(self):
        from shadow.app import app
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url='http://test') as client:
            for method, url in [('GET', '/dashboard/api/ai-catalog'), ('POST', '/dashboard/api/ai-catalog'),
                                ('POST', '/dashboard/api/ai-catalog/select')]:
                result = await client.request(method, url, json={'model': MODEL})
                self.assertEqual(result.status_code, 401)

    async def test_selection_rechecks_current_availability_before_any_write(self):
        from shadow import app as module
        worker = SimpleNamespace(settings=gateway_settings(), account_id=1, _setup_lock=asyncio.Lock(), assistant=None)
        with patch.object(module, 'agent', worker), patch.object(module, '_dashboard_allowed', return_value=True), \
             patch.object(module, 'gateway_models', AsyncMock(return_value=[{'id': MODEL, 'available': True, 'execution_status': 'exhausted'}])), \
             patch.object(module, 'save_env_vars', AsyncMock()) as save, patch.dict(os.environ, {'SHADOW_FREE_GATEWAY_BASE_URL': BASE}):
            async with httpx.AsyncClient(transport=httpx.ASGITransport(app=module.app), base_url='http://test') as client:
                result = await client.post('/dashboard/api/ai-catalog/select', json={'model': MODEL})
                self.assertEqual(result.status_code, 409)
                save.assert_not_called()
        self.assertEqual(worker.settings.ai_model, 'auto:smart')

    async def test_ready_model_selection_saves_the_pin_and_auto_fallback(self):
        from shadow import app as module
        worker = SimpleNamespace(settings=gateway_settings(), account_id=1, _setup_lock=asyncio.Lock(), assistant=None)
        with patch.object(module, 'agent', worker), patch.object(module, '_dashboard_allowed', return_value=True), \
             patch.object(module, 'gateway_models', AsyncMock(return_value=[{'id': MODEL, 'available': True, 'execution_status': 'ready'}])), \
             patch.object(module, 'save_env_vars', AsyncMock(return_value=True)) as save, patch.dict(os.environ, {'SHADOW_FREE_GATEWAY_BASE_URL': BASE}):
            async with httpx.AsyncClient(transport=httpx.ASGITransport(app=module.app), base_url='http://test') as client:
                result = await client.post('/dashboard/api/ai-catalog/select', json={'model': MODEL})
                self.assertEqual(result.status_code, 200)
                self.assertEqual(result.json()['model'], MODEL)
                self.assertNotIn('freellmapi-own-key', result.text)
                self.assertEqual(save.call_args.args[0]['AI_MODEL_2'], 'auto:smart')
        self.assertEqual(worker.settings.ai_model, MODEL)
