'use strict';
const test=require('node:test');
const assert=require('node:assert/strict');
const {eligible,safeFiles,healthy,waitHealthy,ciProof,run}=require('./auto-deploy.cjs');
const id='a'.repeat(32),base='base',head='candidate',merged='merged',restored='restored';
const pr=()=>({number:7,state:'open',draft:false,body:'<!-- shadow-auto-deploy:v1 job='+id+' -->',labels:[{name:'shadow-auto-deploy'}],head:{ref:'shadow/development-'+id,sha:head,repo:{full_name:'owner/shadow'}},base:{ref:'main'}});
const health=sha=>({revision:sha,ok:true,connected:true,guardian_running:true,guardian_heartbeat_fresh:true,accounts_configured:2,accounts_connected:2});
function fixture(options={}){
 const calls=[],checks=[];let current=base,reads=0,healthReads=0;
 const candidate=pr();
 const github={paginate:async()=>options.files||[{filename:'shadow/workspace.js'}],rest:{
  repos:{listPullRequestsAssociatedWithCommit:async()=>({data:[{number:7}]})},
  pulls:{listFiles:()=>{},get:async()=>{reads++;const copy=structuredClone(candidate);if(options.changedHead&&reads>1)copy.head.sha='new-head';return {data:copy};},
   merge:async args=>{calls.push(['merge',args]);current=merged;return {data:{merged:true,sha:merged}};}},
  actions:{listWorkflowRuns:async()=>({data:{workflow_runs:[{head_sha:options.ciSha||head,status:'completed',conclusion:options.ciConclusion||'success',event:'pull_request',name:'Office and math tests',path:'.github/workflows/office-tests.yml',head_repository:{full_name:'owner/shadow'}}]}})},
  checks:{create:async args=>{checks.push(args);return {data:{id:17}};},update:async args=>{checks.push(args);return {data:{}};}},
  git:{getRef:async()=>({data:{object:{sha:options.mainAdvanced&&current===merged?'new-owner-change':current}}}),
   getCommit:async({commit_sha})=>({data:{parents:[{sha:base}],tree:{sha:commit_sha===base?'previous-tree':'new-tree'}}}),
   createCommit:async args=>{calls.push(['revert',args]);return {data:{sha:restored}};},
   updateRef:async args=>{calls.push(['updateRef',args]);current=args.sha;return {data:{}};}}
 }};
 const context={repo:{owner:'owner',repo:'shadow'},payload:{repository:{default_branch:'main'},pull_request:{number:7}},runId:9};
 const core={info:()=>{},setFailed:message=>calls.push(['failure',message])};
 const fetchHealth=async()=>{healthReads++;if(healthReads===1)return options.baselineUnavailable?null:health(base);if(options.badRelease&&current===merged)return health(base);return health(current);};
 return {github,context,core,calls,checks,options:{fetchHealth,delay:async()=>{},ciAttempts:1,healthAttempts:1,rollbackAttempts:1}};
}
test('only owner project branches with explicit release markers are eligible',()=>{
 assert.equal(eligible(pr(),'owner/shadow','main'),true);
 for(const mutate of [p=>p.draft=true,p=>p.head.repo.full_name='fork/shadow',p=>p.labels=[],p=>p.body='ordinary request',p=>p.head.ref='other',p=>p.base.ref='release']){
  const p=pr();mutate(p);assert.equal(eligible(p,'owner/shadow','main'),false);
 }
});
test('release gates and renames cannot be changed automatically',()=>{
 assert.equal(safeFiles([{filename:'shadow/workspace.js'}]),true);
 for(const path of ['.github/workflows/office-tests.yml','scripts/auto-deploy.cjs','scripts/auto-deploy.test.cjs','shadow/release_policy.py']){
  assert.equal(safeFiles([{filename:path}]),false);assert.equal(safeFiles([{filename:'shadow/new.js',previous_filename:path}]),false);
 }
});
test('health checks require exact revision and every previously connected worker',async()=>{
 assert.equal(healthy(health(merged),merged,health(base)),true);
 assert.equal(healthy(health(base),merged,health(base)),false);
 assert.equal(healthy({...health(merged),accounts_connected:1},merged,health(base)),false);
 assert.equal(healthy({...health(merged),guardian_heartbeat_fresh:false},merged),false);
 assert.equal(await waitHealthy(merged,health(base),{fetchHealth:async()=>health(base),attempts:2,delay:async()=>{}}),false);
});
test('successful release merges the tested head and verifies Render',async()=>{
 const f=fixture();const result=await run(f,f.options);
 assert.equal(result[0].state,'deployed');assert.equal(f.calls.find(c=>c[0]==='merge')[1].sha,head);
 assert.equal(f.checks.at(-1).conclusion,'success');assert.match(f.checks.at(-1).output.summary,/SHADOW_DEPLOY_STATE=deployed/);
});
test('failed or missing exact-revision CI never merges',async()=>{
 for(const options of [{ciConclusion:'failure'},{ciSha:'old-success'}]){
  const f=fixture(options);assert.equal((await run(f,f.options))[0].state,'blocked');assert.equal(f.calls.some(c=>c[0]==='merge'),false);
 }
});
test('protected edits and changed PR revisions stop before merge',async()=>{
 for(const options of [{files:[{filename:'.github/workflows/office-tests.yml'}]},{changedHead:true},{baselineUnavailable:true}]){
  const f=fixture(options);assert.equal((await run(f,f.options))[0].state,'blocked');assert.equal(f.calls.some(c=>c[0]==='merge'),false);
 }
});
test('unhealthy deployed revision restores prior tree with a forward commit',async()=>{
 const f=fixture({badRelease:true});assert.equal((await run(f,f.options))[0].state,'rolled_back');
 const revert=f.calls.find(c=>c[0]==='revert')[1];assert.equal(revert.tree,'previous-tree');assert.deepEqual(revert.parents,[merged]);
 assert.equal(f.calls.find(c=>c[0]==='updateRef')[1].force,false);assert.equal(f.checks.at(-1).conclusion,'failure');
});
test('rollback preserves changes pushed after the release',async()=>{
 const f=fixture({badRelease:true,mainAdvanced:true});assert.equal((await run(f,f.options))[0].state,'failed');
 assert.equal(f.calls.some(c=>c[0]==='updateRef'||c[0]==='revert'),false);
});
test('completed failed rerun wins over an older green run',async()=>{
 const f=fixture();f.github.rest.actions.listWorkflowRuns=async()=>({data:{workflow_runs:[
  {head_sha:head,status:'completed',conclusion:'failure',event:'pull_request',name:'Office and math tests'},
  {head_sha:head,status:'completed',conclusion:'success',event:'pull_request',name:'Office and math tests'}]}});
 await assert.rejects(ciProof(f.github,f.context.repo,pr(),{attempts:1}),/did not pass/);
});
test('workflow-run completion resolves associated PRs and skips unmarked PRs',async()=>{
 const f=fixture();delete f.context.payload.pull_request;f.context.payload.workflow_run={event:'pull_request',head_sha:head};
 f.github.rest.pulls.get=async()=>({data:{...pr(),labels:[]}});assert.deepEqual(await run(f,f.options),[]);assert.equal(f.checks.length,0);
});
