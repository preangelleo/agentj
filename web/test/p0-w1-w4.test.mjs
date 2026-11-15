import {test} from 'node:test';
import assert from 'node:assert/strict';
import {readFileSync} from 'node:fs';
import vm from 'node:vm';
import {webcrypto} from 'node:crypto';

const pub = new URL('../public/', import.meta.url);

function worker() {
  const listeners = new Map();
  const context = vm.createContext({URL, Response, Request, Blob, File, crypto: webcrypto, Date, self: {
    location: new URL('https://m.agentj.app/seatabcd/sw.js'),
    addEventListener: (name, fn) => listeners.set(name, fn),
  }});
  vm.runInContext(readFileSync(new URL('sw.js', pub), 'utf8'), context);
  return listeners;
}
function shareRequest() {
  const body = new FormData();
  body.append('text', 'seat share');
  return new Request('https://m.agentj.app/seatabcd/share-target', {method: 'POST', body});
}

test('W4: a seat-scoped service worker claims and returns a share', async () => {
  const listeners = worker();
  let response;
  listeners.get('fetch')({request: shareRequest(), respondWith: p => { response = p; }});
  const landing = await response;
  assert.equal(landing.status, 303);
  const id = new URL(landing.headers.get('location')).searchParams.get('share');
  let payload;
  listeners.get('message')({data: {t: 'share-take', id}, source: {url: `https://m.agentj.app/seatabcd/?share=${id}`}, ports: [{postMessage: v => { payload = v; }}]});
  assert.equal(payload.text, 'seat share');
});

test('W1: storage failure falls back instead of making the app require IndexedDB', async () => {
  const store = readFileSync(new URL('js/store.js', pub), 'utf8');
  const app = readFileSync(new URL('app.js', pub), 'utf8');
  assert.match(store, /const memory = new Map\(\)/);
  assert.doesNotMatch(app, /!globalThis\.indexedDB/);
  assert.match(store, /if \(storageFailed\)/);
  assert.match(store, /storageFailed = true/);
});
