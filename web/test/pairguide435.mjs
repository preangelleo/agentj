// #435 real page (encrypted fake host): the first-remote hint is the shared bottom toast for 10 s, once per device;
// with 2+ remotes it never appears; nothing of it stays under the header, enters the composer or reaches the host.
import assert from 'node:assert/strict';
import { mkdirSync, writeFileSync } from 'node:fs';
import { parityRecorder } from '../../parity/lib.mjs';
import { startWebServer } from './serve.mjs';
import { startFakeHost } from './fakehost.mjs';
import { launch, newPage, navigate, waitState, evaluate, sleep, shoot } from './browser.mjs';
import { dictionaries } from '../build.mjs';

const D = dictionaries();
const out = process.env.ISSUE435_OUT || 'reports/qa/fix-435-0268-20261103/screens'; mkdirSync(out, { recursive: true });
const rec = parityRecorder('web/test/pairguide435.mjs');
const web = await startWebServer({ port: 0, relayCsp: 'ws://127.0.0.1:*' });
const fake = await startFakeHost(); const b = await launch();
const devs = (n) => ({ t: 'devs', limit: 10, can_add: true, busy: false, items: Array.from({ length: n }, (_, i) => ({ id: 'd' + i, name: '网页 · macOS Chrome', paired_at: i + 1, seen: 0, online: i === 0, me: i === 0, source: 'local', named_by: 'device', self_name: '网页 · macOS Chrome', pk: 'none' })) });
const look = (p) => evaluate(p, `(()=>{const t=document.getElementById('toast');const r=t.getBoundingClientRect();const g=document.getElementById('pair-guide');
  const chat=document.getElementById('chat-view');
  return {on:t.classList.contains('on'),text:t.textContent,top:r.top,bottom:r.bottom,h:innerHeight,resident:!!g&&!g.hidden,
    inChat:[...(chat?chat.querySelectorAll('[role=status]:not([hidden]):not(#toast)'):[])].map(n=>n.textContent).join('|'),
    input:document.getElementById('input').value,scroll:document.documentElement.scrollWidth,win:innerWidth}})()`);
const sentText = (text) => fake.log.some((m) => JSON.stringify(m).includes(text.slice(0, 12)));
let checks = 0;
try {
  for (const [lang, kind, w] of [['zh', 'computer', 1440], ['en', 'phone', 390]]) {
    const text = D[lang]['pairGuide.' + kind] ?? D[lang].pairGuide?.[kind];
    assert.ok(text, 'existing wording ' + lang + ' ' + kind);
    const p = await newPage(b, w, 852, 'dark', { allow: [web.url, fake.relay + '/'] });
    try {
      await navigate(p, fake.newPairing(web.url + '?lang=' + lang)); await waitState(p, 'awaiting-approval');
      await fake.approve(); await waitState(p, 'ready');
      // exactly one remote: one toast
      await fake.send(devs(1)); await fake.send({ t: 'pair_guide', kind }); await sleep(300);
      let r = await look(p);
      assert.equal(r.on, true, 'toast shows'); assert.ok(r.text.includes(text), r.text);
      assert.ok(r.top > r.h / 2, 'it is the bottom notice, not a line under the header ' + JSON.stringify(r)); assert.equal(r.resident, false);
      assert.ok(!r.inChat.includes(text.slice(0, 8)), 'nothing resident in the chat status lines');
      assert.ok(r.scroll <= r.win + 1); checks += 6;
      writeFileSync(`${out}/${lang}-${kind}-toast.png`, await shoot(p, `${out}/${lang}-${kind}-toast.png`));
      await sleep(9000); r = await look(p); assert.equal(r.on, true, 'still there just before 10 s'); checks++;
      await sleep(1700); r = await look(p); assert.equal(r.on, false, 'gone by itself after 10 s'); checks++;
      writeFileSync(`${out}/${lang}-${kind}-after-10s.png`, await shoot(p, `${out}/${lang}-${kind}-after-10s.png`));
      // the host repeats the hint on every resume; a refresh of the same device never shows it again
      await fake.send({ t: 'pair_guide', kind }); await sleep(300);
      assert.equal((await look(p)).on, false); checks++;
      await navigate(p, web.url + '?lang=' + lang); await waitState(p, 'ready');
      await fake.send(devs(1)); await fake.send({ t: 'pair_guide', kind }); await sleep(400);
      r = await look(p); assert.equal(r.on, false, 'refresh: once per device'); assert.equal(r.resident, false); checks += 2;
      assert.equal(r.input, '', 'never in the composer'); assert.equal(sentText(text), false, 'never sent to the host / Agent'); checks += 2;
    } finally { await p.close(); }
  }
  rec.record('p435-first-remote-toast-once-10s', true);
  // 2+ remotes on a brand-new device (no "seen" mark): an older host that still sends the hint shows nothing
  for (const n of [2, 3, 4]) {
    const p = await newPage(b, 390, 852, 'dark', { allow: [web.url, fake.relay + '/'] });
    try {
      await navigate(p, fake.newPairing(web.url + '?lang=zh')); await waitState(p, 'awaiting-approval');
      await fake.approve(); await waitState(p, 'ready');
      await fake.send(devs(n)); await fake.send({ t: 'pair_guide', kind: 'computer' }); await sleep(400);
      const r = await look(p);
      assert.equal(r.on && r.text.includes('首台遥控器'), false, n + ' remotes: no toast');
      assert.equal(r.resident, false, n + ' remotes: no resident line'); assert.equal(r.input, ''); checks += 3;
      if (n === 4) writeFileSync(`${out}/zh-four-remotes-none.png`, await shoot(p, `${out}/zh-four-remotes-none.png`));
    } finally { await p.close(); }
  }
  rec.record('p435-multi-remote-never', true);
  console.log('PASS #435 real page ' + checks + ' assertions');
} catch (e) {
  rec.record('p435-first-remote-toast-once-10s', false); rec.record('p435-multi-remote-never', false);
  throw e;
} finally { rec.write(); await b.close(); await fake.stop(); await web.stop(); }
