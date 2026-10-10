// P115 (P92 ADR-A196): the first-run checklist card on the real page + encrypted local fake host.
// Phone 360/390 and desktop 1440, zh/en, light/dark: the card shows the host snapshot, "I won't use this" sends a setup_mark whose
// Ed25519 signature verifies over (item, choice, revision), a required item has no "I won't use this", the full 30-item list
// expands, the ready state shows the leaving text; touch targets ≥ 56 px, no horizontal overflow, no console errors.
// Own temporary Chromium (browser.mjs), closed in finally; screenshots → reports/qa/p115/screens.
import assert from 'node:assert/strict';
import { mkdirSync, readFileSync, writeFileSync } from 'node:fs';
import { resolve } from 'node:path';
import { webcrypto } from 'node:crypto';
import { startWebServer } from './serve.mjs';
import { startFakeHost } from './fakehost.mjs';
import { launch, newPage, navigate, waitFor, waitState, evaluate, shoot, sleep } from './browser.mjs';
import * as wire from '../../protocol/wire.js';

const FIX = JSON.parse(readFileSync(new URL('./fixtures/p115-setup.json', import.meta.url), 'utf8'));
const OUT = resolve(process.env.P115_SCREENS || 'reports/qa/p115/screens');
const $ = (id) => `document.getElementById('${id}')`;
const clone = (o) => JSON.parse(JSON.stringify(o));

function ready(snap) {
  const s = clone(snap);
  for (const i of s.items) if (i.choice === 'selected' || i.id === 'exit') i.state = 'verified';
  s.items.find((i) => i.id === 'tg').choice = 'unused';
  s.current = null; s.remote_ready = true; s.blocking = []; s.revision += 5;
  s.counts = { ...s.counts, verified: s.counts.verified + 3, todo: 0, auto: 0, unused: 1 };
  return s;
}

async function run() {
  mkdirSync(OUT, { recursive: true });
  const web = await startWebServer(), fake = await startFakeHost(), B = await launch();
  const facts = [];
  try {
    for (const lang of ['zh', 'en']) for (const [w, h] of [[360, 780], [390, 844], [1440, 900]]) for (const theme of ['light', 'dark']) {
      fake.reset();
      let snap = clone(FIX);
      const marks = [];
      fake.st.onApp = async (c, m, send) => {
        if (m.t === 'setup_get') { await send({ t: 'setup_card', r: m.r, show: false, ...snap }); return true; }
        if (m.t !== 'setup_mark') return false;
        const dev = fake.devs.find((d) => d.id === c.devId);
        const msg = await wire.controlMessage(fake.channel, c.devId, 'setup_mark', m.n, m.ts, { item: m.item, choice: m.choice, rev: m.rev });
        const key = await webcrypto.subtle.importKey('raw', wire.unb64u(dev.sk), { name: 'Ed25519' }, false, ['verify']);
        const ok = await webcrypto.subtle.verify({ name: 'Ed25519' }, key, wire.unb64u(m.sig), msg) && m.rev === snap.revision;
        marks.push({ item: m.item, choice: m.choice, rev: m.rev, ok });
        await send({ t: 'ctl_res', r: m.r, action: 'setup_mark', ok, ...(ok ? {} : { why: 'bad_signature' }) });
        if (ok) {
          snap = clone(snap); snap.revision += 1;
          const it = snap.items.find((i) => i.id === m.item);
          if (m.choice === 'unused') it.choice = 'unused';
          snap.current = 'awake'; snap.group = 2; snap.counts = { ...snap.counts, unused: snap.counts.unused + 1, undecided: snap.counts.undecided - 1 };
          await fake.send({ t: 'setup_card', show: true, ...snap });
        }
        return true;
      };
      const touch = w < 600;
      const p = await newPage(B, w, h, theme, { touch, allow: [web.url, fake.relay + '/'] });
      const tag = `${lang}-${w}-${theme}`;
      try {
        await navigate(p, fake.newPairing(web.url + '?lang=' + lang)); await waitState(p, 'awaiting-approval'); await fake.approve(); await waitState(p, 'ready');
        await evaluate(p, `if(${$('a2hs')} && !${$('a2hs')}.hidden)${$('a2hs-ok')}.click()`);
        await fake.send({ t: 'status', s: 'idle', agent: 'claude', name: 'Agent J' });
        await fake.send({ t: 'setup_card', show: true, ...snap });
        await waitFor(p, `${$('setupcard')} && !${$('setupcard')}.hidden`);
        const title = await evaluate(p, `${$('setup-title')}.textContent`);
        assert.equal(title, lang === 'zh' ? '手机麦克风与相机' : 'Phone microphone and camera', tag);
        const geo = async (ids) => evaluate(p, `(${JSON.stringify(ids)}).map(id=>{const n=document.getElementById(id);if(!n)return [id,null];const r=n.getBoundingClientRect();return [id,{w:r.width,h:r.height}]})`);
        for (const [id, r] of await geo(['setup-use', 'setup-unused', 'setup-later', 'setup-expand', 'setup-close'])) {
          assert.ok(r && r.h >= 56 && r.w >= 44, `${tag} ${id} ${JSON.stringify(r)}`);
        }
        assert.equal(await evaluate(p, 'document.documentElement.scrollWidth > innerWidth'), false, tag + ' overflow');
        writeFileSync(resolve(OUT, `step-${tag}.png`), await shoot(p, ''));
        // "I won't use this" → a signed setup_mark the fake host verifies → the next (required) item, without that button
        await evaluate(p, `${$('setup-unused')}.click()`);
        await waitFor(p, `${$('setupcard')}.dataset.revision === '${FIX.revision + 1}'`);
        assert.deepEqual(marks, [{ item: 'phonepermissions', choice: 'unused', rev: FIX.revision, ok: true }], tag);
        assert.equal(await evaluate(p, `${$('setup-title')}.textContent`), lang === 'zh' ? '接电源、不睡眠' : 'Plugged in, no sleep');
        assert.equal(await evaluate(p, `!!${$('setup-unused')}`), false, tag + ' required item has no "I won\'t use this"');
        assert.equal(await evaluate(p, `!!${$('setup-check')} && !!${$('setup-later')}`), true, tag);
        writeFileSync(resolve(OUT, `required-${tag}.png`), await shoot(p, ''));
        await evaluate(p, `${$('setup-expand')}.click()`);
        await waitFor(p, `document.querySelectorAll('#setupcard .setup-row').length === 30`);
        assert.equal(await evaluate(p, `document.querySelector('#setupcard .setup-row[data-state="unused"]') !== null`), true);
        writeFileSync(resolve(OUT, `overview-${tag}.png`), await shoot(p, ''));
        // "I'm done, check" asks the host to re-probe (read-only), never marks anything itself
        await evaluate(p, `${$('setup-check')}.click()`);
        await sleep(300);
        assert.equal(marks.length, 1, 'a check sends no setup_mark');
        await fake.send({ t: 'setup_card', show: true, ...ready(snap) });
        await waitFor(p, `${$('setup-title')}.textContent === ${JSON.stringify(lang === 'zh' ? '可以离开电脑了' : 'You can leave the computer')}`);
        writeFileSync(resolve(OUT, `done-${tag}.png`), await shoot(p, ''));
        assert.deepEqual(p.problems.filter((x) => !/favicon/.test(x)), [], tag + ' console');
        facts.push({ tag, ok: true });
      } finally {
        await p.dispose();
      }
    }
  } finally {
    await B.close(); await fake.stop(); await web.stop();
  }
  writeFileSync(resolve(OUT, 'validation.json'), JSON.stringify({ cases: facts.length, facts }, null, 1) + '\n');
  console.log(`PASS p115 setup card: ${facts.length} viewport/language/theme cases`);
}
run().catch((e) => { console.error(e); process.exit(1); });
