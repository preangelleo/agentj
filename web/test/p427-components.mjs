import assert from 'node:assert/strict';import {mkdirSync,writeFileSync}from'node:fs';import{fileURLToPath}from'node:url';
import{startWebServer}from'./serve.mjs';import{startFakeHost}from'./fakehost.mjs';import{launch,newPage,navigate,waitState,waitFor,evaluate,sleep}from'./browser.mjs';
import contract from '../public/js/components.gen.js';
const out=fileURLToPath(new URL('../../../reports/qa/p427/',import.meta.url));mkdirSync(out,{recursive:true});
const web=await startWebServer({port:0,relayCsp:'ws://127.0.0.1:*'}),fake=await startFakeHost(),B=await launch();const records=[];
const fence=v=>'```agentj\n'+JSON.stringify(v)+'\n```';
const samples=contract.components.filter(x=>x.schema).map(x=>x.example);
try{for(const lang of ['zh','en'])for(const theme of ['light','dark'])for(const width of [390,1280]){
 fake.reset();const p=await newPage(B,width,900,theme,{allow:[web.url,fake.relay+'/']});
 try{
 await navigate(p,fake.newPairing(web.url+'?lang='+lang));await waitState(p,'awaiting-approval');await fake.approve();await waitState(p,'ready');
 await evaluate(p,`(()=>{for(const id of ['confirm-no','a2hs-ok','phone-env-dismiss']){const e=document.getElementById(id);if(e&&e.getBoundingClientRect().height)e.click();}})()`);
 await fake.send({t:'status',s:'idle',agent:'claude',name:'Agent J'});
 const fixtures=structuredClone(samples);
 fixtures.find(x=>x.component==='chart').kind=theme==='dark'?'line':'bar';
 fixtures.find(x=>x.component==='chart').items=[{label:'Up',value:60},{label:'Down',value:-40},{label:'Flat',value:0}];
 fixtures.find(x=>x.component==='form').fields.push({id:'choice',label:'Choice',type:'select',options:['A','B']},{id:'notify',label:'Notify',type:'checkbox'},{id:'day',label:'Day',type:'date'});
 fixtures.find(x=>x.component==='flowchart').nodes[0].label='检查库存并整理供应商报价之后生成采购清单';
 const tx=fixtures.map(fence).join('\n\n')+'\n\n```agentj\n{"component":"html","html":"<img src=x onerror=alert(1)>"}\n```\n\n[bad](javascript:alert(1))\n\n'+fence(samples[3]).split('\n').map(x=>'> '+x).join('\n');
 const turn=await fake.addTurn({k:'phone',dev:'x',text:'Show the reply components'},tx,'open');
 await waitFor(p,`document.querySelectorAll('#words .reply-component').length===5`);
 assert.equal(await evaluate(p,`[...document.querySelectorAll('#words .rc-draft')].every(x=>x.disabled)`),true,'streaming has no live actions');
 await fake.updateTurn(turn.id,{end:'done'});await waitFor(p,`document.querySelector('#words .rc-draft').disabled===false`);
 assert.equal(await evaluate(p,`document.querySelectorAll('#words a[href^="javascript:"]').length`),0);
 assert.equal(await evaluate(p,`document.querySelectorAll('#words .codeblock').length>=2`),true,'invalid and quoted block stay text');
 assert.equal(await evaluate(p,`!!document.querySelector('#words [data-component=chart] polyline')`),theme==='dark');
 assert.equal(await evaluate(p,`[...document.querySelectorAll('#words [data-component=flowchart] li')].some(x=>x.textContent==='检查库存并整理供应商报价之后生成采购清单')`),true,'full long node labels remain readable below the graph');
 const before=fake.st.says.length;
 await evaluate(p,`document.querySelector('#words [data-component="confirm"] button').click()`);
 assert.match(await evaluate(p,`document.getElementById('input').value`),/Prepare order/);assert.equal(fake.st.says.length,before,'confirmation only drafts');
 await evaluate(p,`(()=>{const e=document.querySelector('#words [data-component="form"] input');e.value='<script>alert(1)</script>';document.querySelector('#words [data-component=form] select').value='B';document.querySelector('#words [data-component=form] input[type=checkbox]').checked=true;document.querySelector('#words [data-component=form] input[type=date]').value='2026-10-21';document.querySelector('#words [data-component="form"] button').click();})()`);
 assert.match(await evaluate(p,`document.getElementById('input').value`),/<script>alert/);assert.equal(fake.st.says.length,before,'form only drafts');
 assert.match(await evaluate(p,`document.getElementById('input').value`),/Choice: B/);assert.match(await evaluate(p,`document.getElementById('input').value`),/2026-10-21/);
 assert.equal(await evaluate(p,`document.querySelectorAll('#words script, #words [onclick], #words form').length`),0);
 for(const name of ['flowchart','chart','steps','confirm','form']){
  await evaluate(p,`document.querySelector('#words [data-component="${name}"]').scrollIntoView({block:'center'})`);await sleep(80);
  const {data}=await p.send('Page.captureScreenshot',{format:'png'});writeFileSync(out+`151-${lang}-${theme}-${width}-${name}.png`,Buffer.from(data,'base64'));
 }
 await evaluate(p,`document.getElementById('readBtn').click()`);await waitFor(p,`!document.getElementById('rd').hidden && document.querySelectorAll('#rdWords .reply-component').length===5`);
 assert.equal(await evaluate(p,`[...document.querySelectorAll('#rdWords .rc-draft,#rdWords .reply-component input,#rdWords .reply-component select')].every(x=>x.disabled)`),true,'reader is read-only');
 await evaluate(p,`document.getElementById('rdClose').click()`);
 const cap=await evaluate(p,`(async()=>{const {RelayMD}=await import('./js/md.js'),{upgradeComponents}=await import('./js/components.js');const box=document.createElement('div');box.append(RelayMD.toDOM(RelayMD.parse(Array(22).fill(${JSON.stringify(fence(samples[3]))}).join('\\n\\n')),document));upgradeComponents(box);upgradeComponents(box);return [box.querySelectorAll('.reply-component').length,box.querySelectorAll('.codeblock').length];})()`);assert.deepEqual(cap,[20,2],'component limit and repeated upgrade preserve text');
 const isolated=await evaluate(p,`(async()=>{const {RelayMD}=await import('./js/md.js'),{upgradeComponents}=await import('./js/components.js');const box=document.createElement('div');box.append(RelayMD.toDOM(RelayMD.parse(${JSON.stringify(fence(samples[0])+'\n\n'+fence(samples[2]))}),document));const old=document.createElementNS;document.createElementNS=()=>{throw new Error('synthetic renderer failure');};try{upgradeComponents(box);}finally{document.createElementNS=old;}return [box.querySelectorAll('.codeblock').length,box.querySelectorAll('[data-component=steps]').length];})()`);assert.deepEqual(isolated,[1,1],'a failed renderer preserves source and does not break its neighbour');
 assert.deepEqual(p.problems,[]);assert.deepEqual(p.offsite,[]);
 records.push({lang,theme,width,components:5,streamingDisabled:true,draftOnly:true,readerReadOnly:true,fieldKinds:4,componentCap:20,cspErrors:p.problems,external:p.offsite});
 }finally{await p.dispose();}
}writeFileSync(out+'151-components.json',JSON.stringify({verdict:'PASS',records},null,2)+'\n');}finally{await B.close();await fake.stop();await web.stop();}
