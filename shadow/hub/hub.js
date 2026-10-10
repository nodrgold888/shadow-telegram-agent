(() => {
  'use strict';
  const $ = id => document.getElementById(id);
  const paths = {
    arrowup:'M7 17 17 7 M7 7h10v10',
    user:'M20 21v-2a4 4 0 0 0-4-4H8a4 4 0 0 0-4 4v2 M12 11a4 4 0 1 0 0-8 4 4 0 0 0 0 8',
    spark:'m12 3 2.7 6.3L21 12l-6.3 2.7L12 21l-2.7-6.3L3 12l6.3-2.7Z',
    play:'m9 5 11 7-11 7Z', grid:'M3 3h7v7H3Z M14 3h7v7h-7Z M3 14h7v7H3Z M14 14h7v7h-7Z',
    clock:'M12 8v4l3 2 M21 12a9 9 0 1 0-18 0 9 9 0 0 0 18 0',
    bookmark:'M6 3h12v18l-6-4-6 4Z', history:'M3 3v5h5 M3.6 8a9 9 0 1 1-.4 8 M12 7v5l4 2',
    settings:'m12 3 3 2 3-.2.8 3L21 10v4l-2.2 2.2-.8 3-3-.2-3 2-3-2-3 .2-.8-3L3 14v-4l2.2-2.2.8-3 3 .2Z M15 12a3 3 0 1 0-6 0 3 3 0 0 0 6 0',
    search:'M10.5 17a6.5 6.5 0 1 0 0-13 6.5 6.5 0 0 0 0 13 M16 16l5 5', plus:'M12 5v14 M5 12h14',
    close:'m6 6 12 12 M18 6 6 18', pip:'M3 4h18v16H3Z M12 12h7v6h-7Z', expand:'M8 3H3v5 M16 3h5v5 M21 16v5h-5 M8 21H3v-5',
    telegram:'m3 11 18-7-4 17-5-5-4 3 1-7 8-5-10 8Z', trash:'M3 6h18 M9 6V3h6v3 M5 6l1 15h12l1-15 M10 10v7 M14 10v7'
  };
  function icon(name) {
    const span = document.createElement('span'); span.className='icon'; span.setAttribute('aria-hidden','true');
    span.innerHTML='<svg viewBox="0 0 24 24"><path d="'+(paths[name]||paths.play)+'"/></svg>'; return span;
  }
  document.querySelectorAll('[data-icon]').forEach(node => node.replaceChildren(icon(node.dataset.icon).firstChild));
  const animePage = document.body.dataset.page === 'anime';
  $('homePage').hidden=animePage; $('animePage').hidden=!animePage;
  document.querySelector('[data-nav="'+(animePage?'anime':'home')+'"]').classList.add('active');
  const video=$('animeVideo'), dialog=$('watchDialog');
  let library=new Map(), account=null, accountEpoch=0, tab='catalog', page=1, pages=1, catalogRequest=0, catalogController;
  let current=null, episodeIndex=-1, quality='1080', hls=null, hlsPromise=null, playerEpoch=0, detailEpoch=0, episodeRequest=0, hero=null, toastTimer, lastSave=0, lastSaveError=0;
  function element(tag, text, className) { const el=document.createElement(tag); if(text!==undefined)el.textContent=text; if(className)el.className=className; return el; }
  function posterURL(value) { try { const url=new URL(value);return url.protocol==='https:'&&(url.hostname==='anilibria.top'||url.hostname.endsWith('.anilibria.top'))?url.href:''; }catch{return '';} }
  function image(url, alt) { const img=element('img');img.alt=alt||'';img.loading='lazy';img.decoding='async';const src=posterURL(url);if(src)img.src=src;img.addEventListener('error',()=>img.remove(),{once:true});return img; }
  function toast(message) { clearTimeout(toastTimer);$('hubToast').textContent=message;$('hubToast').hidden=false;toastTimer=setTimeout(()=>$('hubToast').hidden=true,5000); }
  async function api(path, options={}) { const response=await fetch(path,{credentials:'same-origin',...options}); let data;try{data=await response.json();}catch{data={};}if(!response.ok){const error=new Error(typeof data.detail==='string'?data.detail:'Sorov bajarilmadi. Qayta urinib koring.');error.status=response.status;throw error;}return data; }
  function cleanRecords(rows) {
    return (Array.isArray(rows)?rows:[]).filter(row=>row&&Number.isSafeInteger(row.release_id)&&row.release_id>0&&typeof row.title==='string').slice(0,500).map(row=>({...row,poster:posterURL(row.poster),position:Number.isFinite(row.position)?Math.max(0,row.position):0,duration:Number.isFinite(row.duration)?Math.max(0,row.duration):0,episode:Number.isFinite(row.episode)?row.episode:0}));
  }
  function guestRecords() { try{return cleanRecords(JSON.parse(localStorage.getItem('shadow-anime:guest')||'[]'));}catch{return [];} }
  function updateLibraryUI() { document.querySelectorAll('[data-save-id]').forEach(btn=>{const saved=!!library.get(Number(btn.dataset.saveId))?.favorite;btn.classList.toggle('saved',saved);btn.setAttribute('aria-pressed',String(saved));}); if(hero){const saved=!!library.get(hero.id)?.favorite;$('heroSave').replaceChildren(icon(saved?'bookmark':'plus'),document.createTextNode(saved?'Saqlangan':'Saqlash'));} $('favoriteCount').textContent=[...library.values()].filter(row=>row.favorite).length;if(tab!=='catalog'&&animePage)renderLibrary();if(current)updateFavorite(); }
  async function loadLibrary() {
    const epoch=++accountEpoch;
    try {
      const result=await api('/anime/api/library');if(epoch!==accountEpoch)return;
      const next=String(result.account_id);if(account!==next&&current)closePlayer();account=next;
      library=new Map(cleanRecords(result.items).map(row=>[row.release_id,row]));$('accountLink').querySelector('span:last-child').textContent='Hisob';
      $('libraryNotice').textContent='Tarix va saqlanganlar faqat shu akkaunt uchun. Muhim tarixni eksport qilish tavsiya etiladi.';
    } catch(error) {
      if(epoch!==accountEpoch)return;
      if(current&&account!==null)closePlayer();account=null;
      library=new Map((error.status===401?guestRecords():[]).map(row=>[row.release_id,row]));
      $('libraryNotice').textContent=error.status===401?'Mehmon tarixi shu qurilmada saqlanadi. Hisobdagi kutubxona alohida.':'Hisob kutubxonasini tekshirib bolmadi. Sahifani yangilang.';
      $('accountLink').querySelector('span:last-child').textContent='Kirish';
    }
    updateLibraryUI();
  }
  function libraryQuery(path) { return path+(account!==null?'?account='+encodeURIComponent(account):''); }
  async function persist(body, card) {
    const owner=account, epoch=accountEpoch;
    let saved={...body,title:card.title,poster:posterURL(card.poster),year:card.year,updated:Date.now()/1000};
    if(owner!==null) saved=await api(libraryQuery('/anime/api/library'),{method:'POST',headers:{'content-type':'application/json'},body:JSON.stringify(body)});
    if(owner!==account||epoch!==accountEpoch)return null;
    library.set(body.release_id,saved);
    if(owner===null){try{localStorage.setItem('shadow-anime:guest',JSON.stringify([...library.values()].slice(-500)));}catch{toast('Brauzer tarixni saqlay olmadi.');}}
    updateLibraryUI();return saved;
  }
  function progressBody(card, changes={}) {
    const old=library.get(card.id)||{};
    return {release_id:card.id,episode:old.episode||0,position:old.position||0,duration:old.duration||0,favorite:!!old.favorite,status:old.status||'planned',quality:old.quality||'1080',...changes};
  }
  async function toggleFavorite(card) {
    try {const saved=await persist(progressBody(card,{favorite:!library.get(card.id)?.favorite}),card);if(saved)toast(saved.favorite?'Saqlanganlarga qoshildi.':'Saqlanganlardan olib tashlandi.');}
    catch(error){toast(error.message);if(error.status===401||error.status===409)loadLibrary();}
  }
  async function removeRecord(card) {
    const owner=account, epoch=accountEpoch;
    try {
      if(owner!==null)await api(libraryQuery('/anime/api/library/'+card.id),{method:'DELETE'});
      if(owner!==account||epoch!==accountEpoch)return;library.delete(card.id);
      if(owner===null)localStorage.setItem('shadow-anime:guest',JSON.stringify([...library.values()]));
      updateLibraryUI();toast('Kutubxonadan olib tashlandi.');
    }catch(error){toast(error.message);if(error.status===401||error.status===409)loadLibrary();}
  }
  function cardNode(card, home=false, record=null) {
    const article=element('article',undefined,'poster-card'),open=element('button',undefined,'poster-open');open.type='button';open.setAttribute('aria-label',card.title+' — tomosha qilish');
    const art=element('div',undefined,'poster-art');art.append(image(card.poster,''));
    const overlay=element('div',undefined,'poster-overlay');overlay.append(icon('play'));art.append(overlay);
    if(Number.isFinite(card.rating)&&card.rating>0){const rating=element('span',undefined,'poster-rating');rating.append(element('span','★'),element('b',Number(card.rating).toFixed(1)));art.append(rating);}
    if(card.ongoing)art.append(element('span','DAVOM ETMOQDA','poster-ongoing'));
    if(record?.duration&&record.position>0){const progress=element('div',undefined,'watch-progress'),bar=element('span');bar.style.width=Math.min(100,record.position/record.duration*100)+'%';progress.append(bar);art.append(progress);}
    open.append(art,element('h3',card.title));open.addEventListener('click',()=>home?location.assign('/anime?title='+card.id):openTitle(card.id));article.append(open);
    const meta=element('div',undefined,'poster-meta');meta.append(element('span',card.year?String(card.year):'Anime'),element('span',card.episodes_total?card.episodes_total+' seriya':'RU ovoz'));article.append(meta);
    if(!home){const footer=element('div',undefined,'poster-footer');footer.append(element('span',record?.episode?record.episode+'-seriya · '+Math.floor(record.position/60)+' daq':(card.genres||[]).slice(0,2).map(g=>g.name).join(' / ')||'Ruscha ovoz'));
      const save=element('button');save.type='button';save.dataset.saveId=card.id;save.classList.toggle('saved',!!library.get(card.id)?.favorite);save.setAttribute('aria-label',card.title+' — saqlash');save.setAttribute('aria-pressed',String(!!library.get(card.id)?.favorite));save.append(icon('bookmark'));save.addEventListener('click',()=>toggleFavorite(card));footer.append(save);
      if(record){const remove=element('button');remove.type='button';remove.setAttribute('aria-label',card.title+' — tarixdan olib tashlash');remove.append(icon('trash'));remove.addEventListener('click',()=>removeRecord(card));footer.append(remove);}article.append(footer);
    }
    return article;
  }
  function skeletons(root,count) {root.replaceChildren();for(let i=0;i<count;i++){const card=element('article',undefined,'poster-card skeleton');card.append(element('div',undefined,'poster-art'),element('h3'));root.append(card);}}
  function catalogNotice(message, retry=false) {const box=$('catalogNotice');box.hidden=!message;box.replaceChildren(element('span',message));if(retry){const btn=element('button','Qayta urinish');btn.type='button';btn.onclick=loadCatalog;box.append(btn);}}
  function updateHero(card) {
    hero=card;$('animeHero').hidden=!card;if(!card)return;
    const src=posterURL(card.poster);if(src)$('heroPoster').src=src;
    $('heroTitle').textContent=card.title;$('heroDescription').textContent=card.description;
    $('heroType').textContent=card.ongoing?'YANGI SERIYALAR':(card.type||'ANIME');
    $('heroMeta').replaceChildren(...[card.rating?'★ '+Number(card.rating).toFixed(1):'',card.year,card.episodes_total?card.episodes_total+' seriya':'',...(card.genres||[]).slice(0,2).map(g=>g.name)].filter(Boolean).map(value=>element('span',String(value))));
    $('heroSave').setAttribute('aria-pressed',String(!!library.get(card.id)?.favorite));
  }
  async function loadCatalog() {
    if(tab!=='catalog')return renderLibrary();
    const seq=++catalogRequest;if(catalogController)catalogController.abort();catalogController=new AbortController();
    const q=$('animeQuery').value.trim(),genre=$('animeGenre').value,sort=$('animeSort').value;
    $('animeEmpty').hidden=true;$('catalogPagination').hidden=true;catalogNotice('');skeletons($('animeGrid'),12);
    const params=new URLSearchParams({q,page:String(page),sort});if(genre)params.set('genre',genre);
    try {
      const result=await api('/anime/api/catalog?'+params,{signal:catalogController.signal});if(seq!==catalogRequest||tab!=='catalog')return;
      pages=result.pages;$('animeGrid').replaceChildren(...result.items.map(card=>cardNode(card)));
      $('catalogCount').textContent=result.total+' anime';$('listHeading').textContent=q?'Qidiruv natijalari':$('animeSort').selectedOptions[0].textContent;
      $('animeEmpty').hidden=result.items.length>0;$('catalogPagination').hidden=pages<=1;
      $('prevPage').disabled=page<=1;$('nextPage').disabled=page>=pages;$('pageNumber').textContent=page+' / '+pages;
      if(!q&&!genre&&page===1)updateHero(result.items[0]);else updateHero(null);
    } catch(error) {if(error.name==='AbortError'||seq!==catalogRequest||tab!=='catalog')return;$('animeGrid').replaceChildren();updateHero(null);catalogNotice(error.message,true);}
  }
  function renderLibrary() {
    if(tab==='catalog')return;
    const q=$('animeQuery').value.trim().toLocaleLowerCase(),rows=[...library.values()].sort((a,b)=>b.updated-a.updated).filter(row=>(tab!=='favorites'||row.favorite)&&(tab!=='continue'||row.position>0&&row.status!=='watched')&&(!q||row.title.toLocaleLowerCase().includes(q)));
    $('animeGrid').replaceChildren(...rows.map(row=>cardNode({id:row.release_id,...row},false,row)));
    $('catalogCount').textContent=rows.length+' anime';$('animeEmpty').hidden=rows.length>0;$('catalogPagination').hidden=true;updateHero(null);catalogNotice('');
  }
  function setTab(value) {
    tab=value;catalogRequest++;catalogController?.abort();page=1;
    document.querySelectorAll('[data-tab]').forEach(btn=>{btn.classList.toggle('active',btn.dataset.tab===value);btn.setAttribute('aria-pressed',String(btn.dataset.tab===value));});
    const titles={catalog:'Keyingi hikoyangiz.',continue:'Hikoyangiz davom etadi.',favorites:'Siz tanlagan hikoyalar.',history:'Tomosha tarixingiz.'};$('catalogTitle').textContent=titles[value];$('listHeading').textContent={catalog:'Yangi qoshilganlar',continue:'Tomoshani davom ettirish',favorites:'Saqlanganlar',history:'Oxirgi tomoshalar'}[value];
    $('catalogFilters').hidden=value!=='catalog';loadCatalog();
  }
  async function hlsLibrary() {
    if(window.Hls)return window.Hls;if(!hlsPromise)hlsPromise=new Promise((resolve,reject)=>{const script=document.createElement('script');script.src='/hub/vendor/hls.min.js';script.onload=()=>resolve(window.Hls);script.onerror=()=>{hlsPromise=null;script.remove();reject(new Error('Playerni yuklab bolmadi. Sahifani yangilang.'));};document.head.append(script);});return hlsPromise;
  }
  function stopVideo() {playerEpoch++;video.pause();if(hls){hls.destroy();hls=null;}video.removeAttribute('src');video.load();}
  function updateFavorite() {const saved=!!library.get(current?.id)?.favorite;$('watchFavorite').setAttribute('aria-pressed',String(saved));$('watchFavorite').replaceChildren(icon('bookmark'),document.createTextNode(saved?'Saqlangan':'Saqlash'));}
  function episodesUI() {
    $('episodeCount').textContent=current.episodes.length+' seriya';$('episodeList').replaceChildren(...current.episodes.map((ep,index)=>{
      const button=element('button',undefined,'episode-button');button.type='button';button.classList.toggle('active',index===episodeIndex);if(index===episodeIndex)button.setAttribute('aria-current','true');
      const copy=element('span',undefined,'episode-copy');copy.append(element('strong',ep.number+'-seriya'),element('small',ep.name||Math.round(ep.duration/60)+' daqiqa'));
      button.append(element('span',String(ep.number),'episode-number'),copy,element('span',ep.streams['1080']?'1080p':ep.streams['720']?'720p':'480p','episode-quality'));button.onclick=()=>selectEpisode(index);return button;
    }));$('previousEpisode').disabled=episodeIndex<=0;$('nextEpisode').disabled=episodeIndex<0||episodeIndex>=current.episodes.length-1;
  }
  async function saveProgress(changes={}) {
    const card=current,ep=card?.episodes[episodeIndex];if(!card||!ep)return;
    const body=progressBody(card,{episode:ep.number,position:Number.isFinite(video.currentTime)?video.currentTime:0,duration:Number.isFinite(video.duration)&&video.duration>0?video.duration:ep.duration,quality,status:video.ended?'watched':video.currentTime>0?'watching':'planned',...changes});
    try {await persist(body,card);}catch(error){if(Date.now()-lastSaveError>30000){lastSaveError=Date.now();toast('Tomosha tarixi saqlanmadi. '+error.message);}if(error.status===401||error.status===409)loadLibrary();}
  }
  function closePlayer() {
    if(current)saveProgress();detailEpoch++;episodeRequest++;current=null;episodeIndex=-1;stopVideo();dialog.close();dialog.classList.remove('cinema');$('cinemaToggle').setAttribute('aria-pressed','false');
    const url=new URL(location.href);url.searchParams.delete('title');url.searchParams.delete('episode');history.replaceState(null,'',url);
  }
  async function openTitle(id,requestedEpisode) {
    const version=++detailEpoch;catalogNotice('');
    if(current)await saveProgress();if(version!==detailEpoch)return;episodeRequest++;current=null;stopVideo();$('watchTitle').textContent='Anime yuklanmoqda…';$('watchMeta').textContent='';$('episodeList').replaceChildren();$('watchDescription').textContent='';$('playerNotice').textContent='Katalog va mavjud seriyalar tekshirilmoqda.';video.removeAttribute('poster');
    $('videoQuality').replaceChildren();$('videoResolution').textContent='';$('qualityStatus').textContent='Sifat tekshirilmoqda';$('skipIntro').hidden=true;$('watchFavorite').disabled=true;
    if(!dialog.open)dialog.showModal();
    try {
      const card=await api('/anime/api/releases/'+id);if(version!==detailEpoch||!dialog.open)return;
      current=card;episodeIndex=-1;$('watchTitle').textContent=card.title;$('watchMeta').textContent=[card.year,'AniLibria','Ruscha ovoz'].filter(Boolean).join(' · ');$('watchDescription').textContent=card.description;
      $('watchGenres').replaceChildren(...card.genres.map(g=>element('span',g.name)));$('watchFavorite').disabled=false;updateFavorite();
      $('yummyLink').href='https://ru.yummyani.me/catalog?search='+encodeURIComponent(card.title);$('rezkaLink').href='https://hdrezka.film/search/?do=search&subaction=search&q='+encodeURIComponent(card.title);
      const src=posterURL(card.poster);if(src)video.poster=src;
      const resume=library.get(id);let index=card.episodes.findIndex(ep=>ep.number===Number(requestedEpisode??resume?.episode));if(index<0)index=0;
      $('playerNotice').textContent=card.playback_notice||'';episodesUI();
      const url=new URL(location.href);url.searchParams.set('title',id);history.replaceState(null,'',url);
      if(card.episodes.length)await selectEpisode(index,false);else{$('qualityStatus').textContent='Video mavjud emas';$('videoQuality').disabled=true;}
    } catch(error){if(version===detailEpoch&&dialog.open){$('playerNotice').textContent=error.message;$('watchTitle').textContent='Anime yuklanmadi';$('qualityStatus').textContent='Manba bilan aloqa yoq';}}
  }
  async function selectEpisode(index,autoPlay=true) {
    if(!current?.episodes[index])return;
    const request=++episodeRequest,card=current;
    if(episodeIndex>=0)await saveProgress();if(request!==episodeRequest||current!==card||!current?.episodes[index])return;
    episodeIndex=index;const ep=current.episodes[index],saved=library.get(current.id);quality=ep.streams['1080']?'1080':ep.streams['720']?'720':'480';
    $('videoQuality').disabled=false;$('videoQuality').replaceChildren(...['1080','720','480'].map(q=>{const option=element('option',q+'p'+(ep.streams[q]?'':' — mavjud emas'));option.value=q;option.disabled=!ep.streams[q];return option;}));$('videoQuality').value=quality;
    $('skipIntro').hidden=true;episodesUI();const resume=saved?.episode===ep.number&&saved.status!=='watched'?saved.position:0;
    const url=new URL(location.href);url.searchParams.set('episode',ep.number);history.replaceState(null,'',url);
    await loadStream(resume,autoPlay);
  }
  async function loadStream(position=0,autoPlay=false) {
    const ep=current?.episodes[episodeIndex];if(!ep?.streams[quality])return;
    stopVideo();const epoch=playerEpoch,source=ep.streams[quality];$('videoResolution').textContent='';$('qualityStatus').textContent=quality+'p';$('playerNotice').textContent=quality==='1080'?'1080p tanlandi. Video yuklangach tomoshani boshlang.':'Bu seriyada 1080p yoq yoki pastroq sifat tanlandi. Hozir '+quality+'p.';
    const restore=()=>{if(epoch!==playerEpoch)return;if(position>0&&Number.isFinite(video.duration))video.currentTime=Math.min(position,Math.max(0,video.duration-1));if(autoPlay)video.play().catch(()=>{$('playerNotice').textContent='Boshlash uchun playerdagi Play tugmasini bosing.';});};
    video.addEventListener('loadedmetadata',restore,{once:true});
    try {
      if(video.canPlayType('application/vnd.apple.mpegurl'))video.src=source;
      else {const Hls=await hlsLibrary();if(epoch!==playerEpoch||!current)return;if(!Hls.isSupported())throw new Error('Bu brauzer HLS playerni qollamaydi. Yangiroq brauzerdan foydalaning.');
        hls=new Hls({maxBufferLength:40,maxMaxBufferLength:60,startPosition:position||-1});hls.loadSource(source);hls.attachMedia(video);let recovered=false;
        hls.on(Hls.Events.ERROR,(_event,data)=>{if(epoch!==playerEpoch||!data.fatal)return;if(data.type===Hls.ErrorTypes.MEDIA_ERROR&&!recovered){recovered=true;hls.recoverMediaError();}else{$('playerNotice').textContent='Video manbasi yuklanmadi. Boshqa sifat yoki tashqi manbani tanlang.';}});
      }
    }catch(error){if(epoch===playerEpoch)$('playerNotice').textContent=error.message;}
  }
  $('watchClose').onclick=closePlayer;dialog.addEventListener('cancel',event=>{event.preventDefault();closePlayer();});
  $('videoQuality').onchange=()=>{const position=video.currentTime,playing=!video.paused;quality=$('videoQuality').value;loadStream(position,playing);};
  $('previousEpisode').onclick=()=>selectEpisode(episodeIndex-1);$('nextEpisode').onclick=()=>selectEpisode(episodeIndex+1);
  $('watchFavorite').onclick=()=>current&&toggleFavorite(current);
  $('cinemaToggle').onclick=()=>{const expanded=dialog.classList.toggle('cinema');$('cinemaToggle').setAttribute('aria-pressed',String(expanded));};
  $('pictureInPicture').hidden=!document.pictureInPictureEnabled;$('pictureInPicture').onclick=async()=>{try{if(document.pictureInPictureElement)await document.exitPictureInPicture();else await video.requestPictureInPicture();}catch{toast('Video boshlangandan keyin suzuvchi playerni tanlang.');}};
  for(const event of ['loadedmetadata','resize','playing'])video.addEventListener(event,()=>{if(video.videoWidth)$('videoResolution').textContent=video.videoWidth+' × '+video.videoHeight;});
  video.addEventListener('playing',()=>{const ep=current?.episodes[episodeIndex];if(ep)$('playerNotice').textContent=ep.number+'-seriya · '+quality+'p'+(quality!=='1080'&&!ep.streams['1080']?' · Bu seriyada 1080p yoq.':'');});
  video.addEventListener('timeupdate',()=>{const opening=current?.episodes[episodeIndex]?.opening;const start=opening?.start,stop=opening?.stop;$('skipIntro').hidden=!(Number.isFinite(start)&&Number.isFinite(stop)&&stop>start&&video.currentTime>=start&&video.currentTime<stop);if(current&&video.currentTime>1&&Date.now()-lastSave>15000){lastSave=Date.now();saveProgress();}});
  $('skipIntro').onclick=()=>{const stop=current?.episodes[episodeIndex]?.opening?.stop;if(Number.isFinite(stop))video.currentTime=stop;};
  video.addEventListener('pause',()=>{if(current&&video.currentTime>0)saveProgress();});
  video.addEventListener('ended',async()=>{const card=current,index=episodeIndex,epoch=playerEpoch;await saveProgress({status:'watched'});if(current===card&&epoch===playerEpoch&&index===episodeIndex&&$('autoNext').checked&&index+1<current.episodes.length)selectEpisode(index+1);});
  video.addEventListener('error',()=>{if(current&&video.error&&video.getAttribute('src'))$('playerNotice').textContent='Videoni ochib bolmadi. Boshqa sifatni tanlang yoki qayta urinib koring.';});
  document.querySelectorAll('[data-tab]').forEach(button=>button.onclick=()=>setTab(button.dataset.tab));
  $('animeSearch').onsubmit=event=>{event.preventDefault();page=1;loadCatalog();};
  $('animeGenre').onchange=$('animeSort').onchange=()=>{page=1;loadCatalog();};
  $('prevPage').onclick=()=>{page=Math.max(1,page-1);loadCatalog();};$('nextPage').onclick=()=>{page++;loadCatalog();};
  $('libraryExport').onclick=()=>{const blob=new Blob([JSON.stringify({version:1,exported_at:new Date().toISOString(),items:[...library.values()]},null,2)],{type:'application/json'});const url=URL.createObjectURL(blob),link=document.createElement('a');link.href=url;link.download='shadow-anime-library.json';link.click();setTimeout(()=>URL.revokeObjectURL(url),1000);};
  $('emptyBrowse').onclick=()=>{$('animeQuery').value='';setTab('catalog');};
  $('heroWatch').onclick=()=>hero&&openTitle(hero.id);$('heroSave').onclick=()=>hero&&toggleFavorite(hero);
  window.addEventListener('focus',loadLibrary);document.addEventListener('visibilitychange',()=>{if(document.visibilityState==='visible')loadLibrary();else if(current)saveProgress();});
  async function init() {
    await loadLibrary();
    if(animePage){
      document.title='Anime Watch — Shadow';const url=new URL(location.href);$('animeQuery').value=url.searchParams.get('q')||'';
      api('/anime/api/genres').then(rows=>{$('animeGenre').append(...rows.map(row=>{const opt=element('option',row.name);opt.value=row.id;return opt;}));}).catch(()=>{});
      loadCatalog();const title=Number(url.searchParams.get('title'));if(Number.isSafeInteger(title)&&title>0)openTitle(title,url.searchParams.get('episode')??undefined);
    }else{
      skeletons($('homePosters'),6);
      try {const data=await api('/anime/api/catalog?sort=fresh');$('homePosters').replaceChildren(...data.items.slice(0,6).map(card=>cardNode(card,true)));if(data.items[0]?.poster){const art=image(data.items[0].poster,'');art.loading='eager';art.fetchPriority='high';$('homeAnimeArt').append(art);}}
      catch(error){$('homePosters').replaceChildren(element('p','Anime katalogi vaqtincha ochilmadi. Anime Watch bolimida qayta urinib koring.','source-caption'));}
    }
  }
  init();
})();
