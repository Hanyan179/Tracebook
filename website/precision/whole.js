/* Fixed synthetic examples only. No service calls, microphone, persistence or model. */
(() => {
  if(typeof document==='undefined')return;
  const index=document.querySelector('.library-index');if(!index)return;
  const reduced=matchMedia('(prefers-reduced-motion: reduce)'),desktop=matchMedia('(min-width: 900px) and (min-height: 700px)');
  const filters=index.querySelector('[data-library-filters]'),rows=[...index.querySelectorAll('[data-library-kind]')],count=index.querySelector('[data-library-status]');
  filters.hidden=false;
  filters.querySelectorAll('button').forEach(button=>button.addEventListener('click',()=>{
    const kind=button.dataset.kind;filters.querySelectorAll('button').forEach(b=>b.setAttribute('aria-pressed',String(b===button)));
    let total=0;rows.forEach(row=>{row.hidden=kind!=='all'&&row.dataset.libraryKind!==kind;if(!row.hidden)total++;});count.textContent=total+' '+count.dataset.label;
  }));
  const work=document.getElementById('workbench'),source=document.getElementById('work-source'),steps=[...work.querySelectorAll('[data-command-step]')],output=work.querySelector('[data-command-output]');
  let selected=0,manualAt=null,manualOffset=0,queued=false,returnTarget=null;
  const progress=()=>{const motion=work.querySelector('.workbench-motion'),travel=motion.offsetHeight-innerHeight;return travel>0?Math.max(0,Math.min(1,-motion.getBoundingClientRect().top/travel)):0;};
  const targets=[.20,.46,.66];
  const select=(step,{manual=false,focus=false}={})=>{
    selected=step;steps.forEach((button,i)=>button.setAttribute('aria-pressed',String(i===step)));output.textContent=steps[step].dataset.command;
    work.querySelectorAll('[data-command-result]').forEach(el=>el.hidden=Number(el.dataset.commandResult)!==step);
    source.classList.toggle('is-cited',step===2);work.dataset.step=String(step);
    [[source.querySelector('[data-source-excerpt]'),step>=1],[source.querySelector('[data-source-full]'),step===2]].forEach(([layer,visible])=>{if(!visible&&layer.contains(document.activeElement))steps[step].focus({preventScroll:true});layer.toggleAttribute('inert',!visible);layer.setAttribute('aria-hidden',String(!visible));layer.classList.toggle('is-revealed',visible);});
    if(manual){manualAt=scrollY;manualOffset=targets[step]-progress();}if(focus)source.focus({preventScroll:true});
  };
  steps.forEach((button,i)=>{button.hidden=false;button.addEventListener('click',()=>select(i,{manual:true}));});
  const citation=work.querySelector('[data-work-citation]');citation.hidden=false;work.querySelector('.work-fallback').hidden=true;
  citation.addEventListener('click',()=>{select(2,{manual:true,focus:true});});
  const paint=()=>{
    let pinned=desktop.matches&&!reduced.matches;work.classList.toggle('is-pinned',pinned);const stage=work.querySelector('.workbench-stage');if(pinned&&stage.scrollHeight>innerHeight+2){pinned=false;work.classList.remove('is-pinned');}
    if(pinned&&work.dataset.anchorNavigation!=='true'){const motion=work.querySelector('.workbench-motion'),travel=motion.offsetHeight-innerHeight,rect=motion.getBoundingClientRect();
      if(rect.top<=0&&rect.bottom>=innerHeight&&travel>0){const p=Math.max(0,Math.min(1,-rect.top/travel+manualOffset));
        if(manualAt===null||Math.abs(scrollY-manualAt)>40){manualAt=null;select(p<.38?0:p<.55?1:2);}
      }
    }queued=false;
  };
  addEventListener('scroll',()=>{if(!queued){queued=true;requestAnimationFrame(paint);}},{passive:true});
  addEventListener('resize',paint);reduced.addEventListener('change',paint);desktop.addEventListener('change',paint);
  function navigateSource(event){
    event.preventDefault();returnTarget=event.currentTarget;select(2,{manual:true});history.pushState(null,'','#work-source');
    source.scrollIntoView({behavior:reduced.matches?'instant':'smooth',block:'center'});source.focus({preventScroll:true});
    // Lock the chosen read state across the explicit anchor scroll, not only its first frame.
    manualAt=null;work.dataset.anchorNavigation='true';
  }
  document.querySelectorAll('[data-source-route]').forEach(a=>a.addEventListener('click',navigateSource));
  // User intent resumes the scroll narrative after an explicit source jump.
  const resume=()=>{if(work.dataset.anchorNavigation==='true'){delete work.dataset.anchorNavigation;manualAt=scrollY;manualOffset=targets[selected]-progress();}};
  addEventListener('wheel',resume,{passive:true});addEventListener('touchstart',resume,{passive:true});
  addEventListener('keydown',event=>{if(['PageDown','PageUp','ArrowDown','ArrowUp','Home','End',' '].includes(event.key))resume();});
  const decision=document.querySelector('.memory-decision'),action=document.querySelector('[data-memory-action]'),reset=document.querySelector('[data-memory-reset]'),status=document.querySelector('[data-memory-status]');
  let memoryState='candidate';
  function memory(state){
    memoryState=state;decision.dataset.memoryState=state;action.dataset.state=state;status.textContent=status.dataset[state];
    action.textContent=state==='candidate'?action.dataset.confirm:state==='confirmed'?action.dataset.deactivate:action.dataset.restore;
    document.querySelectorAll('[data-memory-badge]').forEach(badge=>{badge.textContent=badge.dataset[state];badge.dataset.memoryState=state;});
  }
  action.hidden=false;reset.hidden=false;action.addEventListener('click',()=>memory(memoryState==='candidate'?'confirmed':memoryState==='confirmed'?'inactive':'confirmed'));
  reset.addEventListener('click',()=>memory('candidate'));
  document.querySelector('.memory-route').addEventListener('click',event=>{event.preventDefault();history.pushState(null,'','#memory');document.getElementById('memory').scrollIntoView({behavior:reduced.matches?'instant':'smooth',block:'start'});status.focus({preventScroll:true});});
  const hash=()=>{if(location.hash==='#work-source'){select(2,{manual:true});work.dataset.anchorNavigation='true';source.scrollIntoView({behavior:'instant',block:'center'});source.focus({preventScroll:true});}};
  addEventListener('popstate',hash);addEventListener('hashchange',hash);memory('candidate');select(0);paint();hash();
})();
