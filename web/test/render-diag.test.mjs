// Render-health counters (js/render-diag.js): closed enums / buckets / small counts, one sample per event, hourly cap,
// and no way for page text, a URL, a file name or the raw UA to reach the sample. Stub DOM only; nothing touches HOME.
import test from 'node:test';
import assert from 'node:assert/strict';
import {readFileSync} from 'node:fs';
const R = await import('../public/js/render-diag.js');
const src = (f) => readFileSync(new URL('../public/js/' + f, import.meta.url), 'utf8');
const SECRET_UA = 'Mozilla/5.0 (Linux; Android 15; PKX110 Build/SECRETBUILD) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/142.0.7444.171 Mobile Safari/537.36';
const KEYS = ['reason', 'agent', 'alive', 'hidden', 'hiddenMax', 'gl', 'glLost', 'glRestored', 'mem', 'cache', 'cards', 'nodes'];

function world({ua = SECRET_UA, hidden = false, gl = 'ok', memory = 8, pg = '497 / 497', nodes = 1234} = {}) {
  const saved = {document: globalThis.document, navigator: Object.getOwnPropertyDescriptor(globalThis, 'navigator'), ric: globalThis.requestIdleCallback};
  const listeners = {}, canvases = [];
  const doc = {hidden, listeners,
    addEventListener(type, fn) { (listeners[type] ||= []).push(fn); }, removeEventListener(type, fn) { listeners[type] = (listeners[type] || []).filter((f) => f !== fn); },
    getElementById: (id) => id === 'pg' ? {textContent: pg} : {get textContent() { throw new Error('content read: #' + id); }},
    getElementsByTagName: () => ({length: nodes}),
    createElement(tag) {
      assert.equal(tag, 'canvas');
      if (gl === 'throws') throw new Error('no canvas');
      const c = {on: {}, lost: false, addEventListener(type, fn) { c.on[type] = fn; },
        getContext(kind, opts) {
          c.asked = (c.asked || []).concat(kind); c.opts = opts;
          if (gl === 'getContext-throws') throw new Error('blocked');
          return gl === 'none' ? null : {isContextLost: () => c.lost};
        }};
      canvases.push(c); return c;
    }};
  globalThis.document = doc;
  Object.defineProperty(globalThis, 'navigator', {configurable: true, value: {userAgent: ua, maxTouchPoints: 5, deviceMemory: memory}});
  delete globalThis.requestIdleCallback;
  let clock = 1_800_000_000_000;
  return {doc, canvases, now: () => clock, tick: (ms) => { clock += ms; },
    show(on) { doc.hidden = !on; for (const f of listeners.visibilitychange || []) f(); },
    done() { R.reset(); globalThis.document = saved.document; if (saved.navigator) Object.defineProperty(globalThis, 'navigator', saved.navigator); else delete globalThis.navigator; if (saved.ric) globalThis.requestIdleCallback = saved.ric; }};
}
function closed(s) {
  assert.deepEqual(Object.keys(s).sort(), [...KEYS].sort());
  assert.ok(R.REASONS.includes(s.reason)); assert.ok(R.AGENTS.includes(s.agent)); assert.ok(R.ALIVE.includes(s.alive));
  assert.ok(R.HIDDEN_MAX.includes(s.hiddenMax)); assert.ok(R.GL.includes(s.gl)); assert.ok(R.CACHE.includes(s.cache));
  assert.ok(R.CARDS.includes(s.cards)); assert.ok(R.NODES.includes(s.nodes));
  for (const k of ['hidden', 'glLost', 'glRestored']) assert.ok(Number.isInteger(s[k]) && s[k] >= 0 && s[k] <= 9999, k);
  assert.ok(s.mem === null || R.MEMORY.includes(s.mem));
  for (const v of Object.values(s)) assert.ok(v === null || typeof v === 'number' || (typeof v === 'string' && /^[a-z0-9_-]{1,16}$/.test(v)), String(v));
}

test('bucket: edges, last name, and refusal of anything that is not a finite non-negative number', () => {
  const e = [10, 100], n = ['a', 'b', 'c'];
  assert.deepEqual([0, 9.9, 10, 99, 100, 1e12].map((v) => R.bucket(v, e, n)), ['a', 'a', 'b', 'b', 'c', 'c']);
  for (const v of [-1, NaN, Infinity, '5', null, undefined, {}, true]) assert.equal(R.bucket(v, e, n), null);
});

test('agentClass: a coarse family, never the string', () => {
  const cases = {
    'android-chrome': SECRET_UA,
    'android-webview': 'Mozilla/5.0 (Linux; Android 14; Pixel 8 Build/X; wv) AppleWebKit/537.36 (KHTML, like Gecko) Version/4.0 Chrome/130.0.0.0 Mobile Safari/537.36',
    'android-other': 'Mozilla/5.0 (Linux; U; Android 15; zh-cn; PKX110 Build/X) AppleWebKit/537.36 (KHTML, like Gecko) Version/4.0 Chrome/115.0.5790.168 Mobile Safari/537.36 HeyTapBrowser/45.11.3.1',
    'android-firefox': 'Mozilla/5.0 (Android 15; Mobile; rv:132.0) Gecko/132.0 Firefox/132.0',
    'ios-safari': 'Mozilla/5.0 (iPhone; CPU iPhone OS 18_0 like Mac OS X) AppleWebKit/605.1.15 (KHTML, like Gecko) Version/18.0 Mobile/15E148 Safari/604.1',
    'ios-other': 'Mozilla/5.0 (iPhone; CPU iPhone OS 18_0 like Mac OS X) AppleWebKit/605.1.15 (KHTML, like Gecko) CriOS/130.0 Mobile/15E148 Safari/604.1',
    'desktop-chrome': 'Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/142.0.0.0 Safari/537.36',
    'desktop-safari': 'Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/605.1.15 (KHTML, like Gecko) Version/18.0 Safari/605.1.15',
    'desktop-firefox': 'Mozilla/5.0 (X11; Linux x86_64; rv:132.0) Gecko/20100101 Firefox/132.0',
    'desktop-other': 'Mozilla/5.0 (X11; Linux x86_64) Something/1.0',
    'other': 'curl/8.0',
  };
  for (const [want, ua] of Object.entries(cases)) assert.equal(R.agentClass(ua, 0), want, ua);
  assert.equal(R.agentClass(cases['desktop-safari'], 5), 'ios-safari', 'iPadOS presents as a Mac with touch');
  for (const v of [undefined, null, 5, {}]) assert.equal(R.agentClass(v), 'other');
});

test('a normal page: every field present, every value inside its closed set', () => {
  const w = world();
  try {
    R.start({now: w.now, defer: false});
    w.tick(125_000);
    const s = R.snapshot('manual', {cacheBytes: 3 * 1024 * 1024});
    closed(s);
    assert.deepEqual(s, {reason: 'manual', agent: 'android-chrome', alive: 'lt10m', hidden: 0, hiddenMax: 'none', gl: 'ok', glLost: 0, glRestored: 0, mem: 8, cache: 'lt8mb', cards: 'lt500', nodes: 'lt1500'});
    const c = w.canvases[0];
    assert.equal(w.canvases.length, 1); assert.equal(c.width, 1); assert.equal(c.height, 1);
    assert.equal(c.opts.powerPreference, 'low-power'); assert.equal(c.opts.failIfMajorPerformanceCaveat, false); assert.equal(c.opts.preserveDrawingBuffer, false);
    R.start({now: w.now, defer: false}); assert.equal(w.canvases.length, 1, 'one canary per page');
    assert.equal(w.doc.listeners.visibilitychange.length, 1);
  } finally { w.done(); }
});

test('nothing from the page, the UA or the caller leaks: serialized sample holds no text, URL or file name', () => {
  const w = world({pg: 'SECRET-TITLE https://example.com/private/report.pdf 12 / 497'});
  try {
    R.start({now: w.now, defer: false});
    const text = JSON.stringify(R.snapshot('SECRET-REASON https://example.com/x', {cacheBytes: 'SECRET-BYTES'}));
    for (const needle of ['SECRET', 'PKX110', 'Mozilla', '142.0', 'http', '://', '.pdf', 'example', '/', '\\']) assert.ok(!text.includes(needle), needle + ' in ' + text);
    closed(JSON.parse(text));
    assert.equal(JSON.parse(text).reason, 'manual'); assert.equal(JSON.parse(text).cache, 'unknown'); assert.equal(JSON.parse(text).cards, 'lt500');
  } finally { w.done(); }
});

test('odd inputs fall back to fixed words', () => {
  const w = world({pg: '', nodes: NaN, memory: 3.7, ua: null});
  try {
    R.start({now: w.now, defer: false});
    const s = R.snapshot('long_hidden', {cacheBytes: 70 * 1024 * 1024}); closed(s);
    assert.equal(s.cards, 'unknown'); assert.equal(s.nodes, 'unknown'); assert.equal(s.mem, null); assert.equal(s.agent, 'other'); assert.equal(s.cache, 'ge64mb');
    assert.equal(R.snapshot('manual', {cacheBytes: 0}).cache, 'none'); assert.equal(R.snapshot('manual').cache, 'unknown');
  } finally { w.done(); }
  const z = world({pg: '0 / 0'});
  try { R.start({now: z.now, defer: false}); assert.equal(R.snapshot('manual').cards, 'none'); } finally { z.done(); }
});

test('hidden spans: counted, longest kept in a bucket; only a span of ten minutes or more asks for a sample, once', () => {
  const w = world(), heard = [];
  try {
    R.start({now: w.now, defer: false, emit: (r) => heard.push(r)});
    w.show(false); w.tick(30_000); w.show(true);
    assert.deepEqual(heard, []); let s = R.snapshot('manual'); assert.equal(s.hidden, 1); assert.equal(s.hiddenMax, 'lt1m');
    w.show(false); w.show(false); w.tick(R.LONG_HIDDEN_MS - 1);
    assert.equal(R.snapshot('manual').hiddenMax, 'lt10m', 'a span still running counts');
    w.show(true); assert.deepEqual(heard, []);
    w.show(false); w.tick(R.LONG_HIDDEN_MS); w.show(true); w.show(true);
    assert.deepEqual(heard, ['long_hidden']); s = R.snapshot('long_hidden'); closed(s);
    assert.equal(s.hidden, 3); assert.equal(s.hiddenMax, 'lt1h'); assert.equal(s.reason, 'long_hidden');
    w.show(false); w.tick(7 * 3_600_000); w.show(true); assert.equal(R.snapshot('manual').hiddenMax, 'ge6h');
  } finally { w.done(); }
  const h = world({hidden: true});
  try { R.start({now: h.now, defer: false}); assert.equal(R.snapshot('manual').hidden, 1, 'a page opened in the background starts hidden'); } finally { h.done(); }
});

test('GPU context lost: counted, exactly one request per loss, restore tracked, a throwing listener is contained', () => {
  const w = world(), heard = [];
  try {
    R.start({now: w.now, defer: false, emit: (r) => { heard.push(r); throw new Error('listener bug'); }});
    const c = w.canvases[0]; let prevented = 0;
    c.lost = true; c.on.webglcontextlost({preventDefault: () => { prevented++; }});
    assert.deepEqual(heard, ['context_lost']); assert.equal(prevented, 1);
    let s = R.snapshot('context_lost'); closed(s); assert.equal(s.gl, 'lost'); assert.equal(s.glLost, 1); assert.equal(s.glRestored, 0); assert.equal(s.reason, 'context_lost');
    c.lost = false; c.on.webglcontextrestored({});
    s = R.snapshot('manual'); assert.equal(s.gl, 'ok'); assert.equal(s.glRestored, 1); assert.deepEqual(heard, ['context_lost']);
    for (let i = 0; i < 150; i++) c.on.webglcontextlost({preventDefault() { throw new Error('frozen event'); }});
    assert.equal(R.snapshot('manual').glLost, 99, 'capped');
  } finally { w.done(); }
});

test('no WebGL: unavailable, never an exception, counters still work', () => {
  for (const gl of ['none', 'throws', 'getContext-throws']) {
    const w = world({gl});
    try {
      assert.doesNotThrow(() => R.start({now: w.now, defer: false}));
      const s = R.snapshot('manual'); closed(s); assert.equal(s.gl, 'unavailable', gl); assert.equal(s.glLost, 0);
      w.show(false); w.tick(1000); w.show(true); assert.equal(R.snapshot('manual').hidden, 1);
    } finally { w.done(); }
  }
  const saved = globalThis.document; delete globalThis.document;
  try { assert.doesNotThrow(() => R.start({defer: false})); const s = R.snapshot('manual'); closed(s); assert.equal(s.gl, 'unavailable'); assert.equal(s.cards, 'unknown'); }
  finally { R.reset(); if (saved) globalThis.document = saved; }
});

test('deferred canary: not created at start', () => {
  const w = world(); const timers = []; const st = globalThis.setTimeout;
  globalThis.setTimeout = (fn, ms) => { timers.push([fn, ms]); return 0; };
  try {
    R.start({now: w.now});
    assert.equal(w.canvases.length, 0); assert.equal(R.snapshot('manual').gl, 'pending'); assert.equal(timers.length, 1); assert.ok(timers[0][1] >= 1000);
    timers[0][0](); assert.equal(w.canvases.length, 1); assert.equal(R.snapshot('manual').gl, 'ok');
  } finally { globalThis.setTimeout = st; w.done(); }
});

test('hourly cap: AUTO_MAX unattended samples per page per hour, then room again as the window slides', () => {
  const w = world();
  try {
    R.start({now: w.now, defer: false});
    const got = []; for (let i = 0; i < R.AUTO_MAX + 4; i++) { got.push(R.allow()); w.tick(60_000); }
    assert.deepEqual(got, [...Array(R.AUTO_MAX).fill(true), false, false, false, false]);
    w.tick(R.AUTO_WINDOW_MS - (R.AUTO_MAX + 4) * 60_000); assert.equal(R.allow(), true, 'the first one left the window');
    assert.equal(R.allow(), false);
    w.tick(R.AUTO_WINDOW_MS); assert.deepEqual([R.allow(), R.allow(), R.allow(), R.allow()], [true, true, true, false]);
  } finally { w.done(); }
});

test('static contract: no storage, no network, no imports, no loop, no content reads in the tracker; sender wiring', () => {
  const code = src('render-diag.js').replace(/\/\*[\s\S]*?\*\/|\/\/[^\n]*/g, '');
  for (const banned of [/localStorage|sessionStorage|indexedDB|document\.cookie/, /\bfetch\(|XMLHttpRequest|WebSocket|sendBeacon/, /\bimport\b/, /requestAnimationFrame|setInterval/,
    /location\b|\.href|\.title|innerText|innerHTML|\.value\b|dataset/, /appendChild|\.append\(|insertBefore|\.before\(|\.after\(/, /drawArrays|clear\(|readPixels|getExtension|getParameter/]) assert.doesNotMatch(code, banned);
  assert.equal((code.match(/textContent/g) || []).length, 1, 'only the page counter, digits only');
  assert.match(code, /\/\(\\d\{1,7\}\)\\s\*\$\/\.exec\(document\.getElementById\('pg'\)/);
  assert.equal((code.match(/userAgent/g) || []).length, 1); assert.match(code, /agent: agentClass\(nav\.userAgent, nav\.maxTouchPoints\)/);
  const diag = src('layout-diag.js');
  assert.match(diag, /health\.start\(\{emit:auto\}\)/); assert.doesNotMatch(diag, /from '\.\/media\.js'/, 'importing media.js here reorders the page modules (WebKit pairing broke)');
  assert.match(src('../app.js'), /setCacheBytes\(media\.cacheSize\)/);
  assert.match(diag, /health\.autoQueue\(\{ready:isReady,busy:\(\)=>running,hidden:\(\)=>document\.hidden,send:/);
  assert.match(diag, /visibilitychange',\(\)=>\{if\(!document\.hidden\)queue\.wake\(\);\}/);
  const auto = diag.slice(diag.indexOf('const queue=health.autoQueue'), diag.indexOf('// @ix app.layout_diagnostic'));
  assert.ok(auto.length > 100); assert.doesNotMatch(auto, /Upload|paint\(|result=|getElementById/, 'an unattended sample never uploads a file and never touches the screen');
  assert.doesNotMatch(diag, /localStorage|sessionStorage/);
  assert.match(src('layout-sample.js'), /\.\.\.\(render&&typeof render==='object'\?\{render\}:\{\}\)/);
});

test('host and page agree on every closed set', () => {
  const py = readFileSync(new URL('../../host/agentj/layout_diag.py', import.meta.url), 'utf8');
  const block = py.slice(py.indexOf('RENDER_ENUMS'), py.indexOf('COLOR = '));
  const tuple = (key) => [...new RegExp(`'${key}':\\(([^)]*)\\)`).exec(block)[1].matchAll(/'([^']+)'/g)].map((m) => m[1]);
  for (const [key, values] of Object.entries({reason: R.REASONS, agent: R.AGENTS, alive: R.ALIVE, hiddenMax: R.HIDDEN_MAX, gl: R.GL, cache: R.CACHE, cards: R.CARDS, nodes: R.NODES})) assert.deepEqual(tuple(key), values, key);
  assert.match(block, /RENDER_COUNTS = \{'hidden':9999,'glLost':99,'glRestored':99\}/);
  assert.deepEqual(/RENDER_MEMORY = \(([^)]*)\)/.exec(block)[1].split(',').map(Number), R.MEMORY);
});

// ── the unattended sender's queue (fake timers; AUTO_RETRY_MS apart) ─────────────────────────────────────────────────
function bench(over = {}) {
  const st = {ready: true, busy: false, hidden: false, may: true, sent: [], timers: [], gate: null, ...over};
  const q = R.autoQueue({ready: () => st.ready, busy: () => st.busy, hidden: () => st.hidden, may: () => st.may,
    send: (reason) => { st.sent.push(reason); return st.gate ? new Promise((r) => { st.release = r; }) : Promise.resolve(); },
    setTimer: (fn, ms) => { const t = {fn, ms, live: true}; st.timers.push(t); return t; }, clearTimer: (t) => { if (t) t.live = false; }});
  st.tick = async () => { const t = st.timers.filter((x) => x.live).at(-1); if (!t) return false; t.live = false; await t.fn(); return true; };
  st.waiting = () => st.timers.filter((x) => x.live).length;
  return [q, st];
}
const settle = () => new Promise((r) => setImmediate(r));

test('queue: a connected, visible, idle page sends at once; other words are ignored', async () => {
  const [q, st] = bench();
  q.push('manual'); q.push('anything'); q.push(undefined); await settle(); assert.deepEqual(st.sent, []);
  q.push('context_lost'); await settle(); assert.deepEqual(st.sent, ['context_lost']); assert.equal(q.pending(), null); assert.equal(st.waiting(), 0);
});

test('queue: not connected → tried every 5 s, dropped after 12 tries, never sent late', async () => {
  const [q, st] = bench({ready: false});
  q.push('long_hidden'); await settle();
  let ticks = 0; while (await st.tick()) ticks++;
  assert.equal(ticks, R.AUTO_TRIES, '12 retries, then nothing left to wait for'); assert.ok(st.timers.every((t) => t.ms === R.AUTO_RETRY_MS));
  assert.equal(q.pending(), null); assert.equal(st.waiting(), 0);
  st.ready = true; q.wake(); await settle(); assert.deepEqual(st.sent, [], 'a dropped sample does not come back');
  // connected again on the 5th try → sent once, no timer left
  const [q2, s2] = bench({ready: false});
  q2.push('context_lost'); for (let i = 0; i < 4; i++) await s2.tick();
  s2.ready = true; await s2.tick(); await settle(); assert.deepEqual(s2.sent, ['context_lost']); assert.equal(s2.waiting(), 0);
});

test("queue: while the owner's own send is running the sample waits, then goes out once", async () => {
  const [q, st] = bench({busy: true});
  q.push('context_lost'); await settle(); assert.deepEqual(st.sent, []); assert.equal(q.pending(), 'context_lost'); assert.equal(st.waiting(), 1);
  await st.tick(); assert.deepEqual(st.sent, [], 'still running');
  st.busy = false; await st.tick(); await settle();
  assert.deepEqual(st.sent, ['context_lost']); assert.equal(q.pending(), null); assert.equal(st.waiting(), 0);
});

test('queue: hidden page keeps the sample without a timer; wake() on the return sends it', async () => {
  const [q, st] = bench({hidden: true});
  q.push('context_lost'); await settle(); assert.deepEqual(st.sent, []); assert.equal(st.waiting(), 0, 'no timer in the background'); assert.equal(q.pending(), 'context_lost');
  q.wake(); await settle(); assert.deepEqual(st.sent, []);
  st.hidden = false; q.wake(); await settle(); assert.deepEqual(st.sent, ['context_lost']);
  q.wake(); await settle(); assert.deepEqual(st.sent, ['context_lost'], 'nothing pending, nothing sent');
});

test('queue: one reason kept (a lost context outranks a long absence), one send at a time, cap and errors contained', async () => {
  const [q, st] = bench({hidden: true});
  q.push('long_hidden'); q.push('context_lost'); q.push('long_hidden'); assert.equal(q.pending(), 'context_lost');
  st.hidden = false; q.wake(); await settle(); assert.deepEqual(st.sent, ['context_lost']);
  const [q2, s2] = bench({gate: true});
  q2.push('context_lost'); await settle(); q2.push('long_hidden'); q2.push('context_lost'); await settle();
  assert.deepEqual(s2.sent, ['context_lost'], 'the second waits for the first'); s2.release(); await settle(); await settle();
  assert.deepEqual(s2.sent, ['context_lost', 'context_lost']);
  const [q3, s3] = bench({may: false});
  q3.push('context_lost'); await settle(); assert.deepEqual(s3.sent, [], 'past the hourly cap: dropped'); assert.equal(q3.pending(), null); assert.equal(s3.waiting(), 0);
  const q4 = R.autoQueue({ready: () => true, busy: () => false, hidden: () => false, may: () => true, send: async () => { throw new Error('link fell'); }});
  q4.push('context_lost'); await settle(); assert.equal(q4.pending(), null);
});
