/* Trusted release controller. Never load or execute code from the candidate PR. */
'use strict';
const LABEL='shadow-auto-deploy';
const CHECK='Shadow automatic deployment';
const HEALTH_URL='https://shadow-telegram-agent.onrender.com/healthz';
const PROTECTED=['.github/','scripts/auto-deploy.cjs','scripts/auto-deploy.test.cjs','shadow/release_policy.py'];
const sleep=ms=>new Promise(resolve=>setTimeout(resolve,ms));
function eligible(pr,repository,base){
 const match=pr.head.ref.match(/^shadow\/development-([a-f0-9]{32})$/);
 return !!(match&&pr.head.repo?.full_name===repository&&pr.base.ref===base&&!pr.draft&&
  pr.labels.some(label=>label.name===LABEL)&&pr.body?.includes('<!-- shadow-auto-deploy:v1 job='+match[1]+' -->'));
}
function safeFiles(files){return files.every(file=>[file.filename,file.previous_filename].filter(Boolean).every(path=>!PROTECTED.some(p=>path.startsWith(p))));}
function healthy(value,sha,baseline={}){
 return value?.revision===sha&&value.ok===true&&value.guardian_running===true&&value.guardian_heartbeat_fresh===true&&
  (!baseline.connected||value.connected===true)&&
  (value.accounts_configured??0)>=(baseline.accounts_configured??0)&&
  (value.accounts_connected??0)>=(baseline.accounts_connected??0);
}
async function live(){
 try{const response=await fetch(HEALTH_URL,{signal:AbortSignal.timeout(12000),redirect:'error',cache:'no-store'});return response.ok?await response.json():null;}catch{return null;}
}
async function waitHealthy(sha,baseline,{fetchHealth=live,delay=sleep,attempts=40}={}){
 for(let i=0;i<attempts;i++){if(healthy(await fetchHealth(),sha,baseline))return true;if(i+1<attempts)await delay(15000);}return false;
}
async function ciProof(github,repo,pr,{delay=sleep,attempts=60}={}){
 for(let i=0;i<attempts;i++){
  const {data}=await github.rest.actions.listWorkflowRuns({...repo,workflow_id:'office-tests.yml',branch:pr.head.ref,event:'pull_request',per_page:30});
  const run=data.workflow_runs.find(run=>run.head_sha===pr.head.sha&&run.event==='pull_request'&&run.name==='Office and math tests'&&
   (!run.head_repository||run.head_repository.full_name===pr.head.repo.full_name)&&(!run.path||run.path==='.github/workflows/office-tests.yml'));
  if(run?.status==='completed'){if(run.conclusion!=='success')throw Error('Required tests did not pass on this exact PR revision.');return run;}
  if(i+1<attempts)await delay(10000);
 }
 throw Error('Required tests are missing or still running. No merge was performed.');
}
async function rollback(github,repo,base,mergedSha,previousSha){
 const head=(await github.rest.git.getRef({...repo,ref:'heads/'+base})).data.object.sha;
 if(head!==mergedSha)throw Error('Main advanced after the failed release; rollback stopped to preserve newer changes.');
 const before=(await github.rest.git.getCommit({...repo,commit_sha:previousSha})).data;
 const revert=(await github.rest.git.createCommit({...repo,message:'Revert unhealthy Shadow automatic deployment '+mergedSha.slice(0,12),tree:before.tree.sha,parents:[mergedSha]})).data;
 await github.rest.git.updateRef({...repo,ref:'heads/'+base,sha:revert.sha,force:false});
 return revert.sha;
}
async function run({github,context,core},options={}){
 const repo=context.repo,repository=repo.owner+'/'+repo.repo,base=context.payload.repository.default_branch;
 const delay=options.delay||sleep,fetchHealth=options.fetchHealth||live;
 let numbers=[];
 if(context.payload.pull_request)numbers=[context.payload.pull_request.number];
 else if(context.payload.inputs?.pr_number)numbers=[Number(context.payload.inputs.pr_number)];
 else if(context.payload.workflow_run?.event==='pull_request'){
  const linked=await github.rest.repos.listPullRequestsAssociatedWithCommit({...repo,commit_sha:context.payload.workflow_run.head_sha,per_page:100});
  numbers=linked.data.map(pr=>pr.number);
 }
 const results=[];
 for(const number of numbers){
  if(!Number.isSafeInteger(number)||number<1)continue;
  let pr=(await github.rest.pulls.get({...repo,pull_number:number})).data;
  if(!eligible(pr,repository,base)||pr.state!=='open'){core.info('Skip PR '+number+': not an open owner automatic-release request.');continue;}
  let checkId,mergedSha,previousSha,baseline={};
  const report=async(state,message,complete=false)=>{
   const output={title:'Shadow: '+state,summary:'SHADOW_DEPLOY_STATE='+state+'\n\n'+message};
   if(checkId)await github.rest.checks.update({...repo,check_run_id:checkId,status:complete?'completed':'in_progress',
    ...(complete?{conclusion:state==='deployed'?'success':'failure',completed_at:new Date().toISOString()}:{}),output});
   core.info(output.summary);
  };
  try{
   const check=await github.rest.checks.create({...repo,name:CHECK,head_sha:pr.head.sha,status:'in_progress',
    details_url:'https://github.com/'+repository+'/actions/runs/'+context.runId});checkId=check.data.id;
   const files=await github.paginate(github.rest.pulls.listFiles,{...repo,pull_number:number,per_page:100});
   if(!safeFiles(files))throw Error('Automatic changes to test/release policy need a reviewed PR.');
   await report('waiting_ci','Waiting for the protected test workflow on '+pr.head.sha+'.');
   const testedSha=pr.head.sha;
   await ciProof(github,repo,pr,{delay,attempts:options.ciAttempts??60});
   pr=(await github.rest.pulls.get({...repo,pull_number:number})).data;
   if(!eligible(pr,repository,base)||pr.state!=='open'||pr.head.sha!==testedSha)throw Error('PR revision or release authorization changed after testing.');
   const source=(await github.rest.git.getCommit({...repo,commit_sha:testedSha})).data;
   previousSha=(await github.rest.git.getRef({...repo,ref:'heads/'+base})).data.object.sha;
   if(source.parents.length!==1||source.parents[0].sha!==previousSha)throw Error('Main changed since this task was built. Rebuild against the current version.');
   baseline=await fetchHealth();
   if(!healthy(baseline,previousSha))throw Error('Current Render revision is unavailable or unhealthy. No merge was performed.');
   await report('deploying','Exact-revision tests passed. Merging; Render deployment and runtime checks follow.');
   const merge=(await github.rest.pulls.merge({...repo,pull_number:number,sha:testedSha,merge_method:'squash'})).data;
   if(!merge.merged)throw Error('GitHub refused the merge; repository protection remains in force.');
   mergedSha=merge.sha;
   const merged=(await github.rest.git.getCommit({...repo,commit_sha:mergedSha})).data;
   const actualParent=merged.parents[0]?.sha;
   if(!actualParent)throw Error('Merged commit has no rollback parent.');
   if(actualParent!==previousSha){previousSha=actualParent;throw Error('Main advanced during merge; restoring the actual prior tree.');}
   if(!await waitHealthy(mergedSha,baseline,{fetchHealth,delay,attempts:options.healthAttempts??40}))throw Error('The new Render revision did not pass live health/account checks.');
   await report('deployed','Tests passed and Render is serving revision '+mergedSha+' with healthy account workers.\nhttps://shadow-telegram-agent.onrender.com/dashboard',true);
   results.push({number,state:'deployed',sha:mergedSha});
  }catch(error){
   let state='blocked',message=error.message;
   if(mergedSha){
    try{
     const sha=await rollback(github,repo,base,mergedSha,previousSha);
     const verified=await waitHealthy(sha,baseline,{fetchHealth,delay,attempts:options.rollbackAttempts??20});
     state=verified?'rolled_back':'failed';message+='\nRollback commit: '+sha+(verified?'\nPrior code is serving and healthy.':'\nRollback queued, but live restoration was not verified. Inspect Render.');
    }catch(rollbackError){state='failed';message+='\n'+rollbackError.message;}
   }
   await report(state,message,true);core.setFailed(message);results.push({number,state,message});
  }
 }
 return results;
}
module.exports={eligible,safeFiles,healthy,waitHealthy,ciProof,rollback,run};
