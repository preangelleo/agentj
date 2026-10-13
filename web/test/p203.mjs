// P203 — the open-cards snapshot (§29.1) and the upgrade confirmation card (§29.2), real Chromium + the Noise fake host.
// 1. a card the page never heard (made while it was frozen / offline) appears when the page comes back to the front;
// 2. a card answered on another remote while this one was away closes quietly; one whose time ran out says 「已过期」;
// 3. the snapshot request is signed with this device's approval key (the fake verifies it like the real host);
// 4. ≡ /update and Settings 「升级电脑上的 Agent J」: something in the way → the card lists it, 「现在升级」 = the signed /update,
//    nothing in the way → one tap; the computer's own card (the Agent ran `agentj update apply`): 「先不升」 closes it on the
//    computer, a remote's /update closes it here; 5. the one 「空闲时自动更新」 switch writes both keys.
// zh + en; screenshots → reports/qa/p203/.
import assert from 'node:assert/strict';
import {mkdirSync, writeFileSync} from 'node:fs';
import {randomBytes} from 'node:crypto';
import {startWebServer} from './serve.mjs';
import {startFakeHost} from './fakehost.mjs';
import {launch, newPage, navigate, waitFor, waitState, evaluate, shoot} from './browser.mjs';
import {parityRecorder} from '../../parity/lib.mjs';

const out = new URL('../../../reports/qa/p203/', import.meta.url); mkdirSync(out, {recursive: true});
const web = await startWebServer(), fake = await startFakeHost(), B = await launch();
const results = {};
const sleep = (ms) => new Promise((r) => setTimeout(r, ms));
const id32 = () => randomBytes(16).toString('hex');
const front = (p) => evaluate(p, `document.dispatchEvent(new Event('visibilitychange'))`);   // back in front on a live session
async function click(p, id){
  const b = await evaluate(p, `(()=>{const r=document.getElementById(${JSON.stringify(id)}).getBoundingClientRect();return {x:r.x+r.width/2,y:r.y+r.height/2}})()`);
  await p.send('Input.dispatchMouseEvent', {type: 'mousePressed', button: 'left', clickCount: 1, ...b});
  await p.send('Input.dispatchMouseEvent', {type: 'mouseReleased', button: 'left', clickCount: 1, ...b});
}
const shot = async (p, name) => writeFileSync(new URL(name + '.png', out), await shoot(p, ''));
const T = {
  zh: {expired: '已过期，已自动拒绝', upgTitle: '会打断一些事', ask: '2 张等待中的审批卡', turn: 'Agent 正在进行的回合', auto: '升级方式'},
  en: {expired: 'Expired — denied automatically', upgTitle: 'would interrupt something', ask: '2 approval card(s) waiting', turn: "the Agent's turn in progress", auto: 'Upgrade method'},
};
try {
  for (const language of ['zh', 'en']) {
    fake.reset();
    Object.assign(fake.st, {extraCaps: ['cards1', 'upg1'], cards: true, prefs: {updates: {auto_install: true, mode: 'auto'}, appearance: {language, theme: 'system'}}});
    const L = T[language];
    const p = await newPage(B, 390, 844, 'light', {touch: true, allow: [web.url, fake.relay + '/']});
    await navigate(p, fake.newPairing(web.url + '?lang=' + language)); await waitState(p, 'awaiting-approval'); await fake.approve(); await waitState(p, 'ready');

    // 3. ready → one signed snapshot request (the fake checks the Ed25519 signature over control-v1 / cards_get)
    for (let i = 0; i < 50 && !fake.st.cardGets.length; i++) await sleep(100);
    assert.equal(fake.st.cardGets.length, 1, 'one cards_get after ready');
    assert.equal(fake.st.cardGets[0].sigOk, true, 'signed with the approval key');
    results['snapshot-signed-' + language] = 'PASS';

    // 1. a card made while the page could not hear it: back in front → it is there
    const a1 = id32();
    fake.askSilently({id: a1, tool: 'Bash', summary: 'rm -rf build/', ttl: 120});
    await sleep(1600); await front(p);
    await waitFor(p, `document.body.dataset.sheet === '1' && document.getElementById('tool').textContent === 'Bash'`, 8000);
    assert.equal(fake.st.cardGets.at(-1).have.length, 0);
    await shot(p, `missed-card-${language}`);
    results['missed-card-appears-' + language] = 'PASS';

    // the next snapshot lists it as shown: not sent again
    const before = fake.log.length;
    await sleep(1600); await front(p);
    for (let i = 0; i < 50 && fake.st.cardGets.at(-1).have?.[0] !== a1; i++) await sleep(100);
    assert.deepEqual(fake.st.cardGets.at(-1).have, [a1]);
    assert.ok(fake.log.length > before);

    // 2. answered on another remote while this page missed the ask_done → closes quietly
    fake.forget(a1);
    await sleep(1600); await front(p);
    await waitFor(p, `document.body.dataset.sheet === '0' || !document.getElementById('askState').textContent.includes(${JSON.stringify(L.expired)})`, 8000);
    await waitFor(p, `document.body.dataset.sheet === '0'`, 9000);
    results['answered-elsewhere-closes-' + language] = 'PASS';

    // 2b. a card whose time ran out while the page was away → 「已过期」
    const a2 = id32();
    await fake.ask({id: a2, tool: 'Bash', summary: 'git push', ttl: 1});
    await waitFor(p, `document.body.dataset.sheet === '1'`, 5000);
    await sleep(1700); fake.forget(a2); await front(p);
    await waitFor(p, `document.getElementById('askState').textContent.includes(${JSON.stringify(L.expired)}) || document.getElementById('toast').textContent.includes(${JSON.stringify(L.expired)})`, 8000);
    // the finished card shows no 批准 / 拒绝 (brand/base.css's phone-width !important used to override [hidden])
    assert.equal(await evaluate(p, `getComputedStyle(document.getElementById('apprBtns')).display`), 'none');
    await shot(p, `expired-${language}`);
    results['expired-card-says-so-' + language] = 'PASS';
    await waitFor(p, `document.body.dataset.sheet === '0'`, 9000);

    // 4. Settings → 升级电脑上的 Agent J with something in the way: the card lists it, 「现在升级」 sends the signed /update
    fake.st.upgItems = [{k: 'turn', n: 1}, {k: 'ask', n: 2}];
    await click(p, 'setBtn'); await waitFor(p, `!document.getElementById('settings').hidden`);
    // P218: the merged 升级方式 is P207's two-way switch — left (aria-checked false) = 自动 = install when idle (P203's on)
    assert.equal(await evaluate(p, `document.getElementById('set-updates').getAttribute('aria-checked')`), 'false');
    await evaluate(p, `document.getElementById('set-hostupg').scrollIntoView({block:'center'})`);
    await click(p, 'set-hostupg');
    await waitFor(p, `!document.getElementById('confirm').hidden && document.getElementById('confirm-title').textContent.includes(${JSON.stringify(L.upgTitle)})`, 8000);
    const body = await evaluate(p, `document.getElementById('confirm-text').textContent`);
    assert.ok(body.includes(L.ask) && body.includes(L.turn), body);
    assert.equal(await evaluate(p, `getComputedStyle(document.getElementById('confirm-text')).whiteSpace`), 'pre-line');
    await shot(p, `upgrade-card-${language}`);
    const n0 = fake.st.slashes.length;
    await click(p, 'confirm-yes');
    for (let i = 0; i < 50 && fake.st.slashes.length === n0; i++) await sleep(100);
    const up = fake.st.slashes.at(-1);
    assert.equal(up.cmd, 'update'); assert.match(up.sig, /^[A-Za-z0-9_-]{80,}$/); assert.match(up.n, /^[0-9a-f]{32}$/);
    results['upgrade-card-then-signed-update-' + language] = 'PASS';

    // nothing in the way → one tap, no card
    fake.st.upgItems = [];
    await click(p, 'setBtn'); await waitFor(p, `!document.getElementById('settings').hidden`);
    await evaluate(p, `document.getElementById('set-hostupg').scrollIntoView({block:'center'})`);
    const n1 = fake.st.slashes.length;
    await click(p, 'set-hostupg');
    for (let i = 0; i < 50 && fake.st.slashes.length === n1; i++) await sleep(100);
    assert.equal(fake.st.slashes.at(-1).cmd, 'update');
    assert.equal(await evaluate(p, `document.getElementById('confirm').hidden`), true, 'no card when nothing is in the way');
    results['upgrade-nothing-in-the-way-one-tap-' + language] = 'PASS';

    // the computer's own card (the Agent ran `agentj update apply`): 「先不升」 closes it on the computer
    const u1 = id32();
    await fake.upgCard({id: u1, items: [{k: 'shared_turn', n: 1}, {k: 'task_soon', n: 1}], from: '0.20.1', to: '0.20.2', ttl: 600});
    await waitFor(p, `!document.getElementById('confirm').hidden && document.getElementById('confirm-title').textContent.includes('0.20.2')`, 8000);
    await shot(p, `host-upgrade-card-${language}`);
    await click(p, 'confirm-no');
    for (let i = 0; i < 50 && !fake.st.upgOffs.includes(u1); i++) await sleep(100);
    assert.deepEqual(fake.st.upgOffs, [u1]);
    // a newer card answered on another remote → it closes here by itself
    const u2 = id32();
    await fake.upgCard({id: u2, items: [{k: 'bg', n: 3}], from: '0.20.1', to: '0.20.2', ttl: 600});
    await waitFor(p, `!document.getElementById('confirm').hidden`, 8000);
    await fake.upgDone(u2, 'started');
    await waitFor(p, `document.getElementById('confirm').hidden`, 5000);
    results['host-upgrade-card-cancel-and-closed-elsewhere-' + language] = 'PASS';

    // 5. one switch, both keys (P218: P207's two-way 升级方式 — left 自动 = false, right 先问我 = true)
    await click(p, 'setBtn'); await waitFor(p, `!document.getElementById('settings').hidden`);
    assert.ok((await evaluate(p, `document.getElementById('set-updates').closest('.setrow').textContent`)).includes(L.auto));
    await evaluate(p, `document.getElementById('set-updates').scrollIntoView({block:'center'})`);
    await shot(p, `settings-auto-update-${language}`);
    const k0 = fake.st.prefSets.length;
    await click(p, 'set-updates');
    for (let i = 0; i < 50 && fake.st.prefSets.length < k0 + 2; i++) await sleep(100);
    assert.deepEqual(fake.st.prefSets.slice(k0).map((m) => [m.key, m.value]), [['updates.auto_install', false], ['updates.mode', 'ask']]);
    await waitFor(p, `document.getElementById('set-updates').getAttribute('aria-checked') === 'true'`, 5000);
    await click(p, 'set-updates');
    for (let i = 0; i < 50 && fake.st.prefSets.length < k0 + 4; i++) await sleep(100);
    assert.deepEqual(fake.st.prefSets.slice(k0 + 2).map((m) => [m.key, m.value]), [['updates.auto_install', true], ['updates.mode', 'auto']]);
    results['auto-update-one-switch-' + language] = 'PASS';
    await p.dispose();
  }

  // an older computer (no cards1 / upg1): no snapshot request, /update is the old one step
  fake.reset();
  Object.assign(fake.st, {cards: true});
  const p = await newPage(B, 390, 844, 'light', {touch: true, allow: [web.url, fake.relay + '/']});
  await navigate(p, fake.newPairing(web.url)); await waitState(p, 'awaiting-approval'); await fake.approve(); await waitState(p, 'ready');
  await sleep(1800); await front(p); await sleep(500);
  assert.equal(fake.st.cardGets.length, 0, 'no cards_get to a computer without cards1');
  await click(p, 'setBtn'); await waitFor(p, `!document.getElementById('settings').hidden`);
  await evaluate(p, `document.getElementById('set-hostupg').scrollIntoView({block:'center'})`);
  await click(p, 'set-hostupg');
  for (let i = 0; i < 50 && !fake.st.slashes.length; i++) await sleep(100);
  assert.equal(fake.st.slashes.at(-1).cmd, 'update');
  assert.ok(!fake.log.some((m) => m.t === 'upg_check'), 'no upg_check to an older computer');
  results['older-computer-unchanged'] = 'PASS';
  await p.dispose();
} catch (e) {
  console.error(e); results.error = String(e && e.stack || e).slice(0, 2000);
} finally {
  writeFileSync(new URL('results.json', out), JSON.stringify(results, null, 1));
  console.log(JSON.stringify(results, null, 1));
  await B.close?.(); await fake.stop(); await web.stop();
}
// parity cases (features.json p203-*): language-independent names pass when both languages passed
const rec = parityRecorder('web/test/p203.mjs');
const CASES = ['snapshot-signed', 'missed-card-appears', 'answered-elsewhere-closes', 'expired-card-says-so', 'upgrade-card-then-signed-update',
  'upgrade-nothing-in-the-way-one-tap', 'host-upgrade-card-cancel-and-closed-elsewhere', 'auto-update-one-switch'];
for (const c of CASES) rec.record('p203-' + c, results[c + '-zh'] === 'PASS' && results[c + '-en'] === 'PASS');
rec.record('p203-older-computer', results['older-computer-unchanged'] === 'PASS');
console.log('parity results → ' + rec.write());
const failed = Object.values(rec.results).filter((v) => v !== 'pass').length;
console.log(failed ? `p203: ${failed} failed` : 'p203: all passed');
process.exit(failed ? 1 : 0);
