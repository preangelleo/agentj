// P447: real encrypted fake-host session → first-success hint on phone and computer, both languages.
import assert from 'node:assert/strict';
import {mkdirSync,writeFileSync} from 'node:fs';
import {startWebServer} from './serve.mjs';
import {startFakeHost} from './fakehost.mjs';
import {launch,newPage,navigate,waitState,evaluate,sleep,shoot} from './browser.mjs';
const out='reports/qa/p447/screens';mkdirSync(out,{recursive:true});
const web=await startWebServer({port:0,relayCsp:'ws://127.0.0.1:*'});
const fake=await startFakeHost();const b=await launch();let checks=0;
try{
 for(const lang of ['zh','en'])for(const kind of ['phone','computer']){
  const p=await newPage(b,kind==='phone'?390:1440,852,'dark',{allow:[web.url,fake.relay+'/']});
  try{
   await navigate(p,fake.newPairing(web.url+'?lang='+lang));await waitState(p,'awaiting-approval');
   await fake.approve();await waitState(p,'ready');
   // #435: exactly one remote → the shared bottom toast (10 s), never a resident line
   await fake.send({t:'devs',limit:10,can_add:true,busy:false,items:[{id:'d0',name:'x',paired_at:1,seen:0,online:true,me:true,source:'local',named_by:'device',self_name:'x',pk:'none'}]});
   await fake.send({t:'pair_guide',kind});await sleep(300);
   const look=`(()=>{const e=document.getElementById('toast');const b=e.getBoundingClientRect();return {on:e.classList.contains('on'),text:e.textContent,width:b.width,scroll:document.documentElement.scrollWidth,win:innerWidth,resident:!!document.getElementById('pair-guide')}})()`;
   const r=await evaluate(p,look);
   assert.equal(r.on,true);assert.equal(r.resident,false);assert.ok(r.width>0);assert.ok(r.scroll<=r.win+1);
   assert.ok(r.text.includes(kind==='phone'?'m.agentj.app':lang==='zh'?'添加遥控器':'Add a remote'));checks+=4;
   writeFileSync(out+'/'+lang+'-'+kind+'.png',await shoot(p,out+'/'+lang+'-'+kind+'.png'));
   await navigate(p,web.url+'?lang='+lang);await waitState(p,'ready');
   await fake.send({t:'pair_guide',kind});await sleep(300);
   const again=await evaluate(p,look);assert.ok(!(again.on&&again.text===r.text));assert.equal(again.resident,false);checks++;   // #435: once per device
  }finally{await p.close();}
 }
 console.log('PASS P447 real page '+checks+' assertions');
}finally{await b.close();await fake.stop();await web.stop();}
