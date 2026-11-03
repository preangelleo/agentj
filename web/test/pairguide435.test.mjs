// #435 (owner 11-03 17:33 / 21:37): 「首台遥控器配好了。建议再配…」 stayed under the chat header with 3–4 remotes paired and
// survived refreshes. Now: one bottom notice (the shared one, 10 s), only with exactly one remote, once per device;
// never a resident line, never in the composer, never a message to the Agent.
import { test } from 'node:test';
import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import vm from 'node:vm';

const base = new URL('../', import.meta.url);
const read = (p) => readFileSync(new URL(p, base), 'utf8');
const SRC = read('public/js/pair-guide.js');

function load({ store = new Map(), throwing = false } = {}) {
  const toasts = [], nodes = [];
  const ls = {
    getItem: (k) => { if (throwing) throw new Error('blocked'); return store.has(k) ? store.get(k) : null; },
    setItem: (k, v) => { if (throwing) throw new Error('blocked'); store.set(k, String(v)); },
  };
  const ctx = vm.createContext({
    localStorage: ls,
    t: (k) => 'T:' + k,
    notifyOnce(spec, key) { try { if (ls.getItem(key)) return false; ls.setItem(key, '1'); } catch {} toasts.push(JSON.parse(JSON.stringify(spec))); return true; },
    onLang: () => {},
    el: (id) => { const n = { id, hidden: true, dataset: {} }; nodes.push(n); return n; },
    Date,
  });
  vm.runInContext(SRC.replace(/^import .*;$/gm, '').replace(/export (function|const|let)/g, '$1'), ctx);
  const run = (code) => vm.runInContext(code, ctx);
  return { toasts, nodes, store, run, guide: (k) => run(`showPairGuide(${JSON.stringify(k)})`), remotes: (n) => run(`noteRemotes(${JSON.stringify({ t: 'devs', items: Array.from({ length: n }, (_, i) => ({ id: 'd' + i })) })})`) };
}

test('issue 435: one remote: one bottom notice for 10 seconds, then never again on this device', () => {
  const store = new Map();
  const a = load({ store });
  a.remotes(1); a.guide('computer');
  assert.deepEqual(a.toasts, [{ kind: 'info', shape: 'rect', text: 'T:pairGuide.computer', ms: 10000 }]);
  assert.equal(a.nodes.filter((n) => n.hidden === false).length, 0, 'no resident line is shown');
  a.guide('computer'); a.guide('phone');                       // the host repeats it on every resume
  assert.equal(a.toasts.length, 1, 'same page: once');
  const b = load({ store });                                   // a refresh / reopened page on the same device
  b.remotes(1); b.guide('computer');
  assert.equal(b.toasts.length, 0, 'same device after refresh: never again');
});

test('issue 435: two or more remotes: never shown, even when an older host still suggests one', () => {
  for (const n of [2, 3, 4]) {
    const a = load();
    a.remotes(n);
    for (const k of ['computer', 'phone']) a.guide(k);
    assert.equal(a.toasts.length, 0, n + ' remotes');
  }
  const unknown = load();                                     // no remote list yet: do not guess
  unknown.guide('computer');
  assert.equal(unknown.toasts.length, 0);
});

test('issue 435: blocked storage still shows at most once per page and null / other kinds show nothing', () => {
  const a = load({ throwing: true });
  a.remotes(1); a.guide(null); a.guide('other'); a.guide('tablet');
  assert.equal(a.toasts.length, 0);
  a.guide('phone'); a.guide('phone');
  assert.deepEqual(a.toasts, [{ kind: 'info', shape: 'rect', text: 'T:pairGuide.phone', ms: 10000 }]);
});

test('issue 435: no resident hint element and no path into the composer or the send queue', () => {
  const html = read('public/index.html'), app = read('public/app.js');
  assert.doesNotMatch(html, /id="pair-guide"/, 'the resident <p id="pair-guide"> under the header is gone');
  assert.doesNotMatch(app, /\$\('pair-guide'\)/);
  assert.match(app, /case 'pair_guide': showPairGuide\(m\.kind\); return;/);
  assert.match(app, /case 'devs':[^\n]*noteRemotes\(m\)/, 'the remote count comes from the same devs list Settings shows');
  // the module only renders a toast: no composer, outbox, relay send or Agent path is reachable from it
  const imports = [...SRC.matchAll(/^import .* from '(.+)';$/gm)].map((m) => m[1]).sort();
  assert.deepEqual(imports, ['./t.js', './ui.js']);
  assert.doesNotMatch(SRC, /\b(send|outbox|composer|words|textarea|input|value)\b/i);
  assert.match(SRC, /notifyOnce\(\{ kind: 'info', shape: 'rect',[^\n]*ms: PAIR_GUIDE_MS \}/);
  assert.match(SRC, /PAIR_GUIDE_MS = 10000/);
});
