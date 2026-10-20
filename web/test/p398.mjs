#!/usr/bin/env node
// P398 · phone message state and buttons (backlog #154 #155 #156 #157). Real page + local fake host (real Noise relay),
// own headless Chromium (never :9222). zh / en × iPhone 390 × 844 and Android 412 × 915 (+ a 1280 × 800 computer for
// 截图自动进附件). Not a physical device: env(safe-area-inset-*) is 0 here, so #157 is measured as the padding rule.
//   later-tap     #154  a message about something else after a question: its buttons stay on; a tap goes as a reply to it
//   pill          #155  queued → 取消; handed over → 已送达 (a tap stops nothing); then 停止这一轮 (the stop, worded so)
//   reconnect     #155  the say_state is lost while away: after the reconnect say_sync settles it (no 取消 left);
//                       a host without ss1: the page itself (closed / answered) settles it
//   dedupe        #156  the same screenshot auto-added and pasted again → one chip, 「自动放入 · 设置里可关」 opens the row
//   shot-row      #156  the row is in 网页版 App on a phone too, with 「仅电脑上的 Chrome 或 Edge 可用」 instead of the switch
//   page-end      #157  Settings / ≡ pages end with --page-end below the last row
// Shots → reports/qa/p398/.
import assert from 'node:assert/strict';
import { mkdirSync, writeFileSync } from 'node:fs';
import { fileURLToPath } from 'node:url';
import { parityRecorder } from '../../parity/lib.mjs';
import { startWebServer } from './serve.mjs';
import { startFakeHost } from './fakehost.mjs';
import { launch, newPage, evaluate, navigate, waitFor, waitState, shoot, sleep } from './browser.mjs';

const ROOT = fileURLToPath(new URL('../../../', import.meta.url));
const OUT = ROOT + 'reports/qa/p398/';
mkdirSync(OUT, { recursive: true });
const ONLY = (process.env.AJ_P398_ONLY || '').split(',').filter(Boolean);
const rec = parityRecorder('web/test/p398.mjs');
const web = await startWebServer({ port: 0, relayCsp: 'ws://127.0.0.1:*' });
const fake = await startFakeHost();
const B = await launch();
const $ = (id) => `document.getElementById('${id}')`;
const ev = (p, s) => evaluate(p, s);
const results = [];
let failed = 0;

const SIZES = [{ dev: 'ios', w: 390, h: 844 }, { dev: 'android', w: 412, h: 915 }];
const ME = { k: 'phone', dev: 'X', name: 'iPhone' };
const ASK = { zh: '补货清单好了。要我现在向 B 家下单吗？\n\n派不派？[派](reply:派) [先不派](reply:先不派)',
  en: 'The restock list is ready. Shall I order from supplier B now?\n\nDispatch it? [Dispatch](reply:Dispatch) [Not yet](reply:Not yet)' };
const YES = { zh: '派', en: 'Dispatch' };
const L = (lang, zh, en) => (lang === 'en' ? en : zh);

async function paired({ w, h, lang, turns = [], touch = w < 600, caps = [] }) {
  fake.reset();
  fake.st.extraCaps = caps;
  for (const [src, reply, end] of turns) await fake.addTurn(src, reply, end || 'done');
  const p = await newPage(B, w, h, 'light', { allow: [web.url, fake.relay + '/'], touch });
  await navigate(p, fake.newPairing(web.url + (lang === 'en' ? '?lang=en' : '')));
  await waitState(p, 'awaiting-approval');
  await fake.approve();
  await waitState(p, 'ready');
  await ev(p, `(()=>{document.getElementById('phone-env-dismiss')?.click();if(!${$('a2hs')}.hidden)${$('a2hs-ok')}.click();return true})()`);
  await fake.send({ t: 'status', s: 'idle', agent: 'claude', name: 'Agent J' });
  if (turns.length) await waitFor(p, `${$('pg')}.textContent.trim().endsWith('/ ${turns.length}')`, 12000);
  await sleep(200);
  return p;
}
async function C(name, fn) {
  if (ONLY.length && !ONLY.includes(name.split(' ')[0])) return;
  try { const d = await fn(); results.push({ name, ok: true, d }); rec.record(name, true); console.log(`  ✓ ${name}`); }
  catch (e) { failed++; results.push({ name, ok: false, err: String(e && e.stack || e) }); rec.record(name, false); console.log(`  ✗ ${name}: ${e && e.stack || e}`); }
}
const noProblems = (p) => assert.equal(p.problems.length, 0, p.problems.join('\n'));
const pill = (p) => ev(p, `(()=>{const c=${$('sayCancel')};const vis=!c.hidden&&getComputedStyle(c).display!=='none';
  return {vis,mode:c.dataset.mode||null,word:(c.querySelector('[data-i18n]')||{}).textContent||'',aria:c.getAttribute('aria-label')}})()`);
const status = async (p, s) => { await fake.send({ t: 'status', s, agent: 'claude', name: 'Agent J' }); await waitFor(p, `document.body.dataset.status===${JSON.stringify(s)}`); await sleep(120); };
const type = (p, v) => ev(p, `(()=>{const i=${$('input')};i.value=${JSON.stringify(v)};i.dispatchEvent(new Event('input'));return true})()`);

try {
  for (const lang of ['zh', 'en']) for (const { dev, w, h } of SIZES) {
    const tag = `${lang}-${dev}-${w}`;

    await C(`later-tap ${tag}`, async () => {
      const p = await paired({ w, h, lang, turns: [[{ ...ME, text: L(lang, '补货', 'restock') }, ASK[lang]], [{ ...ME, text: L(lang, '另外，明天几点开会？', 'Also, when is the meeting tomorrow?') }, L(lang, '上午 10 点。', '10 am.')]] });
      try {
        await ev(p, `${$('pgPrev')}.click()`); await sleep(300);
        const b = await ev(p, `[...document.querySelectorAll('#words button.rbtn')].map(b=>({say:b.dataset.say,off:b.classList.contains('rb-off')}))`);
        assert.equal(b.length, 2); assert.ok(b.every((x) => !x.off), 'still tappable after a later message: ' + JSON.stringify(b));
        writeFileSync(`${OUT}${tag}-later-tap-before.png`, await shoot(p, ''));
        const askId = fake.st.turns[0].id, n = fake.st.says.length;
        await ev(p, `document.querySelector('#words button.rbtn[data-say=${JSON.stringify(YES[lang])}]').click()`);
        for (let i = 0; i < 60 && fake.st.says.length === n; i++) await sleep(50);
        const say = fake.st.says.at(-1);
        assert.equal(say.text, YES[lang]); assert.equal(say.reply_to, askId, 'sent as a reply to the question');
        await waitFor(p, `${$('pg')}.textContent.trim().endsWith('/ 3')`, 6000);
        await ev(p, `${$('pgPrev')}.click()`); await sleep(150); await ev(p, `${$('pgPrev')}.click()`); await sleep(300);
        const after = await ev(p, `({off:[...document.querySelectorAll('#words button.rbtn')].every(b=>b.classList.contains('rb-off')),note:(document.querySelector('#words .rb-note')||{}).textContent||''})`);
        assert.ok(after.off, 'answered now: off'); assert.match(after.note, new RegExp(YES[lang]));
        writeFileSync(`${OUT}${tag}-later-tap-answered.png`, await shoot(p, ''));
        noProblems(p);
        return { reply_to: say.reply_to, note: after.note };
      } finally { await p.dispose(); }
    });

    await C(`pill ${tag}`, async () => {
      const p = await paired({ w, h, lang, turns: [[{ k: 'agent' }, L(lang, '收到，正在看。', 'On it.')]] });
      try {
        await status(p, 'working');
        fake.st.sayMode = 'queued';
        await type(p, L(lang, '排队的一条', 'a queued one')); await ev(p, `${$('send')}.click()`); await sleep(1000);
        const q = await pill(p);
        assert.equal(q.mode, 'queued'); assert.equal(q.word, L(lang, '取消', 'Cancel'));
        writeFileSync(`${OUT}${tag}-pill-queued.png`, await shoot(p, ''));
        await fake.deliverQueued(false); await sleep(250);
        const d = await pill(p);
        assert.equal(d.mode, 'delivered', JSON.stringify(d)); assert.equal(d.word, L(lang, '已送达', 'Delivered'));
        writeFileSync(`${OUT}${tag}-pill-delivered.png`, await shoot(p, ''));
        const before = fake.st.slashes.length;
        await ev(p, `${$('sayCancel')}.click()`); await sleep(300);
        assert.equal(fake.st.slashes.length, before, 'a tap on 已送达 stops nothing: ' + JSON.stringify(fake.st.slashes.slice(before)));
        await sleep(2300);
        const s = await pill(p);
        assert.equal(s.mode, 'turn'); assert.equal(s.word, L(lang, '停止这一轮', 'Stop this turn'));
        writeFileSync(`${OUT}${tag}-pill-stop.png`, await shoot(p, ''));
        await ev(p, `${$('sayCancel')}.click()`); await sleep(300);
        assert.ok(fake.st.slashes.slice(before).some((m) => m.cmd === 'stop'), 'the worded stop is the real /stop');
        fake.st.sayMode = 'delivered';
        noProblems(p);
        return { queued: q.word, delivered: d.word, stop: s.word };
      } finally { await p.dispose(); }
    });
  }

  for (const caps of [['ss1'], []]) {
    const lang = 'zh', tag = `zh-ios-390-${caps.length ? 'ss1' : 'old-host'}`;
    await C(`reconnect ${tag}`, async () => {
      const p = await paired({ w: 390, h: 844, lang, turns: [[{ k: 'agent' }, '收到。']], caps });
      try {
        await status(p, 'working');
        fake.st.sayMode = 'queued';
        await type(p, '断线前排队的一条'); await ev(p, `${$('send')}.click()`); await sleep(1000);
        assert.equal((await pill(p)).mode, 'queued');
        const turnId = fake.st.turns.at(-1).id;
        fake.deliverQueuedUnheard();                       // taken while the phone is away: no say_state reaches it
        if (!caps.length) await fake.updateTurn(turnId, { reply: { text: '收到，接着做。' }, end: 'open' });
        fake.kick();
        await waitState(p, 'ready', 15000).catch(() => {});
        await waitFor(p, `document.body.dataset.conn==='on'`, 15000);
        await sleep(800);
        const g = await pill(p);
        assert.notEqual(g.mode, 'queued', 'no 取消 left on a message the Agent has: ' + JSON.stringify(g));
        if (caps.length) assert.ok((fake.st.syncs || []).some((x) => Array.isArray(x) && x.length === 1), 'say_sync asked');
        writeFileSync(`${OUT}${tag}-reconnect.png`, await shoot(p, ''));
        fake.st.sayMode = 'delivered';
        return { pill: g, syncs: (fake.st.syncs || []).length };
      } finally { await p.dispose(); }
    });
  }

  for (const lang of ['zh', 'en']) {
    await C(`dedupe ${lang}-desktop-1280`, async () => {
      const p = await paired({ w: 1280, h: 800, lang, touch: false });
      try {
        const add = (auto) => ev(p, `(async()=>{const c=document.createElement('canvas');c.width=80;c.height=50;const g=c.getContext('2d');g.fillStyle='#629887';g.fillRect(0,0,80,50);
          const b=await new Promise(r=>c.toBlob(r,'image/png'));const m=await import('./js/relay.js');return (await m.attachShot(new File([b],'image',{type:'image/png'}),false,${auto})).added})()`);
        assert.equal(await add(true), true, 'the automatic one goes in');
        assert.equal(await add(false), false, 'the same picture pasted again does not');
        const chips = await ev(p, `[...${$('tray')}.querySelectorAll('[role=listitem]')].map(c=>({auto:(c.querySelector('.auto')||{}).textContent||''}))`);
        assert.equal(chips.length, 1); assert.equal(chips[0].auto, L(lang, '自动放入 · 设置里可关', 'Auto-added · off in Settings'));
        writeFileSync(`${OUT}${lang}-desktop-dedupe-chip.png`, await shoot(p, ''));
        await ev(p, `${$('tray')}.querySelector('.auto').click()`); await sleep(700);
        const st = await ev(p, `(()=>{const r=${$('set-shot-row')}.getBoundingClientRect();return {open:!${$('settings')}.hidden,inView:r.top>=0&&r.bottom<=innerHeight,group:${$('set-shot-row')}.closest('.setcard').id}})()`);
        assert.ok(st.open && st.inView, JSON.stringify(st)); assert.equal(st.group, 'set-s-app');
        writeFileSync(`${OUT}${lang}-desktop-shot-row.png`, await shoot(p, ''));
        noProblems(p);
        return { chips, st };
      } finally { await p.dispose(); }
    });
  }

  for (const lang of ['zh', 'en']) for (const { dev, w, h } of SIZES) {
    const tag = `${lang}-${dev}-${w}`;
    await C(`shot-row ${tag}`, async () => {
      const p = await paired({ w, h, lang });
      try {
        await ev(p, `import('./js/settings.js').then(m=>m.open())`); await sleep(400);
        const r = await ev(p, `(()=>{const row=${$('set-shot-row')};row.scrollIntoView({block:'center'});return {row:!row.hidden&&row.getClientRects().length>0,
          sw:!${$('set-shot')}.hidden,na:${$('set-shot-na')}.textContent,group:row.closest('.setcard').id}})()`);
        assert.ok(r.row, 'the row is there on a phone'); assert.equal(r.sw, false, 'no switch on a phone');
        assert.match(r.na, L(lang, /仅电脑上的 Chrome 或 Edge 可用/, /Only in Chrome or Edge on a computer/)); assert.equal(r.group, 'set-s-app');
        await sleep(200);
        writeFileSync(`${OUT}${tag}-shot-row.png`, await shoot(p, ''));
        const found = await ev(p, `import('./js/settings.js').then(m=>m.searchItems(${JSON.stringify(L(lang, '截图', 'screenshot'))}).some(n=>n.id==='set-shot-row'))`);
        assert.ok(found, 'Settings search finds it');
        return r;
      } finally { await p.dispose(); }
    });

    await C(`page-end ${tag}`, async () => {
      const p = await paired({ w, h, lang });
      try {
        await ev(p, `import('./js/settings.js').then(m=>m.open())`); await sleep(400);
        const m = await ev(p, `(()=>{const main=${$('set-main')};main.scrollTop=main.scrollHeight;const cs=getComputedStyle(main);
          const kit=${$('set-brandkit')}.getBoundingClientRect(),box=main.getBoundingClientRect();
          return {pad:parseFloat(cs.paddingBottom),gap:Math.round(box.bottom-kit.bottom),arc:getComputedStyle(document.documentElement).getPropertyValue('--corner-arc').trim(),
            coarse:matchMedia('(pointer:coarse)').matches}})()`);
        await sleep(200);
        writeFileSync(`${OUT}${tag}-settings-end.png`, await shoot(p, ''));
        const want = 24 + (m.coarse ? 28 : 0);   // --space-section (1.5rem) + the arc; safe area 0 in headless
        assert.ok(m.pad >= want - 0.5, 'padding-bottom = --page-end ' + JSON.stringify(m));
        assert.ok(m.gap >= want - 1, 'the Brand Kit row keeps the gap below it ' + JSON.stringify(m));
        return m;
      } finally { await p.dispose(); }
    });
  }
} finally {
  rec.write();
  writeFileSync(OUT + 'p398-result.json', JSON.stringify({ scope: 'real page + local fake host, headless Chromium (not a physical device; safe areas are 0)', failed, results }, null, 2));
  await B.close(); await fake.stop(); await web.stop();
}
console.log(failed ? `P398: ${failed} failed` : 'P398: all passed');
process.exit(failed ? 1 : 0);
