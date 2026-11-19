// Exercise the shipped card controller with a memory-only DOM and no browser, images or transport.
import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import test from 'node:test';
import { runInNewContext } from 'node:vm';
import DICT from '../public/i18n.js';
import { KIND, padJson } from '../public/proto/wire.js';

const source = readFileSync(new URL('../public/js/logincard.js', import.meta.url), 'utf8');
const id = 'a'.repeat(32), other = 'b'.repeat(32), site = 'fixture';
const request = { op: 'request', site, id: '', why: 'failed', reason: 'page_not_ready' };
const reply = { ...request, op: 'refresh', id };

function fixture(lang = 'en') {
  const nodes = new Map(), timers = new Map(), sent = [];
  function mk() {
    return {
      dataset: {}, append() {}, appendChild() {}, setAttribute(k, v) { this[k] = v; },
      querySelector: s => nodes.get(s.slice(1)), set id(k) { nodes.set(k, this); },
      getAttribute(k) { return this[k] ?? null; }, removeAttribute(k) { delete this[k]; },
      addEventListener(k, handler) { this[k] = handler; },
    };
  }
  let timer = 0, now = 10000;
  const box = {
    Date: { now: () => now }, document: { body: mk() }, mk, lang, t: k => DICT[lang][k], registerLayer() {}, onLang() {},
    TOP_RANK: 1, toast() {}, generation: {}, gen: () => box.generation,
    sendApp: async m => { sent.push({ ...m }); },
    setTimeout: (fn, ms) => { timers.set(++timer, { fn, ms }); return timer; },
    clearTimeout: k => timers.delete(k),
  };
  runInNewContext(source.replace(/^import .*$/gm, '').replace(/^export /gm, '') + '\nthis.api={failed,add,done,clear}', box);
  const { api } = box;
  const showCard = (cid = id, ttl = 120) => api.add({ id: cid, n: other, site, title: 'Fixture', ttl, img: 'AA==' }); // a non-image byte
  const expire = (cid = id) => { showCard(cid); api.done({ id: cid, result: 'expired' }); };
  return { api, advance: ms => { now += ms; }, node: k => nodes.get('lqr' + (k ? '-' + k : '')), box, timers, sent, expire, showCard };
}

test('local expiry clears the image and actions when host completion is lost', async () => {
  for (const trigger of ['timer', 'check', 'later']) {
    const f = fixture();
    f.showCard(id, 1);
    f.advance(500);
    f.showCard(id, 120);
    f.advance(500);
    if (trigger === 'timer') [...f.timers.values()][0].fn();
    else await f.node(trigger).click();
    assert.equal(f.node('img').getAttribute('src'), null);
    for (const control of ['check', 'later', 'tg']) assert.equal(f.node(control).hidden, true);
    assert.equal(f.sent.length, 0);
  }
});

test('first failure needs a targeted request envelope and a fixed reason, and offers only Close', () => {
  for (const lang of ['zh', 'en']) {
    const { api, node, sent } = fixture(lang);
    for (const bad of [{ op: undefined }, { reason: 'raw detail' }, { id }, { op: 'refresh' }, { site: '<untrusted>' }]) {
      api.failed({ ...request, ...bad }); assert.equal(node(), undefined);
    }
    for (const reason of ['browser_unavailable', 'page_not_ready', 'qr_not_found', 'capture_timeout', 'capture_blank']) {
      api.failed({ ...request, reason, title: 'raw detail', hint: 'raw detail' });
      assert.equal(node().hidden, false);
      assert.equal(node('note').textContent, DICT[lang]['lqr.err.' + reason]);
      assert.equal(node('title').textContent, DICT[lang]['lqr.unavailable']);
      assert.equal(node('img').getAttribute('src'), null);
      for (const k of ['frame', 'check', 'refresh', 'tg', 'later', 'left']) assert.equal(node(k).hidden, true, k);
      node('refresh').click();
      assert.equal(sent.length, 0);
      assert.equal(node('close').hidden, false);
      node('close').click(); assert.equal(node().hidden, true);
    }
  }
});

test('only the local pending refresh accepts a result, late replies cannot remove a replacement', async () => {
  const f = fixture(), { api, node } = f;
  f.expire();
  api.failed(reply);
  assert.equal(node('note').textContent, DICT.en['lqr.expiredNote']);
  await node('refresh').click();
  assert.deepEqual(f.sent, [{ t: 'login_qr_refresh', site, id }]);
  for (const ignored of [request, { ...reply, id: other }, { ...reply, site: 'elsewhere' }, { ...reply, op: undefined }]) {
    api.failed(ignored); assert.equal(node('refresh').disabled, true);
  }
  api.failed(reply);
  assert.equal(node('note').textContent, DICT.en['lqr.err.page_not_ready']);
  assert.equal(node('refresh').disabled, false);
  await node('refresh').click();
  api.failed({ id: '', site, why: 'no_browser' }); // pre-diagnostic host
  assert.equal(node('note').textContent, DICT.en['lqr.err.no_browser']);
  await node('refresh').click();
  f.showCard(other);
  api.failed(reply); api.failed(request);
  assert.equal(node('check').hidden, false);
  api.done({ id: other, result: 'expired' });
  await node('refresh').click();
  api.failed({ ...reply, id: other, reason: undefined, why: 'gone' });
  assert.equal(node('note').textContent, DICT.en['lqr.err.gone']);
  assert.equal(node('refresh').hidden, true);
});

test('dismissal, missing reply, and disconnect retire the pending refresh', async () => {
  const { api, node, box, timers, expire, sent } = fixture();
  expire(); await node('refresh').click(); node('close').click();
  api.failed(reply); assert.equal(node().hidden, true);
  expire(other); await node('refresh').click();
  api.failed({ id: '', site, why: 'busy' }); // ambiguous legacy reply after a dismissed operation
  assert.equal(node('refresh').disabled, true);
  const timeout = [...timers.values()].find(t => t.ms === 60000);
  timeout.fn();
  api.failed({ ...reply, id: other });
  assert.equal(node('note').textContent, DICT.en['lqr.err.no_reply']);
  assert.equal(node('refresh').hidden, true);
  node('refresh').click(); assert.equal(sent.length, 2);
  api.clear(); box.generation = null;
  api.failed(request); assert.equal(node().hidden, true);
  box.generation = {}; api.failed(reply); assert.equal(node().hidden, true);
});

test('an in-flight decode reaches the app only on its original session and receive key', async () => {
  const prior = globalThis.WebSocket;
  globalThis.WebSocket = class { close() {} };
  const session = await import('../public/js/session.js?qr-failure-test');
  let deliveries = 0;
  session.configure({ setStatus() {}, onApp() { deliveries++; } });
  const ctx = { relay: 'ws://invalid.test', channel: 'fixture' };
  try {
    for (const change of ['none', 'session', 'receiver']) {
      const s = session.openSession('pair', ctx);
      s.phase = 'ready';
      const started = Promise.withResolvers(), decoded = Promise.withResolvers();
      s.recv = { decrypt() { started.resolve(); return decoded.promise; } };
      s.ws.onmessage({ data: new Uint8Array([KIND.DATA]).buffer });
      await started.promise;
      if (change === 'receiver') s.recv = {};
      if (change === 'session') session.openSession('pair', ctx);
      const before = deliveries;
      decoded.resolve(padJson({ t: 'login_qr_err', ...request }));
      await s.chain;
      assert.equal(deliveries - before, change === 'none' ? 1 : 0, change);
    }
  } finally {
    session.closeSession();
    globalThis.WebSocket = prior;
  }
});
