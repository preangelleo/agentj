import { test } from 'node:test';
import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import vm from 'node:vm';
import { controlObject, CONTROL_ACTIONS } from '../../protocol/wire.js';

const source = readFileSync(new URL('../public/js/owner-hosts.js', import.meta.url), 'utf8');
function setup() {
  const elements = new Map(), requests = [], writes = [];
  class Element {
    constructor(tag) { this.tag = tag; this.children = []; this.dataset = {}; this.listeners = {}; this.value = ''; this.hidden = false; }
    set id(v) { this._id = v; elements.set(v, this); } get id() { return this._id; }
    append(...nodes) { for (const n of nodes) { n.parent = this; n.previousElementSibling = this.children.at(-1); this.children.push(n); } }
    before(n) { elements.set(n.id, n); }
    replaceChildren(...nodes) { this.children = []; this.append(...nodes); }
    addEventListener(k, f) { this.listeners[k] = f; }
    setAttribute() {} focus() {}
    querySelector(q) { return this.children.find(n => q === '[data-title]' ? Object.hasOwn(n.dataset, 'title') : Object.hasOwn(n.dataset, 'note')); }
  }
  const anchor = new Element('div'); anchor.id = 'set-budget-row';
  const context = vm.createContext({ document: { createElement: tag => new Element(tag), getElementById: id => elements.get(id) },
    t: k => k, onLang: () => {}, copyable: n => n, isReady: () => true,
    request: (obj, fn) => { requests.push({ obj, fn }); return 'request-id'; }, done: () => {},
    signedWriteFa: async (action, target, extra, faceId) => { writes.push({ action, target, extra, faceId }); return { ok: true }; },
    settings: { faceIdStep: async () => ({ uv: true }) }, whyText: x => x, console });
  const script = source.replace(/^import .*;\n/gm, '').replace(/export /g, '')
    .replace("await import('./settings.js')", 'settings');
  vm.runInContext(script + '\nglobalThis.api = {init, capability, onState, targetFor};', context);
  context.api.init();
  const sample = { t: 'owner_hosts_state', scope: { channel: 'CH', host: 'HOST', seat: 'SEAT' }, rev: 'revision', valid: true,
    rows: [{ alias: 'box', hostname: '192.168.1.9', user: 'dev', port: 22, public_key: 'ssh-ed25519 public',
      fingerprint: 'SHA256:public', peer: '', owner: 'self', expires: 1799999999, status: 'active', ssh: 'ssh pinned', scp: 'scp pinned' }],
    candidates: [{ alias: 'lan', hostname: '192.168.1.10', user: 'coder', port: 22, status: 'pending' }] };
  return { api: context.api, elements, requests, writes, sample };
}
const tick = () => new Promise(resolve => setImmediate(resolve));

test('old Host has no visible component, no request or write', () => {
  const h = setup(); h.api.capability({});
  assert.equal(h.elements.get('owner-hosts').hidden, true);
  h.api.onState(h.sample);
  assert.equal(h.elements.get('owner-hosts').hidden, true);
  assert.equal(h.requests.length, 0); assert.equal(h.writes.length, 0);
});

test('capable Host loads status, pinned commands and pending candidates without granting them', () => {
  const h = setup(); h.api.capability({ owner_hosts: 1 });
  assert.equal(h.requests[0].obj.t, 'owner_hosts_get');
  h.requests[0].fn(h.sample);
  assert.equal(h.elements.get('owner-hosts').hidden, false);
  const list = h.elements.get('owner-hosts-list');
  assert.equal(list.children.length, 2);
  assert.match(list.children[0].children[0].textContent, /ownerHosts.active/);
  list.children[1].listeners.click();
  assert.equal(h.elements.get('owner-hosts-alias').value, 'lan');
  assert.equal(h.writes.length, 0);
});

test('editing an alias replaces its old binding through signed UV control with exact scope and revision', async () => {
  const h = setup(); h.api.capability({ owner_hosts: 1 }); h.api.onState(h.sample);
  h.elements.get('owner-hosts-list').children[0].children.find(n => n.textContent === 'ownerHosts.edit').listeners.click();
  h.elements.get('owner-hosts-alias').value = 'renamed';
  h.elements.get('owner-hosts-form').listeners.submit({ preventDefault() {} }); await tick();
  assert.equal(h.writes.length, 1);
  const w = h.writes[0]; assert.equal(w.action, 'owner_hosts_set');
  assert.equal(w.target.rev, 'revision'); assert.equal(w.target.scope.seat, 'SEAT');
  assert.equal(w.target.rows.length, 1); assert.equal(w.target.rows[0].alias, 'renamed');
  assert.equal(w.target.rows[0].owner, 'self'); assert.equal(w.extra.target, w.target);
  assert.equal(typeof w.faceId, 'function');
});

test('revocation uses the same signed UV control; state never optimistically grants', async () => {
  const h = setup(); h.api.capability({ owner_hosts: 1 }); h.api.onState(h.sample);
  h.elements.get('owner-hosts-list').children[0].children.find(n => n.textContent === 'ownerHosts.revoke').listeners.click();
  await tick(); assert.equal(h.writes.length, 1); assert.equal(h.writes[0].target.rows.length, 0);
  assert.equal(h.writes[0].action, 'owner_hosts_set');
});

test('wire action binds the full operation, scope, revision, rows and host key', () => {
  assert.ok(CONTROL_ACTIONS.includes('owner_hosts_set'));
  const h = setup(), target = h.api.targetFor(h.sample, h.sample.rows);
  const original = controlObject('owner_hosts_set', target);
  for (const field of ['channel', 'host', 'seat']) {
    const changed = structuredClone(target); changed.scope[field] += 'other';
    assert.notEqual(controlObject('owner_hosts_set', changed), original);
  }
  for (const field of ['alias', 'hostname', 'public_key', 'fingerprint', 'owner', 'peer', 'expires', 'port', 'user']) {
    const changed = structuredClone(target); changed.rows[0][field] += 'changed';
    assert.notEqual(controlObject('owner_hosts_set', changed), original);
  }
});

test('bilingual source dictionaries provide every component label', () => {
  const keys = ['title','note','confirm','confirmYes','add','save','refresh','edit','revoke','pending','active','expired','unbound','invalid','failed',
    'alias','hostname','user','port','public_key','fingerprint','peer','expires'];
  for (const lang of ['zh.src', 'zh', 'en']) {
    const d = JSON.parse(readFileSync(new URL(`../i18n/web.${lang}.json`, import.meta.url), 'utf8')).ownerHosts;
    for (const key of keys) assert.equal(typeof d[key], 'string', `${lang}: ${key}`);
  }
});
