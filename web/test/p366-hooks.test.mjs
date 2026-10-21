// P366 · Settings → Webhook (js/webhooks.js) — node unit checks against a minimal DOM: a `hook_list_res` renders live hooks
// first (resident before the others; pending = 「等你批准」, paused = 「已暂停」 + 恢复), ended ones (24 h) greyed below with no
// actions, today's count and the connection line, 「最近投递」 with the hook's name and the Agent's verdict; 撤销 asks first and
// only then sends `hook_set revoke`; 暂停 / 恢复 send their op; `hook_changed` re-reads once while the page is open (debounced)
// and never when it is closed; a token hook's URL placeholder is never copied. Plus the `via: hook` page label (snap.js keeps
// it, relay.js names it), both dictionaries, and the 技能 page blurbs for every bundled skill (agentj-hooks included).
// The real page in Chromium: test/p366.mjs (gate p366-phone).
import { test } from 'node:test';
import assert from 'node:assert/strict';
import { readFileSync, readdirSync, existsSync } from 'node:fs';
import { webcrypto } from 'node:crypto';

if (!globalThis.crypto) globalThis.crypto = webcrypto;

// ---------------------------------------------------------------- a minimal DOM (only what the page and ui.js mk() touch)
class El {
  constructor(tag) { this.tagName = tag.toUpperCase(); this.kids = []; this.attrs = {}; this.dataset = {}; this.className = ''; this.hidden = false; this.disabled = false; this.on = {}; this._t = null; this.ownerDocument = globalThis.document; this.classList = {add: (...xs) => {this.className += ' '+xs.join(' ');}, remove: (...xs) => {this.className = this.className.split(' ').filter(x=>!xs.includes(x)).join(' ');}, contains: x=>this.className.split(' ').includes(x)}; }
  get childNodes() { return this.kids; }
  removeAttribute(k) { delete this.attrs[k]; }
  append(...ns) { for (const n of ns) this.kids.push(typeof n === 'string' ? text(n) : n); }
  appendChild(n) { this.append(n); return n; }
  replaceChildren(...ns) { this.kids = []; this.append(...ns); }
  setAttribute(k, v) { this.attrs[k] = String(v); }
  getAttribute(k) { return k in this.attrs ? this.attrs[k] : null; }
  addEventListener(ev, fn) { (this.on[ev] ||= []).push(fn); }
  click() { if (!this.disabled) for (const f of this.on.click || []) f({ target: this, currentTarget: this }); }
  get textContent() { return this._t !== null ? this._t : this.kids.map((k) => k.textContent).join(''); }
  set textContent(v) { this.kids = []; this._t = String(v); }
  all() { const out = []; const st = [...this.kids].reverse(); while (st.length) { const n = st.pop(); if (n instanceof El) { out.push(n); st.push(...[...n.kids].reverse()); } } return out; }
  find(cls) { return this.all().filter((n) => n.className.split(' ').includes(cls)); }
}
const text = (s) => { const n = new El('#text'); n._t = String(s); return n; };
globalThis.window ??= globalThis;
globalThis.addEventListener ??= () => {};
globalThis.document = { createElement: (t) => new El(t), createElementNS: (_,t) => new El(t), createTextNode: text, addEventListener() {}, getElementById: () => null, querySelectorAll: () => [],
  documentElement: { dataset: {}, style: { setProperty() {} } }, body: { dataset: {} }, visibilityState: 'visible' };

const W = await import('../public/js/webhooks.js');
const { sample: fixtureSample, TOKEN_TAIL } = await import('./p366-fixture.mjs');
const snap = await import('../public/js/snap.js');
const DICT = (await import('../public/i18n.js')).default;
const SRC = readFileSync(new URL('../public/js/webhooks.js', import.meta.url), 'utf8');
const RELAY = readFileSync(new URL('../public/js/relay.js', import.meta.url), 'utf8');
const SNAP = readFileSync(new URL('../public/js/snap.js', import.meta.url), 'utf8');
const HTML = readFileSync(new URL('../public/index.html', import.meta.url), 'utf8');
const SETTINGS = readFileSync(new URL('../public/js/settings.js', import.meta.url), 'utf8');
const APP = readFileSync(new URL('../public/app.js', import.meta.url), 'utf8');

// ---------------------------------------------------------------- a sample list (made-up ids, names and URLs; no secret)
const NOW = Math.floor(Date.now() / 1000);
const id = (c) => c.repeat(22);
const sample = () => fixtureSample(NOW);

/** The page against a fake session: every ask is recorded and answered by `answer(obj)`. */
function harness(answer = () => sample()) {
  const asks = [], toasts = [], copies = [], confirms = [];
  const st = { confirmWith: true, ready: true };
  W.configure({
    ask: async (obj, want) => { asks.push(obj); assert.equal(want, 'hook_list_res'); return answer(obj); },
    ready: () => st.ready,
    confirm: async (title, body, yes, danger) => { confirms.push({ title, body, yes, danger }); return st.confirmWith; },
    copy: async (s) => { copies.push(s); return true; },
    toast: (s) => { toasts.push(s); },
  });
  const root = new El('div');
  return { asks, toasts, copies, confirms, st, root };
}
const tick = (ms = 0) => new Promise((r) => setTimeout(r, ms));
const rows = (root) => root.find('whrow');
const rowOf = (root, hid) => rows(root).find((r) => r.dataset.id === hid);
const btnOf = (row, cls) => row.find(cls)[0];

test('parse: malformed hooks / deliveries dropped, only known fields kept (no secret field survives), recent newest first', () => {
  const m = sample();
  m.hooks[0].secret = 'whsec_never'; m.hooks[0].preset = 'weird'; m.recent[0].body = 'never';
  const s = W.parse(m);
  assert.equal(s.hooks.length, 6);
  assert.equal(s.hooks[0].preset, 'hmac', 'unknown preset → a neutral one');
  assert.ok(!JSON.stringify(s).includes('never'), 'no stray field is kept');
  assert.deepEqual(s.recent.map((d) => d.id), ['d' + '3'.repeat(12), 'd' + '2'.repeat(12), 'd' + '1'.repeat(12)]);
  assert.deepEqual(s.quota, { used: 3, day_max: 1000, hooks_max: 20 });
  assert.equal(s.up, true);
  assert.deepEqual(s.hooks[3].target, { k: 'order', id: 'o42' });
  assert.equal(W.parse({ t: 'hook_list_res', r: 'x', ok: false, why: 'shape' }), null);
  assert.equal(W.parse({ t: 'keys', ok: true }), null);
});

test('sortHooks: live first (resident before others, then newest), ended within 24 h newest first, older ended dropped', () => {
  const { live, ended } = W.sortHooks(W.parse(sample()).hooks, NOW);
  assert.deepEqual(live.map((h) => h.name), ['线上告警', 'Stripe 收款', '视频-123', '表单']);
  assert.deepEqual(ended.map((h) => h.name), ['昨天的回调']);
});

test('copyable: the URL, never the token placeholder', () => {
  assert.equal(W.TOKEN_TAIL, TOKEN_TAIL, 'the host\'s placeholder');
  const s = W.parse(sample());
  assert.equal(W.copyable(s.hooks[0]), `https://hook.agentj.app/hb/${id('A')}`);
  assert.equal(W.copyable(s.hooks[3]), null, 'URL-token hook');
  assert.equal(W.copyable({ ...s.hooks[0], url: `https://hook.agentj.app/hb/x${W.TOKEN_TAIL}`, auth: 'hmac' }), null, 'placeholder tail alone is enough');
  assert.equal(W.copyable({ ...s.hooks[0], url: '' }), null);
});

test('render: header, live rows in order with plain words, pending / paused states, ended greyed without actions, recent deliveries', async () => {
  const h = harness();
  await W.show(h.root);
  assert.deepEqual(h.asks, [{ t: 'hook_list' }]);
  const R = h.root;
  assert.equal(R.find('whused')[0].textContent, '今天 3 / 1000 条');
  assert.equal(R.find('whup')[0].textContent, '在线');
  assert.deepEqual(R.find('setg').map((n) => n.textContent), ['正在使用', '已结束（24 小时内）', '最近投递']);
  const live = R.find('whlist--live')[0].find('whrow');
  assert.deepEqual(live.map((r) => r.find('whname')[0].textContent), ['线上告警', 'Stripe 收款', '视频-123', '表单']);
  // pending: 「等你批准」, only 撤销
  const pend = rowOf(R, id('C'));
  assert.equal(pend.find('whchip--pending')[0].textContent, '等你批准');
  assert.deepEqual(pend.find('aj-btn').map((b) => b.textContent), ['撤销']);
  assert.equal(pend.find('whkind')[0].textContent, '服务器告警 · 常驻 · 密钥校验 → 你');
  // resident Stripe: no chip, plain words, last delivery + count, three actions
  const stripe = rowOf(R, id('B'));
  assert.equal(stripe.find('whchip').length, 0);
  assert.equal(stripe.find('whkind')[0].textContent, 'Stripe · 常驻 · Stripe 签名 → 主 Agent');
  assert.equal(stripe.find('whexp').length, 0, 'resident: no expiry line');
  assert.match(stripe.find('whlast')[0].textContent, /^最近一次：.+ · 送达 · 共 2 条$/);
  assert.deepEqual(stripe.find('aj-btn').map((b) => b.textContent), ['暂停', '复制地址', '撤销']);
  // once: expiry; never delivered
  const once = rowOf(R, id('A'));
  assert.match(once.find('whkind')[0].textContent, /^完成回调 · 一次性 · 签名校验 → 主 Agent$/);
  assert.match(once.find('whexp')[0].textContent, / 到期$/);
  assert.equal(once.find('whlast')[0].textContent, '还没收到过', 'never delivered: no 「共 0 条」');
  // paused ttl to an order: chip 已暂停, toggle says 恢复
  const paused = rowOf(R, id('D'));
  assert.equal(paused.dataset.paused, '1');
  assert.equal(paused.find('whchip--paused')[0].textContent, '已暂停');
  assert.equal(btnOf(paused, 'whtoggle').textContent, '恢复');
  assert.equal(paused.find('whkind')[0].textContent, '表单 · 临时 · 地址里带令牌 → 派单 o42');
  // ended: greyed list, state chip, no buttons; the 25 h old one is not shown
  const ended = R.find('whlist--ended')[0].find('whrow');
  assert.equal(ended.length, 1);
  assert.ok(ended[0].className.includes('whrow--ended'));
  assert.equal(ended[0].find('whchip')[0].textContent, '已用完');
  assert.equal(ended[0].find('aj-btn').length, 0);
  assert.ok(!rowOf(R, id('F')));
  // recent: newest first, names looked up, verdicts in words, an unknown hook named as removed
  const del = R.find('whdel');
  assert.deepEqual(del.map((d) => d.find('whdel-name')[0].textContent), ['已删除的 Webhook', 'Stripe 收款', '昨天的回调']);
  assert.deepEqual(del.map((d) => (d.find('whdel-tri')[0] || { textContent: '' }).textContent), ['', '请你拍板', '继续']);
  assert.ok(del.every((d) => d.find('whdel-res')[0].textContent === '送达'));
  assert.ok(R.find('whroutes')[0].textContent.includes('Cloudflare'));
  // P366 add-on 3: every row says whether our relay sees the body; the page says it plainly once
  assert.equal(rowOf(R, id('A')).find('whmode')[0].textContent, '端到端加密');
  assert.equal(rowOf(R, id('A')).find('whmode')[0].dataset.e2e, '1');
  assert.equal(rowOf(R, id('B')).find('whmode')[0].textContent, '明文经过中继');
  assert.equal(rowOf(R, id('C')).find('whmode')[0].dataset.e2e, '0', 'no e2e field → plain (never claim more than the host said)');
  assert.match(R.find('whhonest')[0].textContent, /承诺不看、不存/);
  assert.ok(!R.textContent.includes(W.TOKEN_TAIL) && !R.textContent.includes('https://'), 'no URL is printed on the page');
  W.close();
});

test('撤销 asks first: cancel sends nothing; confirm sends hook_set revoke and draws the fresh list', async () => {
  let gone = false;
  const h = harness((o) => {
    const m = sample();
    if (o.t === 'hook_set' && o.op === 'revoke') gone = true;
    if (gone) m.hooks = m.hooks.map((x) => (x && x.id === id('B') ? { ...x, state: 'revoked', ended: NOW } : x));
    return m;
  });
  await W.show(h.root);
  h.st.confirmWith = false;
  btnOf(rowOf(h.root, id('B')), 'whrevoke').click(); await tick();
  assert.equal(h.confirms.length, 1);
  assert.equal(h.confirms[0].title, '撤销「Stripe 收款」？');
  assert.equal(h.confirms[0].yes, '撤销'); assert.equal(h.confirms[0].danger, true);
  assert.equal(h.asks.filter((a) => a.t === 'hook_set').length, 0, 'cancel: nothing sent');
  h.st.confirmWith = true;
  btnOf(rowOf(h.root, id('B')), 'whrevoke').click(); await tick(); await tick();
  assert.deepEqual(h.asks.at(-1), { t: 'hook_set', id: id('B'), op: 'revoke' });
  assert.equal(rowOf(h.root, id('B')).dataset.state, 'revoked', 'fresh list drawn: the row moved to the ended list');
  assert.ok(rowOf(h.root, id('B')).className.includes('whrow--ended'));
  assert.ok(h.toasts.includes('已撤销「Stripe 收款」'));
  W.close();
});

test('暂停 / 恢复 send their op; a refused hook_set (shape) re-reads the list', async () => {
  const h = harness((o) => (o.t === 'hook_set' && o.id === id('A') ? { t: 'hook_list_res', r: 'r', ok: false, why: 'shape' } : sample()));
  await W.show(h.root);
  btnOf(rowOf(h.root, id('B')), 'whtoggle').click(); await tick(); await tick();
  assert.deepEqual(h.asks.at(-1), { t: 'hook_set', id: id('B'), op: 'pause' });
  btnOf(rowOf(h.root, id('D')), 'whtoggle').click(); await tick(); await tick();
  assert.deepEqual(h.asks.at(-1), { t: 'hook_set', id: id('D'), op: 'resume' });
  const n = h.asks.length;
  btnOf(rowOf(h.root, id('A')), 'whtoggle').click(); await tick(); await tick(); await tick();
  assert.deepEqual(h.asks.slice(n), [{ t: 'hook_set', id: id('A'), op: 'pause' }, { t: 'hook_list' }]);
  assert.ok(h.toasts.includes('这个 Webhook 已经不在了，列表已刷新。'));
  W.close();
});

test('hook_changed re-reads once (debounced) while the page is open, never when closed', async () => {
  const h = harness();
  await W.show(h.root);
  assert.equal(h.asks.length, 1);
  W.changed(); W.changed(); W.changed();
  await tick(W.DEBOUNCE_MS + 80);
  assert.deepEqual(h.asks.slice(1), [{ t: 'hook_list' }], 'a burst = one read');
  W.close();
  W.changed();
  await tick(W.DEBOUNCE_MS + 80);
  assert.equal(h.asks.length, 2, 'closed: no read');
  // app.js routes the broadcast to the page
  assert.match(APP, /case 'hook_changed': webhooks\.changed\(\); return;/);
});

test('复制地址: a plain URL is copied; a token hook copies nothing and says the full address is on the computer', async () => {
  const h = harness();
  await W.show(h.root);
  btnOf(rowOf(h.root, id('B')), 'whcopy').click(); await tick();
  assert.deepEqual(h.copies, [`https://hook.agentj.app/hb/${id('B')}`]);
  assert.equal(btnOf(rowOf(h.root, id('B')), 'whcopy').dataset.done, '1');
  assert.equal(btnOf(rowOf(h.root, id('B')), 'whcopy').getAttribute('aria-label'), '已复制');
  btnOf(rowOf(h.root, id('D')), 'whcopy').click(); await tick();
  assert.equal(h.copies.length, 1, 'the placeholder is never copied');
  assert.ok(!h.copies.some((c) => c.includes('<')));
  assert.equal(h.toasts.at(-1), '完整地址在电脑上：它里面带着令牌，手机上不显示。');
  W.close();
});

test('empty list: one friendly paragraph + the two routes; offline: nothing asked; English page', async () => {
  const h = harness(() => ({ t: 'hook_list_res', r: 'r', ok: true, hooks: [], recent: [], up: false, quota: { used: 0, day_max: 1000, hooks_max: 20 } }));
  await W.show(h.root);
  assert.equal(h.root.find('whempty')[0].textContent, '还没有 Webhook。跟 Agent 说「做完了回调我」「接 Stripe 收款」「服务器告警发给我」，它会帮你开。');
  assert.equal(h.root.find('whup')[0].textContent, '未连接');
  assert.equal(h.root.find('setg').length, 0);
  assert.equal(h.root.find('whroutes').length, 1);
  W.close();
  const off = harness(); off.st.ready = false;
  await W.show(off.root);
  assert.equal(off.asks.length, 0);
  assert.equal(off.root.find('whmsg')[0].textContent, '连上电脑后才能看。');
  W.close();
  const old = harness(() => ({ t: 'timeout' }));
  await W.show(old.root);
  assert.match(old.root.find('whmsg')[0].textContent, /升级后才有 Webhook/);
  W.close();
  const nocap = harness(); W.configure({ supported: () => false });   // a p33 computer without the `hooks1` capability
  await W.show(nocap.root);
  assert.equal(nocap.asks.length, 0);
  assert.match(nocap.root.find('whmsg')[0].textContent, /升级后才有 Webhook/);
  W.close(); W.configure({ supported: () => true });
  const fmt = (s, v) => (v ? String(s).replace(/\{(\w+)\}/g, (m, k) => (k in v ? String(v[k]) : m)) : s);
  window.AJLang = { t: (D, k, v) => fmt(D.en[k] ?? k, v), get: () => 'en', onChange() {} };
  try {
    const en = harness();
    await W.show(en.root);
    assert.equal(en.root.find('whused')[0].textContent, 'Today 3 / 1000');
    assert.equal(rowOf(en.root, id('B')).find('whkind')[0].textContent, 'Stripe · Always on · Stripe signature → Main Agent');
    assert.ok(!/[一-鿿]/.test(en.root.textContent.replace(/视频-123|Stripe 收款|线上告警|表单|昨天的回调/g, '')), 'English only');
    W.close();
  } finally { delete window.AJLang; }
});

test('via: hook — snap.js keeps it on Agent pages (history + search), relay.js labels it 「Webhook」', () => {
  assert.equal(snap.toPage({ id: 1, ts: 1000, src: { k: 'agent', via: 'hook', text: '' }, reply: { text: 'ok' } }).source.via, 'hook');
  assert.equal(snap.toPage({ id: 2, src: { k: 'agent', via: 'quota' } }).source.via, 'quota', 'the others stay');
  assert.equal(snap.toPage({ id: 3, src: { k: 'phone', via: 'hook' } }).source.via, undefined, 'only on Agent pages');
  assert.equal(snap.toPage({ id: 4, src: { k: 'agent', via: 'evil' } }).source.via, undefined);
  assert.equal((SNAP.match(/\['loop', 'task', 'quota', 'auto', 'hook'\]\.includes\(src\.via\)/g) || []).length, 2, 'history page + search hit');
  assert.match(RELAY, /src\.via === "hook" \? t\('p366\.src'\)/);
  assert.equal(DICT.zh['p366.src'], 'Webhook'); assert.equal(DICT.en['p366.src'], 'Webhook');
});

test('Settings → Webhook is wired: nav row, its sub-page, the title, leaving closes the page', () => {
  assert.match(HTML, /id="set-hooks-nav" class="setnav"/);
  assert.match(HTML, /<div id="set-hooks" class="setsub sethooks" hidden>[\s\S]*?id="hooks-box"/);
  assert.match(SETTINGS, /hooks: 'p366\.nav'/);
  assert.match(SETTINGS, /if \(v === 'hooks'\) webhooks\.show\(el\('hooks-box'\)\);/);
  assert.match(SETTINGS, /if \(from === 'hooks' && v !== 'hooks'\) webhooks\.close\(\);/);
  assert.match(SETTINGS, /el\('set-hooks-nav'\)\.addEventListener\('click', \(\) => showSub\('hooks'\)\);/);
});

test('every p366 string the page uses exists in zh and en with the same placeholders; no secret path in the module', () => {
  const used = new Set([...SRC.matchAll(/'(p366\.[\w.]+)'/g)].map((x) => x[1]));
  for (const p of ['callback', 'stripe', 'github', 'hmac', 'bearer', 'alert', 'form', 'ping']) used.add('p366.preset.' + p);
  for (const l of ['once', 'ttl', 'resident']) used.add('p366.life.' + l);
  for (const a of ['stripe', 'github', 'hmac', 'bearer', 'token']) used.add('p366.auth.' + a);
  for (const g of ['main', 'owner', 'wait', 'order', 'bot']) used.add('p366.to.' + g);
  for (const r of ['ok', 'dup', 'signature', 'stale', 'ip', 'method', 'quota', 'seal', 'plain', 'other']) used.add('p366.res.' + r);
  for (const s of ['pending', 'paused', 'used', 'revoked', 'expired']) used.add('p366.st.' + s);
  for (const v of ['continue', 'ask', 'spam']) used.add('p366.tri.' + v);
  for (const o of ['pause', 'resume', 'revoke']) used.add('p366.done.' + o);
  for (const k of ['p366.nav', 'p366.navNote']) used.add(k);
  for (const k of [...used]) if (k.endsWith('.')) used.delete(k);
  const ph = (s) => [...String(s).matchAll(/\{(\w+)\}/g)].map((m) => m[1]).sort().join();
  for (const k of used) {
    assert.ok(DICT.zh[k], 'zh ' + k); assert.ok(DICT.en[k], 'en ' + k);
    assert.equal(ph(DICT.zh[k]), ph(DICT.en[k]), 'placeholders ' + k);
  }
  assert.equal(DICT.zh['p366.life.once'] + DICT.zh['p366.life.ttl'] + DICT.zh['p366.life.resident'], '一次性临时常驻');
  for (const bad of ['localStorage', 'sessionStorage', 'indexedDB', 'console.', 'innerHTML', 'insertAdjacentHTML', 'hook_secret', '.secret']) assert.ok(!SRC.includes(bad), bad);
});

test('技能 page: every bundled skill has its p299.sk / p299.su blurb in both languages (agentj-hooks included)', () => {
  assert.match(DICT.zh['p299.sk.agentj-hooks'], /^Webhook：外部服务/);
  assert.equal(DICT.zh['p299.su.agentj-hooks'], '说「做完了回调我」或「接 Stripe 收款」。');
  assert.ok(DICT.en['p299.sk.agentj-hooks'] && DICT.en['p299.su.agentj-hooks']);
  const dir = new URL('../../host/agentj/skills/', import.meta.url);
  if (!existsSync(dir)) return;                       // the public export of web/ alone
  const names = readdirSync(dir, { withFileTypes: true }).filter((d) => d.isDirectory() && existsSync(new URL(d.name + '/SKILL.md', dir))).map((d) => d.name);
  assert.ok(names.includes('agentj-hooks'));
  for (const n of names) for (const L of ['zh', 'en']) for (const k of ['sk', 'su']) assert.ok(DICT[L][`p299.${k}.${n}`], `${L} p299.${k}.${n}`);
});
