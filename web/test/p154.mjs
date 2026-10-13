#!/usr/bin/env node
// P154 phone-page fixes, real page + local fake host, independent headless Chromium (never :9222).
//   a) approval card: 长按批准 first (on top at 390 px, left at 1280 px) and the larger one; a long command folds to 2 lines
//      with 展开全部 / 收起; the copy icon copies the WHOLE command; a short command has no 展开; 长按批准 visible without
//      scrolling the sheet
//   b) Settings 提示音音量: default 15 %, persists, 0 = 静音 (no whoosh), the level reaches a Web Audio gain (iPhone
//      ignores <audio>.volume), the mute button restores the previous level
//   c) the 「在办」 dialog shows each whole title on one line, ellipsised, every row the same width and the titles in one
//      column (item 3's flowing dots were replaced by the robots of k)
//   d) 截图自动进附件 (computer Chromium): off by default; on → a new clipboard image after a short away/back goes into the
//      tray once (not again for the same image, not after a long absence); 10 attachments → the 10-limit message
//   e) 追加 2: ⌘/Ctrl+Enter approves the open card (same signed path as the hold), even from the text box (which keeps its
//      text, nothing sent); the ⌘↩/Ctrl↩ hint on a computer only; with no card, Ctrl+Enter in the box still sends
//   f) 追加 3: one click on inline `code` copies it (a flash + 「已拷贝」, title 点击复制); a code block is not inline; a press
//      that selected text does not copy
//   g) 追加 4: the load line under the reply header — 追加 6: fixed at half the card's content width, one way 6 s at load 0 … 1 s at 1 (linear),
//      no glide without a load or while disconnected, still under reduced motion; light + dark shots
//   h) 追加 5: no ghost card behind the source card; the line exactly half the card's content width (追加 6);
//      the name bold with a text shadow, the time not; a tap on the line opens the explainer (load now %, three sample
//      lines gliding 6 s / 3 s / 1 s), Esc / 知道了 closes it
//   i) 追加 8: "/" outside the field = focus the field with "/" typed after the draft; an empty draft opens the command menu;
//      not while Settings is open, not from another field; "i" unchanged
//   j) 追加 9: a ⚠ card's button reads 长按批准 / Hold to approve too (「这一条」 gone), the ⌘↩ / Ctrl↩ hint inside the button
//   k) 追加 10: one original pixel robot per worker, 18 px in the header from the native 18×18 sheet, 24 px in the dialog
//      (12×12 at 2×); crisp at 1× / 2× / 3× (every rendered pixel is a sprite colour or the card); its row = the worker's
//      colour; dancing out of step; a new one pops in, a finished one bows away and the rest are the same nodes; six fit
//      at 390 px right of the time; reduced motion stills them and a finished one just goes
//   l) 追加 12: hold the grey (empty) Send ~450 ms → a vertical slider grows up out of it, the same 提示音音量 (live +
//      persisted); ↑/↓ from the keyboard, Esc closes back to the field; hold-and-slide with a finger sets it and letting go
//      closes; with text in the box a hold never opens it
//   m) 追加 13: the logo's text-size step survives a reload (the iPhone home-screen app reopening is the same path);
//      a hold no longer resets (P207 追加 2: hold = first unread); the tap cycle reaches 中 and forgets it
// Shots → reports/qa/p154/screens/*.png
import assert from 'node:assert/strict';
import { mkdirSync, writeFileSync } from 'node:fs';
import { join } from 'node:path';
import { fileURLToPath } from 'node:url';
import { webcrypto } from 'node:crypto';
import { inflateSync } from 'node:zlib';
import { parityRecorder } from '../../parity/lib.mjs';
import { startWebServer } from './serve.mjs';
import { startFakeHost } from './fakehost.mjs';
import { launch, newPage, navigate, waitFor, waitState, evaluate, shoot, sleep, key } from './browser.mjs';

const ROOT = fileURLToPath(new URL('../../../', import.meta.url));
const OUT = join(ROOT, 'reports/qa/p154/screens');
const $ = (id) => `document.getElementById('${id}')`;
const hex = (n) => Array.from(webcrypto.getRandomValues(new Uint8Array(n)), (b) => b.toString(16).padStart(2, '0')).join('');
export const CASES = { a: 'approval-allow-first-fold-copy', b: 'send-sound-volume', c: 'busy-full-titles', d: 'screenshot-to-attachments',
  e: 'approval-hotkey', f: 'inline-code-click-copy', g: 'load-divider', h: 'load-explainer-bold-name-no-ghost',
  i: 'slash-key-into-field', j: 'approval-hold-label-short', k: 'busy-robots', l: 'send-hold-volume-slider', m: 'page-text-size-remembered' };
const LONG_CMD = 'cd /srv/me/work/app/.worktrees/p154-ux-batch && git fetch origin && git rebase origin/main && ' +
  'python3 -m unittest discover -s host/tests -p "test_*.py" 2>&1 | tee /tmp/p154-host.log | tail -n 40 && ' +
  'node web/build.mjs && node --test web/test/*.test.mjs && rsync -a --delete agentjarvis/site/public/ /srv/www/agentj/ && ' +
  'systemctl --user restart agentj-serve.service && journalctl --user -u agentj-serve -n 80 --no-pager';
const S = {
  zh: { more: '展开全部', less: '收起', off: '静音', added: /刚截的图已放进附件/, tooMany: /最多 10 个附件/, copied: '已拷贝', tap: '点击复制',
    reply: '在终端里跑 `git status -sb` 看一下，然后：\n\n```sh\nnpm test\n```',
    titles: ['把 0.18.5 的发布说明整理成中英两版，再对一遍安装校验值', '巡检代理节点并把延迟最高的三个换掉', '转录昨天的会议录音'] },
  en: { more: 'Show all', less: 'Show less', off: 'Muted', added: /Screenshot added/, tooMany: /At most 10 attachments/, copied: 'Copied', tap: 'Click to copy',
    reply: 'Run `git status -sb` in a terminal, then:\n\n```sh\nnpm test\n```',
    titles: ['Write the 0.18.5 release notes in both languages and recheck the install checksums', 'Scan the proxy nodes and replace the three slowest', 'Transcribe yesterday\'s meeting'] },
};
const box = (p, sel) => evaluate(p, `(()=>{const e=document.querySelector(${JSON.stringify(sel)});if(!e)return null;const r=e.getBoundingClientRect();return {l:r.left,r:r.right,t:r.top,b:r.bottom,w:r.width,h:r.height}})()`);

/** A PNG (8-bit RGB/RGBA, as Chromium writes them) → ['r,g,b', …] per pixel. */
function decodePng(buf) {
  let o = 8, w = 0, h = 0, ch = 4; const idat = [];
  while (o < buf.length) {
    const n = buf.readUInt32BE(o), t = buf.toString('ascii', o + 4, o + 8), d = buf.subarray(o + 8, o + 8 + n);
    if (t === 'IHDR') { w = d.readUInt32BE(0); h = d.readUInt32BE(4); ch = d[9] === 6 ? 4 : 3; }
    if (t === 'IDAT') idat.push(d);
    o += 12 + n;
  }
  const raw = inflateSync(Buffer.concat(idat)), stride = w * ch, out = [], prev = Buffer.alloc(stride), cur = Buffer.alloc(stride);
  for (let y = 0; y < h; y++) {
    const f = raw[y * (stride + 1)], row = raw.subarray(y * (stride + 1) + 1, (y + 1) * (stride + 1));
    for (let x = 0; x < stride; x++) {
      const a = x >= ch ? cur[x - ch] : 0, b = prev[x], c = x >= ch ? prev[x - ch] : 0;
      const pr = () => { const q = a + b - c, pa = Math.abs(q - a), pb = Math.abs(q - b), pc = Math.abs(q - c); return pa <= pb && pa <= pc ? a : pb <= pc ? b : c; };
      cur[x] = (row[x] + [0, a, b, (a + b) >> 1, f === 4 ? pr() : 0][f]) & 255;
    }
    for (let x = 0; x < w; x++) out.push(`${cur[x * ch]},${cur[x * ch + 1]},${cur[x * ch + 2]}`);
    cur.copy(prev);
  }
  return out;
}

async function paired(B, web, fake, lang, w, h, opts = {}) {
  fake.reset();
  const p = await newPage(B, w, h, opts.theme || 'light', { touch: !!opts.touch, allow: [web.url, fake.relay + '/'] });
  if (opts.reduce) await p.send('Emulation.setEmulatedMedia', { features: [{ name: 'prefers-reduced-motion', value: 'reduce' }, { name: 'prefers-color-scheme', value: opts.theme || 'light' }] });
  await p.send('Emulation.setFocusEmulationEnabled', { enabled: true }).catch(() => {});
  await navigate(p, fake.newPairing(web.url + '?lang=' + lang)); await waitState(p, 'awaiting-approval'); await fake.approve(); await waitState(p, 'ready');
  await evaluate(p, `(()=>{${$('phone-env-dismiss')}?.click(); const n=${$('confirm-no')}; if(n&&!${$('confirm')}.hidden)n.click(); const a=${$('a2hs-ok')}; if(a&&!${$('a2hs')}.hidden)a.click();})()`);
  await fake.send({ t: 'status', s: 'idle', agent: 'claude', name: 'AgentJ-Father' });
  return p;
}

export async function runP154() {
  mkdirSync(OUT, { recursive: true });
  const rec = parityRecorder('web/test/p154.mjs');
  const web = await startWebServer({ port: 0, relayCsp: 'ws://127.0.0.1:*' });
  const fake = await startFakeHost();
  const B = await launch();
  let checks = 0;
  const check = (ok, label) => { assert.ok(ok, label); checks++; };
  const ran = {};
  const only = process.env.P154_ONLY || '';   // e.g. P154_ONLY=k: a quick local re-run of one case (the gate runs all)
  const step = async (c, f) => { if (only && !only.includes(c)) return; try { await f(); ran[c] = ran[c] !== false; } catch (e) { ran[c] = false; throw e; } };
  try {
    for (const lang of ['zh', 'en']) {
      const s = S[lang];
      // ---------------------------------------------------------------- a) approval card, phone + computer
      await step('a', async () => {
        for (const [w, h, touch, tag] of [[390, 844, true, 'phone'], [1280, 800, false, 'desktop']]) for (const theme of tag === 'phone' ? ['light', 'dark'] : ['light']) {
          const p = await paired(B, web, fake, lang, w, h, { touch, theme });
          try {
            await fake.send({ t: 'status', s: 'waiting', agent: 'claude', name: 'AgentJ-Father' });
            await fake.ask({ id: hex(16), tool: 'Bash', summary: LONG_CMD + (lang === 'zh' ? '\n— 跑完整门禁再重启服务' : '\n— Run the gates and restart'), ttl: 120 });
            await waitFor(p, `document.body.dataset.sheet==='1' && !${$('apprBtns')}.hidden && ${$('cmd')}.textContent.length>100`);
            await sleep(700);
            // the sheet slides up: wait until 长按批准 stops moving (a fixed sleep raced it under a loaded gate run)
            for (let i = 0, prev = null; i < 50; i++) {
              const b = await box(p, '#apprAllow');
              const moving = await evaluate(p, `${$('sheet')}.getAnimations({subtree:true}).some(a=>a.playState==='running')`);
              if (prev && b.t === prev.t && !moving) break;
              prev = b; await sleep(100);
            }
            check(await evaluate(p, `${$('apprAllow')}.compareDocumentPosition(${$('apprDeny')}) & Node.DOCUMENT_POSITION_FOLLOWING`) > 0, 'allow comes first in the DOM');
            const al = await box(p, '#apprAllow'), de = await box(p, '#apprDeny');
            if (tag === 'phone') check(al.b <= de.t + 1, `phone: 长按批准 above 拒绝 (${al.b} / ${de.t})`);
            else check(al.r <= de.l + 1 && Math.abs((al.t + al.h / 2) - (de.t + de.h / 2)) < 2, `computer: 长按批准 left of 拒绝 (${al.r} / ${de.l})`);
            check(al.h > de.h && (tag === 'phone' || al.w > de.w), `allow is the larger (${al.w}x${al.h} vs ${de.w}x${de.h})`);
            check(al.b <= h && al.t >= 0, `长按批准 visible without scrolling (${al.t}..${al.b} in ${h})`);
            const pre = await box(p, '#cmd');
            const lh = await evaluate(p, `parseFloat(getComputedStyle(${$('cmd')}).lineHeight)`);
            check(pre.h <= 2 * lh + 1, `command folded to 2 lines (${pre.h} ≤ ${2 * lh})`);
            check(await evaluate(p, `!${$('cmdMore')}.hidden && ${$('cmdMoreLbl')}.textContent`) === s.more, 'show-all appears');
            await writeFileSync(join(OUT, `${lang}-${tag}-${theme}-approval-folded.png`), await shoot(p, ''));
            await evaluate(p, `${$('cmdMore')}.click()`);
            check((await box(p, '#cmd')).h > pre.h + lh, 'expanded');
            check(await evaluate(p, `${$('cmdMoreLbl')}.textContent`) === s.less && await evaluate(p, `${$('cmdMore')}.getAttribute('aria-expanded')`) === 'true', 'show-less');
            if (theme === 'light') writeFileSync(join(OUT, `${lang}-${tag}-approval-open.png`), await shoot(p, ''));
            await evaluate(p, `${$('cmdMore')}.click()`);
            check((await box(p, '#cmd')).h <= 2 * lh + 1, 'folded again');
            await evaluate(p, `(()=>{window.__copied=null;Object.defineProperty(navigator.clipboard,'writeText',{configurable:true,value:async(t)=>{window.__copied=t;}});})()`);
            await evaluate(p, `${$('cmdCopy')}.click()`);
            await waitFor(p, `window.__copied!==null`);
            check(await evaluate(p, `window.__copied`) === LONG_CMD, 'the copy icon copies the whole command (without the description)');
            check(await evaluate(p, `!${$('cmdDesc')}.hidden && ${$('cmdDesc')}.textContent`) === (lang === 'zh' ? '跑完整门禁再重启服务' : 'Run the gates and restart'), 'the readable description stands above the command');
            // a short command: nothing to unfold, no fade (answer the long one first; asks queue oldest first)
            await evaluate(p, `${$('apprDeny')}.click()`);
            await fake.ask({ id: hex(16), tool: 'Bash', summary: 'git status', ttl: 120 });
            await waitFor(p, `${$('cmd')}.textContent==='git status'`);
            await sleep(200);
            check(await evaluate(p, `${$('cmdMore')}.hidden && !${$('cmd')}.classList.contains('clip')`), 'short command: no show-all, no fade');
            // 追加 ①: the copy icon sits at the right of the title row (「运行一条命令」 ⧉), not under the command
            const ti = await box(p, '#sheetTitle'), cp = await box(p, '#cmdCopy'), cb = await box(p, '#cmdBox');
            check(cp.l >= ti.r - 1 && cp.t < ti.b && cp.b > ti.t && cp.b <= cb.t + 1, `copy icon at the right of the title (${JSON.stringify([ti, cp])})`);
            // 追加 ③: the remote that holds 长按批准 puts the card away at once; the host's word only confirms
            await evaluate(p, `${$('apprDeny')}.click()`);              // one tap: put away at once too
            await waitFor(p, `document.body.dataset.sheet==='0'`, 1500);
            await sleep(400);
            fake.st.holdAnswers = true;
            const hold = async (id, summary) => {
              await fake.ask({ id, tool: 'Bash', summary, ttl: 120 });
              await waitFor(p, `document.body.dataset.sheet==='1' && ${$('cmd')}.textContent===${JSON.stringify(summary)} && !${$('apprBtns')}.hidden`);
              await sleep(200);
              await evaluate(p, `${$('apprAllow')}.dispatchEvent(new PointerEvent('pointerdown',{bubbles:true,button:0,pointerType:'touch'}))`);
              await waitFor(p, `document.body.dataset.sheet==='0'`, 2500);
              const n = fake.st.answers.length;
              check(n && fake.st.answers[n - 1].id === id && fake.st.answers[n - 1].ok === true, 'the hold was sent');
              check(await evaluate(p, `document.body.dataset.sheet==='0' && ${$('pendTag')}.hidden`), 'put away before the host answered, no pending tag');
            };
            const id1 = hex(16);
            await hold(id1, 'ls -la');
            await fake.send({ t: 'ask_done', id: id1, result: 'allow' });
            await sleep(500);
            check(await evaluate(p, `document.body.dataset.sheet`) === '0', 'the host confirms: it stays away');
            const id2 = hex(16);
            await hold(id2, 'du -sh .');
            await fake.send({ t: 'ask_done', id: id2, result: 'timeout' });
            await waitFor(p, `document.body.dataset.sheet==='1' && !${$('askState')}.hidden`);
            check(await evaluate(p, `${$('askState')}.textContent`) === await evaluate(p, `import('./js/t.js').then(m=>m.t('r.appr.st.timeout'))`), 'the host says it timed out first: the card comes back to say so');
            fake.st.holdAnswers = false;
            check(!p.problems.length && !p.offsite.length, 'no CSP/network errors ' + JSON.stringify(p.problems.slice(0, 2)));
          } finally { await p.dispose(); }
        }
      });

      // ---------------------------------------------------------------- b) Settings 提示音音量
      await step('b', async () => {
        const p = await paired(B, web, fake, lang, 390, 844, { touch: true });
        try {
          await evaluate(p, `localStorage.removeItem('aj.sfxVolume')`);
          await navigate(p, web.url + '?lang=' + lang); await waitState(p, 'ready');
          await evaluate(p, `import('./js/settings.js').then(m=>m.open())`);
          await waitFor(p, `!${$('settings')}.hidden`);
          check(await evaluate(p, `${$('set-sfx')}.value`) === '15', 'default 15 %');
          check(await evaluate(p, `${$('set-sfx-val')}.textContent`) === '15%', 'shows 15%');
          await evaluate(p, `${$('set-sfx-row')}.scrollIntoView({block:'center'})`);
          writeFileSync(join(OUT, `${lang}-settings-volume.png`), await shoot(p, ''));
          await evaluate(p, `(()=>{const r=${$('set-sfx')};r.value='40';r.dispatchEvent(new Event('input'));r.dispatchEvent(new Event('change'));})()`);
          const dbg = await evaluate(p, `import('./js/sfx.js').then(m=>m._debug())`);
          check(dbg.vol === 40 && dbg.webAudio && Math.abs(dbg.gain - 0.16) < 1e-6, 'the level reaches the Web Audio gain ' + JSON.stringify(dbg));
          check(await evaluate(p, `localStorage.getItem('aj.sfxVolume')`) === '40', 'persisted');
          await evaluate(p, `${$('set-sfx-mute')}.click()`);
          check(await evaluate(p, `${$('set-sfx-val')}.textContent`) === s.off && await evaluate(p, `${$('set-sfx-mute')}.getAttribute('aria-pressed')`) === 'true', 'mute');
          check(await evaluate(p, `${$('set-sfx-test')}.disabled`), 'nothing to preview at 0');
          writeFileSync(join(OUT, `${lang}-settings-volume-muted.png`), await shoot(p, ''));
          await navigate(p, web.url + '?lang=' + lang); await waitState(p, 'ready');
          check(await evaluate(p, `import('./js/sfx.js').then(m=>m.volume())`) === 0, 'mute survives a reload');
          check(await evaluate(p, `localStorage.getItem('aj.sfxVolume')`) === '-40', 'muted is stored as the level it was at, negated');
          // a send at 0 makes no whoosh
          await evaluate(p, `${$('input')}.value='p154 silent';${$('input')}.dispatchEvent(new Event('input'))`);
          await evaluate(p, `${$('send')}.click()`);
          await waitFor(p, `${$('omText')}.textContent==='p154 silent'`);
          await sleep(1500);
          check(!(+(await evaluate(p, `document.body.dataset.whoosh||0`))), 'no whoosh when muted');
          await evaluate(p, `import('./js/settings.js').then(m=>m.open())`);
          await evaluate(p, `${$('set-sfx-mute')}.click()`);
          check(await evaluate(p, `${$('set-sfx')}.value`) === '40', 'un-mute restores the previous level');
          await evaluate(p, `${$('set-close')}.click()`);
          await evaluate(p, `${$('input')}.value='p154 loud';${$('input')}.dispatchEvent(new Event('input'))`);
          await evaluate(p, `${$('send')}.click()`);
          await waitFor(p, `+(document.body.dataset.whoosh||0)>=1`, 6000);
          check(true, 'whoosh plays at 40 %');
        } finally { await p.dispose(); }
      });

      // ---------------------------------------------------------------- c) whole titles in the dialog
      await step('c', async () => {
        for (const theme of ['light', 'dark']) {
          const p = await paired(B, web, fake, lang, 390, 844, { touch: true, theme });
          try {
            await fake.send({ t: 'status', s: 'working', agent: 'claude', name: 'AgentJ-Father' });
            const tasks = s.titles.map((label, i) => ({ id: 'k' + i, who: i === 1 ? (lang === 'zh' ? '玄枢' : 'Xuanshu') : i === 0 ? null : (lang === 'zh' ? '掌枢' : 'Relay'), kind: i ? 'ceo' : 'temp', label, since: 1760000000 + i, c: i }));
            await fake.send({ t: 'busy', by: 'AgentJ-Father', tasks });
            await waitFor(p, `${$('busyDots')}.querySelectorAll('i.rb').length===3`);
            await evaluate(p, `${$('busyDots')}.click()`);
            await waitFor(p, `!${$('busyDlg')}.hidden`);
            const rows = await evaluate(p, `[...document.querySelectorAll('#busyList li')].map(li=>{const s=li.querySelector('span'),r=li.getBoundingClientRect(),q=s.getBoundingClientRect();
              return {text:s.textContent,w:r.width,sl:q.left,sh:q.height,lh:parseFloat(getComputedStyle(s).lineHeight),over:s.scrollWidth>s.clientWidth,ell:getComputedStyle(s).textOverflow}})`);
            check(rows.map((r) => r.text).join('|') === s.titles.join('|'), 'whole titles in the dialog');
            check(rows.every((r) => Math.abs(r.w - rows[0].w) < 0.5), 'every row the same width');
            check(rows.every((r) => Math.abs(r.sl - rows[0].sl) < 0.5), 'titles in one column');
            check(rows.every((r) => r.sh <= r.lh + 1 && r.ell === 'ellipsis'), 'one line each, ellipsis');
            check(rows[0].over, 'the long title is cut at the edge');
            await evaluate(p, `document.getAnimations().forEach(a=>{a.pause();a.currentTime=1800})`);
            writeFileSync(join(OUT, `${lang}-${theme}-dialog-titles.png`), await shoot(p, ''));
            await evaluate(p, `document.getAnimations().forEach(a=>a.play())`);
            await evaluate(p, `${$('busyClose')}.click()`);
          } finally { await p.dispose(); }
        }
      });

      // ---------------------------------------------------------------- k) 追加 10: pixel robots
      await step('k', async () => {
        const who = lang === 'zh' ? ['玄枢', '掌枢', '账房', '巡检', '译者', '剪辑'] : ['Xuanshu', 'Relay', 'Ledger', 'Patrol', 'Lingo', 'Cutter'];
        const mk = (n, from = 0) => Array.from({ length: n }, (_, i) => ({ id: 'r' + (i + from), who: who[(i + from) % 6], kind: 'ceo', label: s.titles[(i + from) % 3], since: 1760000000 + i + from, c: (i + from) % 9 }));
        const SPRITE = new Set(['113,187,222', '193,225,240', '106,155,204', '209,167,232', '235,216,245', '130,125,189', '247,130,126', '251,200,198',
          '217,119,87', '219,178,147', '239,221,207', '240,170,212', '248,218,236', '252,190,124', '254,226,197', '20,20,20']);
        for (const theme of ['light', 'dark']) for (const dpr of theme === 'light' ? [1, 2, 3] : [3]) {
          const p = await paired(B, web, fake, lang, 390, 844, { touch: true, theme });
          try {
            await p.send('Emulation.setDeviceMetricsOverride', { width: 390, height: 844, deviceScaleFactor: dpr, mobile: true });
            // the longest names the rename rule allows (8 CJK / 13 others), so the header is at its widest
            const NAME = lang === 'zh' ? '八个字的名字呀' + '吧' : 'AgentJ-Father';
            await fake.send({ t: 'status', s: 'working', agent: 'claude', name: NAME });
            await fake.send({ t: 'busy', by: NAME, tasks: mk(3) });
            await waitFor(p, `${$('busyDots')}.querySelectorAll('i.rb').length===3 && !${$('busyDots')}.hidden`);
            const r = await evaluate(p, `[...${$('busyDots')}.querySelectorAll('i.rb')].map(i=>{const cs=getComputedStyle(i),b=i.getBoundingClientRect();
              return {h:b.height,w:b.width,img:cs.backgroundImage,ir:cs.imageRendering,an:cs.animationName,d:cs.animationDelay,y:cs.backgroundPositionY,c:+i.dataset.c,r:+i.dataset.r}})`);
            check(r.every((x) => x.h === 18 && x.w === 18 && /robots-18\.png/.test(x.img) && x.ir === 'pixelated'), `18 px robots from the 18×18 sheet, pixelated ${JSON.stringify(r[0])}`);
            check(r.every((x) => x.an === 'rb-dance-18') && new Set(r.map((x) => x.d)).size === 3, 'dancing, out of step');
            check(r.every((x) => x.r === x.c && x.y === -x.r * 18 + 'px'), 'each wears its worker colour (robot = c while free; sprite row = robot)');
            check(!/tengen/.test(JSON.stringify(r)), 'only the original robots');
            // crisp: every pixel of a still robot is a sprite colour or the card behind it — no smoothing, at this DPR
            // (transitions — e.g. the page's grey fading out after connect — are finished first, not frozen half-way)
            await evaluate(p, `document.getAnimations().forEach(a=>{if(typeof CSSTransition!=='undefined'&&a instanceof CSSTransition)a.finish();else{a.pause();a.currentTime=0}})`);
            const rb = await box(p, '#busyDots i.rb');
            const bg = await evaluate(p, `getComputedStyle(${$('read')}).backgroundColor.match(/\\d+/g).slice(0,3).join(',')`);
            const pix = decodePng(Buffer.from((await p.send('Page.captureScreenshot', { format: 'png', clip: { x: rb.l, y: rb.t, width: 18, height: 18, scale: 1 } })).data, 'base64'));
            const back = pix[0];                       // the art's top-left pixel is always clear: what the card shows there
            const off = pix.filter((c) => !SPRITE.has(c) && c !== bg && c !== back);
            check(pix.length === 18 * 18 * dpr * dpr && off.length === 0, `crisp at ${dpr}× (${pix.length} px, ${off.length} not sprite/card: ${[...new Set(off)].slice(0, 4)})`);
            await evaluate(p, `document.getAnimations().forEach(a=>a.play())`);
            if (dpr === 3) {
              await evaluate(p, `document.getAnimations().forEach(a=>{a.pause();a.currentTime=1000})`);
              writeFileSync(join(OUT, `${lang}-${theme}-robots-3.png`), await shoot(p, ''));
              await evaluate(p, `document.getAnimations().forEach(a=>a.play())`);
            }
            if (dpr !== 3 || theme !== 'light') continue;
            // a new worker pops in; the three already dancing are the same nodes
            await evaluate(p, `[...${$('busyDots')}.querySelectorAll('i.rb')].forEach((i,k)=>i.dataset.mark='m'+k)`);
            await fake.send({ t: 'busy', by: NAME, tasks: mk(4) });
            await waitFor(p, `${$('busyDots')}.querySelectorAll('i.rb').length===4`);
            check(await evaluate(p, `(()=>{const l=[...${$('busyDots')}.querySelectorAll('i.rb')];return l.slice(0,3).every((i,k)=>i.dataset.mark==='m'+k) && l[3].classList.contains('in') && !l[3].dataset.mark})()`), 'the new one pops in, the rest keep dancing');
            // P207 追加 4: it dances while it pops, and a pop whose animationend never comes (window hidden) still ends
            check(await evaluate(p, `getComputedStyle(${$('busyDots')}.querySelectorAll('i.rb')[3]).animationName`) === 'rb-dance-18, rb-pop', 'dances underneath the pop');
            await fake.send({ t: 'busy', by: NAME, tasks: [...mk(4), ...mk(1, 9)] });
            await waitFor(p, `${$('busyDots')}.querySelectorAll('i.rb').length===5`);
            await evaluate(p, `document.getAnimations().filter(a=>a.animationName==='rb-pop').forEach(a=>a.pause())`);
            await sleep(900);
            check(await evaluate(p, `(()=>{const i=${$('busyDots')}.querySelectorAll('i.rb')[4];return !i.classList.contains('in') && getComputedStyle(i).animationName==='rb-dance-18'})()`), 'a lost animationend never leaves a robot standing still');
            await fake.send({ t: 'busy', by: NAME, tasks: mk(4) });
            await waitFor(p, `${$('busyDots')}.querySelectorAll('i.rb').length===4 && !${$('busyDots')}.querySelector('.bye')`, 4000);
            // a finished worker bows (the 13th frame) and floats away, then its slot closes
            await fake.send({ t: 'busy', by: NAME, tasks: mk(4).filter((x) => x.id !== 'r1') });
            await waitFor(p, `${$('busyDots')}.querySelector('i.rb.bye')`);
            check(await evaluate(p, `(()=>{const b=${$('busyDots')}.querySelector('i.rb.bye');return b.dataset.mark==='m1' && getComputedStyle(b).backgroundPositionX==='-216px' && getComputedStyle(b).animationName==='rb-bow'})()`), 'the finished one bows');
            await waitFor(p, `${$('busyDots')}.querySelectorAll('i.rb').length===3 && !${$('busyDots')}.querySelector('.bye')`, 4000);
            check(await evaluate(p, `[...${$('busyDots')}.querySelectorAll('i.rb')].map(i=>i.dataset.mark||'new').join()`) === 'm0,m2,new', 'the rest close up');
            // six at 390 px: right of the time, inside the card
            await fake.send({ t: 'busy', by: NAME, tasks: mk(6) });
            await waitFor(p, `${$('busyDots')}.querySelectorAll('i.rb:not(.bye)').length===6 && !${$('busyDots')}.querySelector('.bye')`, 4000);
            await sleep(500);
            const dots = await box(p, '#busyDots i.rb'), last = await evaluate(p, `(()=>{const l=[...${$('busyDots')}.querySelectorAll('i.rb')];const r=l[l.length-1].getBoundingClientRect();return r.right})()`);
            const tm = await evaluate(p, `Math.max(...['agent','rmTime','rmPart'].map(id=>{const e=${$('rmTime')}&&document.getElementById(id);return e&&!e.hidden&&e.offsetWidth?e.getBoundingClientRect().right:0}))`);
            const card = await box(p, '.rmtop>.rmhd');
            check(dots.l >= tm && last <= card.r + 0.5, `six robots right of the name / time (${dots.l} ≥ ${tm}), inside (${last} ≤ ${card.r})`);
            await evaluate(p, `document.getAnimations().forEach(a=>{a.pause();a.currentTime=600})`);
            writeFileSync(join(OUT, `${lang}-${theme}-robots-6.png`), await shoot(p, ''));
            await evaluate(p, `document.getAnimations().forEach(a=>a.play())`);
            // the dialog: 24 px robots (12×12 art at 2×), still ones in the list
            await fake.send({ t: 'busy', by: NAME, tasks: mk(3) });
            await waitFor(p, `${$('busyDots')}.querySelectorAll('i.rb:not(.bye)').length===3`);
            await evaluate(p, `${$('busyDots')}.click()`);
            await waitFor(p, `!${$('busyDlg')}.hidden`);
            const big = await evaluate(p, `[...document.querySelectorAll('#busyBig i.rb')].map(i=>{const cs=getComputedStyle(i),b={width:i.offsetWidth,height:i.offsetHeight};return [b.width,b.height,/robots-12\\.png/.test(cs.backgroundImage),cs.backgroundSize,cs.animationName].join()})`);
            check(big.length === 3 && big.every((x) => x === '24,24,true,312px 384px,rb-dance-24'), `24 px robots in the dialog ${big[0]}`);
            check(await evaluate(p, `[...document.querySelectorAll('#busyList li > i.rb')].every(i=>i.offsetHeight===24 && getComputedStyle(i).animationName==='rb-dance-24')`), '追加 11: a 24 px robot leads each row');
            await evaluate(p, `document.getAnimations().forEach(a=>{a.pause();a.currentTime=1000})`);
            writeFileSync(join(OUT, `${lang}-${theme}-robots-dialog.png`), await shoot(p, ''));
            await evaluate(p, `document.getAnimations().forEach(a=>a.play())`);
            await evaluate(p, `${$('busyClose')}.click()`);
            check(!p.problems.length, 'no CSP errors');
          } finally { await p.dispose(); }
        }
        for (const theme of ['dark']) {
          const p = await paired(B, web, fake, lang, 390, 844, { touch: true, theme });
          try {
            await fake.send({ t: 'busy', by: 'AgentJ-Father', tasks: mk(3) });
            await waitFor(p, `${$('busyDots')}.querySelectorAll('i.rb').length===3`);
            await evaluate(p, `${$('busyDots')}.click()`); await waitFor(p, `!${$('busyDlg')}.hidden`);
            await evaluate(p, `document.getAnimations().forEach(a=>{a.pause();a.currentTime=1000})`);
            writeFileSync(join(OUT, `${lang}-${theme}-robots-dialog.png`), await shoot(p, ''));
          } finally { await p.dispose(); }
        }
        const p = await paired(B, web, fake, lang, 390, 844, { touch: true, reduce: true });
        try {
          await fake.send({ t: 'busy', by: 'AgentJ-Father', tasks: mk(3) });
          await waitFor(p, `${$('busyDots')}.querySelectorAll('i.rb').length===3`);
          const st = await evaluate(p, `[...${$('busyDots')}.querySelectorAll('i.rb')].map(i=>getComputedStyle(i).animationName+' '+getComputedStyle(i).backgroundPositionX).join()`);
          check(/^(none 0(px|%),?){3}$/.test(st), 'reduced motion: standing still ' + st);
          await fake.send({ t: 'busy', by: 'AgentJ-Father', tasks: mk(3).slice(1) });
          await waitFor(p, `${$('busyDots')}.querySelectorAll('i.rb').length===2`, 600);
          check(!(await evaluate(p, `!!${$('busyDots')}.querySelector('.bye,.in')`)), 'reduced motion: no bow, no pop');
        } finally { await p.dispose(); }
        // 追加 11: 16 robots; one task keeps its robot; the header limit follows the computer's capacity (wide screens only)
        for (const [w, h] of [[390, 844], [1280, 800]]) {
          const q = await paired(B, web, fake, lang, w, h, { touch: w < 600 });
          try {
            const all = Array.from({ length: 16 }, (_, i) => ({ id: 'z' + i, who: null, kind: 'temp', label: 'job ' + i, since: 1760000000 + i, c: i % 9 }));
            await fake.send({ t: 'busy', by: 'AgentJ-Father', tasks: all, cap: 10 });
            await waitFor(q, `${$('busyDots')}.dataset.n==='16'`);
            const lim = w < 600 ? 6 : 10;
            const hd = await evaluate(q, `[${$('busyDots')}.querySelectorAll('i.rb').length, ${$('busyDots')}.querySelector('b')?.textContent]`);
            check(hd[0] === lim - 1 && hd[1] === '+' + (16 - lim + 1), `${w}px: ${lim - 1} robots + 「+${16 - lim + 1}」 with cap 10 (${hd})`);
            await evaluate(q, `${$('busyDots')}.click()`); await waitFor(q, `!${$('busyDlg')}.hidden`);
            const rs = await evaluate(q, `[...document.querySelectorAll('#busyList li > i.rb')].map(i=>i.dataset.id+'='+i.dataset.r)`);
            check(rs.length === 16 && new Set(rs.map((x) => x.split('=')[1])).size === 16, `the dialog lists all 16, 16 different robots (${rs.join(' ')})`);
            if (w > 600) writeFileSync(join(OUT, `${lang}-wide-robots-16.png`), await shoot(q, ''));
            await evaluate(q, `${$('busyClose')}.click()`);
            // the first five finish; the rest keep their robots, the new one gets a free one
            await fake.send({ t: 'busy', by: 'AgentJ-Father', tasks: [...all.slice(5), { id: 'zz', who: null, kind: 'temp', label: 'new', since: 1760000100, c: 0 }] });
            await waitFor(q, `${$('busyDots')}.dataset.n==='12'`);
            await evaluate(q, `${$('busyDots')}.click()`); await waitFor(q, `!${$('busyDlg')}.hidden`);
            const rs2 = await evaluate(q, `[...document.querySelectorAll('#busyList li > i.rb')].map(i=>i.dataset.id+'='+i.dataset.r)`);
            const was = Object.fromEntries(rs.map((x) => x.split('=')));
            check(rs2.slice(0, 11).every((x) => was[x.split('=')[0]] === x.split('=')[1]), 'the ones still working keep their robots');
            check(rs2[11] === 'zz=0', `the new one takes its colour's robot, now free (${rs2[11]})`);
            await evaluate(q, `${$('busyClose')}.click()`);
            // no cap from the computer → 6
            await fake.send({ t: 'busy', by: 'AgentJ-Father', tasks: all });
            await waitFor(q, `${$('busyDots')}.dataset.n==='16' && ${$('busyDots')}.querySelectorAll('i.rb:not(.bye)').length===5`, 4000);
            check(true, `${w}px: no cap → 5 + 「+11」`);
          } finally { await q.dispose(); }
        }
      });

      // ---------------------------------------------------------------- d) 截图自动进附件 (computer)
      await step('d', async () => {
        const phone = await paired(B, web, fake, lang, 390, 844, { touch: true });
        try {
          await evaluate(phone, `import('./js/settings.js').then(m=>m.open())`);
          await sleep(300);
          check(await evaluate(phone, `${$('set-shot-row')}.hidden`), 'phones: the switch is not offered');
        } finally { await phone.dispose(); }
        const p = await paired(B, web, fake, lang, 1280, 800, {});
        try {
          const shot = (seed) => evaluate(p, `(async()=>{const c=document.createElement('canvas');c.width=64;c.height=40;const g=c.getContext('2d');
            g.fillStyle='hsl(${seed * 37 % 360} 60% 50%)';g.fillRect(0,0,64,40);g.fillStyle='#fff';g.fillText('${seed}',8,24);
            const b=await new Promise(r=>c.toBlob(r,'image/png'));await navigator.clipboard.write([new ClipboardItem({'image/png':b})]);return b.size;})()`);
          const roundTrip = async (ms = 50) => { await evaluate(p, `window.dispatchEvent(new Event('blur'))`); await sleep(ms); await evaluate(p, `window.dispatchEvent(new Event('focus'))`); await sleep(400); };
          const chips = () => evaluate(p, `${$('tray')}.querySelectorAll('[role=listitem]').length`);
          await shot(1); await roundTrip();
          check(await chips() === 0, 'off by default: nothing taken');
          await evaluate(p, `import('./js/settings.js').then(m=>m.open())`);
          await waitFor(p, `!${$('set-shot-row')}.hidden`);
          await evaluate(p, `${$('set-shot-row')}.scrollIntoView({block:'center'})`);
          await evaluate(p, `${$('set-shot')}.click()`);
          await waitFor(p, `${$('set-shot')}.getAttribute('aria-checked')==='true'`);
          writeFileSync(join(OUT, `${lang}-desktop-settings-shot.png`), await shoot(p, ''));
          await evaluate(p, `${$('set-close')}.click()`);
          await roundTrip();
          check(await chips() === 0, 'the image already there when it was turned on is not a new screenshot');
          await shot(2); await roundTrip();
          await waitFor(p, `${$('tray')}.querySelectorAll('[role=listitem]').length===1`);
          check(s.added.test(await evaluate(p, `${$('toast')}.textContent`)), 'says it was added');
          writeFileSync(join(OUT, `${lang}-desktop-shot-added.png`), await shoot(p, ''));
          await roundTrip();
          check(await chips() === 1, 'the same image is not taken twice');
          await evaluate(p, `import('./js/shotclip.js').then(m=>m._debug())`);
          await shot(3);
          await evaluate(p, `window.dispatchEvent(new Event('blur'))`);
          await evaluate(p, `import('./js/shotclip.js')`);   // a long absence: rewind the away mark past AWAY_MAX_MS
          await evaluate(p, `(()=>{const t=Date.now;Date.now=()=>t()+61000;window.dispatchEvent(new Event('focus'));Date.now=t;})()`);
          await sleep(400);
          check(await chips() === 1, 'not after a long absence');
          for (let i = 0; i < 9; i++) { await shot(10 + i); await roundTrip(); }
          await waitFor(p, `${$('tray')}.querySelectorAll('[role=listitem]').length===10`, 15000);
          await shot(99); await roundTrip();
          check(await chips() === 10 && s.tooMany.test(await evaluate(p, `${$('toast')}.textContent`)), 'the 11th: the 10-attachment message');
          check(!fake.st.says.length, 'nothing was sent by itself');
        } finally { await p.dispose(); }
      });

      // ---------------------------------------------------------------- e) ⌘/Ctrl+Enter = 长按批准 finished
      await step('e', async () => {
        const phone = await paired(B, web, fake, lang, 390, 844, { touch: true });
        try {
          await fake.send({ t: 'status', s: 'waiting', agent: 'claude', name: 'AgentJ-Father' });
          await fake.ask({ id: hex(16), tool: 'Bash', summary: 'ls', ttl: 120 });
          await waitFor(phone, `document.body.dataset.sheet==='1' && !${$('apprBtns')}.hidden`);
          check(await evaluate(phone, `${$('apprKbd')}.hidden`), 'phone: no shortcut hint');
        } finally { await phone.dispose(); }
        const p = await paired(B, web, fake, lang, 1280, 800, {});
        try {
          const key = (ctrl) => evaluate(p, `(()=>{const t=document.activeElement||document.body;t.dispatchEvent(new KeyboardEvent('keydown',{key:'Enter',code:'Enter',ctrlKey:${ctrl},bubbles:true,cancelable:true}));})()`);
          await evaluate(p, `${$('input')}.value='p154 keep me';${$('input')}.dispatchEvent(new Event('input'));${$('input')}.focus()`);
          await fake.send({ t: 'status', s: 'waiting', agent: 'claude', name: 'AgentJ-Father' });
          const id = hex(16);
          await fake.ask({ id, tool: 'Bash', summary: 'git status', ttl: 120 });
          await waitFor(p, `document.body.dataset.sheet==='1' && !${$('apprBtns')}.hidden`);
          await sleep(300);
          check(await evaluate(p, `!${$('apprKbd')}.hidden && ${$('apprKbd')}.textContent`) === 'Ctrl↩', 'computer: Ctrl↩ hint on 长按批准');
          check(await evaluate(p, `${$('apprAllow')}.getAttribute('aria-keyshortcuts')`) === 'Control+Enter', 'aria-keyshortcuts');
          await evaluate(p, `${$('input')}.focus()`);
          const before = fake.st.answers.length;
          await key(false);                                          // plain Enter: not an approval
          await sleep(300);
          check(fake.st.answers.length === before, 'plain Enter does not approve');
          await evaluate(p, `${$('input')}.value='p154 keep me';${$('input')}.dispatchEvent(new Event('input'));${$('input')}.focus()`);
          const says = fake.st.says.length;
          await key(true);
          await waitFor(p, `document.body.dataset.sheet==='0'`, 2500);
          await sleep(300);
          const a = fake.st.answers[fake.st.answers.length - 1];
          check(a && a.id === id && a.ok === true && fake.st.answers.length === before + 1, 'Ctrl+Enter sent the approval (signed, like the hold)');
          check(fake.st.says.length === says && await evaluate(p, `${$('input')}.value`) === 'p154 keep me', 'the card wins: the text box keeps its text, nothing sent');
          await fake.send({ t: 'status', s: 'idle', agent: 'claude', name: 'AgentJ-Father' });
          await sleep(300);
          await evaluate(p, `${$('input')}.focus()`);
          await key(true);
          await waitFor(p, `${$('omText')}.textContent==='p154 keep me'`, 4000);
          check(true, 'no card: Ctrl+Enter in the box sends as before');
          check(!p.problems.length, 'no CSP errors');
        } finally { await p.dispose(); }
      });

      // ---------------------------------------------------------------- f) inline code: one click copies
      await step('f', async () => {
        for (const [w, h, touch] of [[390, 844, true], [1280, 800, false]]) {
          const p = await paired(B, web, fake, lang, w, h, { touch });
          try {
            await fake.say(s.reply, 'agent');
            await waitFor(p, `${$('words')}.querySelector('code.icode')`);
            check(await evaluate(p, `${$('words')}.querySelectorAll('code.icode').length`) === 1, 'the code block is not inline code');
            check(await evaluate(p, `${$('words')}.querySelector('code.icode').title`) === s.tap, 'title 点击复制');
            check(await evaluate(p, `getComputedStyle(${$('words')}.querySelector('code.icode')).cursor`) === 'copy', 'copy cursor');
            await evaluate(p, `(()=>{window.__copied=null;Object.defineProperty(navigator.clipboard,'writeText',{configurable:true,value:async(t)=>{window.__copied=t;}});})()`);
            await evaluate(p, `(()=>{const r=document.createRange();r.selectNodeContents(${$('words')}.querySelector('code.icode'));getSelection().removeAllRanges();getSelection().addRange(r);${$('words')}.querySelector('code.icode').click();})()`);
            await sleep(300);
            check(await evaluate(p, `window.__copied`) === null, 'a press that selected text does not copy');
            await evaluate(p, `getSelection().removeAllRanges()`);
            await evaluate(p, `${$('words')}.querySelector('code.icode').click()`);
            await waitFor(p, `window.__copied!==null`);
            check(await evaluate(p, `window.__copied`) === 'git status -sb', 'one click copied the inline code');
            check(await evaluate(p, `${$('words')}.querySelector('code.icode').classList.contains('copied')`), 'the shade flashes');
            check(await evaluate(p, `${$('toast')}.textContent`) === s.copied, '「已拷贝」');
            writeFileSync(join(OUT, `${lang}-${touch ? 'phone' : 'desktop'}-inline-copied.png`), await shoot(p, ''));
            await sleep(1100);
            check(!(await evaluate(p, `${$('words')}.querySelector('code.icode').classList.contains('copied')`)), 'the flash is gone within ~1 s');
          } finally { await p.dispose(); }
        }
      });

      // ---------------------------------------------------------------- g) the load line
      await step('g', async () => {
        for (const theme of ['light', 'dark']) {
          const p = await paired(B, web, fake, lang, 390, 844, { touch: true, theme });
          try {
            await fake.say(lang === 'zh' ? '负荷分割线：电脑越忙，线上的深色段滑得越快。' : 'The load line: the busier the computer, the faster the dark segment glides.', 'agent');
            await waitFor(p, `${$('rmTime')}.textContent.length>0`);
            const after = () => evaluate(p, `(()=>{const c=getComputedStyle(${$('loadLine')},'::after');return {d:c.display,n:c.animationName,dur:c.animationDuration}})()`);
            check((await after()).d === 'none', 'no load yet: no glide');
            for (const [v, dur] of [[0, '6s'], [0.5, '3.5s'], [1, '1s'], [1.7, '1s']]) {
              await fake.send({ t: 'busy', by: 'AgentJ-Father', tasks: [], load: v });
              await waitFor(p, `${$('loadLine')}.dataset.load===${JSON.stringify(String(Math.min(1, v)))}`);
              const a = await after();
              check(a.d === 'block' && a.n === 'll-slide' && a.dur === dur, `load ${v} → one way ${dur} ` + JSON.stringify(a));
            }
            await sleep(200);
            const ln = await box(p, '#loadLine'), ag = await box(p, '#agent'), tm = await box(p, '#rmTime'), wd = await box(p, '#words');
            const half = await evaluate(p, `Math.round(${$('read')}.clientWidth/2)`);
            check(Math.abs(ln.l - ag.l) < 1.5 && Math.abs(ln.w - half) < 1.5, `追加 6: fixed at half the card, under the name (${JSON.stringify([ln, ag, tm, half])})`);
            check(ln.t >= ag.b - 1 && ln.b <= wd.t, 'between the header and the words');
            await fake.send({ t: 'busy', by: 'AgentJ-Father', tasks: [], load: 0.6 });
            await waitFor(p, `${$('loadLine')}.dataset.load==='0.6'`);
            await evaluate(p, `document.getAnimations().forEach(a=>{a.pause();a.currentTime=1400})`);
            writeFileSync(join(OUT, `${lang}-${theme}-load-line.png`), await shoot(p, ''));
            await evaluate(p, `document.getAnimations().forEach(a=>a.play())`);
            await evaluate(p, `document.body.dataset.conn='off'`);
            check((await after()).d === 'none', 'disconnected: no glide');
            await evaluate(p, `document.body.dataset.conn='on'`);
            await fake.send({ t: 'busy', by: 'AgentJ-Father', tasks: [] });
            await waitFor(p, `!('load' in ${$('loadLine')}.dataset)`);
            check((await after()).d === 'none', 'a host without load: no glide');
          } finally { await p.dispose(); }
        }
        const p = await paired(B, web, fake, lang, 390, 844, { touch: true, reduce: true });
        try {
          await fake.send({ t: 'busy', by: 'AgentJ-Father', tasks: [], load: 0.9 });
          await waitFor(p, `${$('loadLine')}.dataset.load==='0.9'`);
          check(await evaluate(p, `getComputedStyle(${$('loadLine')},'::after').animationName`) === 'none', 'reduced motion: still');
        } finally { await p.dispose(); }
      });

      // ---------------------------------------------------------------- h) 追加 5
      await step('h', async () => {
        for (const theme of ['light', 'dark']) {
          const p = await paired(B, web, fake, lang, 390, 844, { touch: true, theme });
          try {
            await fake.say(lang === 'zh' ? '好的。' : 'OK.', 'agent');
            await waitFor(p, `${$('rmTime')}.textContent.length>0`);
            await fake.send({ t: 'busy', by: 'AgentJ-Father', tasks: [], load: 0.42 });
            await waitFor(p, `${$('loadLine')}.dataset.load==='0.42'`);
            await sleep(200);
            check(await evaluate(p, `getComputedStyle(${$('om')},'::before').content`) === 'none', 'no ghost card behind the source card');
            const ln = await box(p, '#loadLine'), rd = await box(p, '#read'), ag = await box(p, '#agent');
            check(Math.abs(ln.w - Math.round(rd.w / 2)) < 2 && Math.abs(ln.l - ag.l) < 1.5, `追加 6: fixed half the card, left-aligned (${JSON.stringify([ln, rd])})`);
            const sp = await evaluate(p, `(()=>{const a=getComputedStyle(${$('agent')}),t=getComputedStyle(${$('rmTime')});return {w:+a.fontWeight,sh:a.textShadow,tw:+t.fontWeight}})()`);
            check(sp.w >= 600 && sp.sh !== 'none' && sp.tw < 600, 'the name bold + shadowed, the time regular ' + JSON.stringify(sp));
            const hit = await box(p, '#loadHit');
            check(hit.h >= 14, 'the line has tap room');
            await evaluate(p, `${$('loadHit')}.click()`);
            await waitFor(p, `!${$('loadDlg')}.hidden`);
            const now = await evaluate(p, `${$('loadNow')}.textContent`);
            check(now.includes('42%'), 'the load now: ' + now);
            const demo = await evaluate(p, `[...document.querySelectorAll('#loadDlg .load-demo .loadline')].map(e=>{const c=getComputedStyle(e,'::after');return c.display+' '+c.animationName+' '+c.animationDuration})`);
            check(JSON.stringify(demo) === JSON.stringify(['block ll-slide 6s', 'block ll-slide 3s', 'block ll-slide 1s']), 'three sample speeds ' + JSON.stringify(demo));
            check(await evaluate(p, `document.activeElement===${$('loadClose')}`), 'focus on 知道了');
            await evaluate(p, `document.getAnimations().forEach(a=>{a.pause();a.currentTime=700})`);
            writeFileSync(join(OUT, `${lang}-${theme}-load-explainer.png`), await shoot(p, ''));
            await evaluate(p, `document.getAnimations().forEach(a=>a.play())`);
            await evaluate(p, `document.dispatchEvent(new KeyboardEvent('keydown',{key:'Escape',bubbles:true}))`);
            await waitFor(p, `${$('loadDlg')}.hidden`);
            await evaluate(p, `document.body.dataset.conn='off'`);
            await evaluate(p, `${$('loadHit')}.click()`);
            await waitFor(p, `!${$('loadDlg')}.hidden`);
            check((await evaluate(p, `${$('loadNow')}.textContent`)) !== now, 'disconnected: says the line is still');
            await evaluate(p, `${$('loadClose')}.click()`);
            await waitFor(p, `${$('loadDlg')}.hidden`);
            await evaluate(p, `document.body.dataset.conn='on'`);
            writeFileSync(join(OUT, `${lang}-${theme}-card-no-ghost.png`), await shoot(p, ''));
          } finally { await p.dispose(); }
        }
      });

      // ---------------------------------------------------------------- i) 追加 8: "/" outside the field
      await step('i', async () => {
        const p = await paired(B, web, fake, lang, 1280, 800, {});
        try {
          const slash = () => evaluate(p, `(()=>{const t=document.activeElement||document.body;t.dispatchEvent(new KeyboardEvent('keydown',{key:'/',code:'Slash',bubbles:true,cancelable:true}));})()`);
          await evaluate(p, `${$('input')}.value='';${$('input')}.dispatchEvent(new Event('input'));${$('input')}.blur();document.body.focus()`);
          await slash();
          await waitFor(p, `document.activeElement===${$('input')} && ${$('input')}.value==='/'`, 2000);
          check(await evaluate(p, `${$('input')}.selectionStart===1 && ${$('input')}.selectionEnd===1`), 'the caret after the "/"');
          await waitFor(p, `!${$('sug')}.hidden && ${$('sug')}.children.length>0`, 4000);
          check(true, '"/" opens the command menu, like typing it in the field');
          writeFileSync(join(OUT, `${lang}-desktop-slash-menu.png`), await shoot(p, ''));
          // a draft: the "/" goes at its end
          await evaluate(p, `${$('input')}.value='see ';${$('input')}.dispatchEvent(new Event('input'));${$('input')}.blur()`);
          await sleep(300); await evaluate(p, `${$('input')}.blur()`);   // the draft's input event keeps the field focused a moment
          await waitFor(p, `document.activeElement!==${$('input')} && ${$('sug')}.hidden`, 2000);
          await slash();
          { const v = await evaluate(p, `${$('input')}.value + '|' + (document.activeElement && document.activeElement.id)`); check(v.startsWith('see /|'), 'appended to the draft ' + v); }
          // Settings open: not taken
          await evaluate(p, `${$('input')}.value='';${$('input')}.dispatchEvent(new Event('input'));${$('input')}.blur()`);
          await evaluate(p, `import('./js/settings.js').then(m=>m.open())`);
          await waitFor(p, `!${$('settings')}.hidden`);
          await slash();
          check(await evaluate(p, `${$('input')}.value===''`), 'Settings open: "/" is not taken');
          await evaluate(p, `${$('set-close')}.click()`);
          await waitFor(p, `${$('settings')}.hidden`);
          // "i" unchanged: into the field, nothing typed
          await evaluate(p, `${$('input')}.blur();document.body.focus()`);
          await evaluate(p, `document.body.dispatchEvent(new KeyboardEvent('keydown',{key:'i',code:'KeyI',bubbles:true,cancelable:true}))`);
          check(await evaluate(p, `document.activeElement===${$('input')} && ${$('input')}.value===''`), '"i" still just enters the field');
          check(await evaluate(p, `[...document.querySelectorAll('#keys dt kbd')].some(k=>k.textContent==='/')`), 'the shortcut panel lists "/"');
          check(!p.problems.length, 'no CSP errors');
        } finally { await p.dispose(); }
      });

      // ---------------------------------------------------------------- j) 追加 9: 长按批准 on a ⚠ card, the hint inside
      await step('j', async () => {
        const p = await paired(B, web, fake, lang, 1280, 800, {});
        try {
          await fake.send({ t: 'status', s: 'waiting', agent: 'claude', name: 'AgentJ-Father' });
          await fake.ask({ id: hex(16), tool: 'Bash', summary: 'rm victim.txt', cat: ['delete'], why: 'delete', ttl: 120 });
          await waitFor(p, `document.body.dataset.sheet==='1' && !${$('apprBtns')}.hidden && ${$('sheet')}.classList.contains('danger')`);
          await sleep(300);
          const lbl = await evaluate(p, `${$('apprAllowLbl')}.textContent`);
          check(lbl === (lang === 'zh' ? '长按批准' : 'Hold to approve'), `⚠ card: the short label (${lbl})`);
          check(await evaluate(p, `${$('apprAllow')}.contains(${$('apprKbd')}) && !${$('apprKbd')}.hidden && ${$('apprKbd')}.textContent`) === 'Ctrl↩', 'the hint inside the button');
          const al = await box(p, '#apprAllow'), lb = await box(p, '#apprAllowLbl'), kb = await box(p, '#apprKbd');
          check(lb.l >= al.l && kb.r <= al.r + 0.5 && lb.t >= al.t - 0.5 && lb.b <= al.b + 0.5 && kb.l >= lb.r - 0.5, `label + hint fit in one row (${JSON.stringify([al, lb, kb])})`);
          writeFileSync(join(OUT, `${lang}-desktop-danger-hold.png`), await shoot(p, ''));
          await evaluate(p, `${$('apprDeny')}.click()`);
          check(!p.problems.length, 'no CSP errors');
        } finally { await p.dispose(); }
      });
      // ---------------------------------------------------------------- l) 追加 12: hold the grey Send → volume
      await step('l', async () => {
        const vol = (p) => evaluate(p, `import('./js/sfx.js').then(m=>m.volume())`);
        const stored = (p) => evaluate(p, `localStorage.getItem('aj.sfxVolume')`);
        const open = (p) => evaluate(p, `!!${$('volPop')} && !${$('volPop')}.hidden`);
        { // computer: mouse hold, keyboard ↑/↓, Esc
          const p = await paired(B, web, fake, lang, 1280, 800, {});
          try {
            await evaluate(p, `${$('input')}.value='';${$('input')}.dispatchEvent(new Event('input'))`);
            check(await evaluate(p, `${$('send')}.disabled`), 'Send is grey with an empty box');
            const sb = await box(p, '#send'), x = sb.l + sb.w / 2, y = sb.t + sb.h / 2;
            await p.send('Input.dispatchMouseEvent', { type: 'mouseMoved', x, y });
            await p.send('Input.dispatchMouseEvent', { type: 'mousePressed', x, y, button: 'left', clickCount: 1 });
            await sleep(200);
            check(!(await open(p)), 'not open before the hold time');
            await sleep(450);
            check(await open(p), 'a hold opens the slider');
            await p.send('Input.dispatchMouseEvent', { type: 'mouseReleased', x, y, button: 'left', clickCount: 1 });
            await sleep(150);
            check(await open(p), 'a hold let go without sliding leaves it open');
            const pb = await box(p, '#volPop');
            check(pb.b <= sb.t + 0.5 && Math.abs((pb.l + pb.r) / 2 - x) < 2, `it grows up out of Send (${JSON.stringify([pb, sb])})`);
            check(await evaluate(p, `document.activeElement===${$('volPop')}.querySelector('input')`), 'the slider has the focus');
            check(await vol(p) === 15, 'starts at the default 15 %');
            await key(p, 'ArrowUp', { code: 'ArrowUp', keyCode: 38 });
            await sleep(100);
            check(await vol(p) === 20 && await stored(p) === '20', 'ArrowUp: +5 %, live and stored');
            check(await evaluate(p, `${$('volPop')}.querySelector('output').textContent`) === '20%', 'the value shows');
            writeFileSync(join(OUT, `${lang}-desktop-send-volume.png`), await shoot(p, ''));
            await key(p, 'ArrowDown', { code: 'ArrowDown', keyCode: 40 }); await key(p, 'ArrowDown', { code: 'ArrowDown', keyCode: 40 });
            await sleep(100);
            check(await vol(p) === 10, 'ArrowDown ×2: 10 %');
            await key(p, 'Escape', { code: 'Escape', keyCode: 27 });
            await sleep(100);
            check(!(await open(p)) && await evaluate(p, `document.activeElement===${$('input')}`), 'Esc closes, back to the box');
            // Settings shows the same level
            await evaluate(p, `import('./js/settings.js').then(m=>m.open())`);
            await waitFor(p, `!${$('settings')}.hidden`);
            check(await evaluate(p, `import('./js/settings.js').then(m=>m._debug().sfx.vol)`) === 10, 'Settings: the same setting');
            await evaluate(p, `${$('set-close')}.click()`); await waitFor(p, `${$('settings')}.hidden`);
            // with text a hold does not open it (and Send is not grey)
            await evaluate(p, `${$('input')}.value='hi';${$('input')}.dispatchEvent(new Event('input'))`);
            check(!(await evaluate(p, `${$('send')}.disabled`)), 'Send is live with text');
            await evaluate(p, `(()=>{const s=${$('send')},r=s.getBoundingClientRect();s.dispatchEvent(new PointerEvent('pointerdown',{bubbles:true,clientX:r.left+r.width/2,clientY:r.top+r.height/2,pointerId:9,button:0}));})()`);
            await sleep(700);
            check(!(await open(p)), 'with text in the box a hold never opens it');
            await evaluate(p, `document.dispatchEvent(new PointerEvent('pointerup',{bubbles:true,pointerId:9}))`);
            check(!p.problems.length, 'no CSP errors');
          } finally { await p.dispose(); }
        }
        { // phone: finger hold, slide up, let go = done
          const p = await paired(B, web, fake, lang, 390, 844, { touch: true, theme: 'dark' });
          try {
            await evaluate(p, `${$('input')}.value='';${$('input')}.dispatchEvent(new Event('input'));${$('input')}.blur()`);
            const sb = await box(p, '#send'), x = sb.l + sb.w / 2, y = sb.t + sb.h / 2;
            await p.send('Input.dispatchTouchEvent', { type: 'touchStart', touchPoints: [{ x, y }] });
            await sleep(650);
            check(await open(p), 'phone: a finger hold opens it');
            const rb = await box(p, '#volPop input');
            check(rb.t > 0 && rb.r <= 390 && rb.l >= 0, `phone: on screen (${JSON.stringify(rb)})`);
            for (let i = 1; i <= 8; i++) {
              await p.send('Input.dispatchTouchEvent', { type: 'touchMove', touchPoints: [{ x, y: y - (y - rb.t + 10) * i / 8 }] });
              await sleep(20);
            }
            await p.send('Input.dispatchTouchEvent', { type: 'touchEnd', touchPoints: [] });
            await sleep(150);
            check(await vol(p) === 100 && await stored(p) === '100', `phone: sliding to the top = 100 % (${await vol(p)})`);
            check(!(await open(p)), 'phone: letting go after a slide closes it');
            check(await evaluate(p, `${$('input')}.value===''`), 'nothing was sent / typed');
            await p.send('Page.reload');
            await sleep(800);
            await waitFor(p, `!!document.getElementById('send')`);
            check(await vol(p) === 100, 'phone: the level survives a reload');
            check(!p.problems.length, 'no CSP errors');
          } finally { await p.dispose(); }
        }
      });

      // ---------------------------------------------------------------- m) 追加 13: the logo's text size is remembered
      await step('m', async () => {
        const p = await paired(B, web, fake, lang, 390, 844, {});
        try {
          const fz = () => evaluate(p, `(document.documentElement.dataset.fz||'m')+'|'+localStorage.getItem('aj.pageFz')`);
          const press = async (ms) => {
            const ob = await box(p, '#orb'), x = ob.l + ob.w / 2, y = ob.t + ob.h / 2;
            await p.send('Input.dispatchMouseEvent', { type: 'mouseMoved', x, y });
            await p.send('Input.dispatchMouseEvent', { type: 'mousePressed', x, y, button: 'left', clickCount: 1 });
            await sleep(ms);
            await p.send('Input.dispatchMouseEvent', { type: 'mouseReleased', x, y, button: 'left', clickCount: 1 });
            await sleep(150);
          };
          check(await fz() === 'm|null', 'starts at 中, nothing stored');
          await press(40);
          check(await fz() === 'l|l', 'a tap: 大, stored');
          const big = await evaluate(p, `getComputedStyle(document.documentElement).getPropertyValue('--fz').trim()`);
          await p.send('Page.reload'); await sleep(800); await waitFor(p, `!!document.getElementById('orb')`);
          check(await fz() === 'l|l', 'after a reload: still 大');
          check(await evaluate(p, `getComputedStyle(document.documentElement).getPropertyValue('--fz').trim()`) === big, 'the size really applies after the reload');
          // 大 → 中 → 小, reload keeps 小; the next tap goes on from there (→ 中)
          await waitState(p, 'ready');
          await press(40); await press(40);
          check(await fz() === 's|s', 'two more taps: 小');
          await p.send('Page.reload'); await sleep(800); await waitFor(p, `!!document.getElementById('orb')`);
          check(await fz() === 's|s', 'after a reload: still 小');
          await waitState(p, 'ready');
          await press(40);
          check(await fz() === 'm|null', 'the cycle goes on from the remembered step: 中, nothing stored');
          await press(40);
          check(await fz() === 'l|l', '→ 大');
          // P207 追加 2 (Leo): a hold on the logo now jumps to the first unread — it never touches the text size
          await press(900);
          check(await fz() === 'l|l', 'a hold leaves the size alone (P207: hold = first unread)');
          await press(40);
          check(await fz() === 'm|null', 'the tap cycle still reaches 中 and forgets it');
          await p.send('Page.reload'); await sleep(800); await waitFor(p, `!!document.getElementById('orb')`);
          check(await fz() === 'm|null', 'after a reload: 中');
          check(!p.problems.length, 'no CSP errors');
        } finally { await p.dispose(); }
      });
    }
    writeFileSync(join(ROOT, 'reports/qa/p154/results.json'), JSON.stringify({ scope: 'real Chromium + local fake host; not a physical phone', checks, complete: true }, null, 2) + '\n');
    console.log(`p154: ${checks} checks passed`);
  } finally {
    for (const [c, id] of Object.entries(CASES)) rec.record(id, ran[c] === true);
    await B.close(); await fake.stop(); await web.stop(); rec.write();
  }
}

if (process.argv[1] === fileURLToPath(import.meta.url)) await runP154();
