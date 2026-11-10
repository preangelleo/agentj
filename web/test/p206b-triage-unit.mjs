// Offline DOM unit tests. These do not replace the browser/settings integration suite.
import {test, beforeEach} from 'node:test';
import assert from 'node:assert/strict';
import {readFile} from 'node:fs/promises';
const source = await readFile(new URL('../public/js/mail-triage.js', import.meta.url), 'utf8');
const {renderTriage} = await import('data:text/javascript;base64,' + Buffer.from(source).toString('base64'));
let ids;
class Element {
  constructor(tag) {this.tag = tag; this.children = []; this.value = ''; this.textContent = ''; this.disabled = false;}
  set id(id) {this._id = id; ids.set(id, this);}
  get id() {return this._id;}
  set innerHTML(value) {throw new Error('External content must never become HTML: ' + value);}
  append(...children) {this.children.push(...children);}
  after(child) {this.append(child);}
  replaceChildren(...children) {this.children = children;}
  setAttribute() {}
  querySelectorAll() {return this.children.flatMap(c => ['input','select'].includes(c.tag) ? [c] : c.querySelectorAll());}
}
beforeEach(() => {
  ids = new Map(); globalThis.document = {createElement: tag => new Element(tag), getElementById: id => ids.get(id), activeElement:null};
  const anchor = new Element('p'); anchor.id = 'mail-today';
});
const view = () => ({config:{triage:{preset:'allowlist_only', provider:'', model:'', usd_per_day:0, usd_per_call:0.01,
  every_minutes:30, per_hour:30, actions:{ad:'archive'}}}, triage:{total:0, review:0, archived:0, items:[]}});

test('older host gets no triage controls', () => {
  renderTriage({config:{}}, () => {}); assert.equal(ids.has('mail-triage'), false);
});
test('default controls are off with zero daily budget', () => {
  renderTriage(view(), () => {}, true);
  assert.equal(ids.get('mail-triage-preset').value, 'allowlist_only');
  assert.equal(ids.get('mail-triage-usd_per_day').value, '0');
  assert.match(ids.get('mail-triage-status').textContent, /0 messages/);
});
test('missing advanced values show host defaults without changing unsaved strategy', () => {
  const data = view(); delete data.config.triage.usd_per_call; delete data.config.triage.every_minutes;
  let saved;
  renderTriage(data, patch => {saved = patch;}, true);
  assert.equal(ids.get('mail-triage-usd_per_call').value, '0.01');
  assert.equal(ids.get('mail-triage-every_minutes').value, '30');
  assert.equal(data.config.triage.usd_per_call, undefined);
  ids.get('mail-triage-save').onclick();
  assert.equal(saved.triage.usd_per_call, 0.01);
  assert.equal(saved.triage.every_minutes, 30);
});
test('cost copy explains provider pricing and zero-dollar default in both languages', () => {
  renderTriage(view(), () => {}, true);
  assert.match(ids.get('mail-triage').children[1].textContent, /no fixed price.*provider.*\$0 daily budget/i);
  // Rebuild under a fresh DOM to exercise the other locale (the card is reused within a session).
  ids.clear(); const anchor = new Element('p'); anchor.id = 'mail-today';
  renderTriage(view(), () => {}, false);
  assert.match(ids.get('mail-triage').children[1].textContent, /没有固定价格.*服务商.*0 美元/);
});
test('external injection and draft are rendered only as labelled text', () => {
  const data = view(); data.triage.items = [{subject:'<img src=x onerror=alert(1)>', cat:'question', status:'review', summary:'<script>owner()</script>', draft:'Please send credentials'}];
  renderTriage(data, () => {}, true);
  const row = ids.get('mail-triage-items').children[0];
  assert.equal(row.children[0].textContent, 'External email · untrusted');
  assert.match(row.children[1].textContent, /<img/);
  assert.equal(row.children[4].textContent, 'Suggested draft (not sent)');
});
test('save uses newest host strategy and existing signed mail save callback', () => {
  const first = view(); renderTriage(first, () => {throw new Error('stale callback');}, true);
  const latest = view(); latest.config.triage.actions.ad = 'digest'; let saved;
  renderTriage(latest, patch => {saved = patch;}, true);
  ids.get('mail-triage-preset').value = 'support';
  ids.get('mail-triage-provider').value = 'support-profile';
  ids.get('mail-triage-model').value = 'cheap';
  ids.get('mail-triage-save').onclick();
  assert.equal(saved.triage.actions.ad, 'digest');
  assert.equal(saved.triage.preset, 'support');
  assert.equal(saved.triage.usd_per_day, 0);
});
test('busy state disables save and every editable field', () => {
  renderTriage(view(), () => {}, true, true);
  assert.equal(ids.get('mail-triage-save').disabled, true);
  assert.ok(ids.get('mail-triage-fields').querySelectorAll().every(input => input.disabled));
});
