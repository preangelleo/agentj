// P529: real browser, synthetic paired host; host refusal uses existing fp-link styling and command paths.
import assert from 'node:assert/strict';
import {mkdirSync,writeFileSync,cpSync,mkdtempSync,rmSync} from 'node:fs';
import {tmpdir} from 'node:os';
import {join} from 'node:path';
import {execFileSync} from 'node:child_process';
import {i18nModule} from '../build.mjs';
import {startWebServer,PUBLIC_DIR} from './serve.mjs';
import {startFakeHost} from './fakehost.mjs';
import {launch,newPage,evaluate as ev,navigate,waitState,waitFor,tap,shoot,sleep} from './browser.mjs';
const out=process.env.P529_SHOTS||'/tmp/p529-shots';mkdirSync(out,{recursive:true});
const tmp=mkdtempSync(join(tmpdir(),'p529-web-'));cpSync(PUBLIC_DIR,tmp,{recursive:true});writeFileSync(join(tmp,'i18n.js'),i18nModule());
const web=await startWebServer({publicDir:tmp}),fake=await startFakeHost(),B=await launch();
const summary=[];
const texts=JSON.parse(execFileSync('host/.venv/bin/python',['-c',"import sys,json;sys.path.insert(0,'agentjarvis/host');from agentj.slash import refuse;print(json.dumps({k:refuse(k) for k in ('zh','en')}))"],{encoding:'utf8'}));
async function click(p,selector){await ev(p,`document.querySelector(${JSON.stringify(selector)}).scrollIntoView({block:'center'})`);const r=await ev(p,`(()=>{const r=document.querySelector(${JSON.stringify(selector)}).getBoundingClientRect();return {x:r.x+r.width/2,y:r.y+r.height/2}})()`);await tap(p,r.x,r.y);}
try {
 for(const lang of ['zh','en'])for(const w of [390,1440]) {
  fake.reset();await fake.addTurn({k:'cmd',text:'/host-only'},texts[lang],'done',{cmd:'refused',kind:'refused',ok:false});
  const p=await newPage(B,w,1100,'light',{allow:[web.url,fake.relay+'/'],touch:w<500});
  await navigate(p,fake.newPairing(web.url+'?lang='+lang));await waitState(p,'awaiting-approval');await fake.approve();await waitState(p,'ready');
  try { await waitFor(p,`document.querySelectorAll('#words button[data-phone-command]').length===18`); } catch(e) { console.log(await ev(p,`({words:document.getElementById('words').innerHTML,state:window.__ajDebug?.()})`)); throw e; }
  await ev(p,`document.getElementById('phone-env-dismiss')?.click();document.getElementById('input').blur()`);
  // A real catalogue may have already linked /goal as an installed skill. The refusal
  // must still RUN it, rather than open that skill's detail panel.
  assert.equal(await ev(p,`(async()=>{const {commandLinks}=await import('./js/five-pages.js');
    const box=document.createElement('div'),li=document.createElement('li'),span=document.createElement('span'),old=document.createElement('button');
    old.className='fp-link fp-link-skill';old.textContent='/goal';let opened=false,sent='';old.onclick=()=>opened=true;
    span.append(old,document.createTextNode(' — Set a goal'));li.append(span);box.append(li);
    commandLinks(box,c=>sent=c);box.querySelector('button').click();return sent==='goal'&&!opened;})()`),true);
  await sleep(400);
  const layout=await ev(p,`(()=>{const bs=[...document.querySelectorAll('#words button[data-phone-command]')];return {ys:bs.map(b=>b.getBoundingClientRect().top),n:bs.length,overflow:document.documentElement.scrollWidth>innerWidth,classes:bs.map(b=>b.className)}})()`);
  assert.equal(layout.n,18);assert.ok(layout.ys.every((y,i,a)=>!i||y>a[i-1]));assert.equal(layout.overflow,false);assert.ok(layout.classes.every(c=>c==='fp-link fp-link-skill'));
  writeFileSync(join(out,`${lang}-${w}.png`),await shoot(p));
  // /clear uses the existing confirmation; cancel sends nothing.
  const before=fake.log.length;
  await click(p,'#words button[data-phone-command="clear"]');await waitFor(p,`document.getElementById('confirm-yes').getBoundingClientRect().width>0`);
  assert.equal(fake.log.slice(before).filter(m=>m.t==='slash'&&m.cmd==='clear').length,0);
  await click(p,'#confirm-no');
  await click(p,'#words button[data-phone-command="status"]');
  await waitFor(p,`document.getElementById('words').textContent.includes('/status')`);
  assert.ok(fake.log.some(m=>m.t==='slash'&&m.cmd==='status'));
  summary.push({lang,w,...layout,clearConfirmed:true,statusSent:true});await p.dispose();
 }
 writeFileSync(join(out,'result.json'),JSON.stringify({ok:true,summary},null,2));console.log('P529 refusal browser PASS: 4 bilingual layouts, internal-link taps and clear confirmation');
}finally{await B.close();await fake.stop();await web.stop();rmSync(tmp,{recursive:true,force:true});}
