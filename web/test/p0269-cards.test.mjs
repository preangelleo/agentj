// 0.26.9 #472 / §E6 + §E3 (lane w3-cards) — the pure and static half. The page itself (cards under the reply card at 390 / 1440,
// light / dark, zh / en, the budget row, Face ID on raise only) is web/test/p0269-cards.mjs.
//  · every host message in app.js's switch is declared: a card kind with a placement, or explicitly not a card — a new card kind
//    cannot ship without saying where it goes (parity entry `p0269-card-placement`)
//  · every dialog-shaped card root the modules / index.html build is declared, and each exception says why
//  · the class + why-scope line, the budget's pure parts, both dictionaries
import { test } from 'node:test';
import assert from 'node:assert/strict';
import { readFileSync, readdirSync } from 'node:fs';

globalThis.window ??= {};
globalThis.localStorage ??= { getItem() { return null; }, setItem() {}, removeItem() {} };
const CP = await import('../public/js/cardplace.js');
const BU = await import('../public/js/budget.js');
const read = (p) => readFileSync(new URL(p, import.meta.url), 'utf8');

// a tiny matcher for the registry's selectors: `#id`, `.a.b`, `.a:not(.b)`, comma lists
function matches(node, selector) {
  return selector.split(',').map((s) => s.trim()).some((sel) => {
    const nots = [...sel.matchAll(/:not\(([^)]+)\)/g)].map((m) => m[1]);
    const base = sel.replace(/:not\([^)]+\)/g, '');
    const ok = (part, n) => {
      const id = part.match(/#([\w-]+)/)?.[1], cls = [...part.matchAll(/\.([\w-]+)/g)].map((m) => m[1]);
      return (!id || n.id === id) && cls.every((c) => n.cls.includes(c));
    };
    return ok(base, node) && !nots.some((x) => ok(x, node));
  });
}
const kindOf = (node) => {
  let hit = null;
  for (const [kind, c] of Object.entries(CP.CARDS)) {
    if (!c.surface || !matches(node, c.surface)) continue;
    if (c.place === 'exception') return kind;
    hit = hit || kind;
  }
  return hit;
};

test('every host message in app.js onApp() is declared — a card kind with a placement, or not a card', () => {
  const src = read('../public/app.js');
  const body = src.slice(src.indexOf('function onApp(m)'), src.indexOf('default:', src.indexOf('function onApp(m)')));
  const types = [...body.matchAll(/case '([a-z0-9_]+)':/g)].map((m) => m[1]);
  assert.ok(types.length > 60, 'the switch was found (' + types.length + ')');
  const undeclared = types.filter((ty) => !CP.placementOfMsg(ty) && !CP.NOT_CARDS.includes(ty));
  assert.deepEqual(undeclared, [], 'declare these in cardplace.js CARDS[kind].msgs or NOT_CARDS');
  const seen = new Map();
  for (const [kind, c] of Object.entries(CP.CARDS)) for (const m of c.msgs) {
    assert.ok(!seen.has(m), `${m} declared twice (${seen.get(m)} and ${kind})`); seen.set(m, kind);
    assert.ok(!CP.NOT_CARDS.includes(m), `${m} is both a card and not a card`);
  }
  assert.ok(types.includes('budget_state'), 'the budget state reaches the page');
});

test('every kind declares a valid placement; pages and exceptions say why; reply kinds have a surface', () => {
  for (const [kind, c] of Object.entries(CP.CARDS)) {
    assert.ok(Object.values(CP.PLACE).includes(c.place), kind + ' placement');
    assert.ok(Array.isArray(c.msgs), kind + ' msgs');
    assert.ok(typeof c.was === 'string' && c.was, kind + ' old placement (inventory)');
    if (c.place !== 'reply') assert.ok(typeof c.why === 'string' && c.why.length > 20, kind + ' must say why it is not under the reply card');
    else assert.ok(typeof c.surface === 'string' && c.surface, kind + ' surface');
  }
  // only what the platform / the owner forces full screen is an exception
  assert.deepEqual(Object.entries(CP.CARDS).filter(([, c]) => c.place === 'exception').map(([k]) => k).sort(), ['faceid', 'offline', 'secret_full', 'version_notice']);
  assert.equal(CP.CARDS.faceid.surface, null, 'Face ID is drawn by the operating system');
  // the chairman's list (#472): approval, sudo/password, key, login, goal opening, system notices — all under the reply card
  for (const k of ['approval', 'question', 'sudo', 'secret_out', 'login_qr', 'login_sheet', 'goal_opening', 'confirm', 'notice', 'banner', 'contact_card', 'peer_approval', 'help_card'])
    assert.equal(CP.CARDS[k].place, 'reply', k);
  for (const sel of CP.DOCKABLE) assert.ok(Object.values(CP.CARDS).some((c) => c.place === 'reply' && c.surface.split(',').map((s) => s.trim()).some((s) => s === sel || s.startsWith(sel))), sel + ' is a reply card');
});

test('every dialog-shaped card root built by the modules or index.html is declared (new kinds fail)', () => {
  const roots = [];
  const dir = new URL('../public/js/', import.meta.url);
  for (const f of readdirSync(dir).filter((f) => f.endsWith('.js'))) {
    const s = readFileSync(new URL(f, dir), 'utf8');
    for (const m of s.matchAll(/mk\('div', '((?:ajmodal|gdlg)(?:\s[^']*)?)'\)/g)) {
      const after = s.slice(m.index, m.index + 160);
      if (/\.id = id\b/.test(after)) continue;                 // contacts.js shell(id): its callers are scanned below
      const id = after.match(/\.id = '([\w-]+)'/)?.[1] || '';
      roots.push({ where: f, cls: m[1].split(/\s+/), id });
    }
    for (const m of s.matchAll(/shell\('([\w-]+)'/g)) roots.push({ where: f, cls: ['gdlg', 'ct-dlg'], id: m[1] });
  }
  const html = read('../public/index.html');
  for (const m of html.matchAll(/<(?:div|section|aside)\b[^>]*\bid="([\w-]+)"[^>]*\bclass="([^"]*\b(?:ajmodal|sheet)\b[^"]*)"|<(?:div|section|aside)\b[^>]*\bclass="([^"]*\b(?:ajmodal|sheet)\b[^"]*)"[^>]*\bid="([\w-]+)"/g))
    roots.push({ where: 'index.html', id: m[1] || m[4], cls: (m[2] || m[3]).split(/\s+/) });
  assert.ok(roots.length >= 14, 'found the card roots (' + roots.length + ')');
  const skip = Object.keys(CP.NOT_CARD_SURFACES);
  const contactsForward = (r) => r.id === 'ctFwd';   // the owner's own 「转发到邮箱」 dialog, opened from the reply's row
  const missing = roots.filter((r) => !skip.some((s) => matches(r, s)) && !contactsForward(r) && !kindOf(r)).map((r) => `${r.where}: #${r.id} .${r.cls.join('.')}`);
  assert.deepEqual(missing, [], 'declare these card roots in cardplace.js CARDS');
  // the mail-server dialog shares .elev with the key card but is its own kind; a full-screen key reader is the exception
  assert.equal(kindOf({ id: 'mail-servers', cls: ['ajmodal', 'dlgcard', 'elev'] }), 'mail_servers');
  assert.equal(kindOf({ id: '', cls: ['ajmodal', 'dlgcard', 'sout', 'sout-full'] }), 'secret_full');
  assert.equal(kindOf({ id: '', cls: ['ajmodal', 'dlgcard', 'lt', 'lt-caps'] }), 'agent_caps');
});

test('the docked card is an in-flow block in the slot; the over-a-dialog toast ignores docked cards', () => {
  const css = read('../public/app.css'), ui = read('../public/js/ui.js'), ontop = read('../public/js/ontop.js'), app = read('../public/app.js');
  assert.match(css, /\.reply-notices>\.cp-docked\{position:relative;/);
  assert.match(css, /body\[data-sheet="1"\]:has\(\.reply-notices>#sheet\.cp-docked\) \.deck\{filter:none;opacity:1\}/, 'the reply is not blurred behind its own card');
  assert.match(ui, /\.ajmodal\.dlgcard:not\(\[hidden\]\):not\(\.cp-docked\),#confirm:not\(\[hidden\]\):not\(\.cp-docked\)/);
  assert.match(ontop, /cardSync\(\);/);
  assert.ok(app.indexOf('initOntop();') > 0 && app.indexOf('initCardPlace(t);') > app.indexOf('initOntop();'), 'cardplace starts right after ontop.js (P551 keeps initOntop right after initLayers)');
});

test('#472 ②: the card names its class and why the task scope does not cover it — only when the host sends it', () => {
  const tr = (k, v) => (v ? k + ':' + v.why : k);
  assert.deepEqual(CP.scopeText({ cls: 'spend_over_budget', why_scope: '超出今天的预算 ¥20' }, tr), { label: 'p0269c.cls.spend_over_budget', why: '超出今天的预算 ¥20' });
  assert.deepEqual(CP.scopeText({ cls: 'secret_reveal' }, tr), { label: 'p0269c.cls.secret_reveal', why: '' });
  // absent / unknown machine word / junk → nothing invented
  assert.deepEqual(CP.scopeText({}, tr), { label: '', why: '' });
  assert.deepEqual(CP.scopeText({ cls: 'weird_internal_code' }, tr), { label: '', why: '' });
  assert.deepEqual(CP.scopeText({ cls: 7, why_scope: { x: 1 } }, tr), { label: '', why: '' });
  assert.equal(CP.scopeText({ why_scope: 'a\nb c ' + 'x'.repeat(400) }, tr).why.length, 300, 'one line, bounded');
  for (const f of ['relay.js', 'elevate.js', 'secretout.js']) assert.match(read('../public/js/' + f), /paintScope\(/, f + ' paints the scope line');
  assert.match(read('../public/js/snap.js'), /cls: oneLine\(m\.cls, 60\), why_scope: oneLine\(m\.why_scope, 300\)/);
});

test('§E3 budget: parse, raise vs lower, spent never estimated', () => {
  assert.deepEqual(BU.parseState({ day: 20, month: 300, currency: 'CNY' }), { day: 20, month: 300, spent_day: null, spent_month: null, currency: 'CNY' });
  assert.deepEqual(BU.parseState({ day: null, month: 50, spent_day: 3.5, spent_month: 12, currency: 'usd' }), { day: null, month: 50, spent_day: 3.5, spent_month: 12, currency: '' });
  assert.equal(BU.parseState({ day: -1, month: 5 }), null);
  assert.equal(BU.parseState({ day: 'lots' }), null);
  assert.equal(BU.parseState(null), null);
  assert.equal(BU.money(null, 'CNY'), '—');
  assert.equal(BU.money(undefined, ''), '—');
  assert.equal(BU.money(12.345, ''), '12.35');
  assert.equal(BU.readField(''), 0); assert.equal(BU.readField(' 12.5 '), 12.5); assert.ok(Number.isNaN(BU.readField('-3'))); assert.ok(Number.isNaN(BU.readField('abc')));
  const cur = { day: 20, month: 300 };
  assert.deepEqual(BU.plan(cur, 20, 300), { target: {}, raise: false, changed: false });
  assert.deepEqual(BU.plan(cur, 10, 300), { target: { day: 10 }, raise: false, changed: true }, 'lowering: no Face ID');
  assert.deepEqual(BU.plan(cur, 10, 400), { target: { day: 10, month: 400 }, raise: true, changed: true }, 'any raise: Face ID');
  assert.deepEqual(BU.plan({ day: null, month: null }, 5, 0), { target: { day: 5 }, raise: true, changed: true }, 'no budget = nothing in budget: setting one raises');
  const src = read('../public/js/budget.js');
  assert.match(src, /signedWriteFa\('budget_set', p\.target, \{ t: 'budget_set', \.\.\.p\.target \}/, 'signed, only changed fields');
  assert.doesNotMatch(src, /spent_day\s*=\s*[^=]/, 'the page never computes spent');
});

test('dictionaries: every new key in zh and en with the same placeholders; glossary words', () => {
  const zh = JSON.parse(read('../i18n/web.zh.json')), en = JSON.parse(read('../i18n/web.en.json'));
  const src = JSON.parse(read('../i18n/web.zh.src.json')), ov = JSON.parse(read('../i18n/web.zh.override.json'));
  const keys = [...new Set([...read('../public/js/cardplace.js').matchAll(/'(p0269c\.[\w.]+)'/g), ...read('../public/js/budget.js').matchAll(/'(p0269c\.[\w.]+)'/g),
    ...read('../public/index.html').matchAll(/data-i18n="(p0269c\.[\w.]+)"/g)].map((m) => m[1]))];
  for (const c of CP.CLASSES) keys.push('p0269c.cls.' + c);
  keys.push('p0269c.ownerProposed');
  assert.match(read('../public/js/contacts.js'), /proposed: m\.mode === 'owner' && m\.proposed === 'agent'/, 'only the host marker, only on the owner card');
  for (const k of keys.filter((k) => !k.endsWith('.'))) {
    for (const [n, d] of [['zh', zh], ['en', en], ['src', src], ['override', ov]]) assert.ok(typeof d[k] === 'string' && d[k], `${n} ${k}`);
    const ph = (s) => [...s.matchAll(/\{(\w+)\}/g)].map((m) => m[1]).sort().join();
    assert.equal(ph(zh[k]), ph(en[k]), k + ' placeholders');
    assert.doesNotMatch(zh[k], /试用/); assert.doesNotMatch(en[k], /\btrial\b/i);
  }
});

test('security r4 F8: the host badge is built only by cardplace.js — no reply renderer can produce its class / marker', () => {
  const cp = read('../public/js/cardplace.js');
  assert.match(cp, /b\.className = 'cp-host'; b\.dataset\.cpHost = '1';/);
  assert.match(cp, /function dock\(node, slot\) \{\n  badge\(node\);/, 'every docked card gets the badge');
  const dir = new URL('../public/js/', import.meta.url);
  for (const f of readdirSync(dir).filter((f) => f.endsWith('.js') && f !== 'cardplace.js'))
    assert.doesNotMatch(readFileSync(new URL(f, dir), 'utf8'), /cp-host|cpHost|data-cp-host/, f + ' must not build the host badge');
  // md.js never copies a class / data attribute from the text it renders (only its own fixed class names)
  const md = read('../public/js/md.js');
  for (const m of md.matchAll(/\.className\s*=\s*([^;\n]+)/g)) {
    const rhs = m[1].replace(/"[^"]*"|'[^']*'/g, '').replace(/\/\/.*$/, '').replace(/\bnode\.display\b|[?:()\s]/g, '').replace(/\)+$/, '');
    assert.equal(rhs, '', 'md.js class names are literals only: ' + m[0]);
  }
  assert.doesNotMatch(md, /innerHTML\s*=/, 'md.js builds DOM nodes, never HTML strings');
});

