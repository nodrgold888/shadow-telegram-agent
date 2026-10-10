/* Shadow's accessible controls for the actual source video, not an embedded third-party player. */
(() => {
  'use strict';
  window.createShadowPlayer = ({video, icon, episode, onExpand, onClearHistory, notice}) => {
    const $ = id => document.getElementById(id);
    const stage=$('videoStage'), controls=$('playerControls'), menu=$('playerSettings');
    const quality=$('videoQuality').parentElement, qualityAnchor=$('playerSettingsToggle');
    const preferences={autoNext:true,autoSkipIntro:true,autoSkipEnding:true,playerLogEnabled:true,playerNativeControls:false};
    try {
      const saved=JSON.parse(localStorage.getItem('shadow-anime:player')||'{}');
      for(const key of Object.keys(preferences))if(typeof saved[key]==='boolean')preferences[key]=saved[key];
    } catch {}
    let hideTimer, toastTimer, skipped=new Set(), episodeKey='', seeking=false;
    function time(seconds) {
      seconds=Number.isFinite(seconds)?Math.max(0,Math.floor(seconds)):0;
      const hours=Math.floor(seconds/3600),minutes=Math.floor(seconds/60)%60;
      return (hours?hours+':'+String(minutes).padStart(2,'0'):minutes)+':'+String(seconds%60).padStart(2,'0');
    }
    function remember() {
      for(const key of Object.keys(preferences))preferences[key]=$(key).checked;
      try{localStorage.setItem('shadow-anime:player',JSON.stringify(preferences));}catch{}
    }
    function log(message) {
      if(!$('playerLogEnabled').checked||!episode())return;
      const row=document.createElement('li');row.textContent=new Date().toLocaleTimeString('uz-UZ',{hour:'2-digit',minute:'2-digit'})+' · '+message;
      $('playerEventLog').prepend(row);while($('playerEventLog').children.length>30)$('playerEventLog').lastChild.remove();
    }
    function eventToast(message) {
      log(message);clearTimeout(toastTimer);$('playerEventToast').textContent=message;$('playerEventToast').hidden=false;
      toastTimer=setTimeout(()=>$('playerEventToast').hidden=true,3000);
    }
    function showControls() {
      clearTimeout(hideTimer);stage.classList.remove('controls-hidden');
      if(!video.paused&&menu.hidden&&!stage.contains(document.activeElement))hideTimer=setTimeout(()=>stage.classList.add('controls-hidden'),3000);
    }
    function setMenu(open) {
      menu.hidden=!open;$('playerSettingsToggle').setAttribute('aria-expanded',String(open));
      $('playerSettingsFallback').setAttribute('aria-expanded',String(open));showControls();
    }
    function nativeControls() {
      const native=$('playerNativeControls').checked;
      video.controls=native;stage.classList.toggle('native-controls',native);controls.hidden=native;
      $('playerSettingsFallback').hidden=!native;
      if(native)menu.insertBefore(quality,menu.querySelector('hr'));else controls.insertBefore(quality,qualityAnchor);
      showControls();
    }
    function sync() {
      const loaded=Number.isFinite(video.duration)&&video.duration>0,progress=loaded?Math.min(100,video.currentTime/video.duration*100):0;
      $('playerTime').textContent=time(video.currentTime);$('playerDuration').textContent=time(video.duration);
      $('playerSeek').disabled=!loaded;
      if(!seeking)$('playerSeek').value=progress;
      $('playerSeek').style.setProperty('--played',progress+'%');
      let buffered=0;
      if(loaded&&video.buffered.length)buffered=Math.min(100,video.buffered.end(video.buffered.length-1)/video.duration*100);
      $('playerSeek').style.setProperty('--buffered',buffered+'%');
      $('playerPlay').replaceChildren(icon(video.paused?'play':'pause'));
      $('playerPlay').setAttribute('aria-label',video.paused?'Videoni boshlash':'Pauza');
      $('playerCenterPlay').hidden=!video.paused||!menu.hidden;
      const muted=video.muted||video.volume===0;
      $('playerMute').replaceChildren(icon(muted?'muted':'volume'));
      $('playerMute').setAttribute('aria-label',muted?'Ovozni yoqish':'Ovozni ochirish');
      $('playerVolume').value=video.muted?0:video.volume;
      $('playerVolume').style.setProperty('--volume',Number($('playerVolume').value)*100+'%');
    }
    async function playPause() {
      if(!episode())return;
      try{if(video.paused)await video.play();else video.pause();}catch{notice('Video yuklanmadi. Boshqa sifatni tanlang yoki qayta urinib koring.');}
    }
    async function fullscreen() {
      try {
        if(document.fullscreenElement)await document.exitFullscreen();
        else if(stage.requestFullscreen)await stage.requestFullscreen();
        else if(video.webkitEnterFullscreen)video.webkitEnterFullscreen();
        else notice('Bu brauzer toliq ekranni qollamaydi. Kino rejimini tanlang.');
      } catch {notice('Toliq ekranni ochib bolmadi. Kino rejimini tanlang.');}
    }
    function skipRanges() {
      const ep=episode();if(!ep||video.paused||video.seeking||!Number.isFinite(video.duration))return;
      for(const [field,pref,label] of [['opening','autoSkipIntro','Kirish otkazildi'],['ending','autoSkipEnding','Yakun otkazildi']]) {
        const range=ep[field];
        if(!$(pref).checked||skipped.has(field)||!Number.isFinite(range?.start)||!Number.isFinite(range?.stop)||range.stop<=range.start||range.start<0||range.stop>video.duration+1)continue;
        if(video.currentTime>=range.start&&video.currentTime<range.stop) {
          skipped.add(field);video.currentTime=Math.min(range.stop,video.duration);eventToast(label);break;
        }
      }
    }
    for(const [key,value] of Object.entries(preferences)) {
      $(key).checked=value;
      $(key).addEventListener('change',()=>{
        remember();if(key==='playerNativeControls')nativeControls();
        if(key==='playerLogEnabled')$('playerEventPanel').hidden=!$(key).checked;
        showControls();
      });
    }
    $('playerLogEnabled').dispatchEvent(new Event('change'));
    $('playerExpand').onchange=()=>onExpand($('playerExpand').checked);
    $('playerPlay').onclick=$('playerCenterPlay').onclick=playPause;
    $('playerMute').onclick=()=>{if(video.volume===0)video.volume=1;video.muted=!video.muted;};
    $('playerVolume').oninput=()=>{video.volume=Number($('playerVolume').value);video.muted=video.volume===0;};
    $('playerSeek').oninput=()=>{
      if(!Number.isFinite(video.duration)||video.duration<=0)return;
      seeking=true;video.currentTime=video.duration*Number($('playerSeek').value)/100;sync();
    };
    $('playerSeek').onchange=()=>{seeking=false;sync();log('Video vaqti: '+time(video.currentTime));};
    $('playerFullscreen').onclick=fullscreen;
    $('playerSettingsToggle').onclick=$('playerSettingsFallback').onclick=()=>{setMenu(menu.hidden);sync();};
    $('playerSettingsClose').onclick=()=>{setMenu(false);sync();$('playerSettingsToggle').focus();};
    $('playerClearHistory').onclick=async()=>{
      $('playerClearHistory').disabled=true;
      try{await onClearHistory();log('Shu anime tarixi tozalandi');}catch(error){notice(error.message);}
      finally{$('playerClearHistory').disabled=false;}
    };
    document.addEventListener('pointerdown',event=>{if(!menu.hidden&&!menu.contains(event.target)&&!event.target.closest('#playerSettingsToggle,#playerSettingsFallback')){setMenu(false);sync();}});
    stage.addEventListener('pointermove',showControls);
    stage.addEventListener('focusin',showControls);stage.addEventListener('focusout',showControls);
    stage.addEventListener('pointerleave',showControls);
    video.addEventListener('click',()=>{if(video.controls)return;if(matchMedia('(pointer:coarse)').matches)showControls();else playPause();});
    video.addEventListener('dblclick',()=>{if(!video.controls)fullscreen();});
    document.addEventListener('keydown',event=>{
      if(menu.hidden===false&&event.key==='Escape'){event.preventDefault();event.stopImmediatePropagation();setMenu(false);sync();return;}
      if(!stage.contains(event.target)||event.target.matches('input,select,button')||video.controls)return;
      const key=event.key.toLowerCase();
      if(key===' '||key==='k'){event.preventDefault();playPause();}
      else if(key==='f'){event.preventDefault();fullscreen();}
      else if(key==='m'){event.preventDefault();$('playerMute').click();}
      else if((key==='arrowleft'||key==='arrowright')&&Number.isFinite(video.duration)) {
        event.preventDefault();video.currentTime=Math.min(video.duration,Math.max(0,video.currentTime+(key==='arrowright'?10:-10)));
      }
      showControls();
    },true);
    for(const event of ['timeupdate','durationchange','loadedmetadata','volumechange','progress','emptied','pause','play','ended'])video.addEventListener(event,sync);
    video.addEventListener('timeupdate',skipRanges);
    video.addEventListener('play',()=>{log('Tomosha boshlandi');showControls();});
    video.addEventListener('pause',()=>{log('Pauza · '+time(video.currentTime));showControls();});
    video.addEventListener('ended',()=>log('Seriya yakunlandi'));
    document.addEventListener('fullscreenchange',()=>{
      $('playerFullscreen').setAttribute('aria-label',document.fullscreenElement?'Toliq ekrandan chiqish':'Toliq ekran');showControls();
    });
    nativeControls();sync();
    return {
      log,
      expanded(value) {$('playerExpand').checked=value;},
      reset(ep) {
        const key=ep?.id||ep?.number||'';
        if(key!==episodeKey){skipped.clear();episodeKey=key;}
        setMenu(false);seeking=false;$('playerEventToast').hidden=true;sync();showControls();
      },
      close() {setMenu(false);skipped.clear();episodeKey='';$('playerEventLog').replaceChildren();$('playerEventToast').hidden=true;clearTimeout(hideTimer);clearTimeout(toastTimer);}
    };
  };
})();
