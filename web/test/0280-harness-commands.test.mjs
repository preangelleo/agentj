import {test} from 'node:test';
import assert from 'node:assert/strict';
import {renderHarnessCommands} from '../public/js/harness-commands.js';

class Element {
  constructor(tag) { this.tagName = tag; this.children = []; this.events = {}; this.hidden = false; this.attributes = {}; }
  setAttribute(name, value) { this.attributes[name] = String(value); }
  append(...children) { this.children.push(...children); }
  replaceChildren(...children) { this.children = children; }
  addEventListener(name, callback) { this.events[name] = callback; }
  click() { if (!this.disabled) this.events.click?.(); }
}
globalThis.document = {createElement: tag => new Element(tag)};

test('settings lists support, delegation and refusal; invokes the exact registered slash name', () => {
  const box = new Element('div'), calls = [];
  renderHarnessCommands(box, {kind:'codex', native:[
    {cmd:'/review', state:'run', desc:'Review'},
    {cmd:'/mcp', state:'agent', desc:'Explain or configure through normal tools'},
    {cmd:'/permissions', state:'none', desc:'Owner UV required'},
    {cmd:'/not-safe; shell', state:'run'},
    {cmd:'/unregistered-state', state:'made-up'},
  ]}, cmd => calls.push(cmd), 'en');
  assert.equal(box.hidden, false);
  const [summary, review, delegated, blocked] = box.children[0].children;
  assert.equal(summary.textContent, 'codex · Agent commands');
  assert.match(review.children[0].textContent, /Supported/);
  assert.match(delegated.children[0].textContent, /Delegate to Agent/);
  assert.match(blocked.children[0].textContent, /Unavailable/);
  review.children[0].click(); delegated.children[0].click(); blocked.children[0].click();
  assert.deepEqual(calls, ['review', 'mcp']);
  assert.equal(blocked.children[0].disabled, true);
  assert.equal(blocked.children[1].textContent, 'Owner UV required');
  assert.equal(box.children[0].children.length, 4);
});

test('changing harness removes old commands and renders labels as text', () => {
  const box = new Element('div');
  renderHarnessCommands(box, {kind:'codex', native:[{cmd:'/diff',state:'agent'}]}, () => {});
  renderHarnessCommands(box, {kind:'opencode', native:[{cmd:'/sessions',state:'agent',desc:'<script>no HTML</script>'}]}, () => {});
  assert.equal(box.children.length, 1);
  assert.match(box.children[0].children[0].textContent, /opencode/);
  assert.equal(box.children[0].children[1].children[1].textContent, '<script>no HTML</script>');
  renderHarnessCommands(box, null, () => {});
  assert.equal(box.hidden, true);
  assert.equal(box.children.length, 0);
});
