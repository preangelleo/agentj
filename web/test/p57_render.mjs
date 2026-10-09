#!/usr/bin/env node
// P57 (render lane) browser check: the real client (served with the Worker's headers, CSP included) in an independent
// headless Chromium, against the fake relay/host. One reply holds a python block, a display + inline formula, a price
// line, a mermaid flowchart and hostile inputs (```html with <script>/onerror, \href{javascript:…}, a mermaid label with
// <img onerror> and a click callback). Asserts: nothing from vendor/ on first paint; nothing upgraded while the page is
// still open (streaming); afterwards highlighted spans, KaTeX, the diagram as an <img> of a blob: SVG; NO script ran
// (window flag unchanged); zero console errors / CSP violations; no request outside the page's origin (+ the local
// relay); re-rendering does not double-process. Screenshots (light + dark) → /var/tmp/p57-logs/render-shots/ (AJ_SHOTS).
// Run: flock /tmp/p57-heavy.lock node web/test/p57_render.mjs
import { mkdirSync, writeFileSync, statSync } from 'node:fs';
import { join } from 'node:path';
import { fileURLToPath } from 'node:url';
import { startWebServer, PUBLIC_DIR } from './serve.mjs';
import { startFakeHost } from './fakehost.mjs';
import { launch, newPage, evaluate, navigate, waitFor, waitState, shoot, sleep } from './browser.mjs';
import { parityRecorder } from '../../parity/lib.mjs';

const SHOTS = process.env.AJ_SHOTS || '/var/tmp/p57-logs/render-shots';
mkdirSync(SHOTS, { recursive: true });
const failures = [];
const check = (ok, msg) => { if (!ok) failures.push(msg); console.log(`${ok ? 'PASS' : 'FAIL'}  ${msg}`); return ok; };
const rec = parityRecorder('web/test/p57_render.mjs');
let secFails = 0;

const REPLY = [
  '## 代码、公式和图',
  '',
  '```python',
  'def fib(n: int) -> int:',
  '    """Return the n-th Fibonacci number."""',
  '    a, b = 0, 1',
  '    for _ in range(n):',
  '        a, b = b, a + b  # step',
  '    return a',
  '```',
  '',
  '欧拉公式 $e^{i\\pi} + 1 = 0$，二次方程的根：',
  '',
  '$$',
  'x = \\frac{-b \\pm \\sqrt{b^2 - 4ac}}{2a}',
  '$$',
  '',
  '价格行：这件 $5，那件 $10，合计 $15。',
  '',
  '```mermaid',
  'flowchart LR',
  '  A[手机] -->|加密| B(转发服务器)',
  '  B --> C{电脑}',
  '  C -->|回复| A',
  '```',
  '',
  '恶意输入（都必须只是文字）：',
  '',
  '```html',
  '<script>window.__pwned = 1</script><img src=x onerror="window.__pwned = 2">',
  '```',
  '',
  '链接公式 $\\href{javascript:window.__pwned=3}{点我}$ 和 \\(\\url{javascript:window.__pwned=4}\\)',
  '',
  '```mermaid',
  'flowchart TD',
  '  X["<img src=x onerror=window.__pwned=5>"] --> Y[ok]',
  '  click X call pwn()',
  '  click Y href "javascript:window.__pwned=6"',
  '```',
  '',
  '```mermaid',
  'this is not a diagram at all',
  '```',
].join('\n');

const web = await startWebServer({ port: 0, relayCsp: 'ws://127.0.0.1:*' });
const fake = await startFakeHost();
const ALLOW = [web.url, fake.relay + '/'];
const B = await launch();
const sizes = {};
try {
  for (const scheme of ['light', 'dark']) {
    fake.reset();
    const p = await newPage(B, 390, 844, scheme, { allow: ALLOW });
    const urls = [];
    let bytes = 0;
    p.on((m) => {
      if (m.method === 'Network.requestWillBeSent') urls.push(m.params.request.url);
      if (m.method === 'Network.loadingFinished') bytes += m.params.encodedDataLength || 0;
    });
    await p.send('Page.addScriptToEvaluateOnNewDocument', { source: 'window.__pwned = 0; window.pwn = () => { window.__pwned = 9; };' });
    await navigate(p, fake.newPairing(web.url));
    await waitState(p, 'awaiting-approval');
    await fake.approve();
    await waitState(p, 'ready');
    await sleep(500);
    const firstPaint = urls.slice();
    sizes[scheme] = { firstPaintRequests: firstPaint.length, firstPaintBytes: bytes };
    check(!firstPaint.some((u) => /\/vendor\/(hljs|katex|mermaid)\//.test(u)), `${scheme}: first paint loads no vendor file (${firstPaint.length} requests)`);
    check(firstPaint.some((u) => u.endsWith('/js/render.js')), `${scheme}: render.js itself is part of the page`);

    // ---- streaming: an open page is not upgraded
    const turn = await fake.addTurn({ k: 'host', text: '写点代码和公式' }, REPLY.slice(0, 400), 'open');
    await waitFor(p, `document.querySelector('#words pre[data-lang="python"]') !== null`);
    await fake.updateTurn(turn.id, { reply: { text: REPLY.slice(0, 900) }, end: 'open' });
    await sleep(1500);
    check(await evaluate(p, `document.querySelectorAll('#words [data-rx]').length === 0 && !document.querySelector('#words .katex, #words .hljs')`),
      `${scheme}: nothing is upgraded while the page is still open`);
    check(!urls.some((u) => /\/vendor\/(hljs|katex|mermaid)\//.test(u)), `${scheme}: no library loaded while streaming`);

    // ---- the page is done: everything in view upgrades
    await fake.updateTurn(turn.id, { reply: { text: REPLY }, end: 'done' });
    await waitFor(p, `document.querySelector('#words pre[data-lang="python"] code.hljs .hljs-keyword') !== null`, 10000);
    check(true, `${scheme}: python block highlighted (hljs spans)`);
    const hl = await evaluate(p, `(() => { const c = document.querySelector('#words pre[data-lang="python"] code');
      const bad = [...c.querySelectorAll('*')].filter((e) => e.tagName !== 'SPAN' || [...e.attributes].some((a) => a.name !== 'class'));
      return { spans: c.querySelectorAll('span').length, bad: bad.length, text: c.textContent }; })()`);
    check(hl.spans > 5 && hl.bad === 0 && hl.text.startsWith('def fib(n: int) -> int:'), `${scheme}: only class-only spans, text intact (${hl.spans} spans)`);
    await waitFor(p, `document.querySelectorAll('#words .math[data-rx="ok"] .katex').length >= 2`, 10000);
    const mx = await evaluate(p, `(() => { const ms = [...document.querySelectorAll('#words .math')];
      return { n: ms.length, ok: ms.filter((m) => m.dataset.rx === 'ok').length, x: ms.filter((m) => m.dataset.rx === 'x').map((m) => m.dataset.tex),
        display: !!document.querySelector('#words div.math.display .katex-display, #words div.math.display .katex'),
        fontOk: document.fonts ? [...document.fonts].some((f) => /KaTeX/.test(f.family)) : true }; })()`);
    check(mx.ok >= 2 && mx.display, `${scheme}: KaTeX rendered (${mx.ok}/${mx.n} formulas so far; display block typeset; kept as source: ${JSON.stringify(mx.x)})`);
    // no page style leaks into KaTeX's boxes (class names like .mclose are shared with other components)
    const leak = await evaluate(p, `[...document.querySelectorAll('#words .katex *')].filter((e) => { const cs = getComputedStyle(e);
      return cs.backgroundColor !== 'rgba(0, 0, 0, 0)' || cs.borderLeftStyle !== 'none' || cs.borderRightStyle !== 'none' || cs.position === 'fixed'; }).map((e) => e.getAttribute('class'))`);
    check(leak.length === 0, `${scheme}: no page style inside KaTeX output${leak.length ? ' — ' + leak.slice(0, 5).join(', ') : ''}`);
    const price = await evaluate(p, `[...document.querySelectorAll('#words p')].find((e) => e.textContent.startsWith('价格行')).outerText`);
    check(price === '价格行：这件 $5，那件 $10，合计 $15。' && await evaluate(p, `![...document.querySelectorAll('#words p')].find((e) => e.textContent.startsWith('价格行')).querySelector('.math')`),
      `${scheme}: the price line stays plain text`);
    // the diagrams sit below the fold: scroll them in (IntersectionObserver)
    await evaluate(p, `document.querySelectorAll('#words .mermaid-src')[0].scrollIntoView({ block: 'center' })`);
    await waitFor(p, `(() => { const i = document.querySelector('#words .mermaid-src.drawn .diagram img'); return i && i.complete && i.naturalWidth > 0; })()`, 20000);
    const dg = await evaluate(p, `(() => { const i = document.querySelector('#words .mermaid-src .diagram img'); const col = document.getElementById('words').clientWidth;
      return { src: i.src.slice(0, 5), w: i.getBoundingClientRect().width, col, svgInDom: document.querySelectorAll('#words .codeblock svg').length,
        pre: getComputedStyle(i.closest('.codeblock').querySelector('pre')).display }; })()`);
    check(dg.src === 'blob:' && dg.svgInDom === 0 && dg.w <= dg.col + 1 && dg.pre === 'none', `${scheme}: mermaid became an <img> of a blob: SVG, fits the column (${Math.round(dg.w)} ≤ ${dg.col}), no inline <svg>`);
    await shoot(p, '').then((b) => writeFileSync(join(SHOTS, `${scheme}-diagram.png`), b));
    await evaluate(p, `document.querySelectorAll('#words .mermaid-src')[1].scrollIntoView({ block: 'center' })`);
    await evaluate(p, `document.querySelectorAll('#words .mermaid-src')[2].scrollIntoView({ block: 'center' })`);
    await waitFor(p, `[...document.querySelectorAll('#words .mermaid-src')].every((w) => w.dataset.rx === 'ok' || w.dataset.rx === 'x')`, 20000);
    const bad = await evaluate(p, `(() => { const ws = [...document.querySelectorAll('#words .mermaid-src')];
      return ws.map((w) => ({ rx: w.dataset.rx, note: (w.querySelector('.rxnote') || {}).textContent || '', pre: getComputedStyle(w.querySelector('pre')).display })); })()`);
    check(bad[2].rx === 'x' && /图没画出来|Couldn't draw/.test(bad[2].note) && bad[2].pre !== 'none', `${scheme}: a broken diagram falls back to its code + 「${bad[2].note}」`);
    check(bad[1].rx === 'ok' || bad[1].rx === 'x', `${scheme}: the hostile diagram was handled (${bad[1].rx})`);
    // tap the diagrams and the formulas: nothing may run
    await evaluate(p, `for (const d of document.querySelectorAll('#words .diagram')) d.click(); for (const k of document.querySelectorAll('#words .katex')) k.click(); true`);
    await sleep(300);
    check(await evaluate(p, `document.querySelector('#words .diagram').classList.contains('full')`), `${scheme}: a tap shows the diagram full size`);
    await evaluate(p, `for (const d of document.querySelectorAll('#words .diagram.full')) d.click(); true`);
    await shoot(p, '').then((b) => writeFileSync(join(SHOTS, `${scheme}-hostile.png`), b));
    const sec = await evaluate(p, `(() => { const w = document.getElementById('words');
      return { pwned: window.__pwned, links: [...w.querySelectorAll('a[href]')].map((a) => a.getAttribute('href')).filter((h) => !/^https?:/.test(h)),
        imgs: [...w.querySelectorAll('img')].map((i) => i.getAttribute('src')).filter((s) => !/^blob:/.test(s)),
        scripts: w.querySelectorAll('script, iframe, object, embed, foreignObject').length,
        on: [...w.querySelectorAll('*')].filter((e) => [...e.attributes].some((a) => /^on/i.test(a.name))).length,
        html: [...w.querySelectorAll('pre[data-lang="html"] code')].map((c) => c.textContent).join('') }; })()`);
    // every formula on the page has been handled by now (the hostile ones sit above the hostile diagram)
    check(await evaluate(p, `[...document.querySelectorAll('#words .math')].every((m) => m.dataset.rx === 'ok' || m.dataset.rx === 'x')`), `${scheme}: every formula handled`);
    if (!check(sec.pwned === 0, `${scheme}: no injected script ran (window.__pwned = ${sec.pwned})`)) secFails++;
    if (!check(sec.links.length === 0 && sec.imgs.length === 0 && sec.scripts === 0 && sec.on === 0,
      `${scheme}: no javascript: link, no non-blob <img>, no script/iframe/foreignObject, no on* attribute in the reply (${JSON.stringify(sec)})`)) secFails++;
    check(sec.html.includes('<script>window.__pwned = 1</script>'), `${scheme}: the hostile HTML is shown as text`);

    // ---- idempotent: upgrading the same words again changes nothing; a re-render reuses the cache
    const count = `JSON.stringify([document.querySelectorAll('#words .hljs span').length, document.querySelectorAll('#words .katex').length,
      document.querySelectorAll('#words .diagram').length, document.querySelectorAll('#words .rxnote').length])`;
    const before = await evaluate(p, count);
    await evaluate(p, `import('./js/render.js').then((m) => { m.upgrade(document.getElementById('words')); return true; })`);
    await sleep(1200);
    check(await evaluate(p, count) === before, `${scheme}: a second upgrade pass does not double-process (${before})`);
    const vendorReqs = () => urls.filter((u) => /\/vendor\/(hljs|katex|mermaid)\//.test(u));
    const nBefore = vendorReqs().length;
    await fake.addTurn({ k: 'host', text: '再来一遍' }, REPLY + '\n\n（补一句）', 'done');          // a new page, same blocks
    await waitFor(p, `document.getElementById('words').textContent.includes('补一句') || !document.getElementById('newReply').hidden`);
    await evaluate(p, `if (!document.getElementById('newReply').hidden) document.getElementById('newReply').click()`);
    await waitFor(p, `document.getElementById('words').textContent.includes('补一句') && document.querySelector('#words pre[data-lang="python"] code.hljs')`, 10000);
    check(vendorReqs().length === nBefore, `${scheme}: a re-render loads nothing again`);
    // ---- what loaded, all from our own origin
    const vend = [...new Set(vendorReqs().map((u) => new URL(u).pathname))].sort();
    console.log(`      lazily loaded: ${vend.join(', ')}`);
    check(vend.includes('/vendor/hljs/highlight.min.js') && vend.includes('/vendor/katex/katex.min.js') && vend.includes('/vendor/mermaid/mermaid.min.js'), `${scheme}: the three libraries came from /vendor/`);
    check(!vend.some((u) => /\.(woff|ttf)$/.test(u)), `${scheme}: only woff2 KaTeX fonts requested`);
    // screenshots of the code + math at the top
    await evaluate(p, `document.getElementById('words').querySelector('h2').scrollIntoView({ block: 'start' })`);
    await sleep(300);
    writeFileSync(join(SHOTS, `${scheme}-code-math.png`), await shoot(p, ''));
    check(p.problems.length === 0, `${scheme}: zero console errors / CSP violations${p.problems.length ? ' — ' + p.problems.slice(0, 5).join(' | ') : ''}`);
    check(p.offsite.length === 0, `${scheme}: no request leaves the page's origin${p.offsite.length ? ' — ' + p.offsite.join(' | ') : ''}`);
    await p.dispose();
  }
} finally {
  await B.close(); await web.stop(); await fake.stop();
}
const sz = (r) => statSync(join(PUBLIC_DIR, r)).size;
const lazy = { hljs: sz('vendor/hljs/highlight.min.js'), katex: sz('vendor/katex/katex.min.js') + sz('vendor/katex/katex.min.css'), mermaid: sz('vendor/mermaid/mermaid.min.js') };
console.log(`first paint: ${JSON.stringify(sizes)}; render.js ${sz('js/render.js')} B; lazy chunks (bytes, uncompressed): ${JSON.stringify(lazy)}`);
console.log(`shots: ${SHOTS}`);
rec.record('code, math and diagrams render lazily (light + dark)', failures.length === 0);
rec.record('hostile code / TeX / mermaid stay inert', secFails === 0 && failures.length === 0);
console.log(`parity: ${rec.write()}`);
if (failures.length) { console.log(`\n${failures.length} FAILED:\n  ` + failures.join('\n  ')); process.exit(1); }
console.log('\nP57 render: all checks passed');
