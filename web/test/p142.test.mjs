// P142 item 7: the header model label with no model catalogue (shared Claude before its picker is known, an older host).
// Tap = `/model` through the same self-slash path as typing it (its card lists the models as buttons); hold = back to the
// default, catalogue or not. "Not supported" only when the page cannot send at all (offline).
import {test} from 'node:test';
import assert from 'node:assert/strict';
import {readFileSync} from 'node:fs';
import vm from 'node:vm';
const src = readFileSync(new URL('../public/js/relay.js', import.meta.url), 'utf8');
const pillSrc = src.slice(src.indexOf('const PILL_GATHER_MS'), src.indexOf('// One press handler'));

function ctx({catalogue = null, canSend = true} = {}){
  const calls = {slash: [], model: [], toasts: []};
  const node = () => ({textContent: '', dataset: {}, hidden: false, classList: {contains: () => false}});
  const nodes = {};
  const c = vm.createContext({
    cur: {status: 'idle', usage: {model: 'Opus 4.8', effort: 'high'}, switch: catalogue},
    C: {connected: () => true, api: {slash: (...a) => { calls.slash.push(a); return canSend; },
                                    model: async (w) => { calls.model.push(w); return {ok: true}; }}},
    t: (k) => k, toast: (k) => calls.toasts.push(k), toastOff(){}, lang: () => 'zh', hostId: () => 'h',
    el: (id) => (nodes[id] ||= node()), SharedNotice: class { update(){} },
    setTimeout: () => 0, clearTimeout(){}, Date, navigator: {},
  });
  vm.runInContext(pillSrc, c);
  return {c, calls, nodes};
}

test('P142 shared mode, no catalogue: tap sends /model (self-slash), never "unsupported"', () => {
  const {c, calls} = ctx();
  vm.runInContext('pillStep("model")', c);
  assert.equal(JSON.stringify(calls.slash), JSON.stringify([["model"]]));
  assert.deepEqual(calls.toasts, []);
  vm.runInContext('pillStep("effort")', c);
  assert.equal(calls.slash.length, 2);
});

test('P142 shared mode, no catalogue: hold restores the default through model_set', async () => {
  const {c, calls, nodes} = ctx();
  vm.runInContext('pillDefault()', c);
  await new Promise(r => setImmediate(r));
  assert.equal(JSON.stringify(calls.model), JSON.stringify([{default: true}]));
  assert.deepEqual(calls.toasts.filter(x => x === 'r.pill.unsupported'), []);
  assert.equal(nodes.metaModel.textContent, 'Opus 4.8');          // the label keeps the real name while it settles
});

test('P142 offline page: tap still says "unsupported" (nothing could be sent)', () => {
  const {c, calls} = ctx({canSend: false});
  vm.runInContext('pillStep("model")', c);
  assert.deepEqual(calls.toasts, ['r.pill.unsupported']);
});

test('P142 negative: with a catalogue the tap cycles models locally, no /model is sent', () => {
  const cat = {models: [{id: 'opus', name: 'Opus', efforts: ['high']}, {id: 'sonnet', name: 'Sonnet', efforts: ['high']}],
               default: {model: 'opus', effort: 'high'}};
  const {c, calls, nodes} = ctx({catalogue: cat});
  vm.runInContext('cur.usage.model_id = "opus"; pillStep("model")', c);
  assert.deepEqual(calls.slash, []);
  assert.equal(nodes.metaModel.textContent, 'Sonnet');
});
