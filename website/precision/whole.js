/* Fixed fictional sources. No microphone, network, model, storage or real user data. */
const ContinuationDemo = (() => {
  function download(data,lang,state){
    const n=data.note, en=lang==='en', lines=n['lines_'+lang];
    const labels=en?{unselected:'No memory selected',candidate:'Pending candidate — not confirmed',confirmed:'Confirmed memory',inactive:'Inactive memory'}:{unselected:'尚未选择保留内容',candidate:'待确认候选，尚未确认',confirmed:'已确认记忆',inactive:'已停用记忆'};
    const out=['# '+n['title_'+lang],'','fictional_demo: true','source: '+n.kind+'/'+n.id,'content_version: '+n.version,'date: '+n.date+' '+n.time,'','## '+(en?'Original source':'完整原文'),'',...lines,'','## '+(en?'Selection in this webpage':'当前网页选择'),'',labels[state]];
    if(state!=='unselected')out.push('',data.memory['text_'+lang],'','quote: '+lines[data.memory.quote_index],'source: '+n.kind+'/'+n.id,'content_version: '+n.version);
    out.push('',en?'Synthetic webpage example, not a client export, audio file or backup.':'网页合成示例，不是客户端导出、原音文件或备份包。','');return out.join('\n');
  }
  function revealTop(current,choiceTop,decisionBottom,height){
    const lower=decisionBottom-height+32,upper=choiceTop-24;
    return Math.max(0,lower<=upper?Math.max(lower,Math.min(current,upper)):upper);
  }
  return {download,revealTop};
})();
if(typeof module!=='undefined')module.exports=ContinuationDemo;
(() => {
  if(typeof document==='undefined')return;
  const scene=document.querySelector('[data-material-scene]');if(!scene)return;
  const data=JSON.parse(document.getElementById('scenario-data').textContent), lang=document.documentElement.lang.startsWith('en')?'en':'zh';
  const reduced=matchMedia('(prefers-reduced-motion: reduce)'),desktop=matchMedia('(min-width: 900px) and (min-height: 700px)');
  const clamp=n=>Math.max(0,Math.min(1,n)),smooth=n=>{n=clamp(n);return n*n*(3-2*n);};
  const visible=(el,on)=>{el.hidden=!on;el.toggleAttribute('inert',!on);el.setAttribute('aria-hidden',String(!on));};
  const handles=[...scene.querySelectorAll('[data-material-select]')],details=[...scene.querySelectorAll('[data-material-detail]')];
  scene.classList.add('is-ready');
  function material(kind,focus=false){handles.forEach(b=>b.setAttribute('aria-pressed',String(b.dataset.materialSelect===kind)));details.forEach(d=>{const on=d.dataset.materialDetail===kind;visible(d,on);if(on&&focus)d.focus({preventScroll:true});});scene.dataset.selection=kind;scene.querySelector('[data-material-return]').hidden=kind==='note';}
  scene.querySelector('[data-material-return]').addEventListener('click',()=>material('note',true));
  handles.forEach(b=>{b.hidden=false;b.addEventListener('click',()=>material(b.dataset.materialSelect,true));});material('note');
  const work=document.getElementById('workbench'),source=document.getElementById('work-source'),steps=[...work.querySelectorAll('[data-command-step]')],output=work.querySelector('[data-command-output]');
  work.classList.add('is-ready');work.querySelector('.source-nojs').hidden=true;
  let selected=0,manualAt=null,manualOffset=0,queued=false;
  const progress=()=>{const m=work.querySelector('.workbench-motion'),travel=m.offsetHeight-innerHeight;return travel>0?clamp(-m.getBoundingClientRect().top/travel):0;};
  const targets=[.12,.4,.7];
  function select(step,{manual=false,focus=false}={}){
    selected=step;steps.forEach((b,i)=>b.setAttribute('aria-pressed',String(i===step)));output.textContent=steps[step].dataset.command;
    work.querySelectorAll('[data-command-result]').forEach(el=>{const on=Number(el.dataset.commandResult)===step;if(!on&&el.contains(document.activeElement))steps[step].focus({preventScroll:true});visible(el,on);});
    work.dataset.step=String(step);if(manual){manualAt=scrollY;manualOffset=targets[step]-progress();}if(focus)source.focus({preventScroll:true});
  }
  steps.forEach((b,i)=>{b.hidden=false;b.addEventListener('click',()=>select(i,{manual:true}));});
  [['[data-show-query]',1],['[data-show-read]',2]].forEach(([selector,step])=>{const b=work.querySelector(selector);b.hidden=false;b.addEventListener('click',()=>select(step,{manual:true,focus:true}));});
  function paint(){
    const enabled=desktop.matches&&!reduced.matches;
    scene.classList.toggle('is-pinned',enabled);
    const travel=innerHeight*.65, p=enabled?clamp(-scene.getBoundingClientRect().top/travel):0, transition=smooth((p-.22)/.33);
    scene.style.setProperty('--material-progress',transition);scene.style.setProperty('--material-background',1-smooth(transition/.75));const supporting=enabled?smooth((transition-.65)/.25):1;scene.style.setProperty('--material-supports',supporting);
    for(const handle of handles.filter(h=>['input','document'].includes(h.dataset.materialSelect))){const on=!enabled||supporting>.02;if(!on&&handle===document.activeElement)details.find(d=>!d.hidden).focus({preventScroll:true});handle.toggleAttribute('inert',!on);handle.setAttribute('aria-hidden',String(!on));}
    const noteHandle=scene.querySelector('.note-handle');const showNote=!enabled||transition>.02;
    if(!showNote&&noteHandle===document.activeElement)details.find(d=>!d.hidden).focus({preventScroll:true});noteHandle.toggleAttribute('inert',!showNote);noteHandle.setAttribute('aria-hidden',String(!showNote));
    const forward=scene.querySelector('.material-progress'),showForward=!enabled||transition>.02;forward.toggleAttribute('inert',!showForward);forward.setAttribute('aria-hidden',String(!showForward));
    let pinned=enabled;work.classList.toggle('is-pinned',pinned);const stage=work.querySelector('.workbench-stage');
    if(pinned&&stage.scrollHeight>innerHeight+2){pinned=false;work.classList.remove('is-pinned');}
    if(pinned&&work.dataset.anchorNavigation!=='true'){
      const m=work.querySelector('.workbench-motion'),r=m.getBoundingClientRect();
      if(r.top<=0&&r.bottom>=innerHeight&&m.offsetHeight>innerHeight){const q=clamp(progress()+manualOffset);if(manualAt===null||Math.abs(scrollY-manualAt)>40){manualAt=null;select(q<.25?0:q<.55?1:2);}}
    }queued=false;
  }
  addEventListener('scroll',()=>{if(!queued){queued=true;requestAnimationFrame(paint);}},{passive:true});addEventListener('resize',paint);reduced.addEventListener('change',paint);desktop.addEventListener('change',paint);
  function route(event){event.preventDefault();const step=Number(event.currentTarget.dataset.routeStep??2);select(step,{manual:true});history.pushState(null,'','#work-source');source.scrollIntoView({behavior:reduced.matches?'instant':'smooth',block:'center'});source.focus({preventScroll:true});manualAt=null;work.dataset.anchorNavigation='true';}
  document.querySelectorAll('[data-source-route]').forEach(a=>a.addEventListener('click',route));
  const resume=()=>{if(work.dataset.anchorNavigation==='true'){delete work.dataset.anchorNavigation;manualAt=scrollY;manualOffset=targets[selected]-progress();}};
  addEventListener('wheel',resume,{passive:true});addEventListener('touchstart',resume,{passive:true});addEventListener('keydown',e=>{if(['PageDown','PageUp','ArrowDown','ArrowUp','Home','End',' '].includes(e.key))resume();});
  const memorySection=document.getElementById('memory'),decision=document.querySelector('.memory-decision'),choice=document.querySelector('[data-scope-select]'),choiceLabel=document.querySelector('[data-scope-label]'),action=document.querySelector('[data-memory-action]'),reset=document.querySelector('[data-memory-reset]'),status=document.querySelector('[data-memory-status]');
  let memoryState='unselected';memorySection.classList.add('is-ready');choice.hidden=false;document.querySelector('.scope-nojs').hidden=true;
  function memory(state){
    memoryState=state;memorySection.classList.toggle('has-selection',state!=='unselected');decision.dataset.memoryState=state;memorySection.dataset.memoryState=state;action.dataset.state=state;status.textContent=status.dataset[state];
    const chosen=state!=='unselected';choice.setAttribute('aria-pressed',String(chosen));choice.disabled=state==='confirmed'||state==='inactive';choiceLabel.textContent=chosen?choiceLabel.dataset.selected:choiceLabel.dataset.idle;
    visible(document.querySelector('[data-candidate-content]'),chosen);document.querySelector('[data-memory-empty]').hidden=chosen;action.hidden=!chosen;reset.hidden=!chosen;
    action.textContent=state==='candidate'?action.dataset.confirm:state==='confirmed'?action.dataset.deactivate:action.dataset.restore;
    document.querySelectorAll('[data-memory-badge]').forEach(b=>{b.textContent=b.dataset[state];b.dataset.memoryState=state;});
    document.querySelector('[data-export-status]').textContent=status.dataset[state];document.querySelector('[data-export-memory]').textContent=chosen?data.memory['text_'+lang]:(lang==='zh'?'仅保留完整原文，尚未选择记忆。':'Full original only. No memory selected.');
  }
  choice.addEventListener('click',()=>{if(memoryState==='unselected'){memory('candidate');requestAnimationFrame(()=>{const c=choice.getBoundingClientRect(),d=decision.getBoundingClientRect(),bottom=d.bottom??d.top+d.height;scrollTo({top:ContinuationDemo.revealTop(scrollY,scrollY+c.top,scrollY+bottom,innerHeight),behavior:reduced.matches?'instant':'smooth'});status.focus({preventScroll:true});});}});
  action.addEventListener('click',()=>{if(memoryState==='unselected')return;memory(memoryState==='candidate'?'confirmed':memoryState==='confirmed'?'inactive':'confirmed');});
  reset.addEventListener('click',()=>{memory('unselected');choice.focus({preventScroll:true});});
  document.querySelector('.memory-route').addEventListener('click',e=>{e.preventDefault();history.pushState(null,'','#memory');memorySection.scrollIntoView({behavior:reduced.matches?'instant':'smooth',block:'start'});choice.focus({preventScroll:true});});
  const download=document.querySelector('[data-download-demo]');download.hidden=false;
  download.addEventListener('click',()=>{
    const blob=new Blob([ContinuationDemo.download(data,lang,memoryState)],{type:'text/markdown;charset=utf-8'}),url=URL.createObjectURL(blob),a=document.createElement('a');a.href=url;a.download='sound-walk-demo.md';document.body.appendChild(a);a.click();a.remove();setTimeout(()=>URL.revokeObjectURL(url),1000);
    document.querySelector('[data-download-status]').textContent=lang==='zh'?'已准备当前合成示例文件。':'Current synthetic example file prepared.';
  });
  const hash=()=>{if(location.hash==='#work-source'){select(2,{manual:true});work.dataset.anchorNavigation='true';source.scrollIntoView({behavior:'instant',block:'center'});source.focus({preventScroll:true});}};
  addEventListener('popstate',hash);addEventListener('hashchange',hash);memory('unselected');select(0);paint();hash();
})();
