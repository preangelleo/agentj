// P592: real public page card-gap acceptance. No fixture DOM is injected.
import assert from 'node:assert/strict';
import { mkdirSync, writeFileSync, mkdtempSync, cpSync, rmSync } from 'node:fs';
import { join } from 'node:path';
import { tmpdir } from 'node:os';
import { execFileSync } from 'node:child_process';
import { fileURLToPath } from 'node:url';
import { startWebServer } from './serve.mjs';
import { startFakeHost } from './fakehost.mjs';
import { launch, newPage, navigate, waitFor, waitState, evaluate, shoot, sleep } from './browser.mjs';

const ROOT = fileURLToPath(new URL('../../../', import.meta.url));
const OUT = process.env.P592_GAP_OUT || '/home/laogege/.local/state/next-batch-20261115/shots-gap-r4';
mkdirSync(OUT, { recursive: true });
const q = JSON.stringify, ev = evaluate;
const card = { id:'calendar', name:{zh:'日历',en:'Calendar'}, icon:'calendar', accent:'windstorm', official:true, on:true, plus:'agentj-calendar' };
const todoCard = { id:'todo', name:{zh:'待办',en:'To-do'}, icon:'checklist', accent:'windstorm', official:true, on:true, plus:'agentj-add-todo' };
// The owner's real case: one day holding a timed dinner (18:30) plus an all-day reminder; the
// third is a timed meeting. Dates follow the browser's "today" (same host clock / zone), so the
// selected day always holds every event — a pinned date silently empties the list tomorrow.
const pad = x => String(x).padStart(2,'0');
const dayOf = d => `${d.getFullYear()}-${pad(d.getMonth()+1)}-${pad(d.getDate())}`;
const at = (h,m) => { const d = new Date(); d.setHours(h,m,0,0); return d.toISOString(); };
const eventsFor = n => { const today = new Date(), next = new Date(today); next.setDate(next.getDate()+1); return [
  { id:'e0', title:'晚餐', start:at(18,30), end:at(20,0), all_day:false, readonly:false },
  { id:'e1', title:'提醒：交物业费', start:dayOf(today), end:dayOf(next), all_day:true, readonly:false },
  { id:'e2', title:'周会', start:at(10,0), end:at(11,0), all_day:false, readonly:false },
].slice(0,n); };
const todos = [{id:'t1',text:'第一项待办',owner:'owner',status:'pending'},{id:'t2',text:'第二项待办',owner:'agent',status:'pending'},{id:'t3',text:'第三项待办',owner:'owner',status:'pending'}];
function gaps(selector) { return `(()=>{const a=[...document.querySelectorAll(${q(selector)})];return a.slice(1).map((x,i)=>{const p=a[i].getBoundingClientRect(),n=x.getBoundingClientRect();return n.top-p.bottom})})()`; }
const metrics = (page, width, height) => page.send('Emulation.setDeviceMetricsOverride', { width, height, deviceScaleFactor:1, mobile:width<500 });
// Evidence shot of the whole real page with nothing clipped: grow the viewport until no scroll
// container around the stack still hides content, then capture the full viewport (page chrome,
// header buttons and every card included). Cropping to child rects cut the second card off (r3).
async function shootWhole(page, selector, width, height) {
  let tall = height;
  for (let i = 0; i < 4; i++) {
    const extra = await ev(page, `(() => { let e = document.querySelector(${q(selector)}), x = 0;
      for (; e; e = e.parentElement) { e.scrollTop = 0; e.scrollLeft = 0; x += Math.max(0, e.scrollHeight - e.clientHeight); }
      scrollTo(0,0); return x; })()`);
    if (extra < 1) break;
    tall += Math.ceil(extra) + 8; await metrics(page, width, tall); await sleep(200);
  }
  const seen = await ev(page, `[...document.querySelectorAll(${q(selector)})].map(x => { const r = x.getBoundingClientRect(); return r.top >= 0 && r.left >= 0 && r.right <= innerWidth && r.bottom <= innerHeight && r.width > 0 && r.height > 0; })`);
  assert.ok(seen.length >= 2 && seen.every(Boolean), `${selector}: not every card is inside the shot (${seen})`);
  // A very long page (settings) would shrink the cards to nothing: keep full width, cut only height.
  let png;
  if (tall > 2400) {
    const y = await ev(page, `(() => { const r = [...document.querySelectorAll(${q(selector)})].map(x => x.getBoundingClientRect()); return {top:Math.max(0, r[0].top - 120), bottom:Math.min(innerHeight, r.at(-1).bottom + 60)}; })()`);
    await sleep(150);
    png = Buffer.from((await page.send('Page.captureScreenshot', { format:'png', clip:{x:0, y:y.top, width, height:y.bottom - y.top, scale:1} })).data, 'base64');
  } else png = await shoot(page);
  await metrics(page, width, height); await sleep(150);
  return png;
}
async function measure(page, selector, label, shot, enforce, width, height) {
  await waitFor(page, `document.querySelectorAll(${q(selector)}).length>=2`);
  const xs = await ev(page, gaps(selector));
  if (enforce) {
    assert.ok(xs.every(x => x >= 12), `${label}: gaps ${xs}`);
    assert.ok(xs.every(x => x >= 0), `${label}: no overlap`);
  }
  if (shot) writeFileSync(join(OUT, shot), await shootWhole(page, selector, width, height));
  return Math.min(...xs);
}
async function run(publicDir, phase, width, theme, lang, rows) {
  const web = await startWebServer({ port:0, relayCsp:'ws://127.0.0.1:*', ...(publicDir ? {publicDir} : {}) });
  const fake = await startFakeHost(); fake.reset(); fake.st.caps=['jcards1'];
  fake.st.prefs={appearance:{language:lang,theme},voice:{speak_replies:false,wake_enabled:false},agent:{session_mode:'shared'}};
  let events = eventsFor(2);
  fake.st.onApp = async (c,m,send) => {
    const reply = async (t,v) => { await send({t,r:m.r,...v}); return true; };
    if (m.t === 'jcard_list') return reply('jcard_items',{cards:[card,todoCard]});
    if (m.t === 'jcard_get') {
      if (m.id === 'calendar') return reply('jcard_get',{ok:true,id:m.id,template:'calendar',events,revision:'r',actions:[],anchors:[],links:[]});
      return reply('jcard_get',{ok:true,id:m.id,template:'checklist',items:todos,todo:{counts:{all:3,owner:2,agent:1}},revision:'r'});
    }
    return false;
  };
  const height = width===390?844:900;
  const B = await launch(), p = await newPage(B,width,height,theme,{touch:width===390,allow:[web.url,fake.relay+'/']});
  try {
    await navigate(p,fake.newPairing(web.url+'?lang='+lang)); await waitState(p,'awaiting-approval'); await fake.approve(); await waitState(p,'ready');
    await waitFor(p, `document.documentElement.getAttribute('data-theme')===${q(theme)}`);
    const actualTheme = await ev(p, `({attr:document.documentElement.getAttribute('data-theme'), bg:getComputedStyle(document.body).backgroundColor})`);
    assert.equal(actualTheme.attr, theme, `requested ${theme} theme was not applied`);
    assert.notEqual(actualTheme.bg, 'rgba(0, 0, 0, 0)', `${theme} theme has no painted page background`);
    await ev(p,"document.getElementById('phone-env-dismiss')?.click();document.getElementById('a2hs-ok')?.click();import('./js/jcards.js').then(m=>m.refresh())"); await sleep(250);
    const open = async id => { await ev(p,`import('./js/five-pages.js').then(m=>m.openTarget(${q(id)}))`); await waitFor(p,`!!document.querySelector('.jc-body[data-card="${id}"]')`); await sleep(100); };
    await open('calendar');
    for (const n of [2,3]) {
      events=eventsFor(n); await ev(p,"import('./js/five-pages.js').then(m=>m.openTarget('chat'));import('./js/five-pages.js').then(m=>m.openTarget('calendar'))"); await waitFor(p,`document.querySelectorAll('.jc-calendar>.jc-entry').length===${n}`);
      // Every fed event is its own visible card with its own title — never "one card rendered".
      const seen=await ev(p,`[...document.querySelectorAll('.jc-calendar>.jc-entry')].map(x=>({title:x.querySelector('h3').textContent,h:x.getBoundingClientRect().height}))`);
      assert.deepEqual(seen.map(x=>x.title), events.map(x=>x.title), 'calendar '+n+': one card per event');
      assert.ok(seen.every(x=>x.h>40), 'calendar '+n+': every card is laid out');
      const min=await measure(p,'.jc-calendar>.jc-entry','calendar '+n,`${phase}-日历${n}项-${width}-${theme}.png`,phase==='修后',width,height); rows.push({group:'日历'+n+'项',width,theme,lang,phase,min});
    }
    await open('todo'); const minTodo=await measure(p,'.jc-body[data-card="todo"] .jc-list[data-todo-list] > *','todo',`${phase}-待办-${width}-${theme}.png`,phase==='修后',width,height); rows.push({group:'待办',width,theme,lang,phase,min:minTodo});
    await ev(p,"import('./js/five-pages.js').then(m=>m.openTarget('chat'));document.getElementById('setBtn').click()"); await sleep(300);
    const minRescue=await measure(p,'#settings .rscards > .rscard','notification/approval',`${phase}-通知审批-${width}-${theme}.png`,phase==='修后',width,height); rows.push({group:'通知/审批',width,theme,lang,phase,min:minRescue});
    assert.equal(p.problems.length,0,p.problems.join('|'));
  } finally { await p.dispose(); await B.close(); await fake.stop(); await web.stop(); }
}
const rows=[]; const before=mkdtempSync(join(tmpdir(),'p592-main-'));
// Build a clean main public tree for the before screenshots; candidate runs from this worktree.
// git archive extraction is intentionally done outside the source tree.
execFileSync('tar',['-x','-C',before],{cwd:ROOT,input:execFileSync('git',['archive',process.env.P592_BASE||'main','web/public'],{cwd:ROOT,maxBuffer:100*1024*1024}),maxBuffer:100*1024*1024});
const mainPublic=join(before,'web/public');
try {
  for (const width of [390,1440]) for (const theme of ['light','dark']) { await run(mainPublic,'修前',width,theme,'zh',rows); await run(null,'修后',width,theme,'zh',rows); }
} finally { rmSync(before,{recursive:true,force:true}); }
const mins={}; for (const r of rows) (mins[r.group]??=[]).push(r.min);
writeFileSync(join(OUT,'p592-results.json'),JSON.stringify({rows,mins},null,2));
console.log('P592 PASS',rows.length,'real-page checks');
