#!/usr/bin/env node
// 0.30.6 (#0304): real paired client + local Noise fake host + Chromium. No production pairing, no network.
//   A. a host notice with no owner input → the page header reads 「Agent J 主机提示」 / "Agent J host notice"
//   B. Settings → 阅读和声音 → 「语音原文件保留」: hidden until the host lists inbox.voice_retention_hours in `settable`,
//      shows the current value, writes the integer through pref_set, follows the next `preferences` broadcast.
// 390 / 1440 × light / dark × zh / en. P0304_BEFORE=<old public dir> shoots the same header fixture on the old page.
import assert from 'node:assert/strict';
import { mkdirSync, writeFileSync } from 'node:fs';
import { resolve, join } from 'node:path';
import { fileURLToPath } from 'node:url';
import { startWebServer } from './serve.mjs';
import { startFakeHost } from './fakehost.mjs';
import { launch, newPage, navigate, waitFor, waitState, evaluate, shoot, sleep } from './browser.mjs';

const ROOT = fileURLToPath(new URL('../../../', import.meta.url));
const before = process.env.P0304_BEFORE;
const out = resolve(process.env.P0304_OUT || join(ROOT, 'reports/qa/p0304/web'), before ? 'before' : 'after');
mkdirSync(out, { recursive: true });
const KEY = 'inbox.voice_retention_hours';
const HEADER = { zh: 'Agent J 主机提示', en: 'Agent J host notice' };
const OPTS = { zh: ['1 小时', '6 小时', '24 小时（默认）', '3 天', '7 天', '30 天', '不自动删'],
  en: ['1 hour', '6 hours', '24 hours (default)', '3 days', '7 days', '30 days', 'Never delete automatically'] };
const web = await startWebServer(before ? { publicDir: resolve(before) } : {}), fake = await startFakeHost(), B = await launch();
const rows = [];
try {
  for (const width of [390, 1440]) for (const theme of ['light', 'dark']) for (const lang of ['zh', 'en']) {
    const tag = `${width}-${theme}-${lang}`;
    fake.reset(); fake.st.prefSet = true;
    fake.st.prefs = { appearance: { language: lang, theme: 'system' }, voice: { speak_replies: false }, inbox: { voice_retention_hours: 24 } };
    const sets = [];
    fake.st.onApp = async (c, m, send) => {
      if (m.t !== 'pref_set' || m.key !== KEY) return false;
      sets.push(m.value);
      const ok = [0, 1, 6, 24, 72, 168, 720].includes(m.value);
      if (ok) fake.st.prefs.inbox.voice_retention_hours = m.value;
      await send({ t: 'pref_res', r: m.r, ok, key: m.key, ...(ok ? {} : { problem: 'bad_value' }) });
      if (ok) { const pm = fake.prefsMsg(); pm.host.settable.push(KEY); await fake.send(pm); }
      return true;
    };
    const p = await newPage(B, width, 844, theme, { touch: width < 600, allow: [web.url, fake.relay + '/'] });
    try {
      await navigate(p, fake.newPairing(web.url + '?lang=' + lang)); await waitState(p, 'awaiting-approval'); await fake.approve(); await waitState(p, 'ready');
      await fake.send({ t: 'status', s: 'idle', agent: 'claude', name: 'Agent J' });
      await evaluate(p, `document.getElementById('phone-env-dismiss')?.click();document.getElementById('a2hs-ok')?.click()`);
      // A. a host notice (automation report), no owner input
      await fake.addTurn({ k: 'agent', via: 'hook', name: lang === 'zh' ? '备份报告' : 'Backup report', text: '' },
        lang === 'zh' ? '备份已完成，3 个目录都已保存。' : 'Backup finished; 3 folders saved.', 'done');
      await waitFor(p, `document.getElementById('om').dataset.header==='1'`);
      const head = await evaluate(p, `document.getElementById('omText').textContent`);
      writeFileSync(join(out, `${tag}-header.png`), await shoot(p));
      const row = { tag, header: head };
      if (!before) {
        assert.equal(head, HEADER[lang]);
        // B. Settings row: absent while the host does not list the key
        await evaluate(p, `document.getElementById('setBtn').click()`);
        await waitFor(p, `!document.getElementById('settings').hidden`);
        assert.equal(await evaluate(p, `document.getElementById('set-voice-keep-row').hidden`), true, 'older host: no row');
        const pm = fake.prefsMsg(); pm.host.settable.push(KEY); await fake.send(pm);
        await waitFor(p, `!document.getElementById('set-voice-keep-row').hidden`);
        await evaluate(p, `document.getElementById('set-voice-keep-row').scrollIntoView({block:'center'})`); await sleep(200);
        const got = await evaluate(p, `(()=>{const s=document.getElementById('set-voice-keep'),r=s.getBoundingClientRect(),row=document.getElementById('set-voice-keep-row');
          return {value:s.value,opts:[...s.options].map(o=>o.textContent),label:row.querySelector('.setlab').textContent,note:row.querySelector('.setnote').textContent,
          h:r.height,w:r.width,inView:r.top>=0&&r.bottom<=innerHeight,overflow:document.documentElement.scrollWidth>innerWidth}})()`);
        assert.equal(got.value, '24'); assert.deepEqual(got.opts, OPTS[lang]);
        assert.equal(got.label, lang === 'zh' ? '语音原文件保留' : 'Keep voice recordings');
        assert.ok(got.h >= 44, 'touch target ≥ 44 px: ' + got.h); assert.ok(got.inView); assert.equal(got.overflow, false);
        writeFileSync(join(out, `${tag}-settings.png`), await shoot(p));
        await evaluate(p, `(()=>{const s=document.getElementById('set-voice-keep');s.value='168';s.dispatchEvent(new Event('change'))})()`);
        await waitFor(p, `document.getElementById('set-voice-keep').value==='168'`);
        for (let i = 0; i < 40 && !sets.length; i++) await sleep(50);
        assert.deepEqual(sets, [168], 'one integer pref_set');
        await evaluate(p, `(()=>{const s=document.getElementById('set-voice-keep');s.value='0';s.dispatchEvent(new Event('change'))})()`);
        for (let i = 0; i < 40 && sets.length < 2; i++) await sleep(50);
        assert.deepEqual(sets, [168, 0], '不自动删 = 0');
        await evaluate(p, `document.activeElement?.blur()`);
        const pm2 = fake.prefsMsg(); pm2.host.settable.push(KEY); pm2.value.inbox.voice_retention_hours = 720; await fake.send(pm2);
        await waitFor(p, `document.getElementById('set-voice-keep').value==='720'`);
        Object.assign(row, { settings: got, sets });
      }
      assert.deepEqual(p.offsite || [], []);
      rows.push(row);
      console.log('PASS p0304', before ? 'before' : 'after', tag, head);
    } finally { await p.dispose(); }
  }
} finally {
  writeFileSync(join(out, 'result.json'), JSON.stringify({ scope: 'local Noise fake host + real web client; synthetic data', rows }, null, 2));
  await B.close(); await fake.stop(); await web.stop();
}
