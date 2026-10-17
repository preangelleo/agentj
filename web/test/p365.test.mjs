// P365 `agentj notify`: the owner's own scripts / timers post a page to the phone. The page side: snap.js keeps the new
// source kind `auto` (+ its name) and an Agent turn's `via: "auto"`; relay.js sourceOf labels them (the quiet system colour /
// the Agent's colour); Settings → 自动化消息 (js/notify.js) renders a `notify_list`, sends `notify_set` and is hidden on an older
// host. Real browser: p365.mjs.
import { test } from 'node:test';
import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import vm from 'node:vm';

globalThis.window ??= globalThis;
const { toPage } = await import('../public/js/snap.js');
const read = (p) => readFileSync(new URL(p, import.meta.url), 'utf8');
const relay = read('../public/js/relay.js'), settings = read('../public/js/settings.js'), html = read('../public/index.html');
const en = JSON.parse(read('../i18n/web.en.json')), zh = JSON.parse(read('../i18n/web.zh.json'));
const over = JSON.parse(read('../i18n/web.zh.override.json'));

test('P365 toPage keeps k "auto" with its name; an Agent turn keeps via "auto"; other kinds unchanged', () => {
  const a = toPage({ id: 3, ts: 1000, src: { k: 'auto', name: 'wechat-login-push', text: '' },
    reply: { text: '**视频号登录已掉线**\n\n请在 10 分钟内扫码', media: [] }, end: 'done' });
  assert.equal(a.source.k, 'auto');
  assert.equal(a.source.name, 'wechat-login-push');
  assert.equal(a.source.via, undefined);
  assert.match(a.reply, /视频号登录已掉线/);
  const g = toPage({ id: 4, src: { k: 'agent', via: 'auto', name: 'wechat-login-push', text: '' }, reply: { text: '已记下' }, end: 'done' });
  assert.equal(g.source.k, 'agent');
  assert.equal(g.source.via, 'auto');
  assert.equal(g.source.name, 'wechat-login-push');
  assert.equal(toPage({ id: 5, src: { k: 'phone', via: 'auto' }, reply: { text: 'x' } }).source.via, undefined, 'via only on agent');
  assert.equal(toPage({ id: 6, src: { k: 'nonsense' }, reply: { text: 'x' } }).source.k, 'sys', 'unknown kinds stay sys');
  assert.equal(toPage({ id: 7, src: { k: 'agent', via: 'loop' }, reply: { text: 'x' } }).source.via, 'loop');
});

test('P365 history search (hist_find) keeps via "auto" too', () => {
  const snap = read('../public/js/snap.js');
  assert.match(snap, /src\.k === 'agent' && \['loop', 'task', 'quota', 'auto', 'hook'\]\.includes\(src\.via\) \? \{ via: src\.via \}/);
});

function sourceOf(lang = 'zh') {
  const body = relay.slice(relay.indexOf('function sourceOf(src){'), relay.indexOf('function showPage(){'));
  const D = lang === 'en' ? en : zh;
  const flat = (k) => k.split('.').reduce((v, p) => (v && typeof v === 'object' ? v[p] : undefined), D) ?? D[k] ?? k;
  const t = (k, v) => String(flat(k)).replace(/\{(\w+)\}/g, (m, n) => (v && n in v ? String(v[n]) : m));
  const c = { t, C: { myDev: () => 'me', agentName: () => 'Agent J' }, deviceInfo: () => null };
  vm.runInNewContext(body + '\nthis.sourceOf = sourceOf;', c);
  return c.sourceOf;
}

test('P365 sourceOf: auto → quiet system colour, 「来自你的自动化：name」; agent via auto → 「关于自动化：name」', () => {
  const zs = sourceOf('zh'), es = sourceOf('en');
  assert.deepEqual({ ...zs({ k: 'auto', name: 'wechat-login-push' }) }, { kind: 'sys', label: '来自你的自动化：wechat-login-push' });
  assert.deepEqual({ ...zs({ k: 'agent', via: 'auto', name: 'backup' }) }, { kind: 'agent', label: '关于自动化：backup' });
  assert.deepEqual({ ...es({ k: 'auto', name: 'backup' }) }, { kind: 'sys', label: 'From your automation: backup' });
  assert.deepEqual({ ...es({ k: 'agent', via: 'auto', name: 'backup' }) }, { kind: 'agent', label: 'About your automation: backup' });
  assert.equal(zs({ k: 'sys' }).kind, 'sys', 'plain system pages unchanged');
});

test('P365 an automation page renders without TeX (like sys) and ends 「撤销清空」 like any new talk', () => {
  assert.match(relay, /const sysPage = \(p\) => !!\(p && p\.source && \(p\.source\.k === "sys" \|\| p\.source\.k === "auto"\)\);/);
  assert.match(relay, /const NEW_TALK = new Set\(\["phone", "telegram", "host", "agent", "auto"\]\)/);
});

test('P365 dictionary: same keys and placeholders in both languages; the owner-given Chinese wins via override', () => {
  const keys = Object.keys(en).filter((k) => k.startsWith('p365.'));
  assert.ok(keys.length >= 20);
  for (const k of keys) {
    assert.ok(typeof zh[k] === 'string' && zh[k], k);
    const ph = (s) => (s.match(/\{\w+\}/g) || []).sort().join();
    assert.equal(ph(zh[k]), ph(en[k]), k);
  }
  assert.equal(over['p365.src.auto'], '来自你的自动化：{name}');
  assert.equal(over['p365.src.about'], '关于自动化：{name}');
  assert.equal(over['p365.nav'], '自动化消息');
  assert.equal(zh['p365.empty'], '还没有登记的自动化。在电脑终端运行 `agentj notify --allow <名字>` 登记；脚本用 `agentj notify --from <名字> --text …` 发消息。没登记或开关关着的会被拒绝，不会弹窗问你。');
});

test('P365 Settings wiring: entry + sub-page in index.html, pref key, sub-view, hidden unless settable lists notify.enabled', () => {
  for (const id of ['set-notify-nav', 'set-notify', 'ntf-on', 'ntf-msg', 'ntf-autos', 'ntf-autos-empty', 'ntf-how', 'ntf-tasks-h', 'ntf-tasks', 'ntf-off'])
    assert.match(html, new RegExp(`id="${id}"`), id);
  assert.match(html, /id="set-notify-nav" class="setnav" hidden/);
  assert.match(settings, /PREF_KEYS\.push\(ntf\.PREF_KEY\)/);
  assert.match(settings, /el\('set-notify-nav'\)\.hidden = !ntf\.available\(\)/);
  assert.match(settings, /notify: 'p365\.nav'/);
  assert.match(settings, /if \(v === 'notify'\) ntf\.load\(\)/);
});

// ---------------------------------------------------------------- notify.js against a tiny DOM
class Node {
  constructor(tag) { this.tagName = tag.toUpperCase(); this.children = []; this.attrs = {}; this.dataset = {}; this.hidden = false; this.disabled = false; this._ev = {}; this.textContent = ''; this.className = ''; }
  append(...n) { this.children.push(...n); }
  replaceChildren(...n) { this.children = n; }
  setAttribute(k, v) { this.attrs[k] = String(v); }
  getAttribute(k) { return k in this.attrs ? this.attrs[k] : null; }
  addEventListener(e, f) { (this._ev[e] ||= []).push(f); }
  click() { for (const f of this._ev.click || []) f(); }
  all(pred, out = []) { for (const c of this.children) { if (pred(c)) out.push(c); c.all(pred, out); } return out; }
}
globalThis.document = { createElement: (tag) => new Node(tag) };
const ntf = await import('../public/js/notify.js');

function harness({ settable = ['notify.enabled'], answers = [], confirm = true, enabled } = {}) {
  const els = {};
  const el = (id) => (els[id] ||= new Node('div'));
  const sent = [], prefs = [];
  ntf._reset();
  ntf.configure({
    el, ready: () => true, settable: (k) => settable.includes(k),
    pget: (k, d) => (k === 'notify.enabled' && enabled !== undefined ? enabled : d),
    setPref: async (k, v) => { prefs.push([k, v]); return true; },
    ask: async (obj, want) => { sent.push({ obj, want }); return answers.shift() ?? { t: 'timeout' }; },
    confirm: async () => confirm, explain: () => {},
  });
  return { el, sent, prefs };
}
const LIST = { t: 'notify_list', r: 'x', ok: true, enabled: true,
  automations: [{ id: 'wechat-login-push', allow: true }, { id: 'task.bad', allow: true }, { id: '<b>', allow: true }],
  tasks: [{ id: 'daily-report', title: { zh: '每日报告', en: 'Daily report' }, declared: true, allow: false }] };

test('P365 notify.js: hidden on an older host (settable without notify.enabled)', () => {
  harness({ settable: ['heartbeat.enabled'] });
  assert.equal(ntf.available(), false);
  harness();
  assert.equal(ntf.available(), true);
});

test('P365 notify.js: load = notify_get; a notify_list paints one script row on and one task row off (with the override note)', async () => {
  const h = harness({ answers: [LIST] });
  assert.equal(await ntf.load(), true);
  assert.deepEqual(h.sent[0], { obj: { t: 'notify_get' }, want: 'notify_list' });
  const autos = h.el('ntf-autos').children, tasks = h.el('ntf-tasks').children;
  assert.equal(autos.length, 1, 'task.* and malformed names are not script rows');
  assert.equal(autos[0].dataset.id, 'wechat-login-push');
  const tg = (r) => r.all((n) => n.className === 'tgl')[0];
  assert.equal(tg(autos[0]).getAttribute('aria-checked'), 'true');
  assert.equal(tasks.length, 1);
  assert.equal(tasks[0].all((n) => n.className.includes('ntfname'))[0].textContent, '每日报告');
  assert.equal(tg(tasks[0]).getAttribute('aria-checked'), 'false');
  assert.equal(tasks[0].all((n) => n.className.includes('ntfrm')).length, 0, 'tasks cannot be removed');
  assert.ok(tasks[0].all((n) => n.className.includes('ntfnote'))[0].textContent, 'override of the declaration noted');
  assert.equal(h.el('ntf-autos-empty').hidden, true);
  assert.equal(h.el('ntf-how').hidden, false);
  assert.equal(h.el('ntf-on').getAttribute('aria-checked'), 'true');
});

test('P365 notify.js: a script switch → notify_set {id, allow:false}; a task switch → id "task.<id>"', async () => {
  const off = { ...LIST, automations: [{ id: 'wechat-login-push', allow: false }] };
  const h = harness({ answers: [LIST, off, LIST] });
  await ntf.load();
  h.el('ntf-autos').children[0].all((n) => n.className === 'tgl')[0].click();
  await new Promise((r) => setTimeout(r, 0));
  assert.deepEqual(h.sent[1].obj, { t: 'notify_set', id: 'wechat-login-push', allow: false });
  assert.equal(h.sent[1].want, 'notify_list');
  assert.equal(h.el('ntf-autos').children[0].all((n) => n.className === 'tgl')[0].getAttribute('aria-checked'), 'false', 'repainted from the answer');
  h.el('ntf-tasks').children[0].all((n) => n.className === 'tgl')[0].click();
  await new Promise((r) => setTimeout(r, 0));
  assert.deepEqual(h.sent[2].obj, { t: 'notify_set', id: 'task.daily-report', allow: true });
});

test('P365 notify.js: 移除 asks first, then sends allow:null; a cancelled confirm sends nothing', async () => {
  const h = harness({ answers: [LIST, { ...LIST, automations: [] }] });
  await ntf.load();
  h.el('ntf-autos').children[0].all((n) => n.className.includes('ntfrm'))[0].click();
  await new Promise((r) => setTimeout(r, 0));
  assert.deepEqual(h.sent[1].obj, { t: 'notify_set', id: 'wechat-login-push', allow: null });
  assert.equal(h.el('ntf-autos').children.length, 0);
  assert.equal(h.el('ntf-autos-empty').hidden, false, 'empty state');
  assert.equal(h.el('ntf-how').hidden, true);
  const h2 = harness({ answers: [LIST], confirm: false });
  await ntf.load();
  assert.equal(await ntf.remove('wechat-login-push'), false);
  assert.equal(h2.sent.length, 1);
});

test('P365 notify.js: refused / no answer keeps the last list and says why', async () => {
  const h = harness({ answers: [LIST, { t: 'notify_list', r: 'x', ok: false, why: 'rejected' }] });
  await ntf.load();
  assert.equal(await ntf.set('wechat-login-push', false), false);
  assert.equal(h.el('ntf-msg').textContent, '电脑没能保存这个设置。');
  assert.equal(h.el('ntf-autos').children[0].all((n) => n.className === 'tgl')[0].getAttribute('aria-checked'), 'true', 'back to the saved state');
  assert.equal(ntf.failText({ t: 'timeout' }), '电脑没有回应，请稍后再试。');
  assert.equal(ntf.failText({ t: 'notify_list', ok: false, why: 'shape' }), '电脑没有接受这个请求。');
});

test('P365 notify.js: the master switch is the notify.enabled preference (setPref), shown from the broadcast', async () => {
  const h = harness({ answers: [LIST], enabled: false });
  await ntf.load();
  assert.equal(h.el('ntf-on').getAttribute('aria-checked'), 'false');
  assert.equal(h.el('ntf-off').hidden, false);
  h.el('ntf-on').click();
  await new Promise((r) => setTimeout(r, 0));
  assert.deepEqual(h.prefs, [['notify.enabled', true]]);
  assert.deepEqual(ntf.setMessage('a', null), { t: 'notify_set', id: 'a', allow: null });
});
