// R-0051 W8: the login-rescue link (Settings → AI 登录救援 / AI 账号) only ever points at the harness's official https login
// host. Drives the live js/authrescue.js (not the retained 0.30.22 copy) with a stand-in DOM.
import {test} from 'node:test';
import assert from 'node:assert/strict';
import {readFileSync} from 'node:fs';
import vm from 'node:vm';
globalThis.window ??= {};
const {authOf} = await import('../public/js/authcard.js');

function fixture() {
  const nodes = new Map();
  class Node {
    constructor(tag) { this.tag = tag; this.children = []; this.dataset = {}; this.attrs = {}; }
    setAttribute(k, v) { this.attrs[k] = v; }
    append(...xs) { this.children.push(...xs); }
    replaceChildren(...xs) { this.children = [...xs]; }
    addEventListener() {}
    getClientRects() { return []; }
    get textContent() { return this.text || this.children.map((x) => x.textContent || '').join(' '); }
    set textContent(x) { this.text = x; }
  }
  for (const id of ['ar-list', 'ar-flow', 'acct-flow', 'ar-msg']) { const n = new Node('div'); n.id = id; nodes.set(id, n); }
  const ctx = vm.createContext({Date, URL, document: {createElement: (t) => new Node(t), createTextNode: (s) => { const n = new Node('#text'); n.textContent = s; return n; }, addEventListener() {}},
    localStorage: {getItem: () => ''}, notify() {}, t: (x) => x, fillText: (n, s) => { n.textContent = s; }, el: (id) => nodes.get(id), copyable() {}, copyNow() {}, reopenAuth() {}, authOf,
    ask1: async () => ({t: 'auth_res', ok: true}), isReady: () => true, setInterval: () => 0, clearInterval() {}, clearTimeout() {}, setTimeout: () => 0, loginSheet() {}, relayKeys: {unmount() {}}});
  const source = readFileSync(new URL('../public/js/authrescue.js', import.meta.url), 'utf8').replace(/^import .*;.*$/mg, '').replace(/^export /mg, '');
  vm.runInContext(source + ';globalThis.api={onPush,close};', ctx);
  const anchors = (n, out = []) => { if (n && n.tag === 'a' && 'href' in n) out.push(n.href); for (const c of n?.children || []) anchors(c, out); return out; };
  return {push: (flow) => ctx.api.onPush({view: {harnesses: [], flow}}), hrefs: () => anchors(nodes.get('ar-flow')), text: () => nodes.get('ar-flow').textContent};
}
const exp = 2000000000;
const flows = {
  codexDevice: (url) => ({kind: 'codex', stage: 'device', url, code: 'ABCD-1234', expires_at: exp}),
  claudeUrl: (url) => ({kind: 'claude', stage: 'url', url, expires_at: exp, round: 1, rounds: 3}),
  kimiDevice: (url) => ({kind: 'kimi', stage: 'device', url, code: '', expires_at: exp}),
};

test('W8: official https login hosts render the 打开链接 button (positive control)', () => {
  const ok = [[flows.codexDevice, 'https://auth.openai.com/codex/device'], [flows.claudeUrl, 'https://claude.ai/oauth/authorize?code=true&client_id=x'],
    [flows.kimiDevice, 'https://www.kimi.com/code/authorize']];
  for (const [make, url] of ok) {
    const f = fixture(); f.push(make(url));
    assert.deepEqual(f.hrefs(), [url], url);
  }
});

test('W8: javascript:, data:, other origins and look-alikes never become a link', () => {
  const bad = ['javascript:alert(1)', 'JAVASCRIPT:alert(1)', ' javascript:alert(1)', 'data:text/html,<script>alert(1)</script>', 'vbscript:x',
    'intent://scan/#Intent;scheme=zxing;end', 'http://auth.openai.com/codex/device', 'https://evil.example.com/codex/device',
    'https://auth.openai.com.evil.test/codex/device', 'https://user@auth.openai.com/codex/device', 'https://auth.openai.com:8443/codex/device',
    '//auth.openai.com/codex/device', '/relative', '', 'https://claude.ai/oauth/authorize'.replace('claude.ai', 'claude.ai.example.com')];
  for (const make of Object.values(flows)) for (const url of bad) {
    const f = fixture(); f.push(make(url));
    assert.deepEqual(f.hrefs(), [], make(url).kind + ' ' + JSON.stringify(url));
    assert.doesNotMatch(f.text(), /ar\.openLink/);
  }
  // the url of one kind on another kind's card is refused too
  const f = fixture(); f.push(flows.codexDevice('https://claude.ai/oauth/authorize'));
  assert.deepEqual(f.hrefs(), []);
});

test('W8: the anchor gets the vetted URL, never a value the flow object changes afterwards', () => {
  const f = fixture();
  let reads = 0;
  const flow = flows.codexDevice('https://auth.openai.com/codex/device');
  const url = flow.url;
  Object.defineProperty(flow, 'url', {get: () => (++reads > 1 ? 'javascript:alert(1)' : url), enumerable: true});
  f.push(flow);
  assert.ok(f.hrefs().every((h) => h === url), JSON.stringify(f.hrefs()));
});
