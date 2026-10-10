/* Owner-facing development workspace. Model output is rendered only as text. */
(() => {
  'use strict';
  const el = id => document.getElementById(id);
  const root = '/dashboard/api/development';
  const states = {queued:'Navbatda',inspecting:'Manbalarni o‘qiyapti',building:'Tayyorlayapti',validating:'Tekshiryapti',ready:'Ko‘rib chiqish',failed:'Xatolik',cancelled:'To‘xtatildi',interrupted:'Uzildi',publishing:'PR yaratilmoqda',published:'Draft PR tayyor'};
  const releaseLabels={building:'Kod tayyorlanmoqda',waiting_ci:'GitHub testlari kutilmoqda',deploying:'Render deploy tekshirilmoqda',deployed:'Jonli versiya tekshirildi',rolled_back:'Oldingi kodga qaytarildi',blocked:'Avto deploy toxtadi',failed:'Deploy xatoligi',preview:'Faqat korib chiqish'};
  const stage=job=>job.auto_deploy?(releaseLabels[job.auto_deploy_state]||states[job.state]||job.state):(states[job.state]||job.state);
  const taskTypes = Object.create(null);
  let taskCatalog = [];
  let taskTypesLoaded = false;
  const running = new Set(['queued','inspecting','building','validating','publishing']);
  let selected = null, detail = null, status = null, refreshing = false, actionBusy = false, feedbackJob = null;
  let guardian = null, guardianBusy = false;
  const text = (id, value) => { el(id).textContent = value || ''; };
  function notice(message, error=false) {
    const box=el('devNotice'); box.hidden=!message; box.textContent=message||''; box.classList.toggle('is-error',error);
  }
  function node(tag, content, className) {
    const element=document.createElement(tag); element.textContent=content||''; if(className)element.className=className; return element;
  }
  function guardianTime(value) {
    return value ? new Date(value*1000).toLocaleString('uz-UZ',{month:'2-digit',day:'2-digit',hour:'2-digit',minute:'2-digit',second:'2-digit'}) : '—';
  }
  function renderGuardian(value) {
    if(!value)return;
    guardian=value;
    const stale=!value.heartbeat_at||Date.now()/1000-value.heartbeat_at>90;
    const state=!value.running?'stopped':!value.enabled?'paused':stale?'stale':value.checking?'checking':'active';
    const labels={stopped:'Server agenti ishlamayapti',paused:'To‘xtatilgan',stale:'Holat eskirgan',checking:'Tekshirilmoqda',active:'Agent faol'};
    el('guardianState').dataset.state=state;text('guardianState',labels[state]);
    text('guardianLast',guardianTime(value.last_check_at));
    text('guardianNext',value.checking?'Hozir tekshirilmoqda':guardianTime(value.next_check_at));
    text('guardianRepairs',String(value.repairs||0));
    text('guardianAi',value.ai_review?(states[value.ai_job_state]||value.ai_note):'O‘chirilgan');
    el('guardianAi').title=value.ai_note+(value.next_ai_at?' · Keyingi audit: '+guardianTime(value.next_ai_at):'');
    text('guardianToggle',value.enabled?'Agentni to‘xtatish':'Agentni yoqish');
    el('guardianToggle').setAttribute('aria-pressed',String(value.enabled));
    for(const id of ['guardianToggle','guardianInterval','guardianRepair','guardianAiEnabled'])el(id).disabled=guardianBusy;
    el('guardianCheck').disabled=guardianBusy||!value.enabled||!value.running||value.checking;
    if(!guardianBusy){
      if(document.activeElement!==el('guardianInterval'))el('guardianInterval').value=String(value.interval_seconds);
      el('guardianRepair').checked=!!value.auto_repair;el('guardianAiEnabled').checked=!!value.ai_review;
    }
    const checks=value.checks||[];
    el('guardianChecks').replaceChildren(...(checks.length?checks.map(check=>{
      const row=node('article','','guardian-check');row.dataset.state=check.state;
      const mark=node('span',({ok:'✓',warning:'!',error:'×',idle:'−'})[check.state]||'·','guardian-check-mark');
      mark.setAttribute('aria-label',({ok:'Yaxshi',warning:'E’tibor kerak',error:'Xato',idle:'Faol emas'})[check.state]||'Holat');
      const copy=node('div','');copy.append(node('strong',check.title),node('small',check.detail));row.append(mark,copy);return row;
    }):[node('p','Agentning birinchi tekshiruvi kutilmoqda.')]));
    el('guardianEvents').replaceChildren(...((value.events||[]).slice(0,6).map(event=>{
      const row=node('li','');row.dataset.kind=event.kind;row.append(node('time',guardianTime(event.time)),node('span',event.message));return row;
    })));
    if(!value.events?.length)el('guardianEvents').append(node('li','Hali tekshiruv yo‘q.'));
    el('guardianReview').hidden=!value.last_ai_job;el('guardianReview').disabled=guardianBusy;
    if(!value.storage_ok)text('guardianNotice','Holat diskka saqlanmadi. Server diskini tekshiring; agent hozirgi jarayonda ishlaydi.');
  }
  async function guardianAction(path, method, body) {
    if(guardianBusy)return;guardianBusy=true;renderGuardian(guardian);text('guardianNotice','Saqlanmoqda…');
    try {
      guardian=await api(root+'/guardian'+path,json(method,body));
      text('guardianNotice',path? 'Tekshiruv navbatga qo‘yildi. Natija avtomatik yangilanadi.':'Agent sozlamalari saqlandi.');
    }catch(error){text('guardianNotice',errorMessage(error));}
    finally{guardianBusy=false;renderGuardian(guardian);}
  }
  function updateTaskHint() {
    const selectedOption=el('devMode').selectedOptions[0];
    const item=taskCatalog.find(item=>item.id===selectedOption?.value);
    text('devTypeHint',(item?.description||'Vazifa turi tanlang.')+(item?.guide?' · '+item.guide:''));
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
    ['devGitHubSave','devAutoRelease','devPublish','devCancel','devDownload','devAccept','devReject','devDelete'].forEach(id=>el(id).disabled=actionBusy);
    if(!status?.github_ready)el('devPublish').disabled=true;el('devAutoRelease').disabled=actionBusy||!status?.auto_deploy?.ready;
  }
  function showJobs(jobs) {
    const list=el('devJobs');list.replaceChildren();
    if(!jobs.length)list.append(node('p','Hali vazifa yo‘q.','dev-empty'));
    jobs.forEach(job=>{
      const button=node('button','','dev-job'+(selected===job.id?' selected':''));button.type='button';button.setAttribute('aria-pressed',String(selected===job.id));
      button.append(node('strong',job.title||job.objective),node('small',(taskTypes[job.task_type]||'Vazifa')+' · '+stage(job)+' · '+new Date(job.created_at).toLocaleString('uz-UZ',{dateStyle:'short',timeStyle:'short'})));
      button.addEventListener('click',()=>{selected=job.id;loadDetail().catch(error=>notice(errorMessage(error),true));showJobs(jobs);});list.append(button);
    });
  }
  function render(job) {
    detail=job;el('devEmpty').hidden=true;el('devDetail').hidden=false;
    text('devJobState',stage(job)+(taskTypes[job.task_type]?' · '+taskTypes[job.task_type]:''));text('devResultTitle',job.title||job.objective);text('devResultSummary',job.error||job.summary||'Agent vazifani bajarishni boshladi.');
    el('devJobState').dataset.state=job.auto_deploy?job.auto_deploy_state:job.state;
    el('devReleaseStatus').hidden=!job.auto_deploy;text('devReleaseStatus',(releaseLabels[job.auto_deploy_state]||'')+' · '+(job.release_note||''));
    el('devAutoRelease').hidden=job.state!=='ready'||!job.patch;

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
      status=await api(root);if(!guardianBusy)renderGuardian(status.guardian);renderTaskTypes(status.task_types);text('devAiStatus',status.ai_ready?'AI sozlangan':'AI sozlash kerak');text('devGithubStatus',status.github_ready?'GitHub sozlangan':'GitHub: patch yuklash rejimi');
      el('devAiStatus').classList.toggle('ready',status.ai_ready);el('devGithubStatus').classList.toggle('ready',status.github_ready);text('devReleaseReady',status.auto_deploy?.note||'GitHub ulanishi kerak.');
      if(!status.jobs.some(j=>j.id===selected))selected=status.jobs[0]?.id||null;
      showJobs(status.jobs);controls();await loadDetail();
    }catch(error){notice(errorMessage(error),true);text('guardianState','Server bilan aloqa yo‘q');el('guardianState').dataset.state='stale';}finally{refreshing=false;}
  }
  async function action(work, success) {
    if(actionBusy)return;actionBusy=true;controls();notice('');
    try{await work();if(success)notice(success);}catch(error){notice(errorMessage(error),true);}finally{actionBusy=false;await refresh();controls();}
  }
  el('devForm').addEventListener('submit',event=>{event.preventDefault();action(async()=>{const type=el('devMode').value,mode=el('devMode').selectedOptions[0]?.dataset.mode||'audit';const job=await api(root,json('POST',{objective:el('devObjective').value.trim(),mode,task_type:type,auto_deploy:mode==='build'&&el('devAutoDeploy').checked}));selected=job.id;},'Vazifa boshlandi. Natija shu ish maydonida ko‘rinadi.');});
  el('devGitHubForm').addEventListener('submit',event=>{event.preventDefault();action(async()=>{const result=await api(root+'/github',json('POST',{api_key:el('devGitHubKey').value.trim()}));el('devGitHubKey').value='';notice(result.persisted?'GitHub ulanishi saqlandi. Avto deploy tayyor.':'GitHub ulandi, lekin doimiy saqlanmadi. Render Environment da SHADOW_DEV_GITHUB_TOKEN ni sozlang.',!result.persisted);});});
  el('devAutoRelease').addEventListener('click',()=>action(async()=>{await api(root+'/'+detail.id+'/auto-deploy',json('POST'));},'Test va avtomatik deploy navbatga qoyildi.'));
  el('devMode').addEventListener('change',updateTaskHint);
  el('guardianToggle').addEventListener('click',()=>guardianAction('','PATCH',{enabled:!guardian?.enabled}));
  el('guardianCheck').addEventListener('click',()=>guardianAction('/check','POST',{}));
  el('guardianInterval').addEventListener('change',event=>guardianAction('','PATCH',{interval_seconds:Number(event.target.value)}));
  el('guardianRepair').addEventListener('change',event=>guardianAction('','PATCH',{auto_repair:event.target.checked}));
  el('guardianAiEnabled').addEventListener('change',event=>guardianAction('','PATCH',{ai_review:event.target.checked}));
  el('guardianReview').addEventListener('click',()=>{if(!guardian?.last_ai_job)return;selected=guardian.last_ai_job;action(async()=>{await loadDetail();el('devDetail').scrollIntoView({behavior:'smooth',block:'start'});});});
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
