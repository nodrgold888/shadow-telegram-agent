/* Full FreeLLMAPI catalog. Dynamic text uses DOM APIs, never remote HTML. */
(()=>{
 const KINDS={chat:'Chat',image:'Rasm',audio:'Ovoz',embedding:'Embeddings',transcription:'Transkripsiya',video:'Video'};
 const STATES={ready:'Tayyor',connected:'Ulangan',needsKey:'Kalit kerak',exhausted:'Limit / tanaffus',disabled:'Ochirilgan',not_listed:'Gateway da yoq',unknown:'Tekshirilmagan'};
 const ready=m=>['ready','connected'].includes(m.status);
 const el=(tag,cls,text)=>{const node=document.createElement(tag);if(cls)node.className=cls;if(text!=null)node.textContent=text;return node;};
 const context=n=>!n?'—':n>=1000000?(n/1000000).toFixed(1)+'M':Math.round(n/1000)+'K';
 const icon=kind=>({chat:'M4 5h16v11H8l-4 4V5',image:'M3 3h18v18H3V3m0 14 6-6 4 4 3-3 5 5M16 7h.01',audio:'M9 3h6v11a3 3 0 0 1-6 0V3m-3 9v2a6 6 0 0 0 12 0v-2m-6 8v2',embedding:'M4 4h6v6H4V4m10 0h6v6h-6V4M4 14h6v6H4v-6m10 0h6v6h-6v-6',transcription:'M4 4h16v16H4V4m4 4h8m-8 4h8m-8 4h4',video:'M3 5h12v14H3V5m12 4 6-3v12l-6-3'}[kind]||'M4 4h16v16H4V4');
 function glyph(kind){const span=el('span','catalog-icon');span.setAttribute('aria-hidden','true');span.innerHTML='<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.6"><path d="'+icon(kind)+'"/></svg>';return span;}
 function badge(m){const b=el('span','catalog-state state-'+m.status,STATES[m.status]||STATES.unknown);b.title='Holat model ID boyicha. Provayder yolini gateway tanlaydi; bu javob testi emas.';return b;}
 window.ShadowModelCatalog={mount({root,api,onPrepare,onSelected}){
  const panel=el('article','detail-panel catalog-panel');panel.id='modelCatalogPanel';
  panel.innerHTML=`<header class="catalog-header"><div><span class="catalog-eyebrow">MODEL KATALOGI</span><h2>Barcha AI modellar</h2><p>Chat, rasm, ovoz va boshqa imkoniyatlar. Har bir provayderning limiti alohida.</p></div><div class="catalog-header-actions"><a id="catalogSetup" class="btn" target="_blank" rel="noopener noreferrer" hidden>Gateway kalitlari ↗</a><button class="btn" id="catalogRefresh" type="button">Holatni yangilash ↻</button></div></header><div id="catalogStats" class="catalog-stats"></div><p id="catalogNotice" class="model-note" role="status" aria-live="polite">Katalog yuklanmoqda…</p><section class="catalog-strong-section" aria-labelledby="catalogStrongTitle"><div class="catalog-section-head"><div><span class="catalog-eyebrow">KUCHLI TANLOV</span><h3 id="catalogStrongTitle">Kuchli modellar</h3><p>FreeLLMAPI katalogidagi intelligence reytingi asosida. Kichik raqam yuqori orin.</p></div><button id="catalogAuto" class="btn" type="button">Eng aqlli avto tanlash</button></div><p class="catalog-swipe-hint">Yon tomonga surib boshqa kuchli modellarni koring →</p><div id="catalogStrong" class="catalog-strong"></div></section><section aria-labelledby="catalogAllTitle"><div class="catalog-section-head"><h3 id="catalogAllTitle">Toliq katalog</h3><span id="catalogVersion" class="catalog-version"></span></div><div class="catalog-filters"><label>Qidirish<input id="catalogQuery" type="search" placeholder="Model nomi yoki ID…" autocomplete="off"></label><label>Imkoniyat<select id="catalogKind"><option value="">Barcha turlar</option></select></label><label>Provayder<select id="catalogProvider"><option value="">Barcha provayderlar</option></select></label><label>Holat<select id="catalogState"><option value="">Barcha holatlar</option><option value="ready">Foydalanish mumkin</option><option value="needsKey">Kalit kerak</option><option value="exhausted">Limit / tanaffus</option><option value="unknown">Tekshirilmagan</option></select></label><label>Tartib<select id="catalogSort"><option value="smart">Kuchli modellar avval</option><option value="ready">Tayyor modellar avval</option><option value="context">Katta kontekst avval</option><option value="name">Nom boyicha</option></select></label></div><div id="catalogRows" class="catalog-rows" role="list"></div><div class="catalog-pagination"><span id="catalogCount"></span><div><button id="catalogPrev" class="btn" type="button" aria-label="Oldingi model sahifasi">← Oldingi</button><span id="catalogPage"></span><button id="catalogNext" class="btn" type="button" aria-label="Keyingi model sahifasi">Keyingi →</button></div></div></section><footer class="catalog-footer"><span>Media modellari katalogda korsatiladi; Telegram media javoblari alohida sozlanadi.</span><a href="https://freellmapi.co/models" target="_blank" rel="noopener noreferrer">Manba katalog ↗</a></footer>`;
  root.prepend(panel);
  const $=id=>panel.querySelector('#'+id);
  let data=null,account=null,generation=0,busy=false,selecting=false,page=0;const narrow=matchMedia('(max-width:650px)');let pageSize=narrow.matches?12:30;
  for(const [value,label] of Object.entries(KINDS))$('catalogKind').append(Object.assign(document.createElement('option'),{value,textContent:label}));
  function clear(){data=null;page=0;$('catalogRows').replaceChildren();$('catalogStrong').replaceChildren();$('catalogStats').replaceChildren();$('catalogCount').textContent='';$('catalogAuto').disabled=true;}
  function sorted(rows){const mode=$('catalogSort').value;return [...rows].sort((a,b)=>
   (mode==='ready'?(Number(ready(b))-Number(ready(a))):0)||
   (mode==='context'?((b.context||0)-(a.context||0)):0)||
   (mode==='name'?a.name.localeCompare(b.name):((a.rank||10000)-(b.rank||10000)))||a.name.localeCompare(b.name));}
  function info(m){const chips=el('div','catalog-chips');chips.append(el('span','catalog-chip',KINDS[m.kind]));if(m.tools)chips.append(el('span','catalog-chip','Tools'));if(m.vision)chips.append(el('span','catalog-chip','Vision'));if(m.kind==='chat'&&m.rank)chips.append(el('span','catalog-chip catalog-rank','#'+m.rank));return chips;}
  function actions(m){const box=el('div','catalog-actions');const copy=el('button','btn catalog-copy','ID nusxa');copy.type='button';copy.addEventListener('click',async()=>{try{await navigator.clipboard.writeText(m.id);copy.textContent='Nusxalandi ✓';}catch{$('catalogNotice').textContent='Model ID: '+m.id;}});box.append(copy);
   if(m.kind==='chat'){
    const button=el('button','btn catalog-pick',m.selected?'Tanlangan':data.gateway.saved?'Asosiy qilish':'Tanlash');button.type='button';button.dataset.model=m.id;
    button.disabled=selecting||m.selected||(data.gateway.checked&&!ready(m));
    button.addEventListener('click',()=>{if(data.gateway.saved)select(m.id);else{onPrepare(m.id,data.gateway.base_url);$('catalogNotice').textContent='Model tanlandi. FreeLLMAPI formasida unified API kalitini kiriting va saqlang.';}});box.append(button);
   }return box;
  }
  function render(){if(!data)return;
   const setup=$('catalogSetup');setup.hidden=true;try{const url=new URL('/keys',data.gateway.base_url);if(url.protocol==='https:'){setup.href=url.href;setup.hidden=false;}}catch{}
   $('catalogStats').replaceChildren();for(const [kind,label] of Object.entries(KINDS)){const count=data.counts?.[kind]||0;if(!count)continue;const tile=el('button','catalog-stat');tile.type='button';tile.append(glyph(kind),el('strong','',count),el('span','',label));tile.addEventListener('click',()=>{$('catalogKind').value=kind;page=0;render();});$('catalogStats').append(tile);}
   $('catalogAuto').disabled=selecting||(data.gateway.saved&&!data.models.some(m=>m.kind==='chat'&&ready(m)));
   $('catalogStrong').replaceChildren();const byUid=new Map(data.models.map(m=>[m.uid,m]));
   for(const uid of data.strongest||[]){const m=byUid.get(uid);if(!m)continue;const card=el('article','catalog-strong-card'+(m.selected?' selected':''));const head=el('div','catalog-card-top');head.append(glyph('chat'),badge(m));card.append(head,el('h4','',m.name),el('code','catalog-model-id',m.id),el('p','catalog-provider',m.provider),info(m));const meta=el('p','catalog-meta');meta.textContent=context(m.context)+' kontekst';card.append(meta,actions(m));$('catalogStrong').append(card);}
   const q=$('catalogQuery').value.trim().toLowerCase(),kind=$('catalogKind').value,provider=$('catalogProvider').value,state=$('catalogState').value;
   const filtered=sorted(data.models.filter(m=>(!kind||m.kind===kind)&&(!provider||m.platform===provider)&&(!state||(state==='ready'?ready(m):m.status===state))&&(!q||[m.name,m.id,m.provider].join(' ').toLowerCase().includes(q))));
   page=Math.min(page,Math.max(0,Math.ceil(filtered.length/pageSize)-1));$('catalogRows').replaceChildren();
   for(const m of filtered.slice(page*pageSize,(page+1)*pageSize)){const row=el('article','catalog-row');row.setAttribute('role','listitem');const name=el('div','catalog-row-name');name.append(el('strong','',m.name),el('code','catalog-model-id',m.id),info(m));const detail=el('div','catalog-row-detail');detail.append(el('span','',m.provider),el('small','',m.quota||m.note||'Provayder limiti'));const ctx=el('div','catalog-row-context');ctx.append(el('strong','',context(m.context)),el('small','',m.kind==='embedding'?'Kirish tokenlari':'Kontekst'));row.append(name,detail,ctx,badge(m),actions(m));$('catalogRows').append(row);}
   if(!filtered.length)$('catalogRows').append(el('p','catalog-empty','Bu filtrga mos model topilmadi.'));
   $('catalogCount').textContent=filtered.length+' / '+data.models.length+' ta endpoint';$('catalogPage').textContent=(page+1)+' / '+Math.max(1,Math.ceil(filtered.length/pageSize));$('catalogPrev').disabled=page===0;$('catalogNext').disabled=(page+1)*pageSize>=filtered.length;
  }
  async function load(force=false,credentials=null){if(busy||(!force&&data))return;busy=true;const marker=++generation;$('catalogRefresh').disabled=true;$('catalogNotice').textContent='Katalog va gateway holati yuklanmoqda…';
   try{const result=await api('/dashboard/api/ai-catalog',credentials?{method:'POST',headers:{'content-type':'application/json'},body:JSON.stringify(credentials)}:undefined);if(marker!==generation)return;
    data=result;if(!Array.isArray(data.models))throw new Error('Katalog royxati olinmadi.');
    $('catalogProvider').replaceChildren(Object.assign(document.createElement('option'),{value:'',textContent:'Barcha provayderlar'}));for(const p of data.providers||[])$('catalogProvider').append(Object.assign(document.createElement('option'),{value:p.id,textContent:p.name}));
    $('catalogVersion').textContent='Katalog: '+data.version;$('catalogNotice').textContent=data.gateway.notice;render();
   }catch(error){if(marker===generation){$('catalogNotice').textContent=error.message==='auth'?'Panel sessiyasi tugadi. Qayta kiring.':error.message;if(!data)$('catalogRows').append(el('p','catalog-empty','Katalog yuklanmadi. Holatni yangilash orqali qayta urinib koring.'));}}
   finally{if(marker===generation){busy=false;$('catalogRefresh').disabled=false;}}
  }
  async function select(model){if(selecting)return;selecting=true;const marker=generation;$('catalogNotice').textContent='Model tekshirilmoqda va saqlanmoqda…';render();
   try{const result=await api('/dashboard/api/ai-catalog/select',{method:'POST',headers:{'content-type':'application/json'},body:JSON.stringify({model})});if(marker!==generation)return;await onSelected();await load(true);$('catalogNotice').textContent=result.persisted?'Model saqlandi. auto:smart zaxirasi ham saqlangan.':'Model hozir faol, lekin doimiy saqlanmadi. Render saqlash sozlamalarini tekshiring.';}
   catch(error){if(marker===generation)$('catalogNotice').textContent=error.message==='auth'?'Panel sessiyasi tugadi. Qayta kiring.':error.message;}
   finally{selecting=false;render();}
  }
  narrow.addEventListener('change',()=>{pageSize=narrow.matches?12:30;page=0;render();});
  for(const id of ['catalogQuery','catalogKind','catalogProvider','catalogState','catalogSort'])$(id).addEventListener(id==='catalogQuery'?'input':'change',()=>{page=0;render();});
  $('catalogPrev').addEventListener('click',()=>{page--;render();});$('catalogNext').addEventListener('click',()=>{page++;render();});$('catalogRefresh').addEventListener('click',()=>load(true));
  $('catalogAuto').addEventListener('click',()=>{if(data?.gateway.saved)select('auto:smart');else onPrepare('auto:smart',data.gateway.base_url);});
  return {load,setAccount(id){if(String(id??'')===String(account??''))return;account=id;generation++;busy=false;selecting=false;clear();$('catalogRefresh').disabled=false;$('catalogNotice').textContent='Shu akkauntning gateway holati tekshiriladi.';if(!root.hidden)load();},preview(credentials){generation++;busy=false;return load(true,credentials);}};
 }};
})();
