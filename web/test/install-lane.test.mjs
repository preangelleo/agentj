import test from 'node:test';
import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import vm from 'node:vm';

class Element {
  constructor(tag, cls='', text='') { this.tag=tag; this.className=cls; this.textContent=text; this.children=[]; this.dataset={}; this.listeners={}; }
  append(...children) { this.children.push(...children); }
  appendChild(child) { this.append(child); return child; }
  replaceChildren(...children) { this.children=children; }
  addEventListener(name,fn) { this.listeners[name]=fn; }
}
const walk=e=>[e,...e.children.flatMap(walk)];
const tick=()=>new Promise(resolve=>setImmediate(resolve));
async function fixture() {
  const nodes=Object.fromEntries(['setup-main','setup-banner','setup-banner-open','setup-banner-x','setup-close'].map(id=>[id,new Element('div')]));
  const writes=[], keys=[]; let finish;
  const context=vm.createContext({ console, document:{getElementById:id=>nodes[id]||null}, localStorage:{getItem:()=>null}, navigator:{}, Date });
  const deps={
    './session.js':{ask1:async()=>null,isReady:()=>true,sendApp:()=>{},randHex:()=> 'a'.repeat(16)},
    './controls.js':{signedWrite:async(action,target,extra)=>{writes.push({action,target,extra}); if(action==='setup_run')return new Promise(r=>{finish=r;}); return {ok:true};},whyText:String},
    './t.js':{t:k=>k,lang:()=> 'en',onLang:()=>{}},
    './ui.js':{mk:(...args)=>new Element(...args),toast:()=>{}},
    './api.js':{keysSet:async name=>{keys.push(name);return {ok:true};}},
  };
  const module=new vm.SourceTextModule(readFileSync(new URL('../public/js/setup.js',import.meta.url),'utf8'),{context});
  await module.link(async name=>{
    const values=deps[name]; assert.ok(values,name);
    return new vm.SyntheticModule(Object.keys(values),function(){for(const [k,v] of Object.entries(values))this.setExport(k,v);},{context});
  });
  await module.evaluate(); module.namespace.configure({view:()=> 'setup',openPanel:()=>{}});
  return {api:module.namespace,nodes,writes,keys,complete:r=>finish(r)};
}

test('owner install button sends fixed signed target, progress and signed cancel',async()=>{
  const f=await fixture();
  f.api.card({revision:7,items:[],counts:{verified:0},current:null});
  walk(f.nodes['setup-main']).find(e=>e.textContent==='Install OpenCode').listeners.click();
  await tick();
  assert.equal(f.writes[0].action,'setup_run');
  assert.equal(JSON.stringify(f.writes[0].target),JSON.stringify({item:'harness',op:'ai.install',params:{cli:'opencode',source:'official'},rev:7}));
  assert.ok(walk(f.nodes['setup-main']).some(e=>String(e.textContent).includes('Installing OpenCode')));
  walk(f.nodes['setup-main']).find(e=>e.textContent==='Cancel installation').listeners.click();
  await tick(); assert.equal(f.writes[1].action,'setup_cancel');
  f.complete({ok:false,why:'denied'}); await tick();
  assert.ok(walk(f.nodes['setup-main']).some(e=>e.textContent==='Install OpenCode'));
});

test('structured preparation is inert text and key action uses existing key card',async()=>{
  const f=await fixture();
  f.api.card({initialization:[{name:'demo-skill',rows:[{stage:'<img onerror=forbidden>',status:'missing',if_missing:'No service',degrade:'Manual'}],config:['voice'],key_requests:[{provider:'openai',name:'OPENAI_API_KEY'}]}]});
  assert.equal(f.nodes['setup-banner'].hidden,false);
  assert.ok(walk(f.nodes['setup-main']).some(e=>String(e.textContent).includes('<img onerror=forbidden>')));
  assert.ok(!walk(f.nodes['setup-main']).some(e=>e.tag==='img'));
  walk(f.nodes['setup-main']).find(e=>e.textContent==='Key card: OPENAI_API_KEY').listeners.click();
  await tick(); assert.deepEqual(f.keys,['OPENAI_API_KEY']); assert.equal(f.writes.length,0);
  f.api.clear(); assert.equal(f.nodes['setup-banner'].hidden,true);
});
