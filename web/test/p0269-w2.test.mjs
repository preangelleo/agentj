// 0.26.9 selfheal wave 2, lane w2-web (CONTRACT §B3 / §D, security L-2): the pure parts, in node (web-unit).
//   · #471 the card split (js/turnsplit.js): a running turn the owner talked past → head + continuation segment(s)
//   · #468 the assist task feed (js/assist.js helpers, lifted into a vm) and the 「官方远程协助」 source (js/assistsrc.js)
//   · L-2 the terminal's link handler (js/termlink.js): https only, no confirm(), no other scheme
// The pages themselves: test/p0269-split.mjs (cards + steered + merged queued notice), test/p0269-assist.mjs (feed).
import { test } from 'node:test';
import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import vm from 'node:vm';
import { createSplit, pageKey, contIndex, contOfText } from '../public/js/turnsplit.js';
import { isAssistSrc, assistCarry } from '../public/js/assistsrc.js';
import { httpsOnly, linkHandler } from '../public/js/termlink.js';
import * as SH from '../public/js/selfheal.js';

const read = (p) => readFileSync(new URL(p, import.meta.url), 'utf8');
const owner = (id, text = 'next please') => ({ id, source: { k: 'phone', dev: 'd1', text, att: [] }, reply: '', end: 'open', card: null });
const agentTurn = (id, reply, end = 'open', text = 'build the report') => ({ id, source: { k: 'phone', dev: 'd1', text, att: [] }, reply, end, card: { progress: 'x' }, media: [{ mid: 'm1' }] });
const vis = (p) => !!(p.reply || '').trim() || !!(p.source?.text || '').trim();

test('#471 no owner message after the turn → nothing changes (negative control)', () => {
  const s = createSplit();
  const turns = [agentTurn(1, 'step 1')];
  s.note(turns);
  assert.deepEqual(s.layout(turns.filter(vis)), turns);
  // a finished turn is never split, even with a later message
  const done = [agentTurn(1, 'all done', 'done'), owner(2)];
  s.note(done);
  assert.equal(s.layout(done).length, 2);
  assert.equal(s.layout(done)[0].cont, undefined);
  // an open turn with no visible reply yet is not a live reply card: no split
  const quiet = [agentTurn(5, ''), owner(6)];
  s.note(quiet);
  assert.equal(s._cuts().has(5), false);
});

test('#471 owner speaks after a live reply card → later output of T goes to a new card at the bottom', () => {
  const s = createSplit();
  const T = agentTurn(10, 'step 1\n');
  s.note([T]);
  const M = owner(11);
  s.note([T, M]);                                  // the owner's message arrives: the cut sits at "step 1\n"
  // before T says anything more: no empty card, T's page unchanged
  let ps = s.layout([T, M]);
  assert.deepEqual(ps.map(pageKey), ['10', '11']);
  assert.equal(ps[0].cont, undefined);
  // T keeps streaming
  const T2 = { ...T, reply: 'step 1\nstep 2\n' };
  s.note([T2, M]);
  ps = s.layout([T2, M]);
  assert.deepEqual(ps.map(pageKey), ['10', '11', '10~1'], 'head, the owner message, the continuation at the bottom');
  assert.equal(ps[0].reply, 'step 1\n', 'the old card keeps only what it showed');
  assert.equal(ps[0].end, 'done', 'the old card is closed (no working dots)');
  assert.deepEqual(ps[0].cont, { id: 10, seg: 1 });
  assert.equal(ps[0].card, null); assert.deepEqual(ps[0].media, []);
  assert.equal(ps[2].reply, 'step 2\n');
  assert.equal(ps[2].id, 10, 'same turn id'); assert.equal(ps[2].seg, 1);
  assert.equal(ps[2].end, 'open', 'the continuation is the live one');
  assert.deepEqual(ps[2].card, { progress: 'x' }); assert.equal(ps[2].media.length, 1);
  assert.equal(ps[2].source.text, '', 'the continuation has no owner words of its own');
  assert.equal(contOfText(ps[2].source), 'build the report');
  assert.equal(contIndex(ps, ps[0]), 2);
  // T ends: still in the continuation, the head unchanged
  const T3 = { ...T, reply: 'step 1\nstep 2\nfinished.', end: 'done' };
  s.note([T3, M]);
  ps = s.layout([T3, M]);
  assert.equal(ps[0].reply, 'step 1\n'); assert.equal(ps[2].reply, 'step 2\nfinished.'); assert.equal(ps[2].end, 'done');
});

test('#471 the queued message answered after T → T\'s rest sits above it, the answer stays newest', () => {
  const s = createSplit();
  const T = agentTurn(20, 'a');
  const M = owner(21);
  s.note([T, M]);
  const Tend = { ...T, reply: 'a b', end: 'done' };
  const Mans = { ...M, reply: 'answer to M', end: 'done' };
  s.note([Tend, Mans]);
  const ps = s.layout([Tend, Mans]);
  assert.deepEqual(ps.map(pageKey), ['20', '20~1', '21']);
});

test('#471 two owner messages, no output between → one continuation below the newest; output between → two', () => {
  const s = createSplit();
  const T = agentTurn(30, 'one');
  s.note([T, owner(31)]);
  s.note([T, owner(31), owner(32, 'and this')]);
  let T2 = { ...T, reply: 'one two' };
  let ps = s.layout([T2, owner(31), owner(32)]);
  assert.deepEqual(ps.map(pageKey), ['30', '31', '32', '30~1'], 'no empty card between the two messages');
  const s2 = createSplit();
  s2.note([T, owner(31)]);
  T2 = { ...T, reply: 'one two' };
  s2.note([T2, owner(31), owner(32)]);
  const T3 = { ...T, reply: 'one two three' };
  ps = s2.layout([T3, owner(31), owner(32)]);
  assert.deepEqual(ps.map(pageKey), ['30', '31', '30~1', '32', '30~2']);
  assert.deepEqual(ps.map((p) => p.reply), ['one', '', ' two', '', ' three']);
  assert.deepEqual(ps[2].cont, { id: 30, seg: 2 }, 'a middle card points on to the next');
  assert.equal(ps[2].end, 'done');
});

test('#471 a rewritten reply loses nothing: the head keeps its words, the continuation shows the new text whole', () => {
  const s = createSplit();
  const T = agentTurn(40, 'draft text');
  s.note([T, owner(41)]);
  const R = { ...T, reply: 'Final answer, rewritten.' };
  const ps = s.layout([R, owner(41)]);
  assert.equal(ps[0].reply, 'draft text');
  assert.equal(ps.at(-1).reply, 'Final answer, rewritten.');
});

test('#471 only owner messages cut (an agent / system page does not); reset forgets', () => {
  const s = createSplit();
  const T = agentTurn(50, 'x');
  const sys = { id: 51, source: { k: 'sys', text: '' }, reply: 'Codex is answering…', end: 'done', card: { queued: true } };
  s.note([T, sys]);
  assert.equal(s._cuts().size, 0);
  s.note([T, sys, { ...owner(52), source: { k: 'telegram', text: 'from tg', att: [] } }]);
  assert.equal(s._cuts().get(50)[0].after, 52);
  s.reset();
  assert.equal(s._cuts().size, 0);
});

test('#471 merged queued notice words + the key the pager pins to', () => {
  assert.deepEqual(SH.prevText(30), ['p0269.queue.prevWaitedLess', {}]);
  assert.deepEqual(SH.prevText(185), ['p0269.queue.prevWaited', { n: 3 }]);
  assert.equal(pageKey({ id: 7 }), '7'); assert.equal(pageKey({ id: 7, seg: 2 }), '7~2'); assert.equal(pageKey({ id: null }), 'live');
  const R = read('../public/js/relay.js'), CH = read('../public/js/chatheal.js');
  assert.match(R, /const pinned = ps\.findIndex\(p => pageKey\(p\) === oldId\);/, 'the pager pins continuation cards by their own key');
  assert.match(R, /export function onSteered\(m\)/);
  assert.match(R, /function queuedTurn\(id\)\{ return Number\.isInteger\(id\) && !steered\.has\(id\)/, 'steered = never queued');
  assert.match(CH, /t\('p481\.stopPrevious'\)/, '「停下上一件」');
  assert.match(CH, /if \(signed\) \{ endPage = p\.id; endTurn\(\); \} else H\.stop\?\.\(\);/, 'signed end_turn, /stop only on an older host');
  assert.match(read('../public/app.js'), /case 'msg_steered': relay\.onSteered\(m\); return;/);
});

// ---------------------------------------------------------------- #468 the task feed
const AS = read('../public/js/assist.js');
function helpers() {
  const pick = (name) => {
    const i = AS.indexOf(`export function ${name}(`);
    if (i >= 0) { const eol = AS.indexOf('\n', i), line = AS.slice(i, eol); return line.trimEnd().endsWith('}') ? line.slice(7) : AS.slice(i + 7, AS.indexOf('\n}\n', i) + 2); }
    const m = AS.match(new RegExp(`export const ${name} = ([^\\n]+);`));
    assert.ok(m, name);
    return `const ${name} = ${m[1]};`;
  };
  const consts = ['TASK_MAX', 'TEXT_MAX', 'STATES', 'TASK_WHY'].map(pick).join('\n');
  const src = [consts, 'const ID = /^[A-Za-z0-9._:-]{1,64}$/;', isAssistSrc.toString().replace(/^/, 'const isAssistSrc = '),
    "const KIND = 'official_assist';", ...['untilMs', 'parseState', 'parseTask', 'upsertTask', 'turnOf', 'replyText', 'whyKey'].map(pick)].join('\n');
  const box = {};
  vm.runInNewContext(src.replace('const isAssistSrc = (src) =>', 'const isAssistSrc = (src) =>') + '\nObject.assign(globalThis, {TASK_MAX, STATES, TASK_WHY, parseState, parseTask, upsertTask, turnOf, replyText, whyKey});', box);
  return box;
}
const plain = (x) => JSON.parse(JSON.stringify(x));

test('#468 assist_state drives the banner: tasks [{id, state}], junk dropped; inactive carries none', () => {
  const h = helpers(), now = 1_800_000_000_000;
  const st = h.parseState({ active: true, until: (now + 600000) / 1000, id: 'g1', key_id: 'aj-assist-2026a', tasks: [{ id: 't1', state: 'running' }, { id: 'bad id', state: 'done' }, { id: 't2', state: 'hacked' }] }, now);
  assert.deepEqual(plain(st), { active: true, until: now + 600000, key_id: 'aj-assist-2026a', id: 'g1', tasks: [{ id: 't1', state: 'running' }] });
  assert.deepEqual(plain(h.parseState({ active: false, until: (now + 600000) / 1000, tasks: [{ id: 't1', state: 'done' }] }, now).tasks), []);
  assert.equal(h.parseState({ active: true, until: (now - 1) / 1000 }, now).active, false);
});

test('#468 assist_task: id + known state required; words cleaned and capped; why is a code', () => {
  const h = helpers();
  const t = h.parseTask({ id: 't1', ts: 1_800_000_000, text: '  check the disk\u0007 space  ', state: 'queued' });
  assert.deepEqual(plain(t), { id: 't1', ts: 1_800_000_000_000, text: 'check the disk  space', state: 'queued', why: '' });
  assert.equal(h.parseTask({ id: 't1', text: 'x', state: 'deleted' }), null);
  assert.equal(h.parseTask({ id: '<b>', text: 'x', state: 'done' }), null);
  assert.equal(h.parseTask({ id: 't1', state: 'refused', why: 'Not A Code!' }).why, '');
  assert.equal(h.parseTask({ id: 't1', state: 'refused', why: 'admission_filter' }).why, 'admission_filter');
  assert.equal([...h.parseTask({ id: 't9', state: 'done', text: 'y'.repeat(9000) }).text].length, h.TASK_MAX * 0 + 4000);
});

test('#468 the feed: a later frame moves the state and keeps the words; bounded; newest last', () => {
  const h = helpers(), m = new Map();
  h.upsertTask(m, { id: 'a', ts: 1, text: 'first', state: 'queued', why: '' });
  h.upsertTask(m, { id: 'a', ts: 0, text: '', state: 'running', why: '' });
  assert.deepEqual(plain(m.get('a')), { id: 'a', ts: 1, text: 'first', state: 'running', why: '' });
  for (let i = 0; i < 60; i++) h.upsertTask(m, { id: 'x' + i, ts: 2, text: 't', state: 'done', why: '' }, 50);
  assert.equal(m.size, 50); assert.equal([...m.keys()].at(-1), 'x59'); assert.equal(m.has('a'), false);
});

test('#468 every why code the host sends (host/agentj/assist.py) gets its own plain line; unknown → per-state fallback', () => {
  const h = helpers();
  const host = { refused: ['ended', 'length', 'admission_filter', 'busy', 'quota', 'main_unavailable', 'submit_failed'], stopped: ['ended', 'interrupted'], done: ['failed', 'outbound_gate', 'not_sent'] };
  for (const [state, codes] of Object.entries(host)) for (const why of codes) {
    const k = h.whyKey({ state, why });
    assert.notEqual(k, 'p0269.assist.twhy.other', state + ' ' + why); assert.notEqual(k, 'p0269.assist.twhy.otherDone', state + ' ' + why);
  }
  assert.equal(h.whyKey({ state: 'refused', why: 'ended' }), 'p0269.assist.twhy.ended');
  assert.equal(h.whyKey({ state: 'stopped', why: 'ended' }), 'p0269.assist.twhy.endedStop', 'ended mid-task reads differently');
  assert.equal(h.whyKey({ state: 'refused', why: 'made_up' }), 'p0269.assist.twhy.other');
  assert.equal(h.whyKey({ state: 'done', why: 'made_up' }), 'p0269.assist.twhy.otherDone');
  const AS2 = read('../public/js/assist.js');
  assert.match(AS2, /if \(m\.what === 'refused' && typeof m\.why === 'string'.*toast\(whyText\(m\.why\)/, 'a refused-grant notice says the grant reason');
  for (const w of ['shape', 'io', 'passkey_failed', 'passkey_required', 'estop', 'unavailable', 'no_passkey', 'busy', 'bad_signature', 'stale', 'replay', 'no_key'])
    assert.match(AS2.match(/const WHY = \[[^\]]+\]/)[0], new RegExp(`'${w}'`), 'grant refusal ' + w);
});

test('#468 a task\'s reply comes from its turn in the normal chat (task id first, else its words)', () => {
  const h = helpers();
  const turns = [
    { id: 1, ts: 100, source: { k: 'phone', text: 'check the disk' }, reply: 'not this one' },
    { id: 2, ts: 100, source: { k: 'official_assist', text: '[Agent J official remote assist] check the disk', assist_id: 'other' }, reply: 'older' },
    { id: 3, ts: 101, source: { k: 'task', via: 'official_assist', text: 'check the disk' }, reply: 'Disk: 40% used' },
  ];
  assert.equal(h.turnOf({ id: 't1', text: 'check the disk', ts: 100500 }, turns).id, 3, 'by words, assist turns only, newest');
  assert.equal(h.turnOf({ id: 'other', text: 'zzz' }, turns).id, 2, 'by the host task id');
  assert.equal(h.turnOf({ id: 'none', text: 'never said' }, turns), null);
  assert.equal(h.replyText(turns[2]), 'Disk: 40% used');
});

test('#468 「官方远程协助」 source: own kind or task via official_assist; snap keeps it; chat label', () => {
  assert.equal(isAssistSrc({ k: 'official_assist' }), true);
  assert.equal(isAssistSrc({ k: 'task', via: 'official_assist' }), true);
  for (const s of [{ k: 'task' }, { k: 'task', via: 'same-owner' }, { k: 'phone', via: 'official_assist' }, null]) assert.equal(isAssistSrc(s), false, JSON.stringify(s));
  assert.deepEqual(assistCarry({ k: 'task', via: 'official_assist', assist_id: 'as-1' }), { k: 'official_assist', assist_id: 'as-1' });
  assert.deepEqual(assistCarry({ k: 'task', via: 'official_assist', assist_id: 'a b' }), { k: 'official_assist' });
  assert.deepEqual(assistCarry({ k: 'official_assist', assist: 'tk-9', assist_id: 'old' }), { k: 'official_assist', assist_id: 'tk-9' }, 'host sends the task id as src.assist (read first)');
  assert.deepEqual(assistCarry({ k: 'task' }), {});
  assert.match(read('../public/js/snap.js'), /\.\.\.assistCarry\(src\),/);
  assert.match(read('../public/js/relay.js'), /if \(isAssistSrc\(src\)\) return \{kind: "task", label: t\('p0269\.assist\.src'\)\};/);
});

test('#468 nothing terminal-shaped: no term_* in assist.js, assist:true terminal frames dropped before routing', () => {
  const code = AS.replace(/\/\*[\s\S]*?\*\//g, '').replace(/^\s*\/\/.*$/gm, '').replace(/\s\/\/ .*$/gm, '');
  assert.doesNotMatch(code, /term_|xterm|ptyApi|readOnly/);
  const APP = read('../public/app.js');
  assert.match(APP, /if \(m\.assist === true && typeof m\.t === 'string' && m\.t\.startsWith\('term_'\)\) return;/);
  assert.ok(APP.indexOf("m.assist === true && typeof m.t === 'string'") < APP.indexOf("case 'term_out':"));
  assert.match(APP, /case 'assist_task': assist\.onTask\(m\); return;/);
  assert.doesNotMatch(read('../public/js/termpty.js'), /readOnly|p0269\.assist/, 'the read-only xterm viewer is gone');
  assert.doesNotMatch(read('../public/js/terminal.js'), /ptyApi/);
});

// ---------------------------------------------------------------- L-2 terminal links
test('L-2 terminal links: https only, no credentials, no confirm(), opened with no opener', () => {
  assert.equal(httpsOnly('https://agentj.app/docs'), 'https://agentj.app/docs');
  for (const bad of ['http://agentj.app/', 'javascript:alert(1)', 'file:///etc/passwd', 'data:text/html,x', 'vbscript:x', 'ssh://host',
    'https://user:pw@evil.example/', 'https://', 'https://ex ample.com', 'https://a.example/\nb', '', null, 'h'.repeat(10)]) assert.equal(httpsOnly(bad), null, String(bad));
  assert.equal(httpsOnly('https://x.example/' + 'a'.repeat(2100)), null, 'too long');
  const opened = [];
  const h = linkHandler((u) => opened.push(u));
  assert.equal(h.allowNonHttpProtocols, false);
  let prevented = 0;
  assert.equal(h.activate({ preventDefault: () => prevented++ }, 'https://agentj.app/x'), true);
  assert.equal(h.activate({}, 'http://agentj.app/x'), false);
  assert.equal(h.activate({}, 'javascript:alert(1)'), false);
  assert.deepEqual(opened, ['https://agentj.app/x']); assert.equal(prevented, 1);
  // the default opener: a new tab with no opener / referrer, never confirm()
  const calls = [];
  const g = globalThis.open; globalThis.open = (...a) => calls.push(a);
  try { linkHandler().activate({}, 'https://agentj.app/y'); } finally { globalThis.open = g; }
  assert.deepEqual(calls, [['https://agentj.app/y', '_blank', 'noopener,noreferrer']]);
  const src = read('../public/js/termlink.js').replace(/^\s*\/\/.*$/gm, '');
  assert.doesNotMatch(src, /confirm\(/);
  assert.match(read('../public/js/termpty.js'), /linkHandler: linkHandler\(\) \}\);/, 'the xterm gets our handler');
});

test('i18n: the new strings exist in both languages and all Chinese files, one language each, no trial words', () => {
  const zh = JSON.parse(read('../i18n/web.zh.json')), en = JSON.parse(read('../i18n/web.en.json'));
  const src = JSON.parse(read('../i18n/web.zh.src.json')), ov = JSON.parse(read('../i18n/web.zh.override.json'));
  const keys = ['p0269.split.cont', 'p0269.split.contAria', 'p0269.split.contOf', 'p0269.split.contOfNone', 'p0269.queue.prevWaited', 'p0269.queue.prevWaitedLess',
    'p0269.queue.steered', 'p0269.assist.src', 'p0269.assist.reply', 'p0269.assist.replyWait', 'p0269.assist.replyNone', 'p0269.assist.noText', 'p0269.assist.note.task',
    'p0269.assist.x.rules', 'p0269.assist.x.ask', ...['queued', 'running', 'done', 'refused', 'stopped'].map((s) => 'p0269.assist.st.' + s),
    ...['ended', 'endedStop', 'length', 'admission_filter', 'busy', 'quota', 'main_unavailable', 'submit_failed', 'interrupted', 'failed', 'outbound_gate', 'not_sent', 'other', 'otherDone'].map((s) => 'p0269.assist.twhy.' + s),
    ...['shape', 'io', 'passkey_failed', 'passkey_required'].map((s) => 'p0269.assist.why.' + s)];
  for (const k of keys) {
    for (const [n, d] of [['zh', zh], ['en', en], ['src', src], ['override', ov]]) assert.ok(typeof d[k] === 'string' && d[k], `${k} in ${n}`);
    assert.match(zh[k], /[一-鿿]/, 'zh ' + k); assert.doesNotMatch(en[k], /[一-鿿]/, 'en ' + k);
    assert.doesNotMatch(zh[k] + en[k], /试用|\btrial\b/i, k);
    assert.equal((zh[k].match(/\{\w+\}/g) || []).join(), (en[k].match(/\{\w+\}/g) || []).join(), 'placeholders ' + k);
  }
  assert.equal(zh['p0269.split.cont'], '继续见下方'); assert.equal(zh['p0269.queue.steered'], '已插入当前这一轮');
  assert.equal(zh['p0269.assist.src'], '官方远程协助'); assert.equal(zh['p0269.queue.prevWaited'], '上一件还在做，已等 {n} 分钟');
  // the explanation says what B3 is: tasks to YOUR Agent, normal rules, risky steps still ask, live, take back
  assert.match(zh['p0269.assist.x.what'], /给你的 Agent 发任务/); assert.match(zh['p0269.assist.x.rules'], /平时的规则/);
  assert.match(zh['p0269.assist.x.ask'], /一条条/); assert.match(zh['p0269.assist.x.see'], /实时/); assert.match(zh['p0269.assist.x.back'], /收回/);
  for (const k of Object.keys(zh).filter((x) => x.startsWith('p0269.assist.'))) assert.doesNotMatch(zh[k] + en[k], /进.{0,12}终端帮你|step into|terminal and fix/i, 'no terminal-entry wording left: ' + k);
  for (const gone of ['p0269.assist.x.who', 'p0269.assist.x.keys', 'p0269.assist.viewAria', 'p0269.assist.twhy.too_long', 'p0269.assist.twhy.expired']) assert.equal(zh[gone], undefined, gone);
});
