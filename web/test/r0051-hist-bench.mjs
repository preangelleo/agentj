// R-0051 W11: what one streamed reply chunk costs with N turns already loaded on the page (real page, fake host, Chromium).
// Usage: node r0051-hist-bench.mjs [out.json]  → per-N median / p95 ms of upsertTurn for a chunk on the newest turn.
import {writeFileSync} from 'node:fs';
import {startWebServer} from './serve.mjs';
import {startFakeHost} from './fakehost.mjs';
import {launch, newPage, navigate, waitState, waitFor, evaluate} from './browser.mjs';

const SIZES = (process.env.R0051_SIZES || '0,1000,5000,20000').split(',').map(Number);
const CHUNKS = Number(process.env.R0051_CHUNKS || 120);
const web = await startWebServer({port: 0, relayCsp: 'ws://127.0.0.1:*'});
const fake = await startFakeHost();
const B = await launch();
const rows = [];
try {
  for (const n of SIZES) {
    const p = await newPage(B, 390, 844, 'light', {touch: true, allow: [web.url, fake.relay + '/']});
    await navigate(p, fake.newPairing(web.url));
    await waitState(p, 'awaiting-approval'); await fake.approve(); await waitState(p, 'ready');
    await waitFor(p, `import('./js/relay.js').then(R => R.debug().sync === 'ready')`, 15000);
    const r = await evaluate(p, `(async () => {
      const R = await import('./js/relay.js');
      const T = R.loadedTurns(), n = ${n}, now = Math.floor(Date.now() / 1000) - n * 60;
      for (let i = 1; i <= n; i++) T.push({id: i, end: 'done', ts: now + i * 60, source: {k: 'phone', text: 'q' + i}, reply: 'answer ' + i});
      const id = n + 1, base = {id, end: 'open', ts: now + id * 60, source: {k: 'phone', text: 'q' + id}};
      R.upsertTurn({...base, reply: ''});
      const ms = [];
      let reply = '';
      for (let c = 0; c < ${CHUNKS}; c++) {
        reply += 'chunk ' + c + ' ';
        const t0 = performance.now();
        R.upsertTurn({...base, reply});
        ms.push(performance.now() - t0);
      }
      R.upsertTurn({...base, reply, end: 'done'});
      ms.sort((a, b) => a - b);
      const d = R.debug();
      return {loaded: d.hist.length, pages: d.pages, total: d.total, median: ms[ms.length >> 1], p95: ms[Math.floor(ms.length * 0.95)], sum: ms.reduce((a, b) => a + b, 0),
              shown: document.getElementById('rm')?.innerText?.slice(0, 40) || ''};
    })()`);
    rows.push({n, ...r});
    console.log(`N=${n} loaded=${r.loaded} pages=${r.pages} total=${r.total} median=${r.median.toFixed(2)}ms p95=${r.p95.toFixed(2)}ms sum(${CHUNKS})=${r.sum.toFixed(0)}ms`);
    await p.dispose();
  }
  if (process.argv[2]) writeFileSync(process.argv[2], JSON.stringify(rows, null, 2) + '\n');
} finally { await B.close(); await fake.stop(); await web.stop(); }
process.exit(0);
