import importlib
import json
import os
import unittest
from unittest import mock

import httpx
from fastapi import HTTPException

from shadow.release_policy import automatic_paths_allowed, release_readiness


class ReleaseConnectionTests(unittest.IsolatedAsyncioTestCase):
    async def test_connection_validates_write_access_and_never_returns_key(self):
        module=importlib.import_module('shadow.app')
        key='github-test-key-1234567890';requests=[]
        def handle(request):
            requests.append(request)
            return httpx.Response(200,json={'permissions':{'push':True}})
        real_client=httpx.AsyncClient
        with mock.patch.dict(os.environ,{'SHADOW_AGENT_AUTO_DEPLOY':'true'}), mock.patch('shadow.app.save_env_vars',new_callable=mock.AsyncMock,return_value=True) as save, mock.patch('httpx.AsyncClient',side_effect=lambda **kw:real_client(transport=httpx.MockTransport(handle),**kw)):
            result=await module.development_github_connect(module.DevelopmentGitHubKey(api_key=key))
            self.assertEqual(os.environ['SHADOW_DEV_GITHUB_TOKEN'],key)
            save.assert_awaited_once_with({'SHADOW_DEV_GITHUB_TOKEN':key})
        self.assertTrue(result['auto_deploy']['ready']);self.assertTrue(result['persisted'])
        self.assertNotIn(key,json.dumps(result))
        self.assertEqual(requests[0].url.host,'api.github.com')
        self.assertEqual(requests[0].headers['Authorization'],'Bearer '+key)

    async def test_read_only_key_is_not_saved(self):
        module=importlib.import_module('shadow.app');real_client=httpx.AsyncClient
        def handle(request):return httpx.Response(200,json={'permissions':{'push':False}})
        with mock.patch('shadow.app.save_env_vars',new_callable=mock.AsyncMock) as save, mock.patch('httpx.AsyncClient',side_effect=lambda **kw:real_client(transport=httpx.MockTransport(handle),**kw)):
            with self.assertRaises(HTTPException) as raised:
                await module.development_github_connect(module.DevelopmentGitHubKey(api_key='github-test-key-1234567890'))
        self.assertEqual(raised.exception.status_code,400);save.assert_not_awaited()

    async def test_public_health_has_revision_and_counts_without_identity(self):
        module=importlib.import_module('shadow.app')
        with mock.patch.dict(os.environ,{'RENDER_GIT_COMMIT':'abc123'}):result=await module.health()
        self.assertEqual(result['revision'],'abc123')
        self.assertIn('accounts_configured',result);self.assertIn('accounts_connected',result)
        for name in ['account','accounts','username','phone','session','token']:self.assertNotIn(name,result)

    async def test_auto_release_enforces_task_ownership(self):
        module=importlib.import_module('shadow.app')
        from shadow.development import DevelopmentError
        with mock.patch.object(module.development,'get',side_effect=DevelopmentError('Task not found')), mock.patch.object(module.development,'publish',new_callable=mock.AsyncMock) as publish:
            with self.assertRaises(DevelopmentError):await module.development_auto_deploy('another-account-task')
        publish.assert_not_awaited()


class ReleasePolicyTests(unittest.TestCase):
    def test_no_key_does_not_report_ready(self):
        with mock.patch.dict(os.environ,{'SHADOW_AGENT_AUTO_DEPLOY':'true'}),mock.patch('shadow.release_policy.github_token',return_value=''):
            self.assertFalse(release_readiness()['ready'])

    def test_release_gate_paths_are_protected(self):
        for path in ['.github/workflows/ci.yml','scripts/auto-deploy.cjs','scripts/auto-deploy.test.cjs','shadow/release_policy.py']:
            self.assertFalse(automatic_paths_allowed([{'path':path}]))
        self.assertTrue(automatic_paths_allowed([{'path':'shadow/workspace.js'}]))
