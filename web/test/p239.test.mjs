// P239 (Leo 10-13: 「点着玩」): the header model · effort pill only changes what it shows; each half commits once it has been
// left alone 5 s, another tap restarts that half's clock; a pick equal to what the Agent runs is never sent; a refusal puts
// the label back with one toast; hold = the default at once; leaving the page sends a waiting pick; typed /model drops it.
// relay.js's pill section runs in a vm with a fake clock.
import {test} from 'node:test';
import assert from 'node:assert/strict';
import {readFileSync} from 'node:fs';
import vm from 'node:vm';
const src = readFileSync(new URL('../public/js/relay.js', import.meta.url), 'utf8');
const pillSrc = src.slice(src.indexOf('const PILL_GATHER_MS'), src.indexOf('// One press handler'));
const css = readFileSync(new URL('../public/app.css', import.meta.url), 'utf8');

const CAT = {models: [{id: 'opus', name: 'Opus 5.5', efforts: ['low', 'medium', 'high']},
                      {id: 'sonnet', name: 'Sonnet 5.5', efforts: ['low', 'medium', 'high']},
                      {id: 'haiku', name: 'Haiku 5.5', efforts: null},
                      {id: 'fable', name: 'Fable 5.1', efforts: ['low', 'medium', 'high']}],
             default: {model: 'opus', effort: 'medium'}};

function ctx({result = {ok: true}} = {}){
  let now = 0, seq = 0;
  const timers = new Map();
  const calls = {model: [], toasts: [], actions: []};
  const node = () => ({textContent: '', dataset: {}, hidden: false, classList: {contains: () => false}});
  const nodes = {};
  const c = vm.createContext({
    cur: {status: 'idle', usage: {model: 'Opus 5.5', model_id: 'opus', effort: 'medium'}, switch: CAT},
    C: {connected: () => true, api: {slash: () => true, model: async (w) => { calls.model.push(w); return typeof result === 'function' ? result(w) : result; }}},
    t: (k) => k, toast: (k) => calls.toasts.push(k), toastOff(){}, lang: () => 'zh', hostId: () => 'h',
    toastAction: (text, label, fn, ms) => { calls.toasts.push(text); calls.actions.push({text, label, fn, ms}); },
    el: (id) => (nodes[id] ||= node()), SharedNotice: class { update(){} },
    setTimeout: (f, ms) => { const id = ++seq; timers.set(id, {f, at: now + ms}); return id; },
    clearTimeout: (id) => timers.delete(id),
    Date: {now: () => now}, navigator: {},
  });
  vm.runInContext(pillSrc, c);
  const run = (code) => vm.runInContext(code, c);
  const flushP = () => new Promise(r => setImmediate(r));
  async function advance(ms){
    const end = now + ms;
    for (;;){
      const due = [...timers.entries()].filter(([, t]) => t.at <= end).sort((a, b) => a[1].at - b[1].at)[0];
      if (!due) break;
      now = due[1].at; timers.delete(due[0]); due[1].f(); await flushP();
    }
    now = end; await flushP();
  }
  const label = () => nodes.metaModel.textContent + (nodes.metaEffort.textContent ? ' · ' + nodes.metaEffort.textContent : '');
  // the host applies a sent pick: the next meter reports it
  const meter = (model, effort) => { const m = CAT.models.find(x => x.id === model); run(`cur.usage = ${JSON.stringify({model: m.name, model_id: model, effort})}; paintPill()`); };
  return {c, run, calls, nodes, advance, label, meter, timers};
}

test('P239 six fast taps on the model → the label follows at once, exactly one model_set 5 s after the last tap', async () => {
  const x = ctx();
  for (let i = 0; i < 6; i++){ x.run('pillStep("model")'); await x.advance(700); }   // opus → sonnet haiku fable opus sonnet haiku
  assert.equal(x.label(), 'Haiku 5.5', 'the label shows the 6th step at once');
  assert.equal(x.calls.model.length, 0, 'nothing sent while tapping');
  assert.equal(x.nodes.meta.dataset.sw, 'wait');
  assert.equal(x.nodes.meta.dataset.swModel, 'wait');
  await x.advance(4200);
  assert.equal(x.calls.model.length, 0, 'still inside 5 s of the last tap');
  await x.advance(200);
  assert.equal(JSON.stringify(x.calls.model), JSON.stringify([{model: 'haiku', effort: null}]), 'exactly one, the final pick');
  await x.advance(20000);
  assert.equal(x.calls.model.length, 1);
});

test('P239 five fast taps (final ≠ running) → one model_set with the final pick', async () => {
  const x = ctx();
  for (let i = 0; i < 5; i++){ x.run('pillStep("model")'); await x.advance(300); }
  assert.equal(x.label(), 'Sonnet 5.5 · medium', 'the label shows the 5th step at once');
  await x.advance(5000);
  assert.equal(JSON.stringify(x.calls.model), JSON.stringify([{model: 'sonnet', effort: 'medium'}]));
  assert.equal(x.nodes.meta.dataset.swModel, undefined, 'the waiting line is gone once sent');
  assert.equal(x.nodes.meta.dataset.sw, 'pending', 'dashed until the meter reports it');
  x.meter('sonnet', 'medium');
  assert.equal(x.nodes.meta.dataset.sw, undefined);
  assert.equal(x.label(), 'Sonnet 5.5 · medium');
});

test('P239 six fast taps on the effort → one model_set, the effort clock is its own', async () => {
  const x = ctx();
  for (let i = 0; i < 6; i++){ x.run('pillStep("effort")'); await x.advance(400); }   // medium → high → low → medium → high → low → medium
  assert.equal(x.nodes.meta.dataset.swEffort, 'wait');
  assert.equal(x.nodes.meta.dataset.swModel, undefined);
  await x.advance(5000);
  assert.equal(x.calls.model.length, 0, '6 steps of 3 = back to medium → not sent');
  x.run('pillStep("effort")'); x.run('pillStep("effort")');                      // → low
  await x.advance(5000);
  assert.equal(JSON.stringify(x.calls.model), JSON.stringify([{model: 'opus', effort: 'low'}]));
});

test('P239 tapped back to the running value within 5 s → nothing is sent', async () => {
  const x = ctx();
  for (let i = 0; i < 4; i++) x.run('pillStep("model")');   // sonnet, haiku, fable, opus
  await x.advance(6000);
  assert.equal(x.calls.model.length, 0);
  assert.equal(x.label(), 'Opus 5.5 · medium');
});

test('P239 halves are independent: model commits at its own 5 s, the waiting effort keeps the running effort', async () => {
  const x = ctx();
  x.run('pillStep("model")');                       // sonnet · medium, model clock to 5 s
  await x.advance(3000);
  x.run('pillStep("effort")');                      // sonnet · high, effort clock to 8 s
  await x.advance(2000);                            // t = 5 s: model commits, effort still waiting
  assert.equal(JSON.stringify(x.calls.model), JSON.stringify([{model: 'sonnet', effort: 'medium'}]));
  assert.equal(x.label(), 'Sonnet 5.5 · high', 'the label keeps the waiting effort');
  assert.equal(x.nodes.meta.dataset.swEffort, 'wait');
  await x.advance(3000);                            // t = 8 s: effort commits
  assert.equal(JSON.stringify(x.calls.model[1]), JSON.stringify({model: 'sonnet', effort: 'high'}));
  assert.equal(x.calls.model.length, 2);
});

test('P239 a refusal puts the label back and offers an exact-request retry', async () => {
  let attempts = 0;
  const x = ctx({result: () => ++attempts === 1 ? {ok: false, why: 'busy'} : {ok: true}});
  x.run('pillStep("model")');
  assert.equal(x.label(), 'Sonnet 5.5 · medium');
  await x.advance(5000);
  assert.equal(x.calls.model.length, 1);
  assert.equal(x.label(), 'Opus 5.5 · medium', 'rolled back to what runs');
  assert.deepEqual(x.calls.toasts, ['r.pill.fail']);
  assert.equal(x.nodes.meta.dataset.sw, undefined);
  assert.equal(x.calls.actions.length, 1, 'a refusal exposes one actionable recovery');
  const action = x.calls.actions[0];
  assert.equal(action.label, 'p579.retryModel');
  assert.equal(typeof action.fn, 'function');
  assert.equal(action.ms, 10000, 'the recovery stays long enough to tap');
  action.fn();
  await x.advance(0);
  assert.equal(x.calls.model.length, 2, 'actually invoking recovery sends a second model request');
  assert.equal(JSON.stringify(x.calls.model[1]), JSON.stringify(x.calls.model[0]), 'retry preserves the refused model and effort');
  assert.equal(x.nodes.meta.dataset.sw, 'pending', 'successful retry waits for measured state');
  x.meter('sonnet', 'medium');
  assert.equal(x.label(), 'Sonnet 5.5 · medium', 'successful retry settles from the measured model');
  assert.equal(x.nodes.meta.dataset.sw, undefined);
  assert.deepEqual(x.calls.toasts, ['r.pill.fail'], 'successful retry adds no second failure notice');
});

test('P239 hold = the default at once and drops a waiting tap', async () => {
  const x = ctx();
  x.run('cur.usage = {model: "Sonnet 5.5", model_id: "sonnet", effort: "high"}; paintPill()');
  x.run('pillStep("effort")');
  x.run('pillDefault()');
  await x.advance(0);
  assert.equal(JSON.stringify(x.calls.model), JSON.stringify([{default: true}]), 'sent at once');
  await x.advance(6000);
  assert.equal(x.calls.model.length, 1, 'the dropped tap never fires');
});

test('P239 leaving the page sends a waiting pick at once; typed /model drops it', async () => {
  const x = ctx();
  x.run('pillStep("model")'); x.run('pillStep("effort")');   // sonnet · high
  await x.advance(1000);
  x.run('pillFlush()');
  await x.advance(0);
  assert.equal(JSON.stringify(x.calls.model), JSON.stringify([{model: 'sonnet', effort: 'high'}]));
  await x.advance(6000);
  assert.equal(x.calls.model.length, 1, 'the flushed clocks never fire again');
  const y = ctx();
  y.run('pillStep("model")');
  y.run('pillDrop()');
  await y.advance(6000);
  assert.equal(y.calls.model.length, 0);
  assert.equal(y.label(), 'Opus 5.5 · medium');
});

test('P239 another remote superseded this pick → back to the real reading, no toast', async () => {
  const x = ctx({result: {ok: true, superseded: true}});
  x.run('pillStep("model")');
  await x.advance(5000);
  assert.equal(x.calls.model.length, 1);
  assert.deepEqual(x.calls.toasts, []);
  assert.equal(x.label(), 'Opus 5.5 · medium');
});

test('P239 wiring: 5 s constant, page-leave flush, /model drops, CSS look has no fill', () => {
  assert.match(src, /const PILL_GATHER_MS = 5000/);
  assert.match(src, /visibilityState === "hidden"\) pillFlush\(\)/);
  assert.match(src, /addEventListener\("pagehide", pillFlush\)/);
  assert.match(src, /if \(cmd === "model"\) pillDrop\(\)/);
  const rule = css.slice(css.indexOf('.meta[data-sw-model=wait]'), css.indexOf('@keyframes pillwait'));
  assert.ok(rule.length > 0 && !/background-color|background:\s*(#|rgb|var\(--(ink|sooty))/.test(rule), 'only a thin currentColor line, no fill');
});
