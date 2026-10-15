// P280: 「撤销清空」 is never shown once it can no longer work; the `/clear` suggestion does not linger after sending;
// the state-change lines the owner reads are short (one or two phone lines) in both languages.
import {test} from 'node:test';
import assert from 'node:assert/strict';
import {readFileSync} from 'node:fs';
import vm from 'node:vm';
const source=readFileSync(new URL('../public/js/relay.js',import.meta.url),'utf8');
const fn=source.slice(source.indexOf('function paintCmdx(p)'),source.indexOf('function setOm(open)'));
function render(p,turns){
 const box={children:[],replaceChildren(){this.children=[];},appendChild(b){this.children.push(b);}};
 const slashes=[];
 const ctx=vm.createContext({el:()=>box,C:{myDev:()=>'owner',api:{slash:(...a)=>slashes.push(a)}},t:k=>k,hist:{turns},
   document:{createElement:tag=>({tag,dataset:{},addEventListener(k,f){this.click=f;}})},sendApp:()=>Promise.resolve(),p});
 vm.runInContext(fn+'; paintCmdx(p);',ctx);return {box,slashes};
}
const clear={id:5,end:'done',source:{k:'cmd',dev:'owner'},source:{k:'cmd'},card:{cmd:'clear',undo:true}};
test('undo is offered while the new conversation has not started',()=>{
 for(const later of [[],[{id:6,source:{k:'cmd'},card:{cmd:'context'}}],[{id:6,source:{k:'sys'}}]]){
  const {box,slashes}=render(clear,[clear,...later]);
  assert.equal(box.hidden,false);assert.equal(box.children.length,1);
  assert.equal(box.children[0].className,'cmdundo');assert.equal(box.children[0].textContent,'cmd.undo');
  box.children[0].click();assert.deepEqual(slashes,[['undo_clear']]);
 }
});
test('the first message after the clear turns the button into a plain status line',()=>{
 for(const k of ['phone','telegram','host','agent']){
  const {box}=render(clear,[clear,{id:7,source:{k}}]);
  assert.equal(box.children.length,1,k);
  assert.equal(box.children[0].tag,'span',k);assert.equal(box.children[0].className,'cmdundogone');
  assert.equal(box.children[0].textContent,'cmd.undoGone');assert.equal(box.children[0].click,undefined,'not a button');
 }
});
test('an older clear card or an undone one offers nothing',()=>{
 const undone={id:8,source:{k:'cmd'},card:{cmd:'undo_clear'}};
 assert.equal(render(clear,[clear,undone]).box.children.length,0);
});
test('sending settles the field and closes the suggestion list',()=>{
 const settle=source.slice(source.indexOf('function settleField(text){'));
 assert.match(settle.slice(0,settle.indexOf('\n}')),/closeSug\(\)/);
});
const zh=JSON.parse(readFileSync(new URL('../i18n/web.zh.json',import.meta.url),'utf8'));
const en=JSON.parse(readFileSync(new URL('../i18n/web.en.json',import.meta.url),'utf8'));
const get=(d,k)=>k in d?d[k]:k.split('.').reduce((a,p)=>a?.[p],d);
// The P280 rewrite list (REPORT-280 table); limits = humane.json's (zh 40 chars, en 110).
const KEYS=['cmd.undoGone','cmd.clearTitle','cmd.clearYes','r.run.clear','r.emptyReady','r.replyPending','r.replyNoEcho',
 'r.replyCompacting','r.replyStopped','models.changed','r.pill.queued','r.appr.st.timeout','ver.title','ver.body','r.queue.card',
 'r.shared.following.claude','r.shared.following.opencode','r.shared.following','r.say.withdrawn','elev.r.timeout',
 'lt.done.ready','lt.done.enabled','lt.done.cancelled','lt.done.expired'];
test('the rewritten state lines exist in both languages and stay within two phone lines',()=>{
 for(const k of KEYS){
  const z=get(zh,k),e=get(en,k);
  assert.equal(typeof z,'string',k);assert.equal(typeof e,'string',k);
  assert.ok([...z.replace(/\{\w+\}/g,'')].length<=40,`${k} zh too long: ${z}`);
  assert.ok(e.replace(/\{\w+\}/g,'').length<=110,`${k} en too long: ${e}`);
 }
 assert.ok(!get(zh,'r.run.clear').includes('可撤销'),'the command list promises no undo it may not keep');
 assert.ok(!/undoable/i.test(get(en,'r.run.clear')));
});
