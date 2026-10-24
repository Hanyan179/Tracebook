/* Local visual storytelling only. Fixed synthetic sources; no audio or model APIs. */
const PrecisionMotion = (() => {
  const clamp = x => Math.min(1, Math.max(0, x));
  const smooth = (p, a, b) => { const x=clamp((p-a)/(b-a)); return x*x*(3-2*x); };
  function frameAt(value) {
    const p=clamp(value), reveal=smooth(p,.24,.36), dock=smooth(p,.52,.76);
    return {p,reveal,dock, intro:1-smooth(p,.12,.23), audioCopy:smooth(p,.20,.32)*(1-smooth(p,.50,.60)), review:smooth(p,.52,.72),noteOpacity:smooth(p,.74,.82),
      photoOpacity:1-smooth(p,.17,.36), photoScale:1+.08*smooth(p,0,.18),
      photoX:0, photoY:0, photoBlur:0,
      audioOpacity:smooth(p,.24,.34),audioX:64-4.5*reveal+14*dock,audioY:38+30*reveal-32.7*dock,audioWidth:10+57*reveal-31*dock,
      waveHeight:30+120*reveal-108*dock,waveAngle:-12*reveal+12*dock,
      ringOpacity:p>=.1&&p<=.30?1:0,ringScale:.5+1.1*smooth(p,.10,.28),ringOne:Math.sin(Math.PI*clamp((p-.10)/.08)),ringTwo:Math.sin(Math.PI*clamp((p-.21)/.08)),ringOneScale:.6+1.1*clamp((p-.10)/.08),ringTwoScale:.6+1.1*clamp((p-.21)/.08)};
  }
  function contactPoint(width,height){const scale=Math.max(width/1672,height/941);return {x:(width-1672*scale)/2+1065*scale,y:(height-941*scale)/2+349*scale};}
  return {clamp,smooth,frameAt,contactPoint};
})();
if(typeof module!=='undefined')module.exports=PrecisionMotion;
(() => {
  if(typeof document==='undefined')return;
  const story=document.querySelector('[data-story]');if(!story)return;
  const viewport=story.querySelector('.story-viewport'),source=document.getElementById('audio-source');
  const reduced=matchMedia('(prefers-reduced-motion: reduce)'),desktop=matchMedia('(min-width: 900px) and (min-height: 700px)');
  const action=story.querySelector('[data-action-demo]'),reset=story.querySelector('[data-reset-gesture]'),status=story.querySelector('[data-action-status]');
  const noteChoice=story.querySelector('[data-note-select]'),note=document.getElementById('note-original');
  let queued=false, manualOffset=0, animation=0, lastProgress=0, currentBase=0;
  const pinned=()=>desktop.matches&&!reduced.matches;
  const goProgress=(progress,behavior='smooth')=>{cancel();manualOffset=0;scrollTo({top:scrollY+story.getBoundingClientRect().top+(story.offsetHeight-innerHeight)*progress,behavior:reduced.matches?'instant':behavior});};
  const baseProgress=()=>{const travel=story.offsetHeight-innerHeight;return travel>0?PrecisionMotion.clamp(-story.getBoundingClientRect().top/travel):0;};
  const accessible=(el,visible)=>{if(!visible&&el.contains(document.activeElement))viewport.focus({preventScroll:true});el.toggleAttribute('inert',!visible);el.setAttribute('aria-hidden',String(!visible));};
  const paint=()=>{
    const pin=desktop.matches&&!reduced.matches;story.classList.toggle('is-pinned',pin);
    currentBase=pin?(story.dataset.previewProgress!==undefined?Number(story.dataset.previewProgress):baseProgress()):0;
    const p=pin?(note.classList.contains('is-selected')?.88:PrecisionMotion.clamp(currentBase+(1-currentBase)*manualOffset)):0, f=PrecisionMotion.frameAt(p);lastProgress=p;
    const set=(name,value)=>story.style.setProperty('--'+name,String(value));
    set('photo-opacity',f.photoOpacity);set('photo-scale',f.photoScale);set('photo-x',f.photoX+'px');set('photo-y',f.photoY+'px');set('photo-blur',f.photoBlur+'px');set('silver-opacity',f.reveal);
    set('intro-opacity',f.intro);set('intro-y',(-30*(1-f.intro))+'px');set('audio-copy-opacity',f.audioCopy);set('audio-copy-y',(18*(1-f.reveal))+'px');
    set('audio-translate-y',(-50+50*f.dock)+'%');set('audio-opacity',f.audioOpacity);set('audio-x',f.audioX+'%');set('audio-y',f.audioY+'%');set('audio-width',f.audioWidth+'%');set('wave-height',f.waveHeight+'px');set('wave-angle',f.waveAngle+'deg');set('wave-shadow',(24*(1-f.dock))+'px');
    set('duration-size',(24-6*f.dock)+'px');set('meta-top',(20-12*f.dock)+'px');set('meta-gap',(24-10*f.dock)+'px');set('audio-title-size','18px');
    set('note-opacity',f.noteOpacity);set('review-opacity',f.review);set('review-y',(22*(1-f.review))+'px');set('ring-opacity',f.ringOpacity);set('ring-scale',f.ringScale);set('ring-one',f.ringOne);set('ring-two',f.ringTwo);set('ring-one-scale',f.ringOneScale);set('ring-two-scale',f.ringTwoScale);set('progress',(p*100)+'%');
    const rect=viewport.getBoundingClientRect(),point=PrecisionMotion.contactPoint(rect.width||innerWidth,rect.height||innerHeight);set('contact-x',point.x+'px');set('contact-y',point.y+'px');
    reset.hidden=manualOffset<=.001;
    story.dataset.frame=p<.12?'moment':p<.32?'gesture':p<.52?'source':p<.76?'gather':'review';
    for(const panel of story.querySelectorAll('[data-panel]')){const role=panel.dataset.panel;accessible(panel,!pin||(role==='intro'?f.intro>.02:role==='audio'?f.audioCopy>.02:f.review>.02));}
    accessible(noteChoice,(!pin||f.noteOpacity>.02)&&!note.classList.contains('is-selected'));accessible(source,!pin||f.audioOpacity>.02);accessible(story.querySelector('.review-surface'),(!pin||f.review>.02)&&!note.classList.contains('is-selected'));
    queued=false;
  };
  const cancel=()=>{if(animation){cancelAnimationFrame(animation);animation=0;action.disabled=false;}};
  const animateOffset=target=>{
    cancel();const from=manualOffset,begin=performance.now();
    const tick=now=>{const t=PrecisionMotion.smooth(now-begin,0,1050);manualOffset=from+(target-from)*t;paint();
      if(t<1)animation=requestAnimationFrame(tick);else{animation=0;action.disabled=false;status.textContent=status.dataset.saved;source.focus({preventScroll:true});}};
    animation=requestAnimationFrame(tick);
  };
  action.hidden=false;reset.hidden=false;story.querySelector('.no-js-action').hidden=true;
  action.addEventListener('click',()=>{status.textContent=status.dataset.running;action.disabled=true;if(desktop.matches&&!reduced.matches)animateOffset(.42);else{action.disabled=false;status.textContent=status.dataset.saved;source.classList.add('gesture-demonstrated');}});
  reset.addEventListener('click',()=>{cancel();manualOffset=0;status.textContent='';paint();if(currentBase<.12)action.focus({preventScroll:true});});
  const onScroll=()=>{cancel();if(!queued){queued=true;requestAnimationFrame(paint);}};
  addEventListener('scroll',onScroll,{passive:true});addEventListener('resize',()=>{cancel();paint();});
  const modeChange=()=>{cancel();manualOffset=0;paint();if(note.classList.contains('is-selected')){if(pinned())goProgress(.88,'instant');else note.scrollIntoView({behavior:'instant',block:'start'});note.focus({preventScroll:true});}};
  reduced.addEventListener('change',modeChange);desktop.addEventListener('change',modeChange);
  addEventListener('pagehide',cancel);document.addEventListener('visibilitychange',()=>{if(document.hidden)cancel();});
  story.querySelector('[data-advance]').addEventListener('click',event=>{event.preventDefault();if(pinned()&&lastProgress<.76)goProgress(lastProgress<.40?.42:.88);else{const next=document.getElementById('library');if(next){next.scrollIntoView({behavior:reduced.matches?'instant':'smooth',block:'start'});next.setAttribute('tabindex','-1');next.focus({preventScroll:true});}}});
  document.querySelector('[data-review-nav]')?.addEventListener('click',event=>{if(!pinned())return;event.preventDefault();history.pushState(null,'','#review');goProgress(.88);});
  noteChoice.hidden=false;story.querySelector('.note-fallback').hidden=true;
  const openNote=()=>{note.classList.add('is-selected');story.classList.add('is-reading');noteChoice.setAttribute('aria-expanded','true');story.querySelector('.review-surface').toggleAttribute('inert',true);requestAnimationFrame(()=>{if(!story.classList.contains('is-pinned'))note.scrollIntoView({behavior:reduced.matches?'instant':'smooth',block:'start'});note.focus({preventScroll:true});});};
  const closeNote=()=>{if(pinned())goProgress(.88,'instant');note.classList.remove('is-selected');story.classList.remove('is-reading');noteChoice.setAttribute('aria-expanded','false');paint();accessible(story.querySelector('.review-surface'),true);if(!pinned())noteChoice.scrollIntoView({behavior:'instant',block:'center'});noteChoice.focus({preventScroll:true});};
  noteChoice.addEventListener('click',()=>{history.pushState(null,'','#note-original');openNote();});
  document.querySelector('[data-return-review]').addEventListener('click',event=>{event.preventDefault();history.pushState(null,'','#review');closeNote();});
  note.addEventListener('keydown',event=>{if(event.key==='Escape'){event.preventDefault();history.pushState(null,'','#review');closeNote();}});
  const restoreHash=()=>{if(location.hash==='#note-original'){if(pinned())goProgress(.88,'instant');openNote();}else if(note.classList.contains('is-selected'))closeNote();else if(location.hash==='#review'&&pinned())goProgress(.88,'instant');};addEventListener('popstate',restoreHash);addEventListener('hashchange',restoreHash);
  document.body.classList.add('is-enhanced');paint();restoreHash();if(story.getAttribute('data-preview-note')!==null)openNote();
})();
