/* Owner-facing development workspace. Model output is rendered only as text. */
(() => {
  'use strict';
  const el = id => document.getElementById(id);
  const root = '/dashboard/api/development';
  const states = {queued:'Navbatda',inspecting:'Manbalarni o‘qiyapti',building:'Tayyorlayapti',validating:'Tekshiryapti',ready:'Ko‘rib chiqish',failed:'Xatolik',cancelled:'To‘xtatildi',interrupted:'Uzildi',publishing:'PR yaratilmoqda',published:'Draft PR tayyor'};
  const taskTypes = Object.create(null);
  let taskCatalog = [];
  let taskTypesLoaded = false;
  const running = new Set(['queued','inspecting','building','validating','publishing']);
  let selected = null, detail = null, status = null, refreshing = false, actionBusy = false, feedbackJob = null;
  const text = (id, value) => { el(id).textContent = value || ''; };
  function notice(message, error=false) {
    const box=el('devNotice'); box.hidden=!message; box.textContent=message||''; box.classList.toggle('is-error',error);
  }
  function node(tag, content, className) {
    const element=document.createElement(tag); element.textContent=content||''; if(className)element.className=className; return element;
  }
  function updateTaskHint() {
    const selectedOption=el('devMode').selectedOptions[0];
    text('devTypeHint',taskCatalog.find(item=>item.id===selectedOption?.value)?.description||'Vazifa turi tanlang.');
  }
  function renderTaskTypes(types) {
    if(!Array.isArray(types)||!types.length)return;
    const previous=taskTypesLoaded?el('devMode').value:'solve', groups=new Map();
    taskCatalog=types.filter(item=>item&&typeof item.id==='string'&&typeof item.label==='string');
    Object.keys(taskTypes).forEach(key=>delete taskTypes[key]);
    taskCatalog.forEach(item=>{taskTypes[item.id]=item.label;const category=item.category||'Boshqa';if(!groups.has(category))groups.set(category,[]);groups.get(category).push(item);});
    const select=el('devMode');select.replaceChildren();
    groups.forEach((items,category)=>{
      const group=node('optgroup','');group.label=category;
      items.forEach(item=>{const option=node('option',item.label);option.value=item.id;option.dataset.mode=item.mode;option.title=item.description||'';group.append(option);});
      select.append(group);
    });
    select.value=taskCatalog.some(item=>item.id===previous)?previous:(taskCatalog.find(item=>item.id==='solve')?.id||taskCatalog[0]?.id||'');
    taskTypesLoaded=true;
    updateTaskHint();
  }
  function json(method, body) { return {method,headers:{'content-type':'application/json'},...(body===undefined?{}:{body:JSON.stringify(body)})}; }
  function errorMessage(error) {return error.message==='auth'?'Panel sessiyasi tugadi. Sahifani yangilab, qayta kiring.':error.message||'So‘rov bajarilmadi.';}
  function controls() {
    el('devStart').disabled=actionBusy||!status?.ai_ready||status?.busy;
    el('devStart').textContent=status?.busy?'Agent vazifani bajaryapti…':'Agentni ishga tushirish';
    ['devPublish','devCancel','devDownload','devAccept','devReject','devDelete'].forEach(id=>el(id).disabled=actionBusy);
    if(!status?.github_ready)el('devPublish').disabled=true;
  }
  function showJobs(jobs) {
    const list=el('devJobs');list.replaceChildren();
    if(!jobs.length)list.append(node('p','Hali vazifa yo‘q.','dev-empty'));
    jobs.forEach(job=>{
      const button=node('button','','dev-job'+(selected===job.id?' selected':''));button.type='button';button.setAttribute('aria-pressed',String(selected===job.id));
      button.append(node('strong',job.title||job.objective),node('small',(taskTypes[job.task_type]||'Vazifa')+' · '+(states[job.state]||job.state)+' · '+new Date(job.created_at).toLocaleString('uz-UZ',{dateStyle:'short',timeStyle:'short'})));
      button.addEventListener('click',()=>{selected=job.id;loadDetail().catch(error=>notice(errorMessage(error),true));showJobs(jobs);});list.append(button);
    });
  }
  function render(job) {
    detail=job;el('devEmpty').hidden=true;el('devDetail').hidden=false;
    text('devJobState',(states[job.state]||job.state)+(taskTypes[job.task_type]?' · '+taskTypes[job.task_type]:''));text('devResultTitle',job.title||job.objective);text('devResultSummary',job.error||job.summary||'Agent vazifani bajarishni boshladi.');
    el('devJobState').dataset.state=job.state;
    el('devEvents').replaceChildren(...(job.events||[]).map(event=>node('li',new Date(event.time).toLocaleTimeString('uz-UZ',{hour:'2-digit',minute:'2-digit'})+' · '+event.message)));
    const findings=el('devFindings');findings.replaceChildren();
    (job.findings||[]).forEach(finding=>{const row=node('article','','dev-finding');row.append(node('strong',finding.title),node('p',finding.detail));findings.append(row);});
    const files=el('devFiles');files.replaceChildren();
    (job.files||[]).forEach(file=>{const row=node('article','','dev-file');row.append(node('strong',file.path),node('span','+'+file.added+' / −'+file.removed));file.checks.forEach(check=>row.append(node('small',check)));files.append(row);});
    const patch=job.patch||'';el('devDiffSection').hidden=!patch;text('devDiff',patch.length>300000?patch.slice(0,300000)+'\n… To‘liq patchni yuklab oling.':patch);
    el('devChecksSection').hidden=!(job.verification||[]).length;el('devChecks').replaceChildren(...(job.verification||[]).map(check=>node('li',check)));
    el('devDownload').hidden=!patch;el('devPublish').hidden=job.state!=='ready'||!patch||job.feedback?.decision==='rejected';el('devPublish').title=status?.github_ready?'Alohida branch va draft PR yarating':'Avval serverda SHADOW_DEV_GITHUB_TOKEN sozlang';
    el('devCancel').hidden=!['queued','inspecting','building','validating'].includes(job.state);
    const link=el('devPrLink');link.hidden=true;link.removeAttribute('href');
    if(job.pr_url){try{const url=new URL(job.pr_url);if(url.protocol==='https:'&&url.hostname==='github.com'){link.href=url.href;link.hidden=false;}}catch(_){}}
    el('devFeedbackSection').hidden=!['ready','published'].includes(job.state);
    if(feedbackJob!==job.id){el('devFeedback').value=job.feedback?.note||'';feedbackJob=job.id;}
    text('devFeedbackStatus',job.feedback?.decision==='accepted'?'Ma’qul deb belgilandi':job.feedback?.decision==='rejected'?'Keyingi vazifa uchun qayd qilindi':'');
    el('devDelete').hidden=running.has(job.state);controls();
  }
  async function loadDetail() {
    const id=selected;if(!id){detail=null;feedbackJob=null;el('devEmpty').hidden=false;el('devDetail').hidden=true;text('devJobState','Tayyor');return;}
    const job=await api(root+'/'+encodeURIComponent(id));if(selected===id)render(job);
  }
  async function refresh() {
    if(refreshing)return;refreshing=true;
    try {
      status=await api(root);renderTaskTypes(status.task_types);text('devAiStatus',status.ai_ready?'AI sozlangan':'AI sozlash kerak');text('devGithubStatus',status.github_ready?'GitHub sozlangan':'GitHub: patch yuklash rejimi');
      el('devAiStatus').classList.toggle('ready',status.ai_ready);el('devGithubStatus').classList.toggle('ready',status.github_ready);
      if(!status.jobs.some(j=>j.id===selected))selected=status.jobs[0]?.id||null;
      showJobs(status.jobs);controls();await loadDetail();
    }catch(error){notice(errorMessage(error),true);}finally{refreshing=false;}
  }
  async function action(work, success) {
    if(actionBusy)return;actionBusy=true;controls();notice('');
    try{await work();if(success)notice(success);}catch(error){notice(errorMessage(error),true);}finally{actionBusy=false;await refresh();controls();}
  }
  el('devForm').addEventListener('submit',event=>{event.preventDefault();action(async()=>{const type=el('devMode').value,mode=el('devMode').selectedOptions[0]?.dataset.mode||'audit';const job=await api(root,json('POST',{objective:el('devObjective').value.trim(),mode,task_type:type}));selected=job.id;},'Vazifa boshlandi. Natija shu ish maydonida ko‘rinadi.');});
  el('devMode').addEventListener('change',updateTaskHint);
  document.querySelectorAll('[data-dev-preset]').forEach(button=>button.addEventListener('click',()=>{el('devObjective').value=button.dataset.devPreset;el('devMode').value=button.dataset.devType;updateTaskHint();el('devObjective').focus();}));
  el('devRefresh').addEventListener('click',()=>{notice('');refresh();});
  el('devCancel').addEventListener('click',()=>{const id=selected;action(()=>api(root+'/'+id+'/cancel',json('POST')),'Vazifa to‘xtatildi.');});
  el('devPublish').addEventListener('click',()=>{const id=selected;action(async()=>{const job=await api(root+'/'+id+'/publish',json('POST'));if(selected===id)render(job);},'Draft PR yaratildi. Havola orqali tekshirib chiqing.');});
  el('devDownload').addEventListener('click',()=>{if(!detail?.patch)return;const url=URL.createObjectURL(new Blob([detail.patch],{type:'text/plain;charset=utf-8'}));const link=document.createElement('a');link.href=url;link.download='shadow-'+detail.id+'.patch';link.click();setTimeout(()=>URL.revokeObjectURL(url),1000);});
  [['devAccept','accepted'],['devReject','rejected']].forEach(([id,decision])=>el(id).addEventListener('click',()=>{const job=selected;action(()=>api(root+'/'+job+'/feedback',json('POST',{decision,note:el('devFeedback').value})),'Fikringiz keyingi vazifalarda hisobga olinadi.');}));
  el('devDelete').addEventListener('click',()=>{const id=selected;if(!id||!confirm('Bu vazifa va uning lokal patchi o‘chirilsinmi? GitHub PR o‘zgarmaydi.'))return;action(async()=>{await api(root+'/'+id,json('DELETE'));if(selected===id)selected=null;},'Vazifa tarixdan o‘chirildi.');});
  setInterval(()=>{if(!document.hidden&&!el('app').hidden&&!el('developmentPanel').hidden)refresh();},5000);
  window.ShadowDevelopment={refresh};
})();
