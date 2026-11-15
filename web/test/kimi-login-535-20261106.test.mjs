// Standalone recovery from8d69c1be50; old shared PrimaryKey extension excluded.
import {test} from 'node:test';
import assert from 'node:assert/strict';
import {readFileSync} from 'node:fs';
import vm from 'node:vm';
import {retainedUiSource} from './release-ui-source.mjs';
globalThis.window = {};
const {authOf} = await import('../public/js/authcard.js');
function fixture() {
  let now = 1000;
  const ticks = new Set(), nodes = new Map(), sent = [];
  class Node {
    constructor() {this.children=[];this.dataset={};this.attrs={};}
    setAttribute(k,v){this.attrs[k]=v;}
    append(...xs){this.children.push(...xs);}
    replaceChildren(...xs){this.children=[...xs];}
    addEventListener(event,fn){if(event==='click')this.click=fn;}
    getClientRects(){return [];}
    get textContent(){return this.text||this.children.map(x=>x.textContent||'').join(' ');}
    set textContent(x){this.text=x;}
  }
  for(const id of ['ar-list','ar-flow','acct-flow','ar-msg']){const n=new Node();n.id=id;nodes.set(id,n);}
  const ctx = vm.createContext({Date:{now:()=>now},document:{createElement:()=>new Node(),createTextNode:s=>{const n=new Node();n.textContent=s;return n;},addEventListener(){}},localStorage:{getItem:()=>''},
    notify(){},t:x=>x,fillText:(n,s)=>n.textContent=s,el:id=>nodes.get(id),copyable(){},copyNow(){},reopenAuth(){},authOf,ask1:async m=>{sent.push(m);return {t:'auth_res',ok:true};},isReady:()=>true,
    setInterval:fn=>{ticks.add(fn);return fn;},clearInterval:fn=>ticks.delete(fn),clearTimeout:fn=>ticks.delete(fn),setTimeout:fn=>{ticks.add(fn);return fn;},loginSheet(){}});
  const source=retainedUiSource('public/js/authrescue.js').replace(/^import .*;.*$/mg,'').replace(/export /g,'');
  vm.runInContext(source+';globalThis.api={onPush,close};',ctx);
  return {sent,push:v=>ctx.api.onPush({view:v}),text:()=>nodes.get('ar-list').textContent,flow:()=>nodes.get('ar-flow').textContent,
    tick:ms=>{now+=ms;for(const f of [...ticks])f();}};
}
const view = state => ({harnesses:[{kind:'kimi',label:'Kimi Code',installed:true,state}],flow:null});
test('#535 repeated checking pushes cannot keep login status pending forever',()=>{
  const f=fixture();f.push(view('checking'));assert.match(f.text(),/ar.checking/);
  f.tick(45000);f.push(view('checking'));f.tick(46000);
  assert.doesNotMatch(f.text(),/ar.checking/);assert.match(f.text(),/ar.checkTimeout/);assert.match(f.text(),/ar.checkNow/);assert.match(f.text(),/ar.kimiA/);
});
test('#535 completed login checks stay complete; a later check gets a fresh deadline',()=>{
  const f=fixture();f.push(view('checking'));f.tick(1000);f.push(view('ok'));f.tick(100000);
  assert.match(f.text(),/ar.stOk/);assert.doesNotMatch(f.text(),/ar.checkTimeout/);
  f.push(view('checking'));assert.match(f.text(),/ar.checking/);f.tick(91000);assert.match(f.text(),/ar.checkTimeout/);
});
test('#535 Kimi link-only authorization renders a link without a secret-paste step',()=>{
  const f=fixture();f.push({...view('ok'),flow:{kind:'kimi',stage:'device',url:'https://www.kimi.com/code/authorize',code:'',expires_at:9999999999}});
  assert.match(f.flow(),/ar.openLink/);assert.match(f.flow(),/ar.kimiStep1/);assert.doesNotMatch(f.flow(),/ar.deviceStep1/);assert.match(f.flow(),/ar.kimiLinkOnly/);assert.doesNotMatch(f.flow(),/ar.deviceStep2|ar.urlStep2/);
});

test('#535 Kimi authorization accepts only the supported official origins',()=>{
 const base={kind:'kimi',url:'https://auth.kimi.com/device',code:'',expires_at:2000000000,round:1,rounds:1};
 for(const host of ['auth.kimi.com','auth.kimi.ai','www.kimi.com','www.kimi.ai','kimi.com','kimi.ai'])assert.ok(authOf({...base,url:'https://'+host+'/device'}),host);
 for(const url of ['https://evil.kimi.com/device','https://kimi.com.evil.test/device','http://auth.kimi.com/device','https://user@auth.kimi.com/device','https://auth.kimi.com:444/device','https://auth.kimi.com/device#secret'])assert.equal(authOf({...base,url}),null,url);
});
test('#535 current Kimi chat entry refuses before mounting or sending',async()=>{
 const {AGENTS}=await import('../public/js/selfheal.js');
 assert.equal(AGENTS.includes('kimi'),false,'chat support is not enabled by this Settings repair');
 const ctx=vm.createContext({AGENTS,document:{createElement(){throw Error('unsupported agent must not mount');}},t:x=>x});
 const source=readFileSync(new URL('../public/js/loginsheet.js',import.meta.url),'utf8').replace(/^import .*;.*$/mg,'').replace(/export /g,'');
 vm.runInContext(source+';globalThis.api={open,state:()=>S};',ctx);
 ctx.api.open('kimi');assert.equal(ctx.api.state().st,'idle');assert.equal(ctx.api.state().agent,'');
});
