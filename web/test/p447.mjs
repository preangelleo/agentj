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
   await fake.send({t:'pair_guide',kind});await sleep(300);
   const r=await evaluate(p,`(()=>{const e=document.getElementById('pair-guide');const b=e.getBoundingClientRect();return {hidden:e.hidden,text:e.textContent,width:b.width,scroll:document.documentElement.scrollWidth,win:innerWidth}})()`);
   assert.equal(r.hidden,false);assert.ok(r.width>0);assert.ok(r.scroll<=r.win+1);
   assert.ok(r.text.includes(kind==='phone'?'m.agentj.app':lang==='zh'?'添加遥控器':'Add a remote'));checks+=4;
   writeFileSync(out+'/'+lang+'-'+kind+'.png',await shoot(p,out+'/'+lang+'-'+kind+'.png'));
   await navigate(p,web.url+'?lang='+lang);await waitState(p,'ready');
   assert.equal(await evaluate(p,"document.getElementById('pair-guide').hidden"),true);checks++;
  }finally{await p.close();}
 }
 console.log('PASS P447 real page '+checks+' assertions');
}finally{await b.close();await fake.stop();await web.stop();}
