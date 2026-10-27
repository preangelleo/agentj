#!/usr/bin/env node
// P559 追加 (chairman 17:13 / 17:16): Settings → the device-unlock row, the real web client against fakehost.mjs, own headless
// Chromium (never :9222). iPhone = 「本机 Face ID」 with a face icon; Mac and Android / HarmonyOS (a Huawei Pad in desktop
// mode included) = 「本机 Touch ID」 with a fingerprint icon; every one carries the same small explanation line as the rows
// above and below it (遥控器管理 · 转移席位), never 「屏幕锁」 as its title.
// Shots: 390 × 844 (Mac 1440 × 900) zh dark + en light, states local / none →
//   reports/qa/p559/row/<phase>/<os>-<state>-<scheme>-<lang>.png   (P559_PHASE=before for the pre-change tree)
//   node web/test/p559_row.mjs
import assert from 'node:assert/strict';
import { mkdirSync, writeFileSync } from 'node:fs';
import { fileURLToPath } from 'node:url';
import { startWebServer } from './serve.mjs';
import { startFakeHost } from './fakehost.mjs';
import { launch, newPage, evaluate, navigate, waitFor, waitState, shoot, sleep } from './browser.mjs';

const PHASE = process.env.P559_PHASE || 'after';
const OUT = process.env.P559_OUT || fileURLToPath(new URL(`../../../reports/qa/p559/row/${PHASE}/`, import.meta.url));   // gate runs: P559_OUT
mkdirSync(OUT, { recursive: true });
const UA = {
  ios: 'Mozilla/5.0 (iPhone; CPU iPhone OS 26_0 like Mac OS X) AppleWebKit/605.1.15 (KHTML, like Gecko) Version/26.0 Mobile/15E148 Safari/604.1',
  huawei: 'Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/114.0.0.0 Safari/537.36',
  mac: 'Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/141.0.0.0 Safari/537.36',
};
const WANT = { ios: ['Face ID', 'face'], huawei: ['Touch ID', 'finger'], mac: ['Touch ID', 'finger'] };
const web = await startWebServer({ port: 0, relayCsp: 'ws://127.0.0.1:*' });
const fake = await startFakeHost();
const B = await launch();
let checks = 0;
const check = (v, label) => { if (PHASE !== 'after') return; assert.ok(v, label); checks++; };

try {
  for (const os of Object.keys(UA)) for (const [scheme, lang] of [['dark', 'zh'], ['light', 'en']]) {
    fake.reset();
    const [w, h] = os === 'mac' ? [1440, 900] : [390, 844];
    const p = await newPage(B, w, h, scheme, { ua: UA[os], touch: os !== 'mac', allow: [web.url, fake.relay + '/'] });
    await navigate(p, fake.newPairing(web.url + '?lang=' + lang));
    await waitState(p, 'awaiting-approval'); await fake.approve(); await waitState(p, 'ready');
    await fake.send({ t: 'status', s: 'idle', agent: 'claude', name: 'Agent J' });
    await evaluate(p, `document.getElementById('phone-env-dismiss')?.click()`);
    for (const s of ['local', 'none']) {
      await fake.send({ t: 'pk_state', s });
      await evaluate(p, `import('./js/settings.js').then((m) => m.open())`);
      await waitFor(p, `!document.getElementById('set-pk-row').hidden && document.getElementById('set-pk-row').dataset.pk === ${JSON.stringify(s)}`);
      await evaluate(p, `document.getElementById('set-remotes-open').scrollIntoView({ block: 'start' }); window.scrollBy?.(0, -40)`);
      await sleep(250);
      const r = await evaluate(p, `(() => { const row = document.getElementById('set-pk-row'), q = (s) => row.querySelector(s);
        const icon = [...row.querySelectorAll('svg.mi')].find((x) => x.getClientRects().length);
        const sub = document.getElementById('set-pk-sub');
        const nav = document.getElementById('set-remotes-open').querySelector('small');
        return { title: document.getElementById('set-pk-state').textContent, text: row.innerText, icon: icon ? icon.dataset.icon : '',
          sub: sub && sub.getClientRects().length ? sub.textContent : '', subSize: sub ? getComputedStyle(sub).fontSize : '',
          navSize: nav ? getComputedStyle(nav).fontSize : '', btn: !document.getElementById('set-pk-setup').hidden }; })()`);
      const tag = `${os} ${s} ${lang} ${JSON.stringify(r)}`;
      const [name, icon] = WANT[os];
      check(r.title.includes(name) && !/屏幕锁|screen lock|指纹或人脸|fingerprint or face/i.test(r.title), tag + ': title names ' + name);
      check(r.icon === icon, tag + ': ' + icon + ' icon');
      check(r.sub.includes('Face ID') && r.sub.includes('Touch ID') && /屏幕锁|screen lock/.test(r.sub), tag + ': the explanation line names all three');
      check(r.subSize === r.navSize, tag + ': explanation in the same small type as 遥控器管理');
      check(r.btn === (s === 'none'), tag + ': 「在本机设置」 only while not set');
      writeFileSync(`${OUT}${os}-${s}-${scheme}-${lang}.png`, await shoot(p, ''));
    }
    await p.dispose();
  }
} finally {
  await B.close(); await fake.stop(); await web.stop();
}
console.log(`p559_row ${PHASE}: ${checks} checks OK · shots → ${OUT}`);
process.exit(0);
