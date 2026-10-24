/* A non-rendering event/DOM harness. This does not replace browser QA. */
const fs=require('node:fs');
const vm=require('node:vm');
const path=require('node:path');
const {execFileSync}=require('node:child_process');
const root=path.resolve(__dirname,'..');
const parse=`import json,sys
from html.parser import HTMLParser
class P(HTMLParser):
 def __init__(self):
  super().__init__();self.root={'tag':'document','attrs':{},'children':[],'text':''};self.stack=[self.root]
 def handle_starttag(self,t,a):
  n={'tag':t,'attrs':dict(a),'children':[],'text':''};self.stack[-1]['children'].append(n)
  if t not in ['img','source','link','meta','br','hr','input']:self.stack.append(n)
 def handle_data(self,data):
  for n in self.stack:n['text']+=data
 def handle_endtag(self,t):
  for i in range(len(self.stack)-1,0,-1):
   if self.stack[i]['tag']==t:self.stack=self.stack[:i];break
p=P();p.feed(open(sys.argv[1]).read());print(json.dumps(p.root))`;
const html=JSON.parse(execFileSync('python3',['-c',parse,path.join(root,'dist/index.html')],{maxBuffer:2000000}));
class Hub{
 constructor(){this.listeners={};}
 addEventListener(type,fn){(this.listeners[type]??=[]).push(fn);}
 emit(type,extra={}){const event={type,target:this,preventDefault(){this.defaultPrevented=true;},...extra};for(const fn of this.listeners[type]||[])fn(event);return event;}
}
const camel=s=>s.replace(/-([a-z])/g,(_,c)=>c.toUpperCase());
class Element extends Hub{
 constructor(raw,doc,parent){super();this.tagName=raw.tag.toUpperCase();this.attrs={...raw.attrs};this.parentElement=parent;this.doc=doc;this.dataset={};for(const [k,v] of Object.entries(this.attrs))if(k.startsWith('data-'))this.dataset[camel(k.slice(5))]=v||'';this.hidden='hidden' in this.attrs;this.textContent=raw.text||'';this.scrollTop=0;this.clientTop=0;this.offsetTop=0;this.offsetHeight=2498;this.rect={top:0,left:0,height:500};this.children=raw.children.map(n=>new Element(n,doc,this));this.classList={contains:c=>(this.attrs.class||'').split(/\s+/).includes(c),toggle:(c,on)=>{const set=new Set((this.attrs.class||'').split(/\s+/).filter(Boolean));const yes=on??!set.has(c);yes?set.add(c):set.delete(c);this.attrs.class=[...set].join(' ');return yes;},add:(...cs)=>cs.forEach(c=>this.classList.toggle(c,true)),remove:(...cs)=>cs.forEach(c=>this.classList.toggle(c,false))};}
 matches(selector){if(selector.startsWith('.'))return this.classList.contains(selector.slice(1));if(selector.startsWith('#'))return this.attrs.id===selector.slice(1);const m=selector.match(/^\[([^=\]]+)(?:=["']?([^"'\]]+)["']?)?\]$/);if(m)return m[1] in this.attrs&&(m[2]===undefined||this.attrs[m[1]]===m[2]);return this.tagName.toLowerCase()===selector;}
 querySelectorAll(s){const out=[];for(const child of this.children){if(child.matches(s))out.push(child);out.push(...child.querySelectorAll(s));}return out;}
 querySelector(s){return this.querySelectorAll(s)[0]||null;}
 closest(s){return this.matches(s)?this:this.parentElement?.closest(s);}
 contains(e){return e===this||this.children.some(c=>c.contains(e));}
 setAttribute(k,v){this.attrs[k]=String(v);if(k.startsWith('data-'))this.dataset[camel(k.slice(5))]=String(v);}
 getAttribute(k){return this.attrs[k]??null;}
 removeAttribute(k){delete this.attrs[k];}
 toggleAttribute(k,yes){yes?this.setAttribute(k,''):this.removeAttribute(k);}
 getBoundingClientRect(){return this.rect;}
 focus(){this.doc.activeElement=this;this.doc.emit('focusin',{target:this});let p=this;while(p){p.emit('focusin',{target:this});p=p.parentElement;}}
 scrollIntoView(options){this.lastView=options;}
 scrollTo(options){this.scrollTop=options.top;this.lastScroll=options;this.scrollWasExpanded=this.classList.contains('is-expanded');}
}
function setup({width=1180,height=757,hash=''}={}){
 const doc=new Hub();const tree=new Element(html,doc,null);doc.querySelectorAll=s=>tree.querySelectorAll(s);doc.querySelector=s=>tree.querySelector(s);doc.getElementById=id=>doc.querySelector('#'+id);doc.hidden=false;doc.activeElement=null;
 const win=new Hub(), intervals=new Map(),timeouts=new Map(),frames=[],medias=[],observers=[];let clock=0,id=0;
 const ctx={document:doc,innerHeight:height,scrollY:0,performance:{now:()=>clock},Date:{now:()=>clock},setInterval:fn=>{intervals.set(++id,fn);return id;},clearInterval:id=>intervals.delete(id),setTimeout:fn=>{timeouts.set(++id,fn);return id;},clearTimeout:id=>timeouts.delete(id),requestAnimationFrame:fn=>{frames.push(fn);return frames.length;},cancelAnimationFrame:()=>{},addEventListener:(...args)=>win.addEventListener(...args),scrollTo:options=>{win.lastScroll=options;}};
 win.location={hash};win.matchMedia=query=>{const media=new Hub();media.media=query;media.matches=query.includes('reduced-motion')?false:width>=761&&(!query.includes('min-height')||height>=700);media.change=value=>{media.matches=value;media.emit('change',{matches:value});};medias.push(media);return media;};
 class Observer{constructor(fn){this.fn=fn;observers.push(this);}observe(element){this.element=element;this.fire(true);}fire(isIntersecting){this.fn([{isIntersecting,intersectionRatio:isIntersecting?1:0,target:this.element}]);}}
 win.IntersectionObserver=Observer;ctx.IntersectionObserver=Observer;ctx.window=win;
 vm.runInNewContext(fs.readFileSync(path.join(root,'assets/landing.js'),'utf8'),ctx);
 return {doc,win,ctx,intervals,timeouts,medias,observers,one:s=>doc.querySelector(s),all:s=>doc.querySelectorAll(s),tick(ms){clock+=ms;for(const fn of [...intervals.values()])fn();},flush(){while(frames.length)frames.shift()();},media:part=>medias.find(x=>x.media.includes(part))};
}
module.exports={setup};
