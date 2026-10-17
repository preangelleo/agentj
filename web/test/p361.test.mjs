// P361 (PROTOCOL §34) Settings 「执行」 — static rules: the signed action (same object text as controls.py), the page's names =
// the host's whitelist (cmdrun.py RUN_CMDS), the page never sends a command line, Face ID on every run (no fallback), output as
// text only and never stored, the block inside 紧急修复, both dictionaries complete. Rendering: p361.mjs (real browser).
import { test } from 'node:test';
import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import * as wire from '../../protocol/wire.js';
import * as pageWire from '../public/proto/wire.js';

const read = (p) => readFileSync(new URL(p, import.meta.url), 'utf8');
const js = read('../public/js/cmdrun.js'), html = read('../public/index.html'), ctl = read('../public/js/controls.js');
const py = read('../../host/agentj/cmdrun.py'), controlsPy = read('../../host/agentj/controls.py');
const zh = JSON.parse(read('../i18n/web.zh.json')), en = JSON.parse(read('../i18n/web.en.json'));

test('§34 cmd_run: object {"cmd"} → the name, in both wire copies and controls.py', async () => {
  for (const w of [wire, pageWire]) {
    assert.ok(w.CONTROL_ACTIONS.includes('cmd_run'));
    assert.equal(w.controlObject('cmd_run', { cmd: 'doctor' }), 'doctor');
  }
  const msg = new TextDecoder().decode(await wire.controlMessage('C'.repeat(22), 'D'.repeat(22), 'cmd_run', '0'.repeat(32), 1, { cmd: 'doctor' }));
  assert.match(msg, /^agentjarvis-control-v1\nC{22}\nD{22}\ncmd_run\n0{32}\n1\n[0-9a-f]{64}$/);
  assert.match(controlsPy, /ACTIONS \+= \("cmd_run",\)/);
  assert.match(controlsPy, /if action == "cmd_run":[^\n]*\n\s+return str\(obj\["cmd"\]\)/);
});

test('the page knows exactly the host whitelist and sends a name, never a command line', async () => {
  const hostNames = [...py.slice(py.indexOf('RUN_CMDS = {'), py.indexOf('\n}\n', py.indexOf('RUN_CMDS = {'))).matchAll(/^    "([a-z_]+)": \(/gm)].map((m) => m[1]);
  const cmds = js.slice(js.indexOf('export const CMDS = {'), js.indexOf('};', js.indexOf('export const CMDS = {')));
  const pageNames = [...cmds.matchAll(/(\w+): '[a-z -]+'/g)].map((m) => m[1]);
  assert.deepEqual(pageNames, hostNames);
  assert.match(js, /signedWriteFa\('cmd_run', \{ cmd: name \}, \{ t: 'cmd_run', cmd: name \}, faceId\)/);
  assert.equal([...js.matchAll(/signedWriteFa\(/g)].length, 1, 'one place sends');
  assert.match(js, /if \(!Object\.hasOwn\(CMDS, name\) \|\| !names\.includes\(name\)/, 'only a listed name runs');
  assert.doesNotMatch(js, /innerHTML|insertAdjacentHTML|localStorage|sessionStorage/, 'output is text, never stored');
  assert.match(ctl, /obj\.t === 'cmd_run' && m\.action === 'cmd_run' && m\.timeout_ms === 150000/, 'ctl_pending extends the wait once');
  assert.match(py, /PENDING_MS = 150000/);
});

test('the block sits in 紧急修复; dictionaries complete in zh and en', () => {
  const block = html.slice(html.indexOf('id="set-s-rescue"'), html.indexOf('id="set-s-tools"'));
  for (const id of ['rs-cmds', 'rs-cmd-list', 'rs-cmd-out']) assert.ok(block.includes(`id="${id}"`), id);
  assert.match(block, /id="rs-cmds" hidden/, 'hidden until the host lists its commands');
  const keys = [...new Set([...js.matchAll(/t\('(p361\.[a-zA-Z_.]+)'/g)].map((m) => m[1]).filter((k) => !k.endsWith('.')))];
  for (const n of ['doctor', 'reconnect', 'status', 'service_status', 'rescue_status', 'update_check']) keys.push('p361.cmd.' + n);
  for (const n of ['busy', 'timeout', 'no_passkey', 'dropped']) keys.push('p361.why.' + n);
  for (const k of keys) for (const d of [zh, en]) assert.ok(typeof d[k] === 'string' && d[k].length > 1, k);
  for (const d of [zh, en]) assert.doesNotMatch(Object.entries(d).filter(([k]) => k.startsWith('p361.')).map(([, v]) => v).join('\n'), /relay|serve\b|harness|session id/i);
});
