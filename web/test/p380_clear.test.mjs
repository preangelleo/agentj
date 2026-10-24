// P380 #96: the optional resume command stays in the original clear card, collapsed by default.
import {test} from 'node:test';
import assert from 'node:assert/strict';
import {readFileSync} from 'node:fs';
import vm from 'node:vm';
const source=readFileSync(new URL('../public/js/relay.js',import.meta.url),'utf8');
const fn=source.slice(source.indexOf('function paintCmdx(p)'),source.indexOf('function setOm(open)'));
function node(tag){return {tag,children:[],open:false,dataset:{},append(...xs){this.children.push(...xs);},appendChild(x){this.children.push(x);},replaceChildren(){this.children=[];},addEventListener(k,f){this[k]=f;}};}
function render(p){
 const box=node('div'),wired=[],slashes=[];
 const ctx=vm.createContext({paintReportActions:()=>false,el:()=>box,C:{myDev:()=>'owner',api:{slash:(...a)=>slashes.push(a)}},t:k=>k,hist:{turns:[p]},
   document:{createElement:node},sendApp:()=>Promise.resolve(),copyable:pre=>{wired.push(pre);const block=node("div");block.appendChild(pre);return block;},p});
 vm.runInContext(fn+'; paintCmdx(p);',ctx);return {box,wired,slashes};
}
const base={id:5,end:'done',source:{k:'cmd'},card:{cmd:'clear',undo:true}};
test('the original clear card contains exactly one default-collapsed command section for each harness and language',()=>{
 const data=JSON.parse(readFileSync(new URL('../../host/agentj/humane.json',import.meta.url),'utf8'));
 for(const [kind,row] of Object.entries(data.clear_resume)) for(const lang of ['zh','en']){
  const resume={title:row[lang],command:row.command+' new-id_123'};
  const {box,wired,slashes}=render({...base,card:{...base.card,resume}});
  assert.equal(box.hidden,false);
  assert.equal(box.children.length,2,'existing undo action plus one command section');
  assert.equal(box.children[0].textContent,'cmd.undo');
  const d=box.children[1];assert.equal(d.tag,'details');assert.equal(d.open,false,kind+' '+lang);
  assert.equal(d.children[0].tag,'summary');assert.equal(d.children[0].textContent,row[lang]);
  const block=d.children[1],pre=block.children[0];
  assert.equal(pre.tag,'pre');assert.equal(pre.children[0].tag,'code');assert.equal(pre.children[0].textContent,resume.command);
  assert.deepEqual(wired,[pre],'the command block uses the shared copy component');assert.deepEqual(slashes,[]);
 }
});
test('unknown harness or pending session id displays no continuation section',()=>{
 for(const resume of [undefined,null,{title:'unknown'},{command:'unsafe'}]){
  const {box}=render({...base,card:{...base.card,resume}});
  assert.equal(box.children.filter(x=>x.tag==='details').length,0);
 }
});
test('a shared clear without undo still shows the optional command on that same card',()=>{
 const {box}=render({...base,card:{cmd:'clear',resume:{title:'Optional',command:'claude --resume id'}}});
 assert.equal(box.children.length,1);assert.equal(box.children[0].tag,'details');assert.equal(box.children[0].open,false);
});
