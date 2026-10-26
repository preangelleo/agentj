// P543 (#308): the 「来自微信」 page against the fake host — before (the host's old text: the inbox path) and after
// (card.channel: the words, the low-trust mark, a folded long message, attachments as media cards, the path behind 「…」).
//   P543_SHOTS=<dir> node web/test/p543.mjs        (screenshots → $P543_SHOTS, default reports/qa/p543)
import assert from 'node:assert/strict';
import {mkdirSync, writeFileSync} from 'node:fs';
import {createHash} from 'node:crypto';
import {deflateSync, crc32} from 'node:zlib';
import {startWebServer} from './serve.mjs';
import {startFakeHost} from './fakehost.mjs';
import {launch, newPage, evaluate, navigate, waitFor, waitState, shoot} from './browser.mjs';
const OUT = process.env.P543_SHOTS || 'reports/qa/p543';
mkdirSync(OUT, {recursive: true});
const sha = (b) => createHash('sha256').update(b).digest('hex');
function png(w, h, f) {
  const raw = Buffer.alloc((w * 3 + 1) * h);
  for (let y = 0; y < h; y++) for (let x = 0; x < w; x++) { const [r, g, b] = f(x, y); raw.set([r, g, b], y * (w * 3 + 1) + 1 + x * 3); }
  const chunk = (t, d) => { const len = Buffer.alloc(4); len.writeUInt32BE(d.length); const td = Buffer.concat([Buffer.from(t), d]); const c = Buffer.alloc(4); c.writeUInt32BE(crc32(td)); return Buffer.concat([len, td, c]); };
  const ihdr = Buffer.alloc(13); ihdr.writeUInt32BE(w, 0); ihdr.writeUInt32BE(h, 4); ihdr[8] = 8; ihdr[9] = 2;
  return Buffer.concat([Buffer.from('\x89PNG\r\n\x1a\n', 'latin1'), chunk('IHDR', ihdr), chunk('IDAT', deflateSync(raw)), chunk('IEND', Buffer.alloc(0))]);
}
const PHOTO = png(480, 320, (x, y) => [60 + (x * 160 / 480) | 0, 120 + (y * 100 / 320) | 0, 180 - (x * 120 / 480) | 0]);
const PDF = Buffer.from('%PDF-1.4\n1 0 obj<</Type/Catalog/Pages 2 0 R>>endobj 2 0 obj<</Type/Pages/Kids[3 0 R]/Count 1>>endobj ' +
  '3 0 obj<</Type/Page/MediaBox[0 0 200 200]/Parent 2 0 R>>endobj\ntrailer<</Root 1 0 R>>\n%%EOF\n');
const BYTES = new Map([['P'.repeat(22), PHOTO], ['Q'.repeat(22), PDF]]);
const MEDIA = [{mid: 'P'.repeat(22), name: 'IMG_2041.jpg', mime: 'image/png', kind: 'image', bytes: PHOTO.length, sha256: sha(PHOTO), ref: ''},
  {mid: 'Q'.repeat(22), name: '报价单.pdf', mime: 'application/pdf', kind: 'pdf', bytes: PDF.length, sha256: sha(PDF), ref: ''}];
const INBOX = '/srv/owner/coding/.agentj/inbox/wechat/2026-10-26/7f3a9c/wechat-message.json';
const T = {
  zh: {label: '来自微信', short: '越疆科技今天为什么涨？顺便看下这张截图和报价单，帮我判断一下值不值得追。',
       long: ('周五开会前帮我把下面这些事情理一下：\n\n' + '1. 供应商报价今天下午更新了，新的单价比上周高了百分之六，需要对比一下原来的合同条款，看看有没有约定调价上限。\n'.repeat(7) +
              '2. 设计那边说下周二交初稿，但是我们市场活动定在周三，时间可能来不及，需要问一下能不能先交一部分。\n'.repeat(7))},
  en: {label: 'From WeChat', short: 'Why is Dobot up today? Also check this screenshot and the quote, and tell me if it is worth chasing.',
       long: ('Before Friday\'s meeting, please sort these out:\n\n' + '1. The supplier updated the quote this afternoon; the unit price is 6% higher than last week. Compare it with the original contract terms and check for a price-change cap.\n'.repeat(4) +
              '2. Design says the first draft lands next Tuesday, but our campaign is on Wednesday. Ask whether they can deliver part of it first.\n'.repeat(4))},
};
const web = await startWebServer({port: 0, relayCsp: 'ws://127.0.0.1:*'});
const fake = await startFakeHost();
const B = await launch();
let failed = 0;
function serveMedia(st) {
  st.onApp = async (c, m, send) => {
    if (m.t !== 'media_get') return false;
    const b = BYTES.get(m.mid);
    if (!b) { await send({t: 'media_err', mid: m.mid, why: 'gone'}); return true; }
    for (let i = 0; i < 8; i++) {             // the phone's window: 8 chunks of 44 KiB from the asked offset (as p57_media)
      const o = m.o + i * 45056;
      if (o >= b.length) break;
      const d = b.subarray(o, Math.min(b.length, o + 45056));
      await send({t: 'media_chunk', mid: m.mid, o, d: Buffer.from(d).toString('base64url'), last: o + d.length >= b.length});
    }
    return true;
  };
}
async function paired(lang, theme) {
  fake.reset(); fake.setUp?.(true); serveMedia(fake.st);
  const p = await newPage(B, 390, 844, theme, {touch: true, allow: [web.url, fake.relay + '/']});
  await navigate(p, fake.newPairing(web.url + '?lang=' + lang));
  await waitState(p, 'awaiting-approval'); await fake.approve(); await waitState(p, 'ready');
  await evaluate(p, `document.getElementById('phone-env-dismiss')?.click();if(!document.getElementById('a2hs').hidden)document.getElementById('a2hs-ok').click();document.documentElement.dataset.theme='${theme}'`);
  await fake.send({t: 'status', s: 'idle', agent: 'claude', name: 'Agent J'});
  return p;
}
const chanCard = (lang, text, cut = false) => ({channel: {source: 'wechat', label: T[lang].label, text, cut,
  files: ['IMG_2041.jpg', '报价单.pdf'], paths: [INBOX, INBOX.replace('wechat-message.json', 'IMG_2041.jpg')]}});
async function C(name, fn) {
  try { await fn(); console.log('  ✓ ' + name); } catch (e) { failed++; console.log('  ✗ ' + name + ': ' + (e && e.stack || e)); }
}
try {
  for (const [lang, theme] of [['zh', 'light'], ['en', 'dark']]) {
    const tag = `${lang}-${theme}-390`;
    let p;
    await C(`${tag} before: the old page shows only the inbox path`, async () => {
      p = await paired(lang, theme);
      await fake.addTurn({k: 'sys', text: ''}, (lang === 'zh' ? '来自微信 · ' : 'From WeChat · ') + INBOX, 'done');
      await waitFor(p, `document.getElementById('words').innerText.includes('wechat-message.json')`);
      writeFileSync(`${OUT}/before-${tag}.png`, await shoot(p));
    });
    await C(`${tag} after: words, low-trust mark, media cards, path only behind …`, async () => {
      p = await paired(lang, theme);
      const turn = await fake.addTurn({k: 'sys', text: '', via: 'wechat'}, T[lang].label + (lang === 'zh' ? '：' : ': ') + T[lang].short, 'done', chanCard(lang, T[lang].short));
      await fake.updateTurn(turn.id, {media: MEDIA, media_skip: []});
      await waitFor(p, `document.getElementById('words').innerText.includes(${JSON.stringify(T[lang].short.slice(0, 8))})`);
      await waitFor(p, `!!document.querySelector('#words img')`, 8000).catch(() => {});
      const w = await evaluate(p, `document.getElementById('words').innerText`);
      assert.ok(!w.includes('wechat-message.json'), 'no inbox path in the words');
      assert.ok(w.startsWith(T[lang].label), w);
      assert.equal(await evaluate(p, `document.getElementById('cmdx').hidden`), false);
      assert.equal(await evaluate(p, `document.querySelector('#cmdx details.chanx summary').textContent`), '…');
      writeFileSync(`${OUT}/after-${tag}-short.png`, await shoot(p));
      await evaluate(p, `document.querySelector('#cmdx details.chanx').open=true`);
      assert.ok((await evaluate(p, `document.querySelector('#cmdx details.chanx p').textContent`)).includes('wechat-message.json'));
      await evaluate(p, `document.querySelector('#cmdx details.chanx').scrollIntoView({block:'end'})`);
      writeFileSync(`${OUT}/after-${tag}-details.png`, await shoot(p));
    });
    await C(`${tag} after: a long message folds behind 展开 and opens in place`, async () => {
      p = await paired(lang, theme);
      await fake.addTurn({k: 'sys', text: '', via: 'wechat'}, T[lang].label, 'done', chanCard(lang, T[lang].long.trim()));
      await waitFor(p, `!!document.querySelector('#cmdx [data-chan-fold]')`);
      const before = await evaluate(p, `document.getElementById('words').innerText.length`);
      writeFileSync(`${OUT}/after-${tag}-long-folded.png`, await shoot(p));
      await evaluate(p, `document.querySelector('#cmdx [data-chan-fold]').click()`);
      await waitFor(p, `document.getElementById('words').innerText.length>${before}`);
      writeFileSync(`${OUT}/after-${tag}-long-open.png`, await shoot(p, '', {full: true}));
    });
  }
} finally {
  await B.close?.(); await fake.close?.(); await web.close?.();
}
console.log(failed ? `P543 phone: ${failed} failed` : 'P543 phone: all passed');
process.exit(failed ? 1 : 0);
