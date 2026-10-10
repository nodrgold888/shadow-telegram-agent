import asyncio
import base64
import json
import os
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest import mock

import httpx

from shadow.development import DevelopmentError, DevelopmentStudio, ask_ai, prepare_changes, read_sources, source_path
from tests.test_ai_fallback import make_settings

SETTINGS = SimpleNamespace(ai_ready=True)


class SourceTests(unittest.TestCase):
    def test_paths_confine_reads_and_writes(self):
        for path in ('../.env', '/etc/passwd', 'shadow/../app.py', 'shadow/.env', 'shadow//app.py', '.github/workflows/run.py', 'shadow/state/keys.json', 'shadow/session.session'):
            with self.subTest(path=path), self.assertRaises(DevelopmentError):
                source_path(path, writing=True)
        self.assertEqual(source_path('shadow/development.py', writing=True), 'shadow/development.py')
        with self.assertRaises(DevelopmentError):
            source_path('shadow/skills/community/anthropics/frontend-design/SKILL.md', writing=True)
        self.assertTrue(source_path('shadow/skills/community/anthropics/frontend-design/LOCAL_ADAPTER.md', writing=True))
        with self.assertRaises(DevelopmentError):
            source_path('shadow/skills/community/voltagent/categories/04-quality-security/security-auditor.md', writing=True)
        self.assertTrue(source_path('shadow/skills/community/voltagent/LOCAL_ADAPTER.md', writing=True))
        self.assertEqual(source_path('shadow/new_feature.py', writing=True), 'shadow/new_feature.py')

    def test_source_reader_excludes_secrets_and_symlinks(self):
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp);(root/'shadow').mkdir()
            (root/'shadow/app.py').write_text('answer = 42\n')
            (root/'.env').write_text('SECRET=never\n')
            (root/'shadow/.env').write_text('SECRET=never\n')
            (root/'shadow/state').mkdir();(root/'shadow/state/data.json').write_text('{"secret":1}')
            (root/'shadow/leak.py').symlink_to(root/'.env')
            self.assertEqual(read_sources(root), {'shadow/app.py':'answer = 42\n'})

    def test_exact_edits_and_syntax_must_pass(self):
        source={'shadow/a.py':'value = 1\n'}
        for edit in ({'path':'shadow/a.py','find':'missing','replace':'value = 2'},
                     {'path':'shadow/a.py','find':'value = 1','replace':'value = ('},
                     {'path':'shadow/a.py','content':'value = 2'}):
            with self.subTest(edit=edit), self.assertRaises(DevelopmentError):
                prepare_changes({'changes':[edit]},source,list(source))
        with self.assertRaises(DevelopmentError):
            prepare_changes({'changes':[{'path':'shadow/a.py','find':'value','replace':'new'}]},source,[])

    def test_new_files_and_existing_files_have_applicable_patch(self):
        files,patch=prepare_changes({'changes':[{'path':'shadow/a.py','find':'1','replace':'2'},
            {'path':'shadow/new.py','content':'new = True\n'}]}, {'shadow/a.py':'value = 1\n'}, ['shadow/a.py'])
        self.assertEqual(files[0]['content'],'value = 2\n')
        self.assertIsNone(files[1]['base_hash'])
        self.assertIn('--- /dev/null',patch)
        self.assertIn('+new = True',patch)
        self.assertIn('Python syntax parsed; code was not executed',files[0]['checks'])


class LocalAiFallbackTests(unittest.IsolatedAsyncioTestCase):
    async def test_development_uses_local_model_after_cloud_balance_error(self):
        class BalanceError(Exception):
            status_code = 402

        attempted = []
        class Client:
            def __init__(self, **options):
                attempted.append(options.get('base_url'))
                if options.get('base_url') is None:
                    self.responses = SimpleNamespace(create=mock.AsyncMock(side_effect=BalanceError()))
                else:
                    reply = SimpleNamespace(choices=[SimpleNamespace(message=SimpleNamespace(content='{"ok":true}'))])
                    self.chat = SimpleNamespace(completions=SimpleNamespace(create=mock.AsyncMock(return_value=reply)))

            async def __aenter__(self):
                return self

            async def __aexit__(self, *args):
                return False

        settings = make_settings(local_ai_base_url='http://ollama:11434/v1',
                                 local_ai_api_key='ollama-local', local_ai_model='qwen2.5-coder:7b')
        with mock.patch('shadow.development.AsyncOpenAI', side_effect=Client):
            result = await ask_ai(settings, [{'role': 'user', 'content': 'Check'}], 200)
        self.assertEqual(result, {'ok': True})
        self.assertEqual(attempted, [None, 'http://ollama:11434/v1'])


class DevelopmentTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.tmp=tempfile.TemporaryDirectory();self.root=Path(self.tmp.name)/'repo';self.root.mkdir()
        (self.root/'shadow').mkdir();(self.root/'shadow/example.py').write_text('answer = 1\n')
        self.responses=[];self.prompts=[]
        async def generate(settings,messages,tokens):
            self.prompts.append(messages)
            return self.responses.pop(0)
        self.studio=DevelopmentStudio(self.root,Path(self.tmp.name)/'jobs',generate)

    async def asyncTearDown(self):
        await self.studio.close();self.tmp.cleanup()

    def plan(self, replacement='2'):
        return {'title':'Improve example','summary':'Change a value','findings':[], 'verification':['Run project tests'],
                'changes':[{'path':'shadow/example.py','find':'answer = 1','replace':'answer = '+replacement}]}

    async def build(self):
        self.responses=[{'files':['shadow/example.py']},self.plan(),self.plan()]
        job=await self.studio.start(SETTINGS,'Improve the example module','build')
        await self.studio.task
        return self.studio.get(job['id'])

    async def test_build_is_isolated_and_feedback_is_used(self):
        job=await self.build()
        self.assertEqual(job['state'],'ready')
        self.assertEqual((self.root/'shadow/example.py').read_text(),'answer = 1\n')
        self.assertEqual((self.studio.state_dir/job['id']/'candidate/shadow/example.py').read_text(),'answer = 2\n')
        self.studio.feedback(job['id'],'accepted','Keep changes small')
        await self.build()
        prompt=json.loads(self.prompts[-1][1]['content'])
        self.assertEqual(prompt['previous_owner_feedback'][0]['feedback']['note'],'Keep changes small')
        self.assertNotIn('content',self.studio.public(job)['files'][0])

    async def test_revision_after_validation_failure(self):
        self.responses=[{'files':['shadow/example.py']},self.plan('('),self.plan('('),self.plan('3')]
        job=await self.studio.start(SETTINGS,'Improve the example module','build');await self.studio.task
        result=self.studio.get(job['id'])
        self.assertEqual(result['state'],'ready')
        self.assertEqual(result['files'][0]['content'],'answer = 3\n')
        self.assertEqual(len(self.prompts),4)

    async def test_audit_cannot_generate_code(self):
        self.responses=[{'files':['shadow/example.py']},self.plan(),self.plan()]
        job=await self.studio.start(SETTINGS,'Audit the example module','audit');await self.studio.task
        self.assertEqual(self.studio.get(job['id'])['state'],'failed')
        self.assertFalse((self.studio.state_dir/job['id']/'candidate').exists())

    async def test_double_start_and_immediate_cancel(self):
        job=await self.studio.start(SETTINGS,'Improve the example module','build')
        with self.assertRaises(DevelopmentError):
            await self.studio.start(SETTINGS,'Another improvement task','build')
        await self.studio.cancel(job['id'])
        self.assertEqual(self.studio.get(job['id'])['state'],'cancelled')

    async def test_history_recovers_and_delete_is_confined(self):
        job=await self.build();self.studio.event(job,'building','Simulated restart')
        resumed=DevelopmentStudio(self.root,self.studio.state_dir)
        self.assertEqual(resumed.get(job['id'])['state'],'ready')
        with self.assertRaises(DevelopmentError):
            resumed.delete('../repo')
        resumed.delete(job['id'])
        self.assertTrue((self.root/'shadow/example.py').exists())

    async def test_runtime_context_is_allowlisted(self):
        self.responses=[{'files':['shadow/example.py']},{'title':'Audit','summary':'Done','findings':[], 'changes':[]}]
        job=await self.studio.start(SETTINGS,'Audit the example module','audit',{'connected':True,'account':'private','token':'secret','reply_count':12})
        await self.studio.task
        self.assertEqual(json.loads(self.prompts[0][-1]['content'])['runtime'],{'connected':True,'reply_count':12})

    async def test_security_audit_receives_installed_agent_and_adapter(self):
        relative = 'shadow/skills/community/voltagent/categories/04-quality-security/security-auditor.md'
        adapter = 'shadow/skills/community/voltagent/LOCAL_ADAPTER.md'
        project = Path(__file__).resolve().parents[1]
        for name in (relative, adapter):
            destination = self.root/name
            destination.parent.mkdir(parents=True, exist_ok=True)
            destination.write_bytes((project/name).read_bytes())
        self.responses = [{'files': ['shadow/example.py']}, {'title': 'Audit', 'summary': 'Done', 'findings': [], 'changes': []}]
        job = await self.studio.start(SETTINGS, 'Review account isolation', 'audit', task_type='security_audit')
        await self.studio.task
        request = json.loads(self.prompts[0][-1]['content'])
        self.assertIn(relative, request['required_skill_references'])
        self.assertIn(adapter, request['required_skill_references'])
        self.assertEqual(self.studio.get(job['id'])['state'], 'ready')

    async def test_stale_github_source_prevents_any_write(self):
        job=await self.build();writes=[]
        def handle(request):
            if request.method!='GET':writes.append(request.url.path)
            if '/git/ref/' in request.url.path:return httpx.Response(200,json={'object':{'sha':'head'}})
            return httpx.Response(200,json={'type':'file','encoding':'base64','content':base64.b64encode(b'answer = 99\n').decode()})
        real_client=httpx.AsyncClient
        with mock.patch.dict(os.environ,{'SHADOW_DEV_GITHUB_TOKEN':'test'}), mock.patch('shadow.development.httpx.AsyncClient',side_effect=lambda **kw:real_client(transport=httpx.MockTransport(handle),**kw)):
            with self.assertRaisesRegex(DevelopmentError,'Source changed'):
                await self.studio.publish(job['id'])
        self.assertEqual(writes,[]);self.assertEqual(job['state'],'ready')

    async def test_draft_publication_resumes_without_duplicate_branch(self):
        job=await self.build();writes=[];branch_created=False;fail_pr=True
        def handle(request):
            nonlocal branch_created,fail_pr
            path=request.url.path;body=json.loads(request.content) if request.content else {}
            if request.method=='POST':writes.append((path,body))
            if '/git/ref/heads/main' in path:return httpx.Response(200,json={'object':{'sha':'head'}})
            if '/contents/' in path:return httpx.Response(200,json={'type':'file','encoding':'base64','content':base64.b64encode(b'answer = 1\n').decode()})
            if '/git/commits/head' in path:return httpx.Response(200,json={'tree':{'sha':'tree'}})
            if path.endswith('/git/trees'):return httpx.Response(201,json={'sha':'newtree'})
            if path.endswith('/git/commits'):return httpx.Response(201,json={'sha':'newcommit'})
            if '/git/ref/heads/shadow' in path:return httpx.Response(200,json={'object':{'sha':'newcommit'}}) if branch_created else httpx.Response(404,json={})
            if path.endswith('/git/refs'):
                branch_created=True;return httpx.Response(201,json={})
            if path.endswith('/pulls') and request.method=='GET':return httpx.Response(200,json=[])
            if path.endswith('/pulls'):
                if fail_pr:fail_pr=False;return httpx.Response(503,json={})
                return httpx.Response(201,json={'html_url':'https://github.com/owner/repo/pull/1'})
            raise AssertionError(path)
        real_client=httpx.AsyncClient
        with mock.patch.dict(os.environ,{'SHADOW_DEV_GITHUB_TOKEN':'test'}), mock.patch('shadow.development.httpx.AsyncClient',side_effect=lambda **kw:real_client(transport=httpx.MockTransport(handle),**kw)):
            with self.assertRaises(DevelopmentError):await self.studio.publish(job['id'])
            result=await self.studio.publish(job['id'])
            await self.studio.publish(job['id'])
        self.assertEqual(result['state'],'published')
        self.assertEqual(sum(path.endswith('/git/refs') for path,_ in writes),1)
        self.assertTrue(all(body['draft'] for path,body in writes if path.endswith('/pulls')))
        self.assertTrue(all(body['ref'].startswith('refs/heads/shadow/development-') for path,body in writes if path.endswith('/git/refs')))
        self.assertFalse(any('merge' in path for path,_ in writes))


class ApiAuthTests(unittest.IsolatedAsyncioTestCase):
    async def test_all_development_data_routes_require_auth(self):
        from shadow.app import app
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app),base_url='http://test') as client:
            for method,path,body in [('GET','',None),('POST','',{'objective':'Improve the UI','mode':'build'}),('GET','/bad',None),
                ('GET','/bad/patch',None),('POST','/bad/cancel',{}),('POST','/bad/publish',{}),
                ('POST','/bad/feedback',{'decision':'accepted','note':''}),('DELETE','/bad',None)]:
                response=await client.request(method,'/dashboard/api/development'+path,json=body)
                self.assertEqual(response.status_code,401,(method,path,response.text))

    async def test_script_served_without_secrets(self):
        from shadow.app import app
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app),base_url='http://test') as client:
            response=await client.get('/dashboard/development.js')
            self.assertEqual(response.status_code,200)
            self.assertIn('text/javascript',response.headers['content-type'])
            self.assertIn('ShadowDevelopment',response.text)

    async def test_authenticated_build_download_feedback_and_delete(self):
        import importlib
        module=importlib.import_module('shadow.app')
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp)/'repo';(root/'shadow').mkdir(parents=True)
            (root/'shadow/example.py').write_text('answer = 1\n')
            responses=[{'files':['shadow/example.py']},{'title':'Change','summary':'Updated',
                'changes':[{'path':'shadow/example.py','find':'1','replace':'2'}]}]
            responses.append(responses[-1].copy())  # Independent review returns the complete proposal.
            async def generate(*args):return responses.pop(0)
            studio=DevelopmentStudio(root,Path(tmp)/'jobs',generate)
            with mock.patch.object(module,'development',studio), mock.patch.object(module,'settings',SimpleNamespace(setup_token='test',admin_token='')), mock.patch.object(module.agent,'settings',SETTINGS), mock.patch.object(module.agent,'status',return_value={'connected':False}):
                async with httpx.AsyncClient(transport=httpx.ASGITransport(app=module.app),base_url='http://test',headers={'Authorization':'Bearer test'}) as client:
                    bad=await client.post('/dashboard/api/development',json={'objective':'Long enough task','mode':'deploy'})
                    self.assertEqual(bad.status_code,422)
                    response=await client.post('/dashboard/api/development',json={'objective':'Improve the example','mode':'build'})
                    self.assertEqual(response.status_code,202,response.text)
                    job_id=response.json()['id'];await studio.task
                    detail=await client.get('/dashboard/api/development/'+job_id)
                    self.assertEqual(detail.json()['state'],'ready')
                    patch=await client.get('/dashboard/api/development/'+job_id+'/patch')
                    self.assertEqual(patch.status_code,200)
                    self.assertIn('+answer = 2',patch.text)
                    feedback=await client.post('/dashboard/api/development/'+job_id+'/feedback',json={'decision':'rejected','note':'Try a better change'})
                    self.assertEqual(feedback.status_code,200)
                    publish=await client.post('/dashboard/api/development/'+job_id+'/publish')
                    self.assertEqual(publish.status_code,400)
                    removed=await client.delete('/dashboard/api/development/'+job_id)
                    self.assertEqual(removed.status_code,200)
                    self.assertEqual((await client.get('/dashboard/api/development')).json()['jobs'],[])

if __name__=='__main__':unittest.main()
