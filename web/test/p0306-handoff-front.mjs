// 0.30.6 · ADR-0306-friend-handoff-mail: the visible FRONT of an ordinary (#541 v2) approval card, painted by the real page
// renderers (approval541.paint + approval-source572.paint) over a minimal DOM. Driven by the host test
// host/tests/test_0306_friend_handoff_mail.py with the REAL host `ask` frame:
//   node p0306-handoff-front.mjs < [{"card": <ask frame>, "lang": "zh"|"en"}, …]   →   [{warn, sender, text, reason, extra, title}, …]
// `warn` is '' when the warning line is hidden. Display only; the page's Allow rules are reported as `allowDisabled`.
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

export function front(card, lang) {
  window.AJLang = { get: () => lang, t: (d, k, v) => fmt(d[lang][k] ?? k, v), onChange() {} };
  ids.clear();
  // The page marks the envelope verified after checking the host signature; this helper reads the display only.
  const p = { ...card, envelope_verified: true, review_state: card.review_state || 'pending' };
  A.paint(p, { busy: false, fold() {}, open() {}, locate() {} }); S.paint(p);
  const src = byId('source541'), warn = byId('warn541'), extra = byId('approvalSource572');
  return { warn: warn.hidden ? '' : warn.textContent, sender: src.kids[0]?.kids[0]?.textContent || '', text: src.kids[1]?.textContent || '',
    reason: byId('action541').textContent, extra: extra.hidden ? '' : extra.textContent, title: byId('sheetTitle').textContent,
    allowDisabled: byId('apprAllow').disabled, dict: { external: DICT[lang]['p572source.external'], suspicious: DICT[lang]['p541.suspicious'],
      unknown: DICT[lang]['p541.unknown'], outsider: DICT[lang]['p541.external'], missing: DICT[lang]['p0287.reason.missingFields'] } };
}

if (import.meta.url === `file://${process.argv[1]}`) {
  let s = '';
  process.stdin.on('data', (d) => { s += d; });
  process.stdin.on('end', () => { console.log(JSON.stringify(JSON.parse(s).map((x) => front(x.card, x.lang)))); });
}
