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
  const desktop = window.matchMedia('(min-width: 761px)');
  document.querySelectorAll('[data-capture-demo]').forEach(button => {
    button.hidden = false;
    let started=0, elapsed=0, clock;
    const counter=button.closest('[data-capture]').querySelector('[data-counter]');
    const drawTime=()=>{const seconds=Math.floor(elapsed/1000);counter.textContent=String(Math.floor(seconds/60)).padStart(2,'0')+':'+String(seconds%60).padStart(2,'0');};
    button.addEventListener('click', () => {
      const stage = button.closest('[data-capture]');
      const state = TraceDemo.nextCapture(stage.dataset.capture);
      stage.dataset.capture = state;
      const label = stage.querySelector('.capture-label');
      label.textContent = label.dataset[state];
      stage.querySelector('[data-capture-feedback]').textContent = stage.querySelector('[data-capture-feedback]').dataset[state] || '';
      clearInterval(clock);
      if(state==='recording'){started=Date.now();elapsed=0;clock=setInterval(()=>{elapsed=Date.now()-started;drawTime();},200);}
      else if(state==='saved'){elapsed=Date.now()-started;}else{elapsed=0;}
      drawTime();
      button.textContent = button.dataset[state === 'idle' ? 'start' : state === 'recording' ? 'stop' : 'again'];
    });
  });
  const stories = [...document.querySelectorAll('[data-pinned-story]')];
  const thresholds = [0, .5, .82];
  let ticking = false;
  const update = () => {
    stories.forEach(section => {
      const pin = desktop.matches && !reduced.matches;
      section.classList.toggle('is-pinned', pin);
      const travel = section.offsetHeight - innerHeight;
      const progress = travel > 0 ? Math.max(0, Math.min(1, -section.getBoundingClientRect().top / travel)) : 0;
      const index = TraceDemo.stepAt(progress);
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
      scrollTo({top:start+(section.offsetHeight-innerHeight)*(thresholds[index]+(index ? .004 : 0)),behavior:'smooth'});
    });
  }));
  addEventListener('scroll', () => {if(!ticking){ticking=true;requestAnimationFrame(update);}}, {passive:true});
  addEventListener('resize',update); reduced.addEventListener('change',update); update();
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
    reduced.addEventListener('change',()=>{if(reduced.matches)paused=true;schedule();});
    if('IntersectionObserver' in window)new IntersectionObserver(entries=>{visible=entries[0].isIntersecting;schedule();},{threshold:.25}).observe(carousel);
    schedule();
  });
  document.querySelectorAll('[data-filter-controls]').forEach(controls=>{
    controls.hidden=false;const rows=controls.parentElement.querySelectorAll('[data-source-kind]');
    controls.querySelectorAll('[data-filter]').forEach(button=>button.addEventListener('click',()=>{
      controls.querySelectorAll('[data-filter]').forEach(b=>b.setAttribute('aria-pressed',String(b===button)));
      rows.forEach(row=>row.hidden=!TraceDemo.sourceVisible(button.dataset.filter,row.dataset.sourceKind));
    }));
  });
  document.querySelectorAll('[data-confirm-memory]').forEach(button=>{
    button.hidden=false;
    const reset=button.parentElement.querySelector('[data-memory-reset]');reset.hidden=false;
    const status=button.parentElement.querySelector('[data-memory-status]');
    const render=state=>{button.dataset.state=state;button.setAttribute('aria-pressed',String(state==='confirmed'));button.textContent=button.dataset[state==='candidate'?'confirm':state==='confirmed'?'deactivate':'restore'];status.textContent=status.dataset[state];};
    button.addEventListener('click',()=>render(TraceDemo.nextMemory(button.dataset.state)));
    reset.addEventListener('click',()=>render('candidate'));
  });
  const cliButtons=[...document.querySelectorAll('[data-cli-step]')];
  const original=document.getElementById('original-example');
  if(original)original.classList.add('is-ready');
  cliButtons.forEach(button=>{button.hidden=false;button.addEventListener('click',()=>{
    cliButtons.forEach(other=>other.setAttribute('aria-pressed',String(other===button)));
    document.querySelector('[data-cli-command]').textContent=button.dataset.command;
    document.querySelectorAll('[data-cli-result]').forEach(panel=>{panel.hidden=panel.dataset.cliResult!==button.dataset.cliStep;});
    original.classList.toggle('is-expanded',button.dataset.cliStep==='2');
    original.classList.remove('is-cited');
  });});
  const citation=document.querySelector('[data-citation]');
  if(citation){citation.hidden=false;document.querySelector('.citation-fallback').hidden=true;citation.addEventListener('click',()=>{
    const source=document.getElementById('original-example'),line=source.querySelector('[data-cited-line]');
    source.scrollTo({top:Math.max(0,line.offsetTop-source.offsetTop-45),behavior:reduced.matches?'instant':'smooth'});
    source.classList.add('is-cited','is-expanded');source.focus({preventScroll:true});
  });}
})();
