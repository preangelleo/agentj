// #0304 A3 · Settings → 安全 → 「自动化外发授权」 / "Automation send grants": the page asks `{t:'automation_grants'}` when
// Settings opens, the host answers `{t:'automation_grants', items:[{id, kind, target, actions, to, domains, expires, max_uses,
// uses, created, state}]}` (also the reply to a revoke); each row = target (Webhook / 定时任务 name), action, recipients /
// domains, expiry / uses, state, and 「撤销」 → `{t:'automation_grant_revoke', id}` on this paired remote's session (the same
// plain narrowing message as preauth_revoke, no Face ID). Empty list → the empty line; no answer (older host) → hidden.
// Rule #393: the same row component as 「免问的动作」 (preauth__list / preauth__item / preauth__txt, aj-action--sm button).
// Text is textContent only (a hostile target / address stays text). Node, minimal DOM.
import { test } from 'node:test';
import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';

class El {
  constructor(tag) { this.tagName = tag.toUpperCase(); this.kids = []; this.attrs = {}; this.dataset = {}; this.className = ''; this.hidden = false; this.on = {}; this._t = null; }
  append(...ns) { for (const n of ns) this.kids.push(typeof n === 'string' ? text(n) : n); }
  replaceChildren(...ns) { this.kids = []; this._t = null; this.append(...ns); }
  setAttribute(k, v) { this.attrs[k] = String(v); }
  addEventListener(ev, fn) { (this.on[ev] ||= []).push(fn); }
  click() { for (const f of this.on.click || []) f({ target: this }); }
  get textContent() { return this._t !== null ? this._t : this.kids.map((k) => k.textContent).join(''); }
  set textContent(v) { this.kids = []; this._t = String(v); }
  get innerHTML() { throw new Error('innerHTML must not be used'); }
  set innerHTML(_) { throw new Error('innerHTML must not be used'); }
  all() { const out = []; const st = [...this.kids]; while (st.length) { const n = st.shift(); if (n instanceof El && n.tagName !== '#TEXT') { out.push(n); st.unshift(...n.kids); } } return out; }
  byTag(t) { return this.all().filter((n) => n.tagName === t.toUpperCase()); }
}
const text = (s) => { const n = new El('#text'); n._t = String(s); return n; };
globalThis.window ??= globalThis;
globalThis.addEventListener ??= () => {};
globalThis.document = { createElement: (t) => new El(t), createTextNode: text, getElementById: () => null, querySelector: () => null, querySelectorAll: () => [],
  addEventListener() {}, documentElement: { dataset: {}, style: { setProperty() {} } }, body: { dataset: {} } };

const P = await import('../public/js/pkassist.js');
const DICT = (await import('../public/i18n.js')).default;
const fmt = (s, v) => (v ? s.replace(/\{(\w+)\}/g, (m, x) => (x in v ? String(v[x]) : m)) : s);
const LANG = (l) => { window.AJLang = { get: () => l, t: (d, k, v) => fmt(d[l][k] ?? k, v), onChange() {} }; };
const read = (f) => readFileSync(new URL('../public/' + f, import.meta.url), 'utf8');

const T0 = 1_800_000_000;
const sample = () => ({ t: 'automation_grants', items: [
  { id: 'g1', kind: 'hook', target: 'invoice-in', actions: ['mail'], to: ['billing@example.com'], domains: [], expires: T0 + 86400 * 30, max_uses: 10, uses: 3, created: T0, state: 'live' },
  { id: 'g2', kind: 'task', target: 'weekly-report', actions: ['mail', 'mail+files'], to: [], domains: ['example.org'], expires: null, permanent: true, max_uses: null, uses: 0, created: T0, state: 'stale' },
  { id: 'g3', kind: 'hook', target: 'old', actions: ['mail'], to: ['a@example.com'], domains: [], expires: T0, max_uses: 1, uses: 1, created: T0, state: 'revoked' },
  { id: 'bad id!', kind: 'hook', target: 'x', actions: ['mail'], state: 'live' },
  { id: 'g5', kind: 'shell', target: 'x', actions: ['mail'], state: 'live' },
  null,
] });
const box = () => ({ row: Object.assign(new El('div'), { hidden: true }), list: new El('ul'), empty: Object.assign(new El('p'), { hidden: false }) });

test('parse: rows without a valid id or a known kind dropped; numbers checked; unknown state is never shown as in force', () => {
  const items = P.automationGrantItems(sample());
  assert.deepEqual(items.map((x) => x.id), ['g1', 'g2', 'g3']);
  assert.equal(items[1].expires, null); assert.equal(items[1].max_uses, null);
  const odd = P.automationGrantItems({ items: [{ id: 'g9', kind: 'task', state: 'whatever', uses: -1, expires: 'soon', to: 'x@example.com' }] })[0];
  assert.equal(odd.state, 'stale'); assert.equal(odd.uses, 0); assert.equal(odd.expires, null); assert.deepEqual(odd.to, []);
  assert.deepEqual(P.automationGrantItems({}), []);
  const ctl = P.automationGrantItems({ items: [{ id: 'g8', kind: 'hook', target: 'a‮b\u0007c', to: ['x @example.com'], state: 'live' }] })[0];
  assert.equal(ctl.target, 'abc'); assert.equal(ctl.to[0], 'x@example.com');
});

test('paint (zh): the preauth row component, target / action / recipients / domains / expiry / uses / state, revoke per row', () => {
  LANG('zh');
  const b = box(), revoked = [];
  P.paintAutomationGrants(b.row, b.list, b.empty, P.automationGrantItems(sample()), (x) => revoked.push(x.id));
  assert.equal(b.row.hidden, false); assert.equal(b.empty.hidden, true);
  const rows = b.list.kids;
  assert.equal(rows.length, 3);
  for (const r of rows) {
    assert.equal(r.className, 'preauth__item');
    assert.equal(r.kids[0].className, 'preauth__txt');
  }
  const [g1, g2, g3] = rows.map((r) => r.textContent);
  assert.ok(g1.startsWith('Webhook：invoice-in'), g1);
  for (const s of ['发邮件', '收件人：billing@example.com', '到期', '已用 3/10 次', '有效']) assert.ok(g1.includes(s), s + ' in ' + g1);
  assert.ok(g2.startsWith('定时任务：weekly-report'), g2);
  for (const s of ['域名：@example.org', '长期有效（直到你撤销）', '已用 0 次', '自动化已修改，需要重新授权', '发邮件 · 可带附件']) assert.ok(g2.includes(s), s + ' in ' + g2);
  assert.ok(!g1.includes('可带附件') && !g1.includes('长期有效'), 'mail only, with an end date: ' + g1);
  assert.ok(g3.includes('已撤销'));
  const btn = (r) => r.byTag('button')[0];
  assert.equal(btn(rows[0]).className, 'aj-action aj-action--sm');
  assert.equal(btn(rows[0]).textContent, '撤销');
  assert.equal(btn(rows[2]), undefined, 'a revoked grant has no button');
  btn(rows[1]).click(); btn(rows[0]).click();
  assert.deepEqual(revoked, ['g2', 'g1']);
  assert.equal(rows[0].dataset.grant, 'g1'); assert.equal(rows[1].dataset.state, 'stale');
});

test('empty list → the empty line; no answer yet (older host) → hidden; English only in English', () => {
  LANG('zh');
  const b = box();
  P.paintAutomationGrants(b.row, b.list, b.empty, null, () => {});
  assert.equal(b.row.hidden, true);
  P.paintAutomationGrants(b.row, b.list, b.empty, [], () => {});
  assert.equal(b.row.hidden, false); assert.equal(b.empty.hidden, false); assert.equal(b.list.kids.length, 0);
  LANG('en');
  P.paintAutomationGrants(b.row, b.list, b.empty, P.automationGrantItems(sample()), () => {});
  const all = b.list.textContent;
  assert.ok(all.includes('Webhook: invoice-in') && all.includes('Scheduled task: weekly-report') && all.includes('Used 3 of 10') && all.includes('Revoke'));
  assert.ok(all.includes('Send mail · Attachments allowed') && all.includes('No expiry (until you revoke it)'), all);
  assert.ok(!/[㐀-鿿]/.test(all), all);
  LANG('zh');
});

test('contract-a3 security r1 fields: permanent (or expires null on an older host) → 「长期有效」; mail+files its own action; unknown action stays text', () => {
  const [a, b, c] = P.automationGrantItems({ items: [
    { id: 'p1', kind: 'hook', target: 'h', actions: ['mail'], expires: null, permanent: true, state: 'live' },
    { id: 'p2', kind: 'hook', target: 'h', actions: ['mail'], expires: null, state: 'live' },
    { id: 'p3', kind: 'task', target: 't', actions: ['mail', 'mail+files', 'zap'], expires: T0 + 60, permanent: false, state: 'live' }] });
  assert.equal(a.permanent, true); assert.equal(b.permanent, true, 'an older host without the field: expires null says the same');
  assert.equal(c.permanent, false);
  LANG('zh');
  assert.ok(P.automationGrantLines(a)[1][1].startsWith('长期有效（直到你撤销）'));
  assert.ok(P.automationGrantLines(c)[1][0].startsWith('发邮件 · 可带附件 · zap'));
  assert.ok(P.automationGrantLines(c)[1][1].includes('到期'));
});

test('hostile target / address stays text (textContent only, innerHTML would throw)', () => {
  LANG('zh');
  const b = box();
  P.paintAutomationGrants(b.row, b.list, b.empty, P.automationGrantItems({ items: [{ id: 'g7', kind: 'hook', target: '<img src=x onerror=alert(1)>', actions: ['mail'], to: ['"<b>"@example.com'], state: 'live' }] }), () => {});
  assert.ok(b.list.textContent.includes('<img src=x onerror=alert(1)>'));
  assert.equal(b.list.byTag('img').length, 0);
});

test('「免问的动作」 still paints the same (shared row component, #393)', () => {
  LANG('zh');
  const row = new El('div'), list = new El('ul'), got = [];
  P.paintPreauth(row, list, [{ action: 'cu', device: 'd1', name: '我的 iPhone', at: T0 }], (x) => got.push(x));
  const li = list.kids[0];
  assert.equal(li.className, 'preauth__item'); assert.equal(li.dataset.action, 'cu');
  assert.equal(li.kids[0].className, 'preauth__txt');
  assert.equal(li.byTag('b').length, 1); assert.equal(li.byTag('small').length, 1);
  assert.ok(li.byTag('small')[0].textContent.startsWith('我的 iPhone'));
  li.byTag('button')[0].click();
  assert.equal(got[0].device, 'd1');
});

test('wiring: Settings asks on open, app.js routes the reply, revoke is the plain session message with {id}, markup reuses preauth', () => {
  const S = read('js/settings.js'), APP = read('app.js'), HTML = read('index.html'), CP = read('js/cardplace.js');
  assert.match(S, /sendApp\(\{ t: 'automation_grants' \}\)/);
  assert.match(S, /sendApp\(\{ t: 'automation_grant_revoke', id: item\.id \}\)/);
  assert.match(APP, /case 'automation_grants': settings\.automationGrants\(m\); return;/);
  assert.match(CP, /'automation_grants'/);
  const block = HTML.slice(HTML.indexOf('id="set-autogrant-row"') - 40, HTML.indexOf('id="set-autogrant-list"') + 60);
  assert.match(block, /class="setrow preauth" id="set-autogrant-row" hidden/);
  assert.match(block, /class="preauth__list" id="set-autogrant-list"/);
  assert.match(block, /data-i18n="p0304\.ag\.title"/);
  assert.ok(HTML.indexOf('id="set-autogrant-row"') > HTML.indexOf('id="set-preauth-row"'), 'right under 「免问的动作」 in 安全');
});

test('dictionaries: both languages, same placeholders, no trial wording', () => {
  const keys = Object.keys(DICT.zh).filter((k) => k.startsWith('p0304.ag.'));
  assert.ok(keys.length >= 19, String(keys.length));
  for (const k of keys) {
    assert.ok(DICT.en[k], k);
    const ph = (s) => (s.match(/\{\w+\}/g) || []).sort().join();
    assert.equal(ph(DICT.zh[k]), ph(DICT.en[k]), k);
    assert.ok(!/试用|trial/i.test(DICT.zh[k] + DICT.en[k]), k);
  }
  for (const s of P.AG_STATES) assert.ok(DICT.zh['p0304.ag.st.' + s] && DICT.en['p0304.ag.st.' + s], s);
});
