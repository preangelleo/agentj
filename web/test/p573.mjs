#!/usr/bin/env node
// P573 (#386, chairman 10-29: 「全屏阅读版式可以」; item 3: 「点一下」 tier): one full-screen reader. A Markdown report the reply
// sent as a file opens in the reply's own reader (#rd, file mode): the same Noir page, md.js rendering, type size, footer icons
// and select-to-quote; the header is the file name with 下载 as a thin icon (no × — the book icon, Back and Esc leave).
// 4b: the file reader inherits every reply-card action the file card has (下载 · 分享 · Telegram · 邮件 · 复制 · 回复 ·
// select-to-quote) and each works. ⑫: a new message while any reader is open (reply reader, file reader, picture viewer)
// neither closes nor repaints it. The key pickup card's full screen has two thin icons (复制 · 返回) instead of text buttons.
// Approval card: 批准 / 批量批准 answer on one tap (no hold); 拒绝 one tap; a finger that slides off answers nothing.
// The real web client against fakehost.mjs, own headless Chromium (never :9222). 390 × 844 light / dark × zh / en, + 1440 light zh.
//   P573_OUT=<dir> P573_TAG=approved|after node p573.mjs
// The same file runs against the step-1 tree for the 「approved」 shots; the checks run on `after` only.
import assert from 'node:assert/strict';
import { mkdirSync, writeFileSync } from 'node:fs';
import { createHash, randomBytes } from 'node:crypto';
import { startWebServer } from './serve.mjs';
import { startFakeHost } from './fakehost.mjs';
import { launch, newPage, evaluate, navigate, waitFor, waitState, shoot, sleep, rect } from './browser.mjs';

const OUT = process.env.P573_OUT || new URL('../../../reports/qa/p573/', import.meta.url).pathname;
const TAG = process.env.P573_TAG || 'after';
mkdirSync(OUT, { recursive: true });
const UA = 'Mozilla/5.0 (Linux; Android 14; PJZ110) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/129.0.0.0 Mobile Safari/537.36';
// Synthetic report (no real names, keys, paths): a heading, a paragraph, a bold label, a numbered and a bulleted list, a
// link, inline code, a code block and a table — every Markdown piece the owner compared.
const MD = {
  zh: `# 本地小模型替代调研（合成样例）

董事长原话：用户没有官方 API key 时，用本地几百 MB 的小模型兜底；文本不离开本机。

**结论**

1. 通用小模型不行：0.6B 误报 13/20，1.7B 误报 9/20，调阈值几乎无效。
2. 查密钥用 \`credsweeper\`：召回 16/20、误报 0，43 条 0.42 秒。
3. 隐私判断仍走云端审阅，本地只做第一道筛。

- 安装包增加约 12 MB
- 详见 [调研方法](https://example.com/method)

\`\`\`bash
agentj check --local --threshold 0.95
\`\`\`

| 方案 | 召回 | 误报 |
|---|---|---|
| 0.6B | 7/20 | 13/20 |
| CredSweeper | 16/20 | 0 |
`,
  en: `# Local small-model survey (synthetic sample)

The owner's words: when a user has no official API key, a local model of a few hundred MB fills in; text never leaves the machine.

**Conclusion**

1. General small models fail: 0.6B 13/20 false alarms, 1.7B 9/20; thresholds barely help.
2. Secrets: \`credsweeper\` finds 16/20 with 0 false alarms, 43 lines in 0.42 s.
3. Privacy calls stay with the cloud reviewer; the local model is the first sieve.

- The install grows by about 12 MB
- See [the method](https://example.com/method)

\`\`\`bash
agentj check --local --threshold 0.95
\`\`\`

| Option | Found | False |
|---|---|---|
| 0.6B | 7/20 | 13/20 |
| CredSweeper | 16/20 | 0 |
`,
};
const NOTE = { zh: '报告写好了，见下面的文件。', en: 'The report is ready — the file is below.' };
const web = await startWebServer({ port: 0, relayCsp: 'ws://127.0.0.1:*', ...(process.env.P573_PUBLIC ? { publicDir: process.env.P573_PUBLIC } : {}) });
web.url = web.url.replace('127.0.0.1', 'localhost');
const fake = await startFakeHost();
const B = await launch();
const ev = (p, s) => evaluate(p, s);
let checks = 0;
const check = (v, label) => { if (TAG !== 'after') return; assert.ok(v, label); checks++; };
const results = {};
const bytes = new Map();

async function paired(w, h, scheme, lang, { caps = [], tgfwd = false, init = '' } = {}) {
  fake.reset(); bytes.clear();
  fake.st.extraCaps = caps; fake.st.tgfwd = tgfwd;
  fake.st.onApp = async (c, m, send) => {
    if (m.t !== 'media_get') return false;
    const b = bytes.get(m.mid);
    for (let i = 0; i < 8; i++) { const o = m.o + i * 45056; if (o >= b.length) break; const d = b.subarray(o, o + 45056); await send({ t: 'media_chunk', mid: m.mid, o, d: d.toString('base64url'), last: o + d.length >= b.length }); }
    return true;
  };
  const p = await newPage(B, w, h, scheme, { ua: w < 500 ? UA : undefined, touch: w < 500, allow: [web.url, fake.relay + '/'] });
  if (init) await p.send('Page.addScriptToEvaluateOnNewDocument', { source: init });
  await navigate(p, fake.newPairing(web.url + (lang === 'en' ? '?lang=en' : '?lang=zh')));
  await waitState(p, 'awaiting-approval');
  await fake.approve();
  await waitState(p, 'ready');
  await fake.send({ t: 'status', s: 'idle', agent: 'claude', name: 'Agent J' });
  await ev(p, `for (const id of ['phone-env-dismiss','a2hs-ok','confirm-no']) { const b = document.getElementById(id); if (b && b.offsetParent) b.click(); }`);
  await sleep(300);
  return p;
}
async function fileTurn(p, lang) {
  const data = Buffer.from(MD[lang]), mid = randomBytes(11).toString('hex').slice(0, 22), name = lang === 'en' ? 'local-model-survey.md' : '本地小模型调研.md';
  bytes.set(mid, data);
  const turn = await fake.addTurn({ k: 'phone', dev: null, text: lang === 'en' ? 'Write up the survey' : '把调研写成报告' }, NOTE[lang], 'done');
  await fake.updateTurn(turn.id, { media: [{ mid, name, mime: 'text/markdown', kind: 'file', bytes: data.length, sha256: createHash('sha256').update(data).digest('hex'), ref: 'out/' + name }] });
  const card = `#mstrip .mcard[data-mid="${mid}"]`;
  await waitFor(p, `!!document.querySelector(${JSON.stringify(card)})`).catch(async (e) => {
    console.error('debug', await ev(p, `JSON.stringify({pg: document.getElementById('pg').textContent, words: document.getElementById('words').textContent.slice(0, 80), strip: document.getElementById('mstrip')?.outerHTML.slice(0, 300), rd: document.getElementById('rd').hidden})`), p.problems.slice(0, 5));
    throw e;
  });
  await ev(p, `document.querySelector(${JSON.stringify(card)}).scrollIntoView({ block: 'center' })`);
  await waitFor(p, `document.querySelector(${JSON.stringify(card)})?.dataset.state === 'ready'`);
  return { card, name };
}
const openFile = async (p, card) => {
  await ev(p, `document.querySelector(${JSON.stringify(card + ' .pv')}).click()`);
  await waitFor(p, `(!document.getElementById('rd').hidden && document.getElementById('rd').dataset.file === '1' && !!document.querySelector('#rdWords ol'))
    || (!!document.querySelector('.mview:not([hidden]) .mdoc .words ol'))`);
  await sleep(350);
};
const readerShown = (p) => ev(p, `(() => { const rd = document.getElementById('rd'), mv = document.querySelector('.mview');
  return { rd: !rd.hidden, file: rd.dataset.file === '1', mview: !!mv && !mv.hidden }; })()`);

try {
  for (const [w, h, scheme, lang] of [[390, 844, 'light', 'zh'], [390, 844, 'dark', 'zh'], [390, 844, 'light', 'en'], [390, 844, 'dark', 'en'], [1440, 900, 'light', 'zh']]) {
    const sfx = `${lang}-${scheme}${w > 500 ? '-1440' : ''}`;
    // 1. the file, full screen
    let p = await paired(w, h, scheme, lang);
    const { card, name } = await fileTurn(p, lang);
    await openFile(p, card);
    writeFileSync(`${OUT}/file-${sfx}.png`, await shoot(p, ''));
    const r = results[`file-${sfx}`] = await readerShown(p);
    if (TAG === 'after') {
      check(r.rd && r.file && !r.mview, `${sfx}: the .md opens in the reply reader (file mode), not the viewer`);
      check(await ev(p, `document.getElementById('rdFileName').textContent`) === name, `${sfx}: header = the file name`);
      check(await ev(p, `!!document.querySelector('#rdWords table') && !!document.querySelector('#rdWords .codeblock') && !!document.querySelector('#rdWords a[href^="https://example.com"]')`), `${sfx}: same md.js rendering (table, code block, link)`);
      const hd = await ev(p, `(() => { const b = document.getElementById('rdfDl').getBoundingClientRect(); return { w: b.width, h: b.height, right: innerWidth - b.right, text: document.getElementById('rdfDl').textContent.replace(/[!\\s]/g, '') }; })()`);
      check(hd.w >= 44 && hd.h >= 44 && hd.right < 24 && hd.text === '', `${sfx}: 下载 is an icon (44 px target) in the top-right corner, no text`);
      const ids = await ev(p, `[...document.querySelectorAll('#rdFileActs .act')].filter((b) => !b.hidden).map((b) => b.id)`);
      check(ids.includes('rdfRead') && ids.includes('rdfCopy') && ids.includes('rdfReply') && ids.at(-1) === 'rdfReply', `${sfx}: footer = the reply card's icons, 回复 in the corner: ${ids}`);
      check(await ev(p, `document.getElementById('rdActs').hidden && getComputedStyle(document.getElementById('rd')).backgroundColor === getComputedStyle(document.getElementById('rd')).backgroundColor`), `${sfx}: the reply's own toolbar steps aside`);
      check(await ev(p, `!document.querySelector('#rd button.aj-btn')`), `${sfx}: no text button in the reader`);
    }
    // 2. a new message arrives while it is open: it stays (after) — the viewer used to close (before)
    // a repaint = the words refilled: mark the rendered nodes and see that the same ones are still there (code highlighting is
    // lazy — it lands when a block scrolls into view — so the HTML itself may change without a repaint)
    const html0 = await ev(p, `(() => { const w = document.getElementById('rdWords'); [...w.children].forEach((c, i) => c.dataset.p573 = i); return w.children.length + '|' + w.textContent; })()`);
    const pos0 = await ev(p, `(() => { const s = document.getElementById('rdScroll'); s.scrollTop = 120; return s.scrollTop; })()`);
    await fake.addTurn({ k: 'agent' }, lang === 'en' ? 'A new message while you read.' : '你读的时候来了一条新消息。', 'done');
    await sleep(900);
    const r2 = results[`newmsg-${sfx}`] = await readerShown(p);
    if (w < 500) writeFileSync(`${OUT}/newmsg-${sfx}.png`, await shoot(p, ''));
    check(r2.rd && r2.file && await ev(p, `!!document.querySelector('#rdWords table')`), `${sfx}: a new message neither closes nor repaints the file`);
    if (TAG === 'after') {
      check(await ev(p, `document.getElementById('rdFileName').textContent`) === name, `${sfx}: still the same file after the new message`);
      const html1 = await ev(p, `(() => { const w = document.getElementById('rdWords'); return [...w.children].every((c, i) => c.dataset.p573 === String(i)) ? w.children.length + '|' + w.textContent : 'refilled'; })()`);
      check(html1 === html0, `${sfx}: the file was not repainted`);
      check(Math.abs(await ev(p, `document.getElementById('rdScroll').scrollTop`) - pos0) < 2, `${sfx}: the reading position stays`);
      check(await ev(p, `document.querySelectorAll('#rdFileHd button').length === 1 && !!document.getElementById('rdfDl')`), `${sfx}: no × in the top right (下载 is the only icon)`);
      // Esc leaves; the card is still on its page, and opens again
      await ev(p, `document.dispatchEvent(new KeyboardEvent('keydown', { key: 'Escape', bubbles: true }))`);
      await waitFor(p, `document.getElementById('rd').hidden`);
      check(await ev(p, `!document.getElementById('rd').dataset.file && !document.getElementById('rdActs').hidden`), `${sfx}: leaving restores the reply reader's own toolbar`);
    }
    await p.dispose();
    if (w > 500) continue;
    // 3. the reply card's reader, the same words as a reply (the reference look)
    p = await paired(w, h, scheme, lang);
    await fake.addTurn({ k: 'phone', dev: null, text: lang === 'en' ? 'Write up the survey' : '把调研写成报告' }, MD[lang], 'done');
    await waitFor(p, `!!document.querySelector('#words ol')`);
    await ev(p, `document.getElementById('readBtn').click()`);
    await waitFor(p, `!document.getElementById('rd').hidden`);
    await sleep(350);
    writeFileSync(`${OUT}/reply-${sfx}.png`, await shoot(p, ''));
    // 3b. (after) a file opened from inside the reply reader comes back to that reply
    if (TAG === 'after') {
      await ev(p, `document.dispatchEvent(new KeyboardEvent('keydown', { key: 'Escape', bubbles: true }))`);
      await waitFor(p, `document.getElementById('rd').hidden`);
      const f = await fileTurn(p, lang);
      await sleep(500);   // the reader's 350 ms click guard after it closed
      await ev(p, `document.getElementById('readBtn').click()`);
      await waitFor(p, `!document.getElementById('rd').hidden && !!document.querySelector('#rdWords .mcard')`).catch(async (e) => {
        console.error('debug3b', await ev(p, `JSON.stringify({rd: document.getElementById('rd').hidden, file: document.getElementById('rd').dataset.file, w: document.getElementById('rdWords').innerHTML.slice(0, 300), pg: document.getElementById('pg').textContent})`), p.problems.slice(0, 5));
        throw e;
      });
      await sleep(500);
      await ev(p, `document.querySelector('#rdWords .mcard .pv').click()`);
      await waitFor(p, `document.getElementById('rd').dataset.file === '1'`);
      check(await ev(p, `document.getElementById('rdFileName').textContent`) === f.name, `${sfx}: a file inside the reply reader opens in place`);
      await ev(p, `document.dispatchEvent(new KeyboardEvent('keydown', { key: 'Escape', bubbles: true }))`);
      await waitFor(p, `!document.getElementById('rd').dataset.file`);
      check(await ev(p, `!document.getElementById('rd').hidden && !!document.querySelector('#rdWords .mcard')`), `${sfx}: Esc from that file = back to the reply, still full screen`);
      {   // ⑫: the reply reader, a new message — neither closed nor repainted
      await ev(p, `[...document.getElementById('rdWords').children].forEach((c, i) => c.dataset.p573 = i)`);
      await fake.addTurn({ k: 'agent' }, lang === 'en' ? 'Another message.' : '又来了一条消息。', 'done');
      await sleep(900);
      check(await ev(p, `!document.getElementById('rd').hidden && [...document.getElementById('rdWords').children].every((c, i) => c.dataset.p573 === String(i))`), `${sfx}: the reply reader stays as it was on a new message`);
    }
    }
    await p.dispose();
    // 4. the key pickup card's full screen (secret_out, a .md file value) — the owner's screenshot
    p = await paired(w, h, scheme, lang);
    const sid = randomBytes(16).toString('hex'), val = Buffer.from(MD[lang]);
    await ev(p, `import('./js/secretout.js').then((m) => m.add({ id: ${JSON.stringify(sid)}, n: ${JSON.stringify(randomBytes(16).toString('hex'))}, kind: 'file', name: ${JSON.stringify(lang === 'en' ? 'Survey' : '调研报告')}, purpose: ${JSON.stringify(lang === 'en' ? 'synthetic sample' : '合成样例')}, filename: 'survey.md', size: ${val.length}, ttl: 600 }))`);
    await waitFor(p, `!document.getElementById('sout').hidden`);
    await ev(p, `import('./js/secretout.js').then((m) => m.value({ id: ${JSON.stringify(sid)}, data: ${JSON.stringify(val.toString('base64'))} }))`);
    await waitFor(p, `!document.getElementById('sout-expand').hidden`);
    await ev(p, `document.getElementById('sout-expand').click()`);
    await waitFor(p, `document.getElementById('sout').classList.contains('sout-full')`);
    await sleep(300);
    writeFileSync(`${OUT}/secret-${sfx}.png`, await shoot(p, ''));
    if (TAG === 'after') {
      const ic = await ev(p, `['sout-back','sout-full-copy'].map((id) => { const e = document.getElementById(id), b = e.getBoundingClientRect(), s = getComputedStyle(e);
        return { w: b.width, h: b.height, text: e.textContent.trim(), svg: !!e.querySelector('svg'), border: parseFloat(s.borderTopWidth), label: e.getAttribute('aria-label') }; })`);
      check(ic.every((x) => x.svg && !x.text && x.w >= 44 && x.h >= 44 && x.border === 0 && x.label), `${sfx}: pickup full screen = two thin icons with labels, no text buttons`);
    }
    await ev(p, `document.getElementById('sout-back').click()`);
    await ev(p, `document.getElementById('sout-done').click()`);
    await p.dispose();
  }

  if (TAG === 'after') for (const [w, h, scheme, lang] of [[390, 844, 'light', 'zh'], [390, 844, 'dark', 'zh'], [390, 844, 'light', 'en'], [390, 844, 'dark', 'en'], [1440, 900, 'dark', 'zh']]) {
    const sfx = `${lang}-${scheme}${w > 500 ? '-1440' : ''}`;
    // 4b. every reply-card action the file card has, in the file reader — shown, and each one works
    const init = `window.__shared = []; window.__dl = [];
      Object.defineProperty(navigator, 'canShare', { value: () => true, configurable: true });
      Object.defineProperty(navigator, 'share', { value: async (d) => { window.__shared.push((d.files || []).map((f) => f.name)); }, configurable: true });
      document.addEventListener('click', (e) => { const a = e.target.closest && e.target.closest('a[download]'); if (a) { window.__dl.push(a.download); e.preventDefault(); } }, true);`;
    let p = await paired(w, h, scheme, lang, { caps: ['tgfile'], tgfwd: true, init });
    await ev(p, `import('./js/mailfwd.js').then((m) => { m.registerMailForward((r) => { window.__mail = { kind: r.kind, mid: r.mid, name: r.name }; return true; }); return true; })`);
    const { card, name } = await fileTurn(p, lang);
    await openFile(p, card);
    const shown = await ev(p, `['rdfDl','rdfShare','rdfTg','rdfMail','rdfCopy','rdfReply','rdfRead'].filter((id) => { const b = document.getElementById(id); return !b.hidden && b.getClientRects().length; })`);
    check(shown.length === 7, `${sfx} 4b: all seven icons show when the card has them: ${shown}`);
    const labels = await ev(p, `['rdfDl','rdfShare','rdfTg','rdfMail','rdfCopy','rdfReply','rdfRead'].map((id) => document.getElementById(id).getAttribute('aria-label'))`);
    check(labels.every((l) => l && (lang === 'en' ? !/[一-鿿]/.test(l) : /[一-鿿]/.test(l))), `${sfx} 4b: labels in the current language only: ${labels}`);
    const tb = await ev(p, `[...document.querySelectorAll('#rdFileActs .act, #rdfDl')].filter((b) => !b.hidden).map((b) => { const r = b.getBoundingClientRect(); return Math.min(r.width, r.height); })`);
    check(tb.every((x) => x >= 44), `${sfx} 4b: 44 px targets ${tb}`);
    writeFileSync(`${OUT}/actions-${sfx}.png`, await shoot(p, ''));
    await ev(p, `document.getElementById('rdfDl').click()`);
    await waitFor(p, `window.__dl.length > 0`);
    check(await ev(p, `window.__dl[0]`) === name, `${sfx} 4b: 下载 saves the file`);
    await ev(p, `document.getElementById('rdfShare').click()`);
    await waitFor(p, `window.__shared.length > 0`);
    check(JSON.stringify(await ev(p, `window.__shared[0]`)) === JSON.stringify([name]), `${sfx} 4b: 分享 hands the file to the share sheet`);
    const n0 = (fake.st.tgFwds || []).length;
    await ev(p, `document.getElementById('rdfTg').click()`);
    await waitFor(p, `document.getElementById('rdfTg').dataset.state === 'sent'`);
    check((fake.st.tgFwds || []).length === n0 + 1 && !!fake.st.tgFwds.at(-1).mid, `${sfx} 4b: Telegram forwards the file by id; the icon shows sent`);
    await ev(p, `document.getElementById('rdfMail').click()`);
    await waitFor(p, `!!window.__mail`);
    check(await ev(p, `window.__mail.kind === 'file' && window.__mail.name === ${JSON.stringify(name)}`), `${sfx} 4b: 邮件 forwards this file`);
    await ev(p, `document.getElementById('rdfCopy').click()`);
    await sleep(300);
    check(await ev(p, `navigator.clipboard.readText()`) === MD[lang], `${sfx} 4b: 复制 copies the whole file`);
    // select-to-quote: words selected in the file become a quote
    await ev(p, `(() => { const n = document.querySelector('#rdWords li'); const r = document.createRange(); r.selectNodeContents(n); const s = getSelection(); s.removeAllRanges(); s.addRange(r); })()`);
    await sleep(900);
    check(await ev(p, `!document.getElementById('qbar').hidden`), `${sfx} 4b: a selection in the file is quoted`);
    await ev(p, `getSelection().removeAllRanges()`);
    await sleep(500);
    // 回复 leaves the reader with the reply quoted and the caret in the field
    const replyTarget = await rect(p, '#rdfReply');
    if (w < 500) {
      await p.send('Input.dispatchTouchEvent', { type: 'touchStart', touchPoints: [{ x: replyTarget.x, y: replyTarget.y }] });
      await p.send('Input.dispatchTouchEvent', { type: 'touchEnd', touchPoints: [] });
    } else {
      await p.send('Input.dispatchMouseEvent', { type: 'mousePressed', x: replyTarget.x, y: replyTarget.y, button: 'left', clickCount: 1 });
      await p.send('Input.dispatchMouseEvent', { type: 'mouseReleased', x: replyTarget.x, y: replyTarget.y, button: 'left', clickCount: 1 });
    }
    await waitFor(p, `document.getElementById('rd').hidden`);
    await sleep(300);
    const rq = await ev(p, `({ focus: document.activeElement?.id, qbar: !document.getElementById('qbar').hidden, n: document.querySelectorAll('#qbar [data-i]').length, html: document.getElementById('qbar').innerText.slice(0, 120) })`);
    check(rq.qbar && rq.focus === 'input', `${sfx} 4b: 回复 = back to the field with the reply quoted ${JSON.stringify(rq)}`);
    await p.send('Input.insertText', { text: lang === 'en' ? 'Please explain this report.' : '请解释这份报告。' });
    check(await ev(p, `document.getElementById('input').value`) === (lang === 'en' ? 'Please explain this report.' : '请解释这份报告。'), `${sfx} 4b: typing reaches the focused composer`);
    writeFileSync(`${OUT}/reply-composer-${sfx}.png`, await shoot(p, ''));
    check(p.problems.length === 0, `${sfx} 4b: no page errors ${p.problems.slice(0, 3)}`);
    await p.dispose();
    // ⑫: a picture open in the viewer — a new message neither closes it nor moves the page
    p = await paired(w, h, scheme, lang);
    const png = Buffer.from('iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mP8/x8AAwMCAO+aD1sAAAAASUVORK5CYII=', 'base64'), mid = randomBytes(11).toString('hex');
    bytes.set(mid, png);
    const turn = await fake.addTurn({ k: 'phone', dev: null, text: 'chart' }, NOTE[lang], 'done');
    await fake.updateTurn(turn.id, { media: [{ mid, name: 'chart.png', mime: 'image/png', kind: 'image', bytes: png.length, sha256: createHash('sha256').update(png).digest('hex'), ref: 'out/chart.png' }] });
    const ic = `#mstrip .mcard[data-mid="${mid}"]`;
    await waitFor(p, `document.querySelector(${JSON.stringify(ic)})?.dataset.state === 'ready'`);
    await ev(p, `document.querySelector(${JSON.stringify(ic + ' .pv')}).click()`);
    await waitFor(p, `!!document.querySelector('.mview:not([hidden])')`);
    const pg0 = await ev(p, `document.getElementById('pg')?.textContent`);
    await fake.addTurn({ k: 'agent' }, lang === 'en' ? 'A new message.' : '新消息。', 'done');
    await sleep(900);
    const pv = await ev(p, `({ open: !!document.querySelector('.mview:not([hidden])'), pg: document.getElementById('pg')?.textContent })`);
    check(pv.open && pv.pg.split('/')[0].trim() === pg0.split('/')[0].trim(), `${sfx} ⑫: the picture viewer stays open, the page under it stays ${JSON.stringify(pv)} was ${pg0}`);
    await p.dispose();
    // 「点一下」: 批准 / 批量批准 one tap, 拒绝 one tap, a finger sliding off answers nothing
    p = await paired(w, h, scheme, lang);
    const ask = async (extra = {}) => { const id = randomBytes(16).toString('hex'); fake.st.holdAnswers = true;
      await fake.ask({ id, tool: 'Bash', summary: 'ls -la ~/reports', ttl: 120, ...extra });
      await waitFor(p, `document.body.dataset.sheet === '1' && !document.getElementById('apprBtns').hidden`); await sleep(500); return id; };
    const press = async (sel, off = false) => {
      const r = await rect(p, sel);
      if (w < 500) {
        await p.send('Input.dispatchTouchEvent', { type: 'touchStart', touchPoints: [{ x: r.x, y: r.y }] });
        await sleep(90);
        if (off) await p.send('Input.dispatchTouchEvent', { type: 'touchMove', touchPoints: [{ x: r.x, y: r.y + 160 }] });
        await p.send('Input.dispatchTouchEvent', { type: 'touchEnd', touchPoints: [] });
      } else {
        await p.send('Input.dispatchMouseEvent', { type: 'mouseMoved', x: r.x, y: r.y });
        await p.send('Input.dispatchMouseEvent', { type: 'mousePressed', x: r.x, y: r.y, button: 'left', clickCount: 1 });
        await sleep(90);
        if (off) await p.send('Input.dispatchMouseEvent', { type: 'mouseMoved', x: r.x, y: r.y + 160, button: 'left' });
        await p.send('Input.dispatchMouseEvent', { type: 'mouseReleased', x: off ? r.x : r.x, y: off ? r.y + 160 : r.y, button: 'left', clickCount: 1 });
      }
      await sleep(500);
    };
    // host `task_grant`: one small line above the buttons; `auto` by: 'task' = its own toast
    const tg = lang === 'en' ? 'Approving lets this task run the same kind of step again without asking.' : '批准后，本任务后面的同类步骤不再问。';
    let id = await ask({ batch: 'Bash：ls', batch_max: 20, batch_secs: 600, task_grant: tg });
    check(await ev(p, `(() => { const n = document.getElementById('apprTask'); return !!n && !n.hidden && n.textContent === ${JSON.stringify(tg)} && n.nextElementSibling === document.getElementById('apprBtns'); })()`), `${sfx} task_grant: the note above the buttons`);
    check(await ev(p, `document.getElementById('apprAllowLbl').textContent`) === (lang === 'en' ? 'Approve' : '批准'), `${sfx} tap: the button says 批准, not 长按批准`);
    writeFileSync(`${OUT}/appr-${sfx}.png`, await shoot(p, ''));
    let n = fake.st.answers.length;
    await press('#apprAllow', true);
    check(fake.st.answers.length === n, `${sfx} tap: slid off = nothing sent`);
    await press('#apprAllow');
    check(fake.st.answers.length === n + 1 && fake.st.answers.at(-1).id === id && fake.st.answers.at(-1).ok === true && fake.st.sigOk.at(-1) === true, `${sfx} tap: one tap approves, signed`);
    await fake.askDone(id, 'allow'); await sleep(600);
    id = await ask({ batch: 'Bash：ls', batch_max: 20, batch_secs: 600 });
    n = fake.st.answers.length;
    await press('#apprBatch');
    check(fake.st.answers.length === n + 1 && fake.st.answers.at(-1).batch === true && fake.st.sigOk.at(-1) === true, `${sfx} tap: one tap on 批量批准, signed over the scope`);
    await fake.askDone(id, 'allow'); await sleep(600);
    id = await ask();
    n = fake.st.answers.length;
    await press('#apprDeny');
    check(fake.st.answers.length === n + 1 && fake.st.answers.at(-1).ok === false, `${sfx} tap: 拒绝 one tap`);
    await fake.askDone(id, 'deny'); await sleep(600);
    await fake.send({ t: 'auto', by: 'task', tool: 'Bash', summary: 'ls -la ~/reports' });
    await waitFor(p, `/${lang === 'en' ? "this task's grant" : '本任务的授权'}/.test(document.body.innerText)`, 4000).then(() => check(true, ''), () => check(false, `${sfx} auto by task: the toast names the task grant`));
    id = await ask({ batch: 'Bash：ls' });
    check(await ev(p, `!document.getElementById('apprTask') || document.getElementById('apprTask').hidden`), `${sfx} task_grant: absent = no line`);
    await fake.askDone(id, 'deny'); await sleep(600);
    // a Face ID card keeps its passkey label (the passkey flow itself: p57_passkey / p547_faceid)
    id = await ask({ passkey_required: true });
    check(await ev(p, `document.getElementById('apprAllowLbl').textContent`) !== (lang === 'en' ? 'Approve' : '批准'), `${sfx} tap: a Face ID card keeps its own label`);
    await p.dispose();
  }
} finally {
  await B.close().catch(() => {});   // macOS: the reaper reads /proc (Linux only)
  await web.stop();
  await fake.stop?.();
}
writeFileSync(`${OUT}/results-${TAG}.json`, JSON.stringify(results, null, 1));
console.log(`p573 ${TAG}: ${Object.keys(results).length} cases, ${checks} checks passed`);
process.exit(0);
