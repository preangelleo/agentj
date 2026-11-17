// R-0051 W11b phone test: a page that holds more than 2 500 turns lets the oldest go while the owner is on the newest page,
// and nothing he can see changes — the page counter stays, 双击「第一条」 fetches the old turns again from the host's paging and
// lands on 1 / total. Real page + fake host (encrypted transport), Chromium; 390 + 1440, light + dark, zh + en shots.
// R0051_EXPECT_TRIM=0 runs the same steps on a tree without the cap (the "before" shots).
import assert from 'node:assert/strict';
import {mkdirSync, writeFileSync} from 'node:fs';
import {startWebServer} from './serve.mjs';
import {startFakeHost} from './fakehost.mjs';
import {launch, newPage, navigate, waitState, waitFor, evaluate, shoot, sleep} from './browser.mjs';

const N = 2600, EXPECT = process.env.R0051_EXPECT_TRIM !== '0';
const out = new URL(process.env.R0051_OUT ? 'file://' + process.env.R0051_OUT.replace(/\/?$/, '/') : '../../../reports/qa/r0051/', import.meta.url);
mkdirSync(out, {recursive: true});
const web = await startWebServer({port: 0, relayCsp: 'ws://127.0.0.1:*'});
const fake = await startFakeHost();
const B = await launch();
const log = [];
const ok = (c, name) => { assert.ok(c, name); log.push(name); console.log('PASS ' + name); };
const R = `import('./js/relay.js')`;
const state = (p) => evaluate(p, `${R}.then(R => { const d = R.debug(); return {loaded: d.hist.length, first: d.hist[0], last: d.hist.at(-1), total: d.total, pg: document.getElementById('pg').textContent, sync: d.sync}; })`);
const dbl = (p, id) => evaluate(p, `document.getElementById(${JSON.stringify(id)}).dispatchEvent(new MouseEvent('dblclick', {bubbles: true}))`);

function seed() {
  const now = Date.now() - N * 60000;
  fake.st.turns = Array.from({length: N}, (_, k) => {
    const i = k + 1;   // every 7th turn is a silent one: it is a turn, not a page — the counter must still not move
    return {id: i, ts: now + i * 60000, src: {k: 'phone', text: 'Q' + i}, reply: {text: i % 7 ? 'Reply ' + i : '〔静默消息〕 note ' + i}, end: 'done'};
  });
  fake.st.nextId = N + 1;
}
try {
  for (const [lang, w, h, scheme] of [['zh', 390, 844, 'light'], ['zh', 390, 844, 'dark'], ['en', 390, 844, 'light'], ['en', 390, 844, 'dark'],
                                       ['zh', 1440, 900, 'light'], ['zh', 1440, 900, 'dark'], ['en', 1440, 900, 'light'], ['en', 1440, 900, 'dark']]
                                       .slice(0, Number(process.env.R0051_COMBOS || 8))) {
    const tag = `${lang}-${w}-${scheme}`;
    seed();
    const p = await newPage(B, w, h, scheme, {touch: w < 600, allow: [web.url, fake.relay + '/']});
    await navigate(p, fake.newPairing(web.url + (lang === 'en' ? '?lang=en' : '')));
    await waitState(p, 'awaiting-approval'); await fake.approve(); await waitState(p, 'ready');
    await waitFor(p, `${R}.then(R => R.debug().sync === 'ready' && R.debug().hist.length >= 50)`, 15000);
    await dbl(p, 'omFirst');                                         // the owner pages all the way back once …
    await waitFor(p, `${R}.then(R => R.debug().hist[0] === 1)`, 60000);
    await dbl(p, 'pgLatest');                                        // … and returns to the newest page
    await waitFor(p, `${R}.then(R => R.debug().follow)`, 10000);
    const a = await state(p);
    ok(a.loaded === N, `${tag}: all ${N} turns loaded before the next turn`);
    const total = Number(a.pg.split('/')[1]);
    ok(a.pg.trim() === `${total} / ${total}` && total < N, `${tag}: newest page counter ${a.pg} (silent turns are not pages)`);
    await fake.addTurn({k: 'phone', text: 'Q' + (N + 1)}, 'Reply ' + (N + 1), 'done');
    await waitFor(p, `${R}.then(R => R.debug().hist.at(-1) === ${N + 1})`, 10000);
    await sleep(200);
    const b = await state(p);
    if (EXPECT) ok(b.loaded <= 2001 && b.first > 1, `${tag}: the oldest turns were let go (${b.loaded} loaded, first id ${b.first})`);
    else console.log(`INFO ${tag}: no cap on this tree (${b.loaded} loaded)`);
    ok(b.pg.trim() === `${total + 1} / ${total + 1}`, `${tag}: the counter moved by the one new page only (${a.pg} → ${b.pg})`);
    writeFileSync(new URL(`${tag}-newest.png`, out), await shoot(p, ''));
    await dbl(p, 'omFirst');                                         // 第一条 again: the host's paging brings them back
    await waitFor(p, `${R}.then(R => R.debug().hist[0] === 1 && R.debug().pageAt === 0)`, 60000);
    await sleep(200);
    const c = await state(p);
    ok(c.pg.trim() === `1 / ${total + 1}` && c.loaded === N + 1, `${tag}: the first message is reachable again: ${c.pg}, ${c.loaded} loaded`);
    writeFileSync(new URL(`${tag}-earliest.png`, out), await shoot(p, ''));
    await p.dispose();
  }
  writeFileSync(new URL('result.json', out), JSON.stringify({ok: true, expectTrim: EXPECT, checks: log}, null, 2) + '\n');
  console.log(`r0051-hist: ${log.length} checks PASS`);
} finally { await B.close(); await fake.stop(); await web.stop(); }
process.exit(0);
