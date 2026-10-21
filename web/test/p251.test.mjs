import {focusDraft} from '../public/js/composer-focus.js';
import test from 'node:test';
import assert from 'node:assert/strict';
globalThis.window={};
const {followsHost}=await import('../public/js/version-notice.js');
test('P251 automatic follow requires the same newer host and server, and never retries a stale edge',()=>{
 const current='0.20.3a1',next='0.21.0a1';
 assert.equal(followsHost(current,next,next,null),true);
 for(const [host,web,tried]of [[current,next,null],[next,current,null],[next,null,null],[null,next,null],['invalid',next,null],[next,'0.21.1a1',null],[next,next,next],[current,current,null]])
  assert.equal(followsHost(current,host,web,tried),false,JSON.stringify({host,web,tried}));
});
import vm from 'node:vm';
import {readFileSync} from 'node:fs';
const uiSrc=readFileSync(new URL('../public/js/ui.js',import.meta.url),'utf8');
const flashSrc=uiSrc.match(/export function notifyOnce[\s\S]*?\n\}\n/)[0].replace(/^import .*;\n/gm,'').replaceAll('export ','');
const src=flashSrc+readFileSync(new URL('../public/js/version-notice.js',import.meta.url),'utf8').replace(/^import .*;$/gm,'').replace(/^import .*;\n/gm,'').replaceAll('export ','');
function fixture(seed={}){
 let now=1000,blocked=false,saveOK=true,version='0.21.0a1',refreshes=0,saves=0;
 const notices=[];
 const listeners={},nodes={},storage=new Map(Object.entries(seed)),timers=[];
 for(const id of ['version-notice','version-notice-text','version-refresh','version-dismiss','version-later'])nodes[id]={hidden:true,addEventListener(){}};
 const ctx={VERSION:{version:'0.20.3a1'},window:{},location:{origin:'https://m.agentj.app'},URL,Response,AbortSignal,
  notify:spec=>notices.push(spec),Date:{now:()=>now},setInterval(){},setTimeout:(f,ms)=>{timers.push({f,ms});return timers.length;},clearTimeout(){},t:(key,args)=>key+JSON.stringify(args||{}),onLang(){},registerLayer(){},
  localStorage:{getItem:k=>storage.get(k)||null,setItem:(k,v)=>storage.set(k,v),removeItem:k=>storage.delete(k)},
  document:{hidden:false,getElementById:id=>nodes[id],addEventListener:(k,f)=>listeners[k]=f},
  fetch:async()=>new Response(JSON.stringify({v:1,version})),
 };
 vm.runInNewContext(src+'\nthis.api={initVersionNotice,setHostVersion,follow,render};',ctx);
 ctx.api.initVersionNotice(async opts=>{if(await opts.beforeReload())refreshes++;},{blocked:()=>blocked,save:async()=>{saves++;return saveOK;}});
 ctx.api.setHostVersion(version);
 return {ctx,nodes,storage,timers,notices,advance:ms=>{now+=ms;},block:v=>{blocked=v;},save:v=>{saveOK=v;},serve:v=>{version=v;},event:k=>listeners[k]({type:k}),counts:()=>({refreshes,saves}),flush:async()=>{await new Promise(r=>setImmediate(r));}};
}
test('P251 idle guard: typing, IME, hidden page, protected work and failed draft storage postpone the same automatic follow',async()=>{
 const f=fixture();await f.flush();assert.equal(f.nodes['version-notice'].hidden,true);
 await f.ctx.api.follow();assert.equal(f.counts().refreshes,0);
 f.advance(10000);f.event('input');await f.ctx.api.follow();assert.equal(f.counts().refreshes,0);
 f.advance(10000);f.event('compositionstart');f.advance(20000);await f.ctx.api.follow();assert.equal(f.counts().refreshes,0);
 f.event('compositionend');f.advance(10000);f.block(true);await f.ctx.api.follow();assert.equal(f.counts().refreshes,0);
 f.block(false);f.ctx.document.hidden=true;await f.ctx.api.follow();assert.equal(f.counts().refreshes,0);
 f.ctx.document.hidden=false;f.save(false);await f.ctx.api.follow();assert.equal(f.counts().refreshes,0);
 f.save(true);await f.ctx.api.follow();assert.equal(f.counts().refreshes,1);assert.ok(f.counts().saves>=3);
 await f.ctx.api.follow();assert.equal(f.counts().refreshes,1,'stale edge is tried only once');
 assert.equal(f.storage.get('aj.verUpdated'),'0.21.0a1');
 assert.ok([...f.storage.values()].every(v=>!v.includes('draft')),'only public version metadata in localStorage');
});
test('P251 final metadata mismatch cancels reload; web-first deployment remains an owner prompt',async()=>{
 const f=fixture();await f.flush();f.advance(10000);f.serve('0.21.1a1');await f.ctx.api.follow();assert.equal(f.counts().refreshes,0);
 f.ctx.api.setHostVersion('0.20.3a1');f.ctx.api.render();assert.equal(f.nodes['version-notice'].hidden,false);
});

const relaySrc=readFileSync(new URL('../public/js/relay.js',import.meta.url),'utf8');
const blockedSource=relaySrc.match(/export function refreshBlocked\(\)\{[\s\S]*?\n\}/)[0].replace('export ','');
test('P251 refresh guard protects recording, sends, attachments, selections, approval holds and card editing',()=>{
 const base={C:{connected:()=>true},ptt:false,asrBusy:false,sending:false,atts:[],quotes:[],askBusy:false,apprBusy:false,apprHold:false,
  askState:()=> 'closed',askSel:{id:null,picks:[]},cur:null,input:{},document:{querySelectorAll:()=>[],activeElement:null},getComputedStyle:()=>({visibility:'visible'})};
 const blocked=changes=>vm.runInNewContext(blockedSource+';refreshBlocked()', {...base,...changes});
 assert.equal(blocked({}),false);
 for(const key of ['ptt','asrBusy','sending','askBusy','apprBusy','apprHold'])assert.equal(blocked({[key]:true}),true,key);
 for(const change of [{C:{connected:()=>false}},{atts:[{}]},{quotes:[{}]},
  {document:{body:{dataset:{view:'cam'}},querySelectorAll:()=>[],activeElement:null}},
  {document:{body:{dataset:{view:'scan'}},querySelectorAll:()=>[],activeElement:null}},
  {askState:()=> 'open',cur:{ask:{id:1}},askSel:{id:1,picks:[['choice']]}},
  {document:{querySelectorAll:()=>[{getClientRects:()=>[{}]}],activeElement:null}},
  {document:{querySelectorAll:()=>[],activeElement:{matches:()=>true}}}])assert.equal(blocked(change),true);
});

test('P251 hiding a non-modal version notice preserves editing focus; modal restoration remains intact',()=>{
 const layerSrc=readFileSync(new URL('../public/js/layers.js',import.meta.url),'utf8').replace(/^import .*;\n/gm,'').replaceAll('export ','');
 for(const [trap,preserveOutsideFocus] of [[false,true],[true,true],[false,false]]){
  let open=true;
  const body={isConnected:true,getClientRects:()=>[{}],closest:()=>null,matches:()=>false};
  const doc={activeElement:body};
  const node=()=>({isConnected:true,getClientRects:()=>[{}],closest:()=>null,matches:()=>true,focus(){doc.activeElement=this;}});
  const input=node(),fallback=node();const box={contains:()=>false};
  const ctx={focusDraft,document:doc};vm.runInNewContext(layerSrc+';this.reg=registerLayer;this.flush=sync;',ctx);
  ctx.reg({element:()=>box,open:()=>open,close(){},fallback:()=>fallback,trap,preserveOutsideFocus});
  doc.activeElement=input;open=false;ctx.flush();assert.equal(doc.activeElement,!trap&&preserveOutsideFocus?input:fallback);
 }
});

test('P425 #60 updated uses shared Noir notification once per version, never the header line',async()=>{
 const f=fixture({'aj.verUpdated':'0.20.3a1'});await f.flush();
 assert.equal(f.nodes['version-later'].hidden,true);
 assert.equal(f.storage.has('aj.verUpdated'),false);
 assert.equal(f.notices.length,1);
 assert.equal(f.notices[0].kind,'success');assert.equal(f.notices[0].shape,'rect');assert.equal(f.notices[0].ms,3000);
 assert.match(f.notices[0].text,/ver\.updated/);
 f.ctx.api.render();assert.equal(f.notices.length,1);
 const g=fixture({'aj.verUpdated':'0.20.3a1','aj.verUpdatedSeen.0.20.3a1':'1'});await g.flush();assert.equal(g.notices.length,0);
 const h=fixture({'aj.verUpdated':'0.19.0a1'});await h.flush();assert.equal(h.notices.length,0);
});
