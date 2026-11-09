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
const diagnostic=process.env.P529_DIAGNOSTIC==='1';
const out=process.env.P529_SHOTS||'/tmp/p529-shots';mkdirSync(out,{recursive:true});
const tmp=mkdtempSync(join(tmpdir(),'p529-web-'));cpSync(PUBLIC_DIR,tmp,{recursive:true});writeFileSync(join(tmp,'i18n.js'),i18nModule());
const web=await startWebServer({publicDir:tmp}),fake=await startFakeHost(),B=await launch();
const summary=[];
const texts=JSON.parse(execFileSync(process.env.P529_PYTHON||'host/.venv/bin/python',['-c',"import sys,json;sys.path.insert(0,'agentjarvis/host');from agentj.slash import refuse;print(json.dumps({k:refuse(k) for k in ('zh','en')}))"],{encoding:'utf8'}));
async function click(p,selector){await ev(p,`document.querySelector(${JSON.stringify(selector)}).scrollIntoView({block:'center'})`);const r=await ev(p,`(()=>{const r=document.querySelector(${JSON.stringify(selector)}).getBoundingClientRect();return {x:r.x+r.width/2,y:r.y+r.height/2}})()`);await tap(p,r.x,r.y);}

async function latestPage(p,w){
 const r=await ev(p,`(()=>{const b=document.getElementById('pgLatest');b.scrollIntoView({block:'center'});const r=b.getBoundingClientRect();return{x:r.x+r.width/2,y:r.y+r.height/2,hit:b.contains(document.elementFromPoint(r.x+r.width/2,r.y+r.height/2))}})()`);
 assert.ok(r.hit,'real latest-page corner must be reachable');
 if(w<500){await tap(p,r.x,r.y);await tap(p,r.x,r.y);} // shared native touch helper; no timeouts or DOM events
 else for(const clickCount of [1,2]){
  await p.send('Input.dispatchMouseEvent',{type:'mousePressed',x:r.x,y:r.y,button:'left',clickCount});
  await p.send('Input.dispatchMouseEvent',{type:'mouseReleased',x:r.x,y:r.y,button:'left',clickCount});
 }
}
async function inspectLegacyActions(p,lang,w){
 const samples=[];
 const capture=async tag=>{const dom=await ev(p,`(async()=>{const m=await import('./js/slashlink.js'),rows=[...document.querySelectorAll('#words li')].map(li=>({text:li.textContent,first:li.firstChild?.firstChild?.nodeType,buttons:[...li.querySelectorAll('button')].map(b=>({text:b.textContent,class:b.className,phone:b.dataset.phoneCommand||null,cmd:b.dataset.cmd||null,hidden:b.hidden}))})),known=[...m.KNOWN],linked=[...document.querySelectorAll('#words button')].map(b=>b.dataset.phoneCommand||(b.dataset.cmd||'').slice(1));return{rows,known,send:[...m.SEND],knownMissing:known.filter(c=>rows.some(r=>r.text.startsWith('/'+c+' — '))&&!linked.includes(c)),oldCount:document.querySelectorAll('#words button[data-phone-command]').length,genericCount:document.querySelectorAll('#words .slash-command-link').length,input:document.getElementById('input').value,confirmHidden:document.getElementById('confirm').hidden,reply:document.getElementById('words').textContent}})()`);const row={tag,lang,w,dom,executionMessages:fake.log.filter(m=>['say','slash','reset','cmd_run','answer'].includes(m.t)).map(m=>({t:m.t,cmd:m.cmd,ok:m.ok})),fixtureTurns:fake.st.turns.map(t=>({id:t.id,k:t.src?.k,card:t.card}))};samples.push(row);console.log('P529_DIAGNOSTIC '+JSON.stringify(row));};
 await ev(p,`document.getElementById('phone-env-dismiss')?.click();document.getElementById('a2hs-ok')?.click();document.getElementById('input').blur()`);
 await capture('original-old-18-timeout');
 const clone=await ev(p,`(async()=>{const box=document.getElementById('words').cloneNode(true),m=await import('./js/five-pages.js'),before=box.querySelectorAll('[data-phone-command]').length;m.commandLinks(box,()=>{});return{cloneOnly:true,before,after:box.querySelectorAll('[data-phone-command]').length,decorated:[...box.querySelectorAll('[data-phone-command]')].map(b=>b.dataset.phoneCommand)}})()`);console.log('P529_CLONE_ONLY '+JSON.stringify(clone));
 await click(p,'#words .slash-command-link[data-cmd="/clear"]');await sleep(150);await capture('after-real-clear-click');
 const at=fake.log.length;await click(p,'#words .slash-command-link[data-cmd="/status"]');for(let i=0;i<20&&!fake.log.slice(at).some(m=>m.t==='slash'&&m.cmd==='status');i++)await sleep(50);await capture('after-real-status-click');
 writeFileSync(join(out,'diagnostic.json'),JSON.stringify({samples,clone,originalFailure:'18 dedicated data-phone-command buttons timed out; assertion unchanged'},null,2)+'\n');
}
try {
 for(const lang of diagnostic?['zh']:['zh','en'])for(const w of diagnostic?[390]:[390,1440]) {
  fake.reset();await fake.addTurn({k:'cmd',text:'/host-only'},texts[lang],'done',{cmd:'refused',kind:'refused',ok:false});
  const p=await newPage(B,w,1100,'light',{allow:[web.url,fake.relay+'/'],touch:w<500});
  await navigate(p,fake.newPairing(web.url+'?lang='+lang));await waitState(p,'awaiting-approval');await fake.approve();await waitState(p,'ready');
  try { await waitFor(p,`document.querySelectorAll('#words button[data-phone-command]').length===18`); } catch(e) { if(diagnostic)await inspectLegacyActions(p,lang,w);console.log(await ev(p,`({words:document.getElementById('words').innerHTML,state:window.__ajDebug?.()})`)); throw e; }
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
  // The typed Host refusal is not inferred from words. Identical text on model/untrusted pages stays ordinary data.
  const execCount=()=>fake.log.filter(m=>['say','slash','cmd_run','reset','answer'].includes(m.t)).length;
  const negative=[];
  for(const kind of ['model','friend_report','owner-channel']){
    const start=execCount(),label=kind==='model'?'Synthetic model page':kind==='friend_report'?'Synthetic friend report':texts[lang];
    const src=kind==='model'?{k:'phone',text:label}:kind==='friend_report'?{k:'friend_report',text:label}:{k:'sys',text:label};
    const card=kind==='owner-channel'?{channel:{source:'mail',text:texts[lang],label:'Synthetic mail'}}:null;
    const turn=await fake.addTurn(src,texts[lang],'done',card);
    if(kind==='owner-channel'){
      await waitFor(p,`import('./js/relay.js').then(m=>m.debug().hist.includes(${turn.id}))`);
      await latestPage(p,w); // actual no-badge corner contract is double tap / double click
      await waitFor(p,`document.getElementById('words').dataset.chan==='1'&&document.getElementById('om').dataset.src==='sys'`);
    }
    await waitFor(p,`document.getElementById('omText').textContent.includes(${JSON.stringify(kind==='owner-channel'?'/clear':label)})`);
    if(kind==='model')await waitFor(p,`document.querySelectorAll('#words .slash-command-link').length===16`);
    const links=await ev(p,`({dedicated:document.querySelectorAll('#words [data-phone-command],#omText [data-phone-command]').length,generic:document.querySelectorAll('#words .slash-command-link,#omText .slash-command-link').length})`);
    assert.equal(links.dedicated,0,kind+': same text never gains typed Host refusal actions');
    if(kind==='model'){
      assert.equal(links.generic,16,'ordinary model reply preserves the original global sixteen-link cap');
      await ev(p,`document.getElementById('input').value='Owner draft'`);
      await click(p,'#words .slash-command-link[data-cmd="/clear"]');
      assert.ok(await ev(p,`document.getElementById('input').value.startsWith('Owner draft')&&document.getElementById('input').value.includes('/clear')&&document.getElementById('confirm').hidden`),'ordinary model /clear only appends to the owner draft, never opens a typed immediate-action confirmation');
    }else assert.equal(links.generic,0,kind+': no command execution links in untrusted report/channel words');
    assert.equal(execCount(),start,kind+': presentation/fill executes nothing');
    negative.push({kind,...links,zeroExecution:true});
  }
  summary.push({lang,w,...layout,clearConfirmed:true,statusSent:true,negative});await p.dispose();
 }
 writeFileSync(join(out,'result.json'),JSON.stringify({ok:true,summary},null,2));console.log('P529 refusal browser PASS: 4 bilingual layouts, internal-link taps and clear confirmation');
}finally{await B.close();await fake.stop();await web.stop();rmSync(tmp,{recursive:true,force:true});}
