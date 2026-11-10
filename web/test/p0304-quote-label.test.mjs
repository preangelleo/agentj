// #0304 A2① · the approval card when the owner's own (verified) turn quotes outside material (an email, a WeChat message …):
// the warning names the material's channel (「含引用的邮件材料 · 发起人是你，引用内容来自外部」), the initiator reads 「你」, not
// 「外部消息」, and #572's 「来源未经确认」 line is not added. Guest / group / unknown roots (root missing or root.verified !== true)
// keep #541's p541.suspicious / p541.unknown wording. The Allow button follows the same rules as before (only the envelope's
// verification and the review state disable it). Node, minimal DOM: approval541.paint + approval-source572.paint.
import { test } from 'node:test';
import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';

class El {
  constructor(tag, id = '') {
    this.tagName = tag.toUpperCase(); this.id = id; this.kids = []; this.attrs = {}; this.dataset = {}; this.className = ''; this.hidden = false;
    this.disabled = false; this.open = false; this._t = null; this.parentNode = null;
    this.classList = { add: (...xs) => { this.className += ' ' + xs.join(' '); }, remove: (...xs) => { this.className = this.className.split(' ').filter((x) => !xs.includes(x)).join(' '); }, contains: (x) => this.className.split(' ').includes(x) };
  }
  append(...ns) { for (const n of ns) { const k = typeof n === 'string' ? text(n) : n; k.parentNode = this; this.kids.push(k); } }
  after() {} before() {} appendChild(n) { this.append(n); return n; } closest() { return null; } contains() { return false; } remove() {}
  replaceChildren(...ns) { this.kids = []; this._t = null; this.append(...ns); }
  setAttribute(k, v) { this.attrs[k] = String(v); }
  removeAttribute(k) { delete this.attrs[k]; }
  addEventListener() {}
  querySelector() { return (this._q ||= new El('button')); }
  get childNodes() { return this.kids; }
  get textContent() { return this._t !== null ? this._t : this.kids.map((k) => k.textContent).join(''); }
  set textContent(v) { this.kids = []; this._t = String(v); }
}
const text = (s) => { const n = new El('#text'); n._t = String(s); return n; };
const ids = new Map();
const byId = (id) => { if (!ids.has(id)) ids.set(id, new El(id === 'full541' ? 'dialog' : 'div', id)); return ids.get(id); };
globalThis.window ??= globalThis;
globalThis.addEventListener ??= () => {};
globalThis.document = { createElement: (t) => new El(t), createComment: () => new El('#comment'), createTextNode: text, getElementById: byId,
  querySelector: () => null, querySelectorAll: () => [], addEventListener() {}, documentElement: { dataset: {}, style: { setProperty() {} } }, body: { dataset: {} } };

const A = await import('../public/js/approval541.js');
const S = await import('../public/js/approval-source572.js');
const DICT = (await import('../public/i18n.js')).default;
const fmt = (s, v) => (v ? s.replace(/\{(\w+)\}/g, (m, x) => (x in v ? String(v[x]) : m)) : s);
const LANG = (l) => { window.AJLang = { get: () => l, t: (d, k, v) => fmt(d[l][k] ?? k, v), onChange() {} }; };

const opts = { busy: false, fold() {}, open() {}, locate() {} };
const card = (source, extra = {}) => ({ id: 'a'.repeat(32), approval_v: 2, envelope_verified: true, review_state: 'pending',
  envelope: { v: 2, request_id: 'a'.repeat(32), action: '发一封邮件', source }, ...extra });
const ownerQuote = (channel) => ({ channel, suspicious: true, verified: false, sender: 'someone@example.com', text: '帮我处理一下这封邮件',
  message_id: 'm1', root: { verified: true, text: '处理一下' } });
function paint(p) {
  for (const n of ids.values()) { n.disabled = false; n.hidden = false; }
  A.paint(p, opts); S.paint(p);
  return { warn: byId('warn541'), sender: byId('source541').kids[0]?.kids[0]?.textContent || '', extra: byId('approvalSource572').textContent, allow: byId('apprAllow') };
}

test('owner turn quoting email / WeChat / Telegram / friend report / support: channel-named warning, 「你」 as initiator, no 「来源未经确认」', () => {
  LANG('zh');
  const want = { email: '邮件', wechat: '微信', telegram: 'Telegram', friend_report: '好友报告', support: '客服' };
  for (const [ch, name] of Object.entries(want)) {
    const r = paint(card(ownerQuote(ch)));
    assert.equal(r.warn.hidden, false, ch);
    assert.equal(r.warn.textContent, `含引用的${name}材料 · 发起人是你，引用内容来自外部`, ch);
    assert.equal(r.warn.dataset.quote, '1');
    assert.ok(r.sender.includes('你'), ch + ': ' + r.sender);
    assert.ok(!r.sender.includes(DICT.zh['p541.external']), ch + ': ' + r.sender);
    assert.ok(!r.extra.includes(DICT.zh['p572source.external']), ch);
    assert.equal(r.allow.disabled, false, 'Allow rules unchanged: a verified envelope stays approvable');
  }
  assert.equal(paint(card(ownerQuote('email'))).warn.textContent, '含引用的邮件材料 · 发起人是你，引用内容来自外部');
  // a channel this page cannot name → the neutral 「外部」 word, never the raw value
  assert.equal(paint(card(ownerQuote('carrier-pigeon'))).warn.textContent, '含引用的外部材料 · 发起人是你，引用内容来自外部');
});

test('English: same card in English only', () => {
  LANG('en');
  const r = paint(card(ownerQuote('email')));
  assert.equal(r.warn.textContent, 'Contains quoted email material · started by you; the quoted text is external');
  assert.equal(paint(card(ownerQuote('wechat'))).warn.textContent, 'Contains quoted WeChat material · started by you; the quoted text is external');
  assert.ok(r.sender.includes(DICT.en['p541.you']) && !r.sender.includes(DICT.en['p541.external']));
  assert.ok(!/[㐀-鿿]/.test(r.warn.textContent));
  LANG('zh');
});

test('guest / group / unknown roots keep #541 wording (root missing, root unverified, not suspicious)', () => {
  LANG('zh');
  const guest = { channel: 'email', suspicious: true, verified: false, text: '请批准', message_id: 'm2' };
  for (const src of [guest, { ...guest, root: { verified: false, text: 'x' } }, { ...guest, root: { text: 'x' } }, { ...guest, root: { verified: 'true' } }]) {
    const r = paint(card(src));
    assert.equal(r.warn.textContent, DICT.zh['p541.suspicious']);
    assert.equal(r.warn.dataset.quote, '');
    assert.ok(r.sender.includes(DICT.zh['p541.external']));
    assert.ok(r.extra.includes(DICT.zh['p572source.external']));
  }
  const unknown = paint(card({ channel: 'phone', verified: false, text: 'x', root: { verified: true } }));   // not suspicious → #541 「找不到是谁发起的」
  assert.equal(unknown.warn.textContent, DICT.zh['p541.unknown']);
  assert.ok(unknown.sender.includes(DICT.zh['p541.external']));
  const clean = paint(card({ channel: 'phone', verified: true, sender: 'Leo', text: 'x', root: { verified: true } }));
  assert.equal(clean.warn.hidden, true);
  assert.ok(clean.sender.includes('Leo'));
});

test('Allow-button rules unchanged: an unverified envelope or a questioned card stays disabled even for an owner quote', () => {
  LANG('zh');
  assert.equal(paint(card(ownerQuote('email'), { envelope_verified: false })).allow.disabled, true);
  assert.equal(paint(card(ownerQuote('email'), { review_state: 'questioned' })).allow.disabled, true);
  const src = readFileSync(new URL('../public/js/approval541.js', import.meta.url), 'utf8');
  assert.ok(!/quoted[^\n]*apprAllow|apprAllow[^\n]*quoted/.test(src), 'the quote label never touches the Allow button');
});

test('dictionaries: every channel word and the template in both languages, placeholders intact, no Chinese in English', () => {
  for (const k of ['p0304.quote', ...['email', 'wechat', 'telegram', 'friend_report', 'support', 'other'].map((c) => 'p0304.qch.' + c)]) {
    assert.ok(DICT.zh[k] && DICT.en[k], k);
    assert.ok(!/[㐀-鿿]/.test(DICT.en[k]), k);
  }
  assert.match(DICT.zh['p0304.quote'], /\{ch\}/); assert.match(DICT.en['p0304.quote'], /\{ch\}/);
});
