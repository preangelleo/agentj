// 0378 (chairman 11-26): the queue panel's 「清空」 + per-row removal (queue_drop, cap `queuedrop1`) and the plain words for an
// owner message that left the queue without being sent (card.dropped / card.undelivered). Node unit checks against a minimal
// DOM; the real page in Chromium against fakehost.mjs is test/p0378-queue-drop.mjs (run from the last test here).
import test from 'node:test';
import assert from 'node:assert/strict';
import {readFileSync, mkdtempSync, rmSync} from 'node:fs';
import {spawnSync} from 'node:child_process';
import {fileURLToPath} from 'node:url';
import {tmpdir} from 'node:os';
import {join} from 'node:path';

// ------------------------------------------------------------ a minimal DOM (only what owner-queue.js and its imports touch)
class El {
  constructor(tag){ this.tagName = String(tag).toUpperCase(); this.kids = []; this.attrs = {}; this.dataset = {}; this.hidden = false; this.disabled = false; this.on = {}; this._t = null; this.open = false; this.style = {};
    this.classList = {add(){}, remove(){}, toggle(){}, contains(){ return false; }}; }
  append(...n){ this.kids.push(...n); } appendChild(n){ this.kids.push(n); return n; } replaceChildren(...n){ this.kids = n; this._t = null; }
  setAttribute(k, v){ this.attrs[k] = String(v); } getAttribute(k){ return k in this.attrs ? this.attrs[k] : null; } removeAttribute(k){ delete this.attrs[k]; }
  addEventListener(e, f){ (this.on[e] ||= []).push(f); } removeEventListener(){}
  get textContent(){ return this._t !== null ? this._t : this.kids.map(k => k.textContent).join(''); } set textContent(v){ this.kids = []; this._t = String(v); }
  querySelector(){ return (this._q ||= new El('span')); } querySelectorAll(){ return []; }
  closest(){ return this.tagName === 'BUTTON' && 'queueDrop' in this.dataset ? this : null; }
  showModal(){ this.open = true; } close(){ this.open = false; for (const f of this.on.close || []) f({}); }
  getBoundingClientRect(){ return {left: 0, right: 0, top: 0, bottom: 0}; }
  fire(type, target = this){ for (const f of this.on[type] || []) f({target, clientX: 0, clientY: 0}); }
}
const ids = new Map();
const byId = id => { if (!ids.has(id)) ids.set(id, new El('div')); return ids.get(id); };
globalThis.addEventListener = () => {}; globalThis.removeEventListener = () => {}; globalThis.window = globalThis;
globalThis.document = {getElementById: byId, createElement: t => new El(t), createElementNS: (ns, t) => Object.assign(new El(t), {ns}), addEventListener(){}, removeEventListener(){},
  documentElement: new El('html'), body: new El('body'), querySelector: () => null, querySelectorAll: () => [], visibilityState: 'visible'};

const activity = await import('../public/js/phone-activity.js');
const panel = await import('../public/js/owner-queue.js');
const api = await import('../public/js/api.js');
const {peer} = await import('../public/js/session.js');
const read = p => readFileSync(new URL(p, import.meta.url), 'utf8');
const tick = () => new Promise(r => setTimeout(r, 0));

const owner = (card, end = 'open', kind = 'phone') => ({id: 7, source: {k: kind === 'terminal' ? 'host' : kind, source_kind: kind, source_role: 'owner', text: 'x'}, card, end, reply: ''});

test('0378 a dropped, undelivered or unconfirmed owner page leaves the queue and is named by one mapping', () => {
  assert.equal(activity.ownerQueue(owner({queued: true})), true, 'still queued');
  assert.equal(activity.queueGone(owner({queued: true})), null);
  const dropped = owner({dropped: true}, 'stopped');
  const lost = {...owner({undelivered: true}, 'failed'), reply: '这条消息没有送达主会话'};
  const unsure = {...owner({unconfirmed: true}, 'stopped'), reply: '主会话可能已经收到'};
  for (const [p, name] of [[dropped, 'dropped'], [lost, 'undelivered'], [unsure, 'unconfirmed']]) {
    assert.equal(activity.ownerQueue(p), false, name);
    assert.equal(activity.queueGone(p), name);
  }
  // a marker that arrives before the host cleared queued / held / end still takes the page out of the queue
  for (const card of [{queued: true, dropped: true}, {held: 'pending', dropped: true}, {held: {state: 'pending'}, undelivered: true}, {queued: true, undelivered: true},
    {held: 'pending', unconfirmed: true}, {queued: true, unconfirmed: true}])
    assert.equal(activity.ownerQueue(owner(card)), false, JSON.stringify(card));
  // only the exact markers on an ended owner page
  assert.equal(activity.queueGone(owner({dropped: true}, 'done')), null);
  assert.equal(activity.queueGone(owner({dropped: true}, 'failed')), null);
  assert.equal(activity.queueGone(owner({dropped: 'true'}, 'stopped')), null);
  assert.equal(activity.queueGone(owner({unconfirmed: 1}, 'stopped')), null);
  assert.equal(activity.queueGone(owner({withdrawn: true}, 'stopped')), null);
  assert.equal(activity.queueGone(owner(null, 'stopped')), null);
  // the host corrected the page: marker gone, open again → an ordinary turn (queued again only if the host says so)
  assert.equal(activity.queueGone({...unsure, card: null, end: 'open'}), null);
  assert.equal(activity.ownerQueue({...unsure, card: null, end: 'open'}), false);
  assert.equal(activity.queueGone({...unsure, end: 'open'}), null, 'an open page is never "gone"');
  assert.equal(activity.queueGone({...dropped, source: {k: 'sys', source_kind: 'system', source_role: 'owner'}}), null, 'not an owner input');
  assert.equal(activity.queueGone({...dropped, source: {k: 'telegram', source_kind: 'telegram', source_role: 'group'}}), null, 'a visitor');
  for (const kind of ['terminal', 'wechat', 'telegram', 'email']) assert.equal(activity.queueGone(owner({undelivered: true}, 'failed', kind)), 'undelivered', kind);
});

// ------------------------------------------------------------------------------------------------------------ the panel
const calls = {drop: [], stop: 0, report: [], refresh: 0};
let answer = () => Promise.resolve({ok: true, turns: [], skipped: []});
let rows = [];
const paint = () => panel.paintOwnerQueue(rows, true, false);
panel.initOwnerQueue(() => { calls.refresh++; paint(); }, () => { calls.stop++; }, text => calls.report.push(text), what => { calls.drop.push(what); return answer(what); });
const row = (turn, drop, text = 'words ' + turn) => ({turn, drop, wait: '', source: 'phone', time: '12:00', text});
const items = () => byId('ownerQueueList').kids;
const btnOf = li => li.kids.find(k => k.tagName === 'BUTTON') || null;
const openPanel = () => { byId('ownerQueueDialog').open = true; paint(); };

test('0378 panel: a row delete sends that page id; controls are disabled while it is in flight', async () => {
  rows = [row(11, false, 'the running turn'), row(12, true, '<b>second</b>'), row(13, true)];
  openPanel();
  assert.equal(items().length, 3);
  assert.equal(btnOf(items()[0]), null, 'the running turn row is not offered for deletion');
  const b = btnOf(items()[1]);
  assert.equal(b.dataset.queueDrop, '12'); assert.equal(b.attrs['aria-label'], '从排队中删除'); assert.equal(b.attrs.title, undefined); assert.equal(b.title, '从排队中删除'); assert.equal(b.attrs['data-aj-owner'], 'aj-reply-action');
  assert.equal(b.kids[0].tagName, 'SVG', 'a line icon, no text');
  const words = items()[1].kids.find(k => k.tagName === 'P');
  assert.equal(words.textContent, '<b>second</b>'); assert.equal(words.kids.length, 0, 'row text stays text');
  assert.equal(byId('ownerQueueClear').hidden, false);
  let done; answer = () => new Promise(r => { done = r; });
  byId('ownerQueueList').fire('click', b);
  await tick();
  assert.deepEqual(calls.drop, [[12]]);
  assert.equal(btnOf(items()[1]).disabled, true, 'the touched row');
  assert.equal(btnOf(items()[2]).disabled, false, 'another row stays usable');
  assert.equal(byId('ownerQueueClear').disabled, true);
  byId('ownerQueueList').fire('click', btnOf(items()[1]));
  await tick();
  assert.deepEqual(calls.drop, [[12]], 'no second frame for the same row');
  done({ok: true, turns: [12], skipped: []});
  await tick();
  assert.deepEqual(calls.report, [], 'success says nothing: the row leaves with the history update');
  assert.equal(byId('ownerQueueClear').disabled, false);
  assert.equal(calls.stop, 0);
});

test('0378 panel: 「清空」 sends all, disables every control, and never stops the running turn', async () => {
  calls.drop.length = 0; calls.report.length = 0;
  rows = [row(21, true), row(22, true)];
  openPanel();
  let done; answer = () => new Promise(r => { done = r; });
  byId('ownerQueueClear').fire('click');
  await tick();
  assert.deepEqual(calls.drop, ['all']);
  assert.deepEqual(items().map(li => btnOf(li).disabled), [true, true]);
  assert.equal(byId('ownerQueueClear').disabled, true);
  byId('ownerQueueList').fire('click', btnOf(items()[0]));
  await tick();
  assert.deepEqual(calls.drop, ['all'], 'nothing else goes out while 清空 is in flight');
  done({ok: true, turns: [21, 22], skipped: []});
  await tick();
  assert.equal(calls.stop, 0, '清空 is not 关闭上一条任务');
  assert.equal(byId('ownerQueueDialog').open, true, 'the panel closes with the empty list, not with the tap');
  rows = []; paint();
  assert.equal(byId('ownerQueueDialog').open, false);
  assert.deepEqual(calls.report, []);
});

test('0378 panel: a refusal, a skipped row and no answer give the control back with the failure line', async () => {
  for (const [r, what] of [[{ok: false, why: 'timeout'}, 'row'], [{ok: true, turns: [], skipped: [31]}, 'row'], [{ok: true, turns: [], skipped: [31, 32]}, 'all'], [{ok: false, why: 'offline'}, 'all']]) {
    calls.report.length = 0;
    rows = [row(31, true), row(32, true)];
    openPanel();
    answer = () => Promise.resolve(r);
    if (what === 'row') byId('ownerQueueList').fire('click', btnOf(items()[0])); else byId('ownerQueueClear').fire('click');
    await tick(); await tick();
    assert.deepEqual(calls.report, ['没删掉，请再试一次。'], JSON.stringify(r));
    assert.deepEqual(items().map(li => btnOf(li).disabled), [false, false]);
    assert.equal(byId('ownerQueueClear').disabled, false);
  }
  // 「清空」 that removed some and skipped a page already taken is not a failure
  calls.report.length = 0; answer = () => Promise.resolve({ok: true, turns: [31], skipped: [32]});
  openPanel(); byId('ownerQueueClear').fire('click'); await tick(); await tick();
  assert.deepEqual(calls.report, []);
});

test('0378 panel: without `queuedrop1` no row is offered and 「清空」 stays hidden', () => {
  rows = [row(41, false), row(42, false)];
  openPanel();
  assert.deepEqual(items().map(btnOf), [null, null]);
  assert.equal(byId('ownerQueueClear').hidden, true);
  // the gate itself: the computer's capability list, read once per session
  const relay = read('../public/js/relay.js'), session = read('../public/js/session.js');
  assert.match(relay, /const drop = C\.api\.canQueueDrop\?\.\(\) === true && Number\.isInteger\(q\.turn\) && q\.turn !== activeTurn/);
  assert.match(session, /peer\.queuedrop1 = peer\.p33 && Array\.isArray\(m\.caps\) && m\.caps\.includes\('queuedrop1'\)/);
  const was = {p33: peer.p33, q: peer.queuedrop1};
  try {
    peer.p33 = true; peer.queuedrop1 = false; assert.equal(api.canQueueDrop(), false);
    peer.queuedrop1 = true; assert.equal(api.canQueueDrop(), true);
    peer.p33 = false; assert.equal(api.canQueueDrop(), false);
  } finally { peer.p33 = was.p33; peer.queuedrop1 = was.q; }
});

test('0378 wire: the two frames, the answer route, and an unpaired page sends nothing', async () => {
  const src = read('../public/js/api.js');
  assert.match(src, /sendApp\(all \? \{ t: 'queue_drop', all: true \} : \{ t: 'queue_drop', turns: ids \}, g\)/);
  assert.equal(api.QUEUE_DROP_WAIT_MS, 8000);
  assert.deepEqual(await api.queueDrop('all'), {ok: false, why: 'offline'});
  assert.deepEqual(await api.queueDrop([3]), {ok: false, why: 'offline'});
  assert.equal(api.route({t: 'queue_dropped', turns: [3], skipped: []}), true, 'ours even when nobody waits');
  assert.equal(api.route({t: 'queue_dropped'}), true);
});

test('0378 words: both languages, every Chinese source, the page markup and the chat page mapping', () => {
  const want = {
    zh: {'p0319.clear': '清空', 'p0319.drop': '从排队中删除', 'p0319.dropFail': '没删掉，请再试一次。', 'p0319.dropped': '已从排队中删除，没有发送'},
    en: {'p0319.clear': 'Clear', 'p0319.drop': 'Remove from queue', 'p0319.dropFail': 'Could not remove it. Please try again.', 'p0319.dropped': 'Removed from the queue. Not sent.'},
  };
  for (const [file, lang] of [['web.zh.src.json', 'zh'], ['web.zh.json', 'zh'], ['web.zh.override.json', 'zh'], ['web.en.json', 'en']]) {
    const d = JSON.parse(read('../i18n/' + file));
    for (const [k, v] of Object.entries(want[lang])) assert.equal(d[k], v, file + ' ' + k);
    assert.equal('p0319.undelivered' in d, false, file + ': an undelivered page shows the host\'s own words');
  }
  assert.equal(JSON.parse(read('../i18n/web.zh.json'))['p0319.stop'], '关闭上一条任务', 'the existing button keeps its words');
  const html = read('../public/index.html');
  assert.equal(html.split('id="ownerQueueClear"').length, 2);
  assert.match(html, /<button type="button" id="ownerQueueClear" hidden data-i18n="p0319\.clear" data-aj-owner="aj-reply-action">清空<\/button>/);
  const relay = read('../public/js/relay.js');
  assert.match(relay, /if \(gone === "dropped" && !visibleReply\(p\)\) renderWords\(t\('p0319\.dropped'\), false, "", false, false, true\);/);
  assert.doesNotMatch(relay, /p0319\.undelivered/);
  assert.match(relay, /document\.body\.dataset\.workElsewhere = gone \|\| /, 'no working robot on a page that left the queue');
  assert.match(relay, /else if \(m\.s === "cancelled"\) m\.why === "dropped" \? dequeue\(m\.sid\) : withdrawn\(q\);/, 'a dropped cancel never refills the field');
  const css = read('../public/app.css');
  assert.match(css, /#ownerQueueList \[data-queue-drop\]\{[^}]*width:44px;height:44px/);
  assert.match(css, /#ownerQueueClear\{[^}]*min-height:44px/);
  const mine = css.split('\n').filter(l => /#ownerQueueClear|data-queue-drop|#ownerQueueDialog>header/.test(l)).join('\n');
  assert.doesNotMatch(mine, /#[0-9a-f]{3,8}\b|\bred\b|rgb\(/i, 'palette tokens only');
});

test('0378 the real page against the fake host: frames, rows leaving on every remote, older host, 390 / 1440 × light / dark × zh / en', {timeout: 280000}, () => {
  const out = mkdtempSync(join(tmpdir(), 'aj-0378-'));
  try {
    const r = spawnSync(process.execPath, [fileURLToPath(new URL('./p0378-queue-drop.mjs', import.meta.url))], {encoding: 'utf8', timeout: 270000, env: {...process.env, P0378_OUT: out, P0378_BEFORE: '', AJ_PARITY_OUT: join(out, 'parity.json')}});
    assert.equal(r.status, 0, r.stdout + '\n' + r.stderr);
    assert.match(r.stdout, /p0378 queue drop: PASS \(12 cases/);
  } finally { rmSync(out, {recursive: true, force: true}); }
});
