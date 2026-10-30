// P570c (P570b 返工 #3): 手机「定时任务」页 gives a task moved to the Agent J host's own scheduler its buttons directly —
// 退回 (task_set on:false → back to Agent J, still off) and, when the task changed since, 重新迁移 (signed task_migrate {id, tsha}).
// Static half (wire object, code paths, dictionaries); the browser half is web/test/p570c.mjs (fake host, real page, shots).
import { test } from 'node:test';
import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import { controlObject, CONTROL_ACTIONS } from '../public/proto/wire.js';

const read = (p) => readFileSync(new URL(p, import.meta.url), 'utf8');
const ctl = read('../public/js/controls.js');
const zh = JSON.parse(read('../i18n/web.zh.json')), en = JSON.parse(read('../i18n/web.en.json'));
const src = JSON.parse(read('../i18n/web.zh.src.json')), ov = JSON.parse(read('../i18n/web.zh.override.json'));
const KEYS = ['migrated', 'changed', 'back', 'remigrate', 'backTitle', 'backText', 'backDone', 'migTitle', 'migText', 'migDone'].map((k) => 'p570c.' + k);
const fn = (name) => ctl.slice(ctl.indexOf(name), ctl.indexOf('\n}\n', ctl.indexOf(name)));

test('task_migrate signs id \\n tsha, exactly like task_on / task_off', () => {
  assert.ok(CONTROL_ACTIONS.includes('task_migrate'));
  assert.equal(controlObject('task_migrate', { id: 'brief', tsha: 'ab12' }), 'brief\nab12');
  assert.equal(controlObject('task_migrate', { id: 'brief', tsha: 'ab12' }), controlObject('task_on', { id: 'brief', tsha: 'ab12' }));
  assert.equal(read('../../protocol/wire.js'), read('../public/proto/wire.js'), 'public/proto is the byte copy (run build.mjs)');
});

test('重新迁移 = signed task_migrate {id, tsha} behind a confirm sheet; success toasts the host text', () => {
  const f = fn('async function remigrateTask(');
  assert.match(f, /confirmSheet\(t\('p570c\.migTitle'\), t\('p570c\.migText', \{ title \}\), t\('p570c\.remigrate'\)\)\) return;/);
  assert.match(f, /command\(await signed\('task_migrate', target, \{ t: 'task_migrate', \.\.\.target \}\)\)/);
  assert.match(f, /const said = typeof res\.text === 'string' && res\.text \? res\.text\.slice\(0, 300\) : '';/);
  assert.match(f, /toast\(said \|\| \(res\.ok \? t\('p570c\.migDone'\) : t\('tasks\.fail', \{ why: why\(res\.why\) \}\)\)/);
  // the test run takes minutes: the host's ctl_pending (sysmigrate.PENDING_MS) extends only this request, bounded
  assert.match(ctl, /m\.t === 'ctl_pending' && obj\.t === 'task_migrate' && m\.action === 'task_migrate' && Number\.isInteger\(m\.timeout_ms\) && m\.timeout_ms >= 20000 && m\.timeout_ms <= 2200000\) \{ extended = true; arm\(m\.timeout_ms\); \}/);
  assert.match(f, /loadTasks\(\);/);
});

test('退回 = the existing task_set on:false (task_off) behind its own confirm sheet', () => {
  const f = fn('async function backTask(');
  assert.match(f, /confirmSheet\(t\('p570c\.backTitle'\), t\('p570c\.backText', \{ title \}\), t\('p570c\.back'\)\)\) return;/);
  assert.match(f, /command\(await signed\('task_off', target, \{ t: 'task_set', on: false, \.\.\.target \}\)\)/);
  assert.match(f, /t\('p570c\.backDone'\)/);
});

test('taskCard: only "migrated" / "changed" switch the card; anything else (an old host) keeps today\'s tag and 启用/停用', () => {
  const f = fn('function taskCard(');
  assert.match(ctl, /const SYS_MOVED = \['migrated', 'changed'\];/);
  assert.match(f, /const sys = SYS_MOVED\.includes\(tk\.sys\) \? tk\.sys : '';/);
  assert.match(f, /if \(sys\) tags\.append\(mk\('span', 'tag ' \+ \(sys === 'migrated' \? 'tag-on' : 'tag-fail'\), t\('p570c\.' \+ sys\)\)\);\n  else tags\.append\(mk\('span', 'tag ' \+ \(tk\.enabled \? 'tag-on' : 'tag-off'\)/);
  // the moved card has no plain 停用: its buttons are 重新迁移 (changed only) + 退回; the old branch is the else
  const moved = f.slice(f.indexOf('if (sys) {'), f.indexOf('} else if (!tk.problems?.length || tk.enabled) {'));
  assert.ok(moved.length > 0, 'moved branch precedes the unchanged 启用/停用 branch');
  assert.match(moved, /if \(sys === 'changed'\)/);
  assert.match(moved, /t\('p570c\.remigrate'\)/);
  assert.match(moved, /t\('p570c\.back'\)/);
  assert.doesNotMatch(moved, /tasks\.disable|tasks\.enable|setTask/);
  assert.match(f, /\} else if \(!tk\.problems\?\.length \|\| tk\.enabled\) \{\n    const on = !tk\.enabled;/);
});

test('dictionaries: both languages, same placeholders, zh source = override = polished', () => {
  for (const k of KEYS) {
    assert.equal(typeof zh[k], 'string', 'zh ' + k);
    assert.equal(typeof en[k], 'string', 'en ' + k);
    assert.equal(src[k], zh[k], 'zh src ' + k);
    assert.equal(ov[k], zh[k], 'zh override ' + k);
    const ph = (s) => [...s.matchAll(/\{(\w+)\}/g)].map((m) => m[1]).sort().join();
    assert.equal(ph(zh[k]), ph(en[k]), 'placeholders ' + k);
  }
  assert.equal(zh['p570c.migrated'], '已迁到系统定时');
  assert.equal(zh['p570c.changed'], '任务改了，没有运行');
  assert.equal(zh['p570c.back'], '退回');
  assert.equal(zh['p570c.remigrate'], '重新迁移');
});

test('wording (TERMS P555): 「Agent J 主机」, never a bare 电脑; no Face ID on this sheet; one language per string', () => {
  for (const k of KEYS) {
    assert.doesNotMatch(zh[k], /电脑|计算机|Face ID|passkey/i, k);
    assert.doesNotMatch(en[k], /computer|Face ID|passkey|[一-鿿]/i, k);
  }
  assert.match(zh['p570c.migText'], /Agent J 主机自己的系统定时/);
  assert.match(zh['p570c.migText'], /不经手机审批/);
  assert.match(zh['p570c.migText'], /花钱、删数据等步骤也不再问/);
  assert.match(zh['p570c.migText'], /马上试跑一次，失败自动退回/);
  assert.match(zh['p570c.backText'], /保持停用/);
  assert.match(zh['p570c.backText'], /「启用」/);
  assert.match(en['p570c.migText'], /Agent J host's own scheduler/);
});
