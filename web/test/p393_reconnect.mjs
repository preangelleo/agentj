// P393 (#122 / #113 / #115) on the real page against the local fake host (real Noise session, never the production relay):
//   A · a 3.5 s drop on the chat page: no disconnected card, no red, only the header dot (data-relink)
//   B · locked screen: the link dies while the page is hidden for 7 s; unlock + reconnect within a second → the card never
//       appears (before the fix it was painted while hidden and flashed on unlock — run with --before to reproduce)
//   C · frozen page (CDP web lifecycle 'frozen' → 'active') + reconnect → no card
//   D · an outage that persists → the card after five seconds (not before), closes on reconnect
//   E · Settings → 紧急修复: amber 「正在重新连接…」 inside the window (no notice, no red); ① tapped while reconnecting waits
//       and runs once connected; ① past five seconds says why + what next + when it checked (a second tap visibly answers);
//       recovery turns the card and the dot green at once (#113); success folds after 30 s (one combination waits it out
//       for real), the × (P380 close) clears it, reopening Settings shows ended results as one line with the log under 详情.
// zh / en × light / dark × 390 / 1440. Shots → reports/qa/p393/ (no token, pairing link, address bar or account name in them).
import assert from 'node:assert/strict';
import { mkdirSync, writeFileSync, existsSync, rmSync } from 'node:fs';
import { execFileSync } from 'node:child_process';
import { join } from 'node:path';
import { homedir } from 'node:os';
import { startWebServer } from './serve.mjs';
import { parityRecorder } from '../../parity/lib.mjs';
import { startFakeHost } from './fakehost.mjs';
import { launch, newPage, navigate, waitFor, waitState, evaluate, shoot, sleep } from './browser.mjs';

const BEFORE = process.argv.includes('--before');      // serve the committed (pre-fix) page to reproduce #122
const OUT = 'reports/qa/p393';
mkdirSync(OUT, { recursive: true });
let publicDir;
if (BEFORE) {
  const dir = join(homedir(), '.cache/p393/reconnect/before');
  if (existsSync(dir)) rmSync(dir, { recursive: true });
  mkdirSync(dir, { recursive: true });
  const tar = execFileSync('git', ['archive', '8b9a9ed252^', 'web/public'], { maxBuffer: 1 << 30 });
  execFileSync('tar', ['-x', '-C', dir], { input: tar });
  publicDir = join(dir, 'web/public/');
}
const $ = (id) => `document.getElementById('${id}')`;
const fake = await startFakeHost(), web = await startWebServer(publicDir ? { publicDir } : {}), B = await launch();
const S = {
  zh: { restored: '已恢复连接', lost: /连接已断开/, pending: '正在重新连接…', wait: '正在重新连接，连上后自动开始…', next: /自动变绿[\s\S]*手机网络[\s\S]*support\.agentj\.app/,
    at: /检查过：仍然连不上$/, sumOk: /^上次：已完成（\d\d:\d\d）$/, details: '详情', close: '关闭执行结果' },
  en: { restored: 'Connection restored', lost: /Connection lost/, pending: 'Reconnecting…', wait: 'Reconnecting — this starts as soon as the link is back…',
    next: /turns green by itself[\s\S]*phone network[\s\S]*support\.agentj\.app/, at: /: still no link$/, sumOk: /^Last run: done \(\d\d:\d\d\)$/, details: 'Details', close: 'Close command result' },
};
// one recorder per page: every moment the disconnected card / Settings red / the 「连接已断开」 notice became visible
const RECORDER = `(()=>{const before=${BEFORE};const R=window.__p393={pops:[],reds:[],lost:[],recovered:[],states:[],relink:false,t0:performance.now()};
  const conn=document.getElementById('set-connection'),toast=document.getElementById('toast');
  if(!conn||!toast)throw new Error('P393 real connection/notification surfaces missing');
  const at=()=>Math.round(performance.now()-R.t0);
  const forbidden=()=>{const old=document.getElementById('offDlg'),blur=document.body.hasAttribute('data-offline-card');if((before ? old&&!old.hidden : old)||blur)R.pops.push({t:at(),vis:document.visibilityState,exists:!!old,blur})};
  // Formal 0286 8b9a9ed252 removed this overlay. Observe its forbidden re-creation, not a null retired node.
  new MutationObserver(records=>{for(const record of records){for(const n of record.addedNodes||[]){if(!before&&n.nodeType===1&&(n.id==='offDlg'||n.querySelector('#offDlg,[data-offline-card]')||n.hasAttribute('data-offline-card')))R.pops.push({t:at(),vis:document.visibilityState,transientInsertion:true});}if(record.attributeName==='data-offline-card'&&record.oldValue!==null)R.pops.push({t:at(),vis:document.visibilityState,transientBlur:true});}forbidden();}).observe(document.body,{childList:true,subtree:true,attributes:true,attributeOldValue:true,attributeFilter:['hidden','data-offline-card']});forbidden();
  R.lastOnline=conn.dataset.online;
  new MutationObserver(()=>{const on=conn.dataset.online;if(on===R.lastOnline)return;R.lastOnline=on;R.states.push({t:at(),online:on});if(on==='false')R.reds.push(at())}).observe(conn,{attributes:true,attributeFilter:['data-online']});
  new MutationObserver(()=>{const text=toast.textContent;if(/连接已断开|Connection lost/.test(text))R.lost.push(at());if(/已恢复连接|Connection restored|本次断开|unknown reason|原因未知/.test(text))R.recovered.push(at())}).observe(toast,{childList:true,subtree:true,characterData:true});
  new MutationObserver(()=>{if(document.body.hasAttribute('data-relink')){R.relink=true;if(R.relinkAt==null)R.relinkAt=at()}}).observe(document.body,{attributes:true,attributeFilter:['data-relink']});
  R.wakes=[];for(const [tg,ev] of [[window,'online'],[window,'pageshow'],[document,'resume'],[document,'visibilitychange']])tg.addEventListener(ev,()=>{forbidden();if(!document.hidden)R.wakes.push({ev,t:at()})});
  navigator.connection?.addEventListener?.('change',()=>{forbidden();if(!document.hidden)R.wakes.push({ev:'connection',t:at()})});
  let beat=performance.now();R.maxGap=0;setInterval(()=>{const n=performance.now();R.maxGap=Math.max(R.maxGap,Math.round(n-beat));beat=n},100);
  return true})()`;
const rec = (p) => evaluate(p, `JSON.parse(JSON.stringify(window.__p393))`);
const reArm = (p) => evaluate(p, `(()=>{const R=window.__p393;R.pops=[];R.reds=[];R.lost=[];R.recovered=[];R.states=[];R.lastOnline=document.getElementById('set-connection').dataset.online;R.relink=false;R.relinkAt=null;R.maxGap=0;R.wakes=[];R.t0=performance.now();return true})()`);
const setHidden = (p, hidden) => evaluate(p, `(()=>{Object.defineProperty(document,'visibilityState',{configurable:true,get:()=>'${hidden ? 'hidden' : 'visible'}'});
  Object.defineProperty(document,'hidden',{configurable:true,get:()=>${hidden}});document.dispatchEvent(new Event('visibilitychange'));return true})()`);

const results = [];
let checks = 0;
const check = (ok, label) => { assert.ok(ok, label); checks++; };
const resetCalls = [];
fake.st.onApp = null;
const handler = async (c, m, send) => {
  if (m.t !== 'reset') return false;
  resetCalls.push(await fake.verifyControl(c, 'reset', m, { what: 'connection' }));
  (async () => { await sleep(60); await send({ t: 'reset_step', flow: 'reset', step: 1, state: 'ok', text: 'ok', r: m.r }); await sleep(60);
    await send({ t: 'reset_done', flow: 'reset', ok: true, step: 1, text: m.r ? 'ok' : '', r: m.r }); })().catch(() => {});
  return true;
};

async function chatTimings(p, lang, tag) {
  const out = {};
  // A · a short drop (3.5 s) on the chat page
  await reArm(p);
  fake.kick(1001); fake.setUp(false);
  await sleep(3500);
  out.shortDot = await evaluate(p, `document.body.hasAttribute('data-relink')`);
  fake.setUp(true); await waitState(p, 'ready'); await sleep(400);
  let r = await rec(p);
  out.short = r;
  if (!BEFORE) {
    check(r.pops.length === 0 && r.reds.length === 0 && r.lost.length === 0 && r.recovered.length === 0, 'A: a 3.5 s drop shows no card / red / notice ' + tag + ' ' + JSON.stringify(r));
    check(out.shortDot && r.relink, 'A: only the header dot said so (data-relink) ' + tag);
    check(!(await evaluate(p, `document.body.hasAttribute('data-relink')`)), 'A: dot back to normal ' + tag);
  }
  // B · the screen locks; the link dies while hidden; unlock, reconnect within a second
  await sleep(300); await reArm(p);
  await setHidden(p, true);
  fake.setUp(false); fake.kick(1001);
  await sleep(7000);
  const hiddenPops = (await rec(p)).pops.length;
  await setHidden(p, false);
  if (BEFORE) await sleep(30), writeFileSync(`${OUT}/before-${tag}-unlock.png`, await shoot(p, ''));
  await sleep(300);
  fake.setUp(true); await waitState(p, 'ready', 15000); await sleep(500);
  r = await rec(p);
  out.lock = { ...r, hiddenPops };
  if (!BEFORE) check(r.pops.length === 0 && r.recovered.length === 0, 'B: unlock + reconnect within a second → the card never appears ' + tag + ' ' + JSON.stringify(r.pops));
  else console.log('BEFORE B (lock):', JSON.stringify(r.pops), r.pops.length ? '→ flash reproduced' : '→ not reproduced');
  // C · a frozen page (web lifecycle), host down while frozen, thawed and reconnected
  await sleep(300); await reArm(p);
  fake.setUp(false); fake.kick(1001);
  const frozen = await p.send('Page.setWebLifecycleState', { state: 'frozen' }).then(() => true, (e) => String(e.message || e));
  await sleep(7000);
  if (frozen === true) await p.send('Page.setWebLifecycleState', { state: 'active' });
  await sleep(300);
  fake.setUp(true); await waitState(p, 'ready', 15000); await sleep(500);
  r = await rec(p);
  out.freeze = { frozen, ...r };
  if (!BEFORE && frozen === true) check(r.pops.length === 0 && r.recovered.length === 0, 'C: thawed + reconnected → no card ' + tag + ' ' + JSON.stringify(r.pops));
  if (frozen !== true) console.log('C: CDP freeze unavailable here:', frozen);
  return out;
}

async function outage(p, tag, s) {
  // 8b9a9ed252 replaced the old mandatory overlay with silent indicators and an operable page.
  // Keep the four-second lower-bound probe and the original real five-second gate timing.
  await sleep(300); await reArm(p);
  const t0=Date.now();fake.setUp(false);fake.kick(1001);
  await sleep(4000);
  let d=await rec(p);
  check(d.pops.length===0&&d.reds.length===0&&d.lost.length===0&&d.recovered.length===0,'D: no overlay/red/notice in the first four seconds '+tag);
  await waitFor(p,`${$('set-connection')}.dataset.online==='false'`,6000);
  const ms=Date.now()-t0;d=await rec(p);
  const from=d.relinkAt??0,down=d.reds[0],gap=down-from,late=d.maxGap>=900;
  const restart=Math.max(from,...d.wakes.filter(w=>w.t>from&&w.t<down).map(w=>w.t));
  const upper=(late?7500+5000+d.maxGap:7500)+(restart-from);
  check(ms>=4900&&gap>=4900&&gap<=upper,`D: real down indicator after five seconds (page ${gap} ms, wall ${ms} ms, max stall ${d.maxGap} ms, bound ${upper}) ${tag}`);
  check(d.reds.length===1&&d.pops.length===0&&d.lost.length===0&&d.recovered.length===0,'D: one down transition, never a retired overlay/blur or automatic notice '+tag);
  check(await evaluate(p,`!document.getElementById('offDlg')&&!document.body.hasAttribute('data-offline-card')&&!!document.getElementById('outbox')&&!!document.getElementById('status')&&window.__ajState!=='ready'`),'D: truthful offline state and outbox survive without the removed blocking overlay '+tag);
  const executionMessages=()=>fake.log.filter(m=>['say','reset','slash','cmd_run','answer'].includes(m.t)).length;const sent=executionMessages();
  // A genuine click must still enter and leave Settings while offline; no Host action is requested.
  await realTap(p,'#setBtn');await waitFor(p,`!${$('settings')}.hidden`);
  await evaluate(p,`import('./js/settings.js').then(m=>m.close())`);await waitFor(p,`${$('settings')}.hidden`);
  check(executionMessages()===sent,'D: offline navigation does not execute or queue a host action '+tag);
  writeFileSync(`${OUT}/web-${tag}-offline-no-overlay.png`,await shoot(p,''));
  fake.setUp(true);await waitState(p,'ready',15000);await sleep(400);
  d=await rec(p);
  check(d.pops.length===0&&d.recovered.length===0&&d.lost.length===0&&!await evaluate(p,`document.body.hasAttribute('data-relink')`),'D: reconnect restores the indicator with no flashed overlay or duplicate automatic notice '+tag);
  check(d.states.filter(x=>x.online==='false').length===1&&d.states.filter(x=>x.online==='true').length===1,'D: connection down/up transitions are not duplicated '+tag);
  return ms;
}
async function realTap(p,selector){
 const point=await evaluate(p,`(()=>{const n=document.querySelector(${JSON.stringify(selector)});n.scrollIntoView({block:'center'});const r=n.getBoundingClientRect();return{x:r.x+r.width/2,y:r.y+r.height/2,hit:n.contains(document.elementFromPoint(r.x+r.width/2,r.y+r.height/2))}})()`);
 check(point.hit,'offline navigation button is reachable: '+selector);
 await p.send('Input.dispatchMouseEvent',{type:'mousePressed',x:point.x,y:point.y,button:'left',clickCount:1});
 await p.send('Input.dispatchMouseEvent',{type:'mouseReleased',x:point.x,y:point.y,button:'left',clickCount:1});
}

async function repair(p, lang, tag, s, realFold) {
  resetCalls.length = 0;
  fake.st.onApp = handler;
  await evaluate(p, `import('./js/rescue.js').then(m=>{m._reset();return true})`);
  await evaluate(p, `import('./js/settings.js').then(m=>{m.openRescue();return true})`);
  await waitFor(p, `!${$('settings')}.hidden`);
  await reArm(p);
  // inside the window: amber, no notice, no red; ① waits for the link and then runs
  fake.setUp(false); fake.kick(1001);
  await waitFor(p, `${$('set-connection')}.dataset.online==='pending'`, 3000);
  check(await evaluate(p, `${$('set-connection')}.textContent`) === s.pending, 'E: Settings dot amber 「正在重新连接…」 ' + tag);
  await evaluate(p, `${$('rs-reset-go')}.click()`);
  await waitFor(p, `${$('rs-reset')}.dataset.state==='run'`);
  check(await evaluate(p, `${$('rs-reset-now')}.textContent`) === s.wait, 'E: ① tapped while reconnecting waits silently ' + tag);
  writeFileSync(`${OUT}/web-${tag}-pending.png`, await shoot(p, ''));
  await sleep(1500);
  fake.setUp(true); await waitState(p, 'ready', 15000);
  await waitFor(p, `${$('rs-reset')}.dataset.state==='ok'`, 5000);
  check(resetCalls.length === 1 && resetCalls[0] === true, 'E: …and ran once connected (a signed reset) ' + tag);
  let r = await rec(p);
  check(r.reds.length === 0 && r.lost.length === 0 && r.pops.length === 0 && r.recovered.length === 0, 'E: a short drop in Settings: no red, no notice, no card ' + tag + ' ' + JSON.stringify(r));
  // × = the P380 close: same words, ≥ 44 px, clears the result
  const x = await evaluate(p, `(()=>{const b=document.getElementById('rs-reset-close');const r=b.getBoundingClientRect();return {label:b.getAttribute('aria-label'),w:r.width,h:r.height,cls:b.className}})()`);
  check(x.label === s.close && x.w >= 44 && x.h >= 44 && /rscmdclose/.test(x.cls), 'E: the × is the P380 close, 44 px ' + tag + ' ' + JSON.stringify(x));
  await evaluate(p, `${$('rs-reset-close')}.click()`);
  check(await evaluate(p, `${$('rs-reset')}.dataset.state==='idle' && !!${$('rs-reset-go')} && !${$('rs-reset-close')}`), 'E: × clears the result ' + tag);

  // past five seconds: why + what next + when; a second tap visibly answers
  await reArm(p);
  fake.setUp(false); fake.kick(1001);
  await waitFor(p, `${$('set-connection')}.dataset.online==='false'`, 13000);   // 5 s window; one late-timer restart (reconnect.js LATE_MS) can add another 5 s
  r = await rec(p);
  check(r.reds.length === 1 && r.reds[0] >= 4800, 'E: Settings red only after five seconds ' + tag + ' ' + JSON.stringify(r.reds));
  check(r.pops.length === 0, 'E: the retired disconnected card stays absent, including behind Settings ' + tag);
  await evaluate(p, `${$('rs-reset-go')}.click()`);
  await waitFor(p, `${$('rs-reset')}.dataset.state==='fail'`);
  check(await evaluate(p, `${$('rs-reset-msg')}.classList.contains('rsfail')`), 'E: why (red) ' + tag);
  check(s.next.test(await evaluate(p, `${$('rs-reset-next')}.textContent`)), 'E: what to do next ' + tag);
  const at1 = await evaluate(p, `${$('rs-reset-at')}.textContent`);
  check(s.at.test(at1), 'E: when it checked ' + tag + ' ' + at1);
  await evaluate(p, `${$('rs-reset-msg')}.scrollIntoView({block:'center'})`);
  writeFileSync(`${OUT}/web-${tag}-offline-explicit.png`, await shoot(p, ''));
  await sleep(1100);
  await evaluate(p, `${$('rs-reset-go')}.click()`);
  await waitFor(p, `${$('rs-reset-at')}?.textContent!==${JSON.stringify(at1)}`, 3000);
  check(true, 'E: a second tap answers visibly (new check time) ' + tag);
  // recovery (#113): the card and the dot turn green at once, with the notice
  fake.setUp(true); await waitState(p, 'ready', 15000);
  await waitFor(p, `${$('rs-reset')}.dataset.state==='ok' && ${$('set-connection')}.dataset.online==='true'`, 3000);
  check((await evaluate(p, `${$('rs-reset-body')}.textContent`)).includes(s.restored), 'E: ① 「现在连不上」 → 「已恢复连接」 ' + tag);
  check(await evaluate(p, `${$('set-connection')}.textContent`) === s.restored, 'E: Settings dot green 已恢复连接 ' + tag);
  await evaluate(p, `${$('rs-reset')}.scrollIntoView({block:'center'})`);
  writeFileSync(`${OUT}/web-${tag}-restored.png`, await shoot(p, ''));
  // fold: 30 s after success on a live link (real wait in one combination; the others use the test hook)
  if (realFold) {
    check(await evaluate(p, `import('./js/rescue.js').then(m=>m._debug().reset.foldTimer)`), 'E: fold timer armed after recovery ' + tag);
    await sleep(29000);
    check(!(await evaluate(p, `${$('rs-reset')}.hasAttribute('data-fold')`)), 'E: still open before 30 s ' + tag);
    await waitFor(p, `${$('rs-reset')}.hasAttribute('data-fold')`, 3000);
    check(true, 'E: folded by itself ~30 s after success ' + tag);
  } else await evaluate(p, `import('./js/rescue.js').then(m=>{m._foldNow();return true})`);
  check(s.sumOk.test(await evaluate(p, `${$('rs-reset-sum')}.textContent`)), 'E: one summary line ' + tag);
  check(await evaluate(p, `!${$('rs-reset-log')}.open && ${$('rs-reset-log')}.querySelector('summary').textContent===${JSON.stringify(s.details)} && !${$('rs-reset-next')}`), 'E: log under a closed 详情 ' + tag);
  writeFileSync(`${OUT}/web-${tag}-folded.png`, await shoot(p, ''));
  // reopening Settings: ended results open folded (a fresh failure too)
  await evaluate(p, `${$('rs-reset-close')}.click()`);
  fake.setUp(false); fake.kick(1001);
  await waitFor(p, `${$('set-connection')}.dataset.online==='false'`, 13000);   // 5 s window; one late-timer restart (reconnect.js LATE_MS) can add another 5 s
  await evaluate(p, `${$('rs-reset-go')}.click()`); await waitFor(p, `${$('rs-reset')}.dataset.state==='fail'`);
  await evaluate(p, `import('./js/settings.js').then(m=>{m.close();return true})`);
  await evaluate(p, `${$('setBtn')}.click()`); await waitFor(p, `!${$('settings')}.hidden`);
  check(await evaluate(p, `${$('rs-reset')}.hasAttribute('data-fold') && !!${$('rs-reset-sum')} && !${$('rs-reset-next')} && !!${$('rs-reset-log')}`), 'E: reopened Settings shows the old result as one line ' + tag);
  await evaluate(p, `import('./js/settings.js').then(m=>{m.close();return true})`);
  fake.setUp(true); await waitState(p, 'ready', 15000);
  fake.st.onApp = null;
}

try {
  let i = 0;
  for (const lang of ['zh', 'en']) for (const theme of ['light', 'dark']) for (const width of [390, 1440]) {
    const tag = `${lang}-${theme}-${width}`, s = S[lang];
    fake.reset(); fake.setUp(true);
    const p = await newPage(B, width, width === 390 ? 844 : 900, theme, { touch: width === 390, allow: [web.url, fake.relay + '/'] });
    try {
      await navigate(p, fake.newPairing(web.url + '?lang=' + lang)); await waitState(p, 'awaiting-approval'); await fake.approve(); await waitState(p, 'ready');
      await evaluate(p, `${$('phone-env-dismiss')}?.click(); if(!${$('a2hs')}.hidden)${$('a2hs-ok')}.click(); document.documentElement.dataset.theme='${theme}'; true`);
      await evaluate(p, RECORDER);
      const res = { tag };
      if (BEFORE) { if (tag === 'zh-light-390' || tag === 'en-dark-1440') res.timings = await chatTimings(p, lang, tag); results.push(res); continue; }
      if (tag === 'zh-light-390' || tag === 'en-dark-1440') res.timings = await chatTimings(p, lang, tag);
      res.cardMs = await outage(p, tag, s);
      await repair(p, lang, tag, s, tag === 'zh-dark-390');
      check(!p.problems.filter((x) => !/WebSocket|ERR_CONNECTION|net::/i.test(x)).length, 'no console errors ' + tag + ' ' + JSON.stringify(p.problems.slice(0, 3)));
      check((await rec(p)).pops.length===0&&(await rec(p)).recovered.length===0,'E: no retired overlay or automatic recovery notice during active repair '+tag);
      results.push(res); i++;
      console.log('PASS P393 reconnect ' + tag);
    } finally { await p.close(); }
  }
  writeFileSync(`${OUT}/${BEFORE ? 'before' : 'web'}-reconnect-result.json`, JSON.stringify({ pass: !BEFORE, checks, results }, null, 2));
  if (!BEFORE) { const rec = parityRecorder('web/test/p393_reconnect.mjs'); rec.record('p393-reconnect', true); rec.write(); }
  console.log(BEFORE ? 'P393 before-run recorded' : `PASS P393 reconnect ${i} combinations, ${checks} checks`);
} finally { fake.st.onApp = null; await B.close(); await fake.stop(); await web.stop(); }
