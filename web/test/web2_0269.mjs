// 0.26.9 lane w4-web2 — real page (encrypted fake host, Chromium) for the leftover web items; unit half: web2_0269.test.mjs.
//   #432 a code block's copy control is the shared copy glyph (44 px, no word) and still copies exactly.
//   #431 a host notice (「Agent J 主机上的输入」, no reply) keeps its reply card with one grey line — never an empty screen.
//   #438 a big desktop window (counts as a remote screen): an arrow key marks the card in its own text colour and the mark
//        is gone by itself after RING_MS (and at once on a pointer) — no white ring / dark band left until a reload.
//   #430 the background task dialog: every row ends in an @ (≥ 44 px); a tap closes the dialog and puts the task's mention
//        (who · what · task id) in the composer, shown as one chip.
//   #442 the locked recording card says the rule (≤ T s → text, longer → voice attachment).
// 390 × 852 and 1440 × 900 (1700 × 900 for #438), dark + light, zh + en. Shots → $W4WEB2_OUT (default reports/qa/p0269-w4web2/);
// W4WEB2_PUBLIC=<old public/> takes the "before" shots from another tree (checks are skipped there).
import assert from 'node:assert/strict';
import { mkdirSync, writeFileSync } from 'node:fs';
import { startWebServer } from './serve.mjs';
import { startFakeHost } from './fakehost.mjs';
import { launch, newPage, navigate, waitState, waitFor, evaluate, sleep, shoot as grab, key } from './browser.mjs';
import { dictionaries } from '../build.mjs';

const D = dictionaries();
const BEFORE = !!process.env.W4WEB2_PUBLIC;
const OUT = (process.env.W4WEB2_OUT || 'reports/qa/p0269-w4web2/') + (BEFORE ? 'before/' : 'after/');
mkdirSync(OUT, { recursive: true });
const web = await startWebServer({ port: 0, relayCsp: 'ws://127.0.0.1:*', ...(BEFORE ? { publicDir: process.env.W4WEB2_PUBLIC } : {}) });
const fake = await startFakeHost(); const B = await launch();
let checks = 0;
const check = (ok, label) => { if (BEFORE) return; assert.ok(ok, label); checks++; };
const ev = (p, e) => evaluate(p, e);
const shoot = async (p, file) => writeFileSync(file, await grab(p, file));
const NOW = () => Math.floor(Date.now() / 1000);
const BUSY = { zh: [['掌枢', 'P160 harness 对等：Codex 子 Agent 接进在办机器人', 'ceo'], [null, '检查依赖版本，列出要升级的包', 'temp']],
  en: [['Relay', 'P160 harness parity: Codex sub-agents as robots', 'ceo'], [null, 'Check dependency versions, list upgrades', 'temp']] };
async function mouse(p, sel, hold = 60) {
  const r = await ev(p, `(()=>{const r=document.querySelector(${JSON.stringify(sel)}).getBoundingClientRect();return {x:r.x+r.width/2,y:r.y+r.height/2}})()`);
  await p.send('Input.dispatchMouseEvent', { type: 'mouseMoved', x: r.x, y: r.y });
  await p.send('Input.dispatchMouseEvent', { type: 'mousePressed', x: r.x, y: r.y, button: 'left', clickCount: 1 });
  await sleep(hold);
  await p.send('Input.dispatchMouseEvent', { type: 'mouseReleased', x: r.x, y: r.y, button: 'left', clickCount: 1 });
}
async function paired(w, h, scheme, lang) {
  const p = await newPage(B, w, h, scheme, { allow: [web.url, fake.relay + '/'] });
  await navigate(p, fake.newPairing(web.url + '?lang=' + lang)); await waitState(p, 'awaiting-approval');
  await fake.approve(); await waitState(p, 'ready'); await sleep(300);
  return p;
}
try {
  for (const [w, h, scheme, lang] of [[390, 852, 'dark', 'zh'], [390, 852, 'light', 'en'], [1440, 900, 'light', 'zh'], [1440, 900, 'dark', 'en']]) {
    const tag = `${w}-${scheme}-${lang}`, L = D[lang];
    const p = await paired(w, h, scheme, lang);
    try {
      // #432 — code block copy glyph
      await fake.addTurn({ k: 'phone', text: 'cmd?' }, 'Run:\n\n```bash\nchown -R olares: /home/olares/.ssh\n```\n', 'done');
      await waitFor(p, `!!document.querySelector('#words .codeblock .copy')`);
      const cb = await ev(p, `(()=>{const b=document.querySelector('#words .codeblock .copy');const r=b.getBoundingClientRect();return {txt:b.textContent.trim(),svg:!!b.querySelector('svg'),w:r.width,h:r.height,aria:b.getAttribute('aria-label'),right:r.right,vw:innerWidth}})()`);
      check(cb.txt === '' && cb.svg, tag + ' #432 glyph, no word ' + JSON.stringify(cb));
      check(cb.w >= 44 && cb.h >= 44 && cb.right <= cb.vw, tag + ' #432 44 px target inside the screen');
      check(cb.aria === L['r.md.copyAria'], tag + ' #432 aria-label from the dictionary');
      await shoot(p, `${OUT}${tag}-432-codeblock.png`);
      await ev(p, `document.querySelector('#words .codeblock .copy').click()`);
      await sleep(250);
      check((await ev(p, `navigator.clipboard.readText().catch(()=> 'ERR')`)) === 'chown -R olares: /home/olares/.ssh', tag + ' #432 copies the code exactly');
      await shoot(p, `${OUT}${tag}-432-copied.png`);
      await sleep(1900);                                 // the 「已复制」 notice goes first
      // #431 — host notice keeps a reply card
      await fake.addTurn({ k: 'host', text: lang === 'zh' ? '11-03 12:18 × Codex → Claude Code：Claude Code 没能完成一次调用' : '11-03 12:18 × Codex → Claude Code: Claude Code could not finish a call' }, '', 'done');
      await sleep(400);
      for (let i = 0; i < 12 && !(await ev(p, `document.querySelector('#rm').classList.contains('host-notice')`)); i++) { await ev(p, `import('./js/relay.js').then(m=>m.remoteMessage(1))`); await sleep(150); }
      try { await waitFor(p, `document.querySelector('#rm').classList.contains('host-notice')`); }
      catch (e) { console.log('DEBUG', await ev(p, `JSON.stringify({om:document.getElementById('omText')?.textContent,pg:document.getElementById('pg').textContent,cls:document.getElementById('rm').className,words:document.getElementById('words').textContent.slice(0,80)})`)); throw e; }
      await sleep(300);
      const nb = await ev(p, `(()=>{const m=document.querySelector('#main'),r=document.querySelector('#rm').getBoundingClientRect();return {main:getComputedStyle(m).display,h:r.height,bg:getComputedStyle(document.querySelector('#rm')).backgroundColor,words:document.querySelector('#words').textContent.trim(),empty:document.querySelector('#words').classList.contains('empty')}})()`);
      check(nb.main !== 'none' && nb.h > 100 && nb.bg !== 'rgba(0, 0, 0, 0)', tag + ' #431 the reply card is drawn ' + JSON.stringify(nb));
      check(nb.words === L['p0269w4.notice'] && nb.empty, tag + ' #431 one grey line');
      await shoot(p, `${OUT}${tag}-431-host-notice.png`);
      // #430 — @ on every background task row
      await fake.send({ t: 'busy', by: 'Agent J', tasks: BUSY[lang].map(([who, label, kind], i) => ({ id: 'agentj-busy-k' + i, who, kind, label, since: NOW() - 700, c: i })) });
      await waitFor(p, `!document.getElementById('busyDots').hidden`);
      await ev(p, `document.getElementById('busyDots').click()`);
      await waitFor(p, `!document.getElementById('busyDlg').hidden && document.querySelectorAll('#busyList li').length===2`);
      await sleep(300);
      const at = await ev(p, `[...document.querySelectorAll('#busyList .busy-at')].map(b=>{const r=b.getBoundingClientRect();const li=b.closest('li').getBoundingClientRect();return {w:r.width,h:r.height,in:r.right<=li.right+7&&r.left>=li.left,aria:b.getAttribute('aria-label')}})`);
      check(at.length === 2 && at.every((a) => a.w >= 44 && a.h >= 44 && a.in && a.aria), tag + ' #430 an @ per row ' + JSON.stringify(at));
      await shoot(p, `${OUT}${tag}-430-busy-at.png`);
      if (!BEFORE) {
        await ev(p, `document.querySelectorAll('#busyList .busy-at')[0].click()`);
        await sleep(400);
        const r = await ev(p, `({dlg:document.getElementById('busyDlg').hidden,val:document.getElementById('input').value,chip:[...document.querySelectorAll('#workflow-compose .workflow-token')].map(x=>x.textContent)})`);
        check(r.dlg === true, tag + ' #430 the dialog closes');
        check(r.val.includes(BUSY[lang][0][0]) && r.val.includes('agentj-busy-k0') && r.val.startsWith('@'), tag + ' #430 the mention text carries who + id ' + r.val);
        check(r.chip.includes('@' + BUSY[lang][0][0]), tag + ' #430 shown as one chip ' + JSON.stringify(r.chip));
        await shoot(p, `${OUT}${tag}-430-mentioned.png`);
        await ev(p, `(()=>{const i=document.getElementById('input');i.value='';i.dispatchEvent(new Event('input'));})()`);
      }
      await fake.send({ t: 'busy', by: 'Agent J', tasks: [] }); await sleep(200);
      // #442 — the locked recording card says the rule
      if (w === 390) {
        await ev(p, `(()=>{const m=document.getElementById('mic');return m&&m.click&&true})()`);
        await mouse(p, '#mic', 80);                       // a tap = lock on release (P272)
        await sleep(1600);
        const seg = await ev(p, `(()=>{const s=document.getElementById('pttSeg');return s?{hidden:s.hidden,txt:s.textContent,lock:document.getElementById('ptt')?.classList.contains('lock')}:null})()`);
        check(seg && !seg.hidden && seg.txt.includes(L['p0269w4.ptt.ruleText'].split('{s}')[1].slice(0, 6)), tag + ' #442 the rule line on the recording card ' + JSON.stringify(seg));
        await shoot(p, `${OUT}${tag}-442-recording.png`);
        await key(p, 'Escape'); await sleep(400);
      }
    } finally { await p.close(); }
  }
  // #438 — a big desktop window: the remote focus mark
  for (const [scheme, lang] of [['dark', 'zh'], ['light', 'en']]) {
    const tag = `1700-${scheme}-${lang}`;
    const p = await paired(1700, 900, scheme, lang);
    try {
      await fake.addTurn({ k: 'phone', text: 'hi' }, 'A reply long enough to fill the card.\n\n' + 'Line.\n\n'.repeat(12), 'done');
      await sleep(500);
      await ev(p, `document.activeElement && document.activeElement.blur()`);
      await key(p, 'Enter');
      await sleep(200);
      const m = await ev(p, `(()=>{const n=document.querySelector('[data-tv-focus]');if(!n)return null;const s=getComputedStyle(n);return {id:n.id,oc:s.outlineColor,c:s.color,bs:s.boxShadow}})()`);
      check(m && m.id === 'rm' && m.oc === m.c && m.bs === 'none', tag + ' #438 the mark is in the card text colour, no dark band ' + JSON.stringify(m));
      await shoot(p, `${OUT}${tag}-438-marked.png`);
      await sleep(4400);
      check(!(await ev(p, `!!document.querySelector('[data-tv-focus]')`)), tag + ' #438 gone by itself after RING_MS');
      await shoot(p, `${OUT}${tag}-438-after.png`);
      await key(p, 'Enter'); await sleep(150);
      await mouse(p, '#words');
      check(!(await ev(p, `!!document.querySelector('[data-tv-focus]')`)), tag + ' #438 a pointer clears it at once');
    } finally { await p.close(); }
  }
  console.log((BEFORE ? 'BEFORE shots → ' + OUT : 'PASS w4-web2 real page ' + checks + ' checks → ' + OUT));
} finally { await B.close(); await fake.stop(); await web.stop(); }
