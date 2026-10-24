/* Local synthetic demonstrations only. No microphone, storage or network APIs. */
const TraceDemo = Object.freeze({
  nextCapture: state => state === 'idle' ? 'recording' : state === 'recording' ? 'saved' : 'idle',
  nextMemory: state => state === 'candidate' ? 'confirmed' : state === 'confirmed' ? 'inactive' : 'confirmed',
  stepAt: progress => progress >= .82 ? 2 : progress >= .5 ? 1 : 0,
  sourceVisible: (filter, kind) => filter === 'all' || filter === kind
});
if (typeof module !== 'undefined') module.exports = TraceDemo;
(() => {
  'use strict';
  if (typeof document === 'undefined') return;
  const reduced = window.matchMedia('(prefers-reduced-motion: reduce)');
  const desktop = window.matchMedia('(min-width: 761px) and (min-height: 700px)');
  document.querySelectorAll('[data-capture-demo]').forEach(button => {
    button.hidden = false;
    const fallback=button.parentElement.querySelector('.hero-demo-fallback');if(fallback)fallback.hidden=true;
    let started=0, elapsed=0, clock;
    const stage=button.closest('[data-capture]');
    const counter=stage.querySelector('[data-counter]');
    const drawTime=()=>{const seconds=Math.floor(elapsed/1000);counter.textContent=String(Math.floor(seconds/60)).padStart(2,'0')+':'+String(seconds%60).padStart(2,'0');};
    const render=state=>{
      clearInterval(clock);
      if(state==='recording'){started=performance.now();elapsed=0;clock=setInterval(()=>{elapsed=Math.max(0,performance.now()-started);drawTime();},200);}
      else if(state==='saved'){elapsed=Math.max(0,performance.now()-started);}else{elapsed=0;}
      stage.dataset.capture=state;
      stage.querySelector('.capture-label').textContent=stage.querySelector('.capture-label').dataset[state];
      stage.querySelector('.capture-source').setAttribute('aria-hidden',String(state!=='saved'));
      stage.querySelector('[data-capture-feedback]').textContent=stage.querySelector('[data-capture-feedback]').dataset[state]||'';
      button.textContent=button.dataset[state==='idle'?'start':state==='recording'?'stop':'again'];
      drawTime();
    };
    button.addEventListener('click',()=>render(TraceDemo.nextCapture(stage.dataset.capture)));
    const stopHiddenDemo=()=>{if(stage.dataset.capture==='recording')render('idle');};
    document.addEventListener('visibilitychange',()=>{if(document.hidden)stopHiddenDemo();});
    addEventListener('pagehide',stopHiddenDemo);
  });
  const stories = [...document.querySelectorAll('[data-pinned-story]')];
  const thresholds = [0, .5, .82];
  let ticking = false;
  const manualTargets=new Map();
  let settleTimer;
  const update = () => {
    stories.forEach(section => {
      const pin = desktop.matches && !reduced.matches;
      section.classList.toggle('is-pinned', pin);
      const travel = section.offsetHeight - innerHeight;
      const progress = travel > 0 ? Math.max(0, Math.min(1, -section.getBoundingClientRect().top / travel)) : 0;
      const manual=manualTargets.get(section);
      if(!pin || (manual && Math.abs(scrollY-manual.top)<2))manualTargets.delete(section);
      const index=manualTargets.has(section)?manualTargets.get(section).index:TraceDemo.stepAt(progress);
      section.querySelectorAll('[data-story-step]').forEach((panel,i) => {
        panel.classList.toggle('is-active', i === index);
        panel.toggleAttribute('inert', pin && i !== index);
        if (pin) panel.setAttribute('aria-hidden', String(i !== index)); else panel.removeAttribute('aria-hidden');
      });
      section.querySelectorAll('[data-story-tab]').forEach((tab,i) => {
        if (pin && i === index) tab.setAttribute('aria-current','step'); else tab.removeAttribute('aria-current');
      });
    });
    ticking = false;
  };
  stories.forEach(section => section.querySelectorAll('[data-story-tab]').forEach((tab,index) => {
    tab.addEventListener('click', event => {
      if (!section.classList.contains('is-pinned')) return;
      event.preventDefault();
      const start = scrollY + section.getBoundingClientRect().top;
      const top=start+(section.offsetHeight-innerHeight)*(thresholds[index]+(index ? .004 : 0));
      manualTargets.set(section,{index,top});update();
      clearTimeout(settleTimer);
      settleTimer=setTimeout(()=>{manualTargets.clear();update();},1600);
      scrollTo({top,behavior:'smooth'});
    });
  }));
  addEventListener('scroll', () => {if(!ticking){ticking=true;requestAnimationFrame(update);}}, {passive:true});
  const cancelManual=()=>{clearTimeout(settleTimer);manualTargets.clear();update();};
  addEventListener('wheel',cancelManual,{passive:true});addEventListener('touchstart',cancelManual,{passive:true});
  addEventListener('keydown',event=>{if(['ArrowUp','ArrowDown','PageUp','PageDown','Home','End','Escape'].includes(event.key))cancelManual();});
  addEventListener('scrollend',cancelManual);addEventListener('resize',cancelManual);reduced.addEventListener('change',cancelManual);update();
  document.querySelectorAll('[data-scene-carousel]').forEach(carousel => {
    const slides=[...carousel.querySelectorAll('[data-scene]')];
    const buttons=[...carousel.querySelectorAll('[data-scene-select]')];
    const pause=carousel.querySelector('[data-scene-pause]');
    let current=0, paused=reduced.matches, visible=false, timer;
    const render = index => {
      current=index;
      slides.forEach((slide,i)=>{slide.classList.toggle('is-active',i===index);slide.toggleAttribute('inert',i!==index);slide.setAttribute('aria-hidden',String(i!==index));});
      buttons.forEach((button,i)=>button.setAttribute('aria-pressed',String(i===index)));
    };
    const schedule = () => {
      clearInterval(timer); pause.hidden=reduced.matches; pause.textContent=pause.dataset[paused?'play':'pause'];
      pause.setAttribute('aria-pressed',String(paused));
      if(!paused && visible && !document.hidden && !reduced.matches)timer=setInterval(()=>render((current+1)%slides.length),7000);
    };
    carousel.classList.add('is-carousel'); carousel.querySelector('.scene-controls').hidden=false;render(0);
    buttons.forEach((button,i)=>button.addEventListener('click',()=>{paused=true;render(i);schedule();}));
    pause.addEventListener('click',()=>{paused=!paused;schedule();});
    carousel.addEventListener('focusin',event=>{if(event.target!==pause){paused=true;schedule();}});
    document.addEventListener('visibilitychange',schedule);
    reduced.addEventListener('change',()=>{
      if(reduced.matches){paused=true;if(document.activeElement===pause)buttons[current].focus();}
      schedule();
    });
    addEventListener('pagehide',()=>clearInterval(timer));
    addEventListener('pageshow',schedule);
    if('IntersectionObserver' in window)new IntersectionObserver(entries=>{visible=entries[0].isIntersecting;schedule();},{threshold:.25}).observe(carousel);
    schedule();
  });
  document.querySelectorAll('[data-filter-controls]').forEach(controls=>{
    controls.hidden=false;const rows=controls.parentElement.querySelectorAll('[data-source-kind]');
    controls.querySelectorAll('[data-filter]').forEach(button=>button.addEventListener('click',()=>{
      controls.querySelectorAll('[data-filter]').forEach(b=>b.setAttribute('aria-pressed',String(b===button)));
      let count=0;rows.forEach(row=>{row.hidden=!TraceDemo.sourceVisible(button.dataset.filter,row.dataset.sourceKind);if(!row.hidden)count++;});
      const status=controls.parentElement.querySelector('[data-filter-status]');
      if(status)status.textContent=count+' '+status.dataset.label;
    }));
  });
  document.querySelectorAll('[data-confirm-memory]').forEach(button=>{
    button.hidden=false;
    const reset=button.parentElement.querySelector('[data-memory-reset]');reset.hidden=false;
    const status=button.parentElement.querySelector('[data-memory-status]');
    const render=state=>{button.dataset.state=state;button.textContent=button.dataset[state==='candidate'?'confirm':state==='confirmed'?'deactivate':'restore'];status.textContent=status.dataset[state];document.querySelectorAll('[data-memory-badge]').forEach(badge=>{badge.dataset.state=state;badge.textContent=badge.dataset[state];});};
    button.addEventListener('click',()=>render(TraceDemo.nextMemory(button.dataset.state)));
    reset.addEventListener('click',()=>render('candidate'));
  });
  const cliButtons=[...document.querySelectorAll('[data-cli-step]')];
  const original=document.getElementById('original-example');
  const citation=document.querySelector('[data-citation]');
  const returnLink=document.querySelector('[data-citation-return]');
  let citationRequest=0,returnTarget=citation;
  if(original){original.classList.add('is-ready');original.toggleAttribute('inert',true);original.setAttribute('aria-hidden','true');}
  const selectCLI=button=>{
    citationRequest++;
    cliButtons.forEach(other=>other.setAttribute('aria-pressed',String(other===button)));
    document.querySelector('[data-cli-command]').textContent=button.dataset.command;
    document.querySelectorAll('[data-cli-result]').forEach(panel=>{panel.hidden=panel.dataset.cliResult!==button.dataset.cliStep;});
    const expanded=button.dataset.cliStep==='2';
    original.classList.toggle('is-expanded',expanded);original.toggleAttribute('inert',!expanded);original.setAttribute('aria-hidden',String(!expanded));
    original.classList.remove('is-cited');
  };
  cliButtons.forEach(button=>{button.hidden=false;button.addEventListener('click',()=>selectCLI(button));});
  const revealSource=origin=>{
    selectCLI(cliButtons[2]);
    returnTarget=origin;
    if(returnLink){returnLink.hidden=false;returnLink.setAttribute('href','#'+origin.getAttribute('id'));}
    const request=++citationRequest;
    original.classList.add('is-cited','is-expanded');
    requestAnimationFrame(()=>{
      if(request!==citationRequest)return;
      const line=original.querySelector('[data-cited-line]');
      const top=Math.max(0,line.getBoundingClientRect().top-original.getBoundingClientRect().top+original.scrollTop-original.clientTop-16);
      original.scrollTo({top,behavior:reduced.matches?'instant':'smooth'});
      original.focus({preventScroll:true});
    });
  };
  if(citation){
    citation.hidden=false;document.querySelector('.citation-fallback').hidden=true;
    citation.addEventListener('click',()=>revealSource(citation));
    original.addEventListener('keydown',event=>{
      if(event.key==='Escape'){
        event.preventDefault();returnTarget.scrollIntoView({block:'center',behavior:reduced.matches?'instant':'smooth'});returnTarget.focus({preventScroll:true});
      }
    });
    if(returnLink)returnLink.addEventListener('click',()=>requestAnimationFrame(()=>returnTarget.focus({preventScroll:true})));
  }
  document.querySelectorAll('[data-source-open]').forEach(link=>link.addEventListener('click',()=>revealSource(link)));
  document.querySelectorAll('[data-memory-open]').forEach(link=>link.addEventListener('click',()=>requestAnimationFrame(()=>document.getElementById('memory-state').focus())));
  const restoreSourceHash=()=>{if(window.location.hash==='#original-example' && original && !original.classList.contains('is-expanded'))revealSource(citation);};
  addEventListener('hashchange',restoreSourceHash);restoreSourceHash();
})();
