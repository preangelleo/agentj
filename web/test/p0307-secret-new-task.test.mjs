// 0.30.7 approval round 2 (P541 N4b): a Settings / Agent secret card whose write the host refused because a new task
// started between the confirmed answer and the write ends with a typed reason (`elev_done` / `mail_res` why =
// new_task | new_task_during_validation, approval_special.LiveContextMoved.REASONS). The page says that sentence —
// never the generic 「没有收下」 / failed line, never silence.
import { test } from 'node:test';
import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
const read = (p) => readFileSync(new URL(p, import.meta.url), 'utf8');
const elevate = read('../public/js/elevate.js');
const mail = read('../public/js/mail.js');
const KEYS = { new_task: 'p0307.secret.newTask', new_task_during_validation: 'p0307.secret.newTaskValidated' };

test('elev_done unknown + typed new-task reason shows its own sentence', () => {
  assert.match(elevate, /export const NEW_TASK = \{ new_task: 'p0307\.secret\.newTask', new_task_during_validation: 'p0307\.secret\.newTaskValidated' \};/);
  const fn = elevate.slice(elevate.indexOf('export function done(m)'), elevate.indexOf('export function refused(m)'));
  assert.match(fn, /if \(c && m\.result === 'unknown' && NEW_TASK\[m\.why\]\) notify\(\{ kind: 'error', text: t\(NEW_TASK\[m\.why\]\)/);
});

test('the Settings mail page maps the same reasons before the generic keyFail line', () => {
  const at = mail.indexOf("const newTask = !m?.ok && { new_task: 'p0307.secret.newTask', new_task_during_validation: 'p0307.secret.newTaskValidated' }[m?.why];");
  assert.ok(at > 0);
  assert.match(mail.slice(at), /newTask \? t\(newTask\) : t\('mail\.keyFail'/);
});

test('both sentences exist in every dictionary and say it was not saved + retry', () => {
  for (const f of ['../i18n/web.en.json', '../i18n/web.zh.json', '../i18n/web.zh.src.json', '../i18n/web.zh.override.json']) {
    const d = JSON.parse(read(f));
    for (const k of Object.values(KEYS)) assert.ok(d[k], f + ' ' + k);
    if (f.endsWith('en.json')) {
      assert.match(d[KEYS.new_task_during_validation], /passed verification.*new task.*not saved.*try again/i);
      assert.match(d[KEYS.new_task], /new task.*not saved.*try again/i);
    } else {
      assert.match(d[KEYS.new_task_during_validation], /验证通过.*新任务.*没有保存.*再试一次/);
      assert.match(d[KEYS.new_task], /新任务.*没有保存.*再试一次/);
      for (const k of Object.values(KEYS)) assert.match(d[k], /Agent J 主机/, 'glossary: never a bare 主机');
    }
  }
  const bundle = read('../public/i18n.js');
  for (const k of Object.values(KEYS)) assert.equal(bundle.split(JSON.stringify(k)).length - 1, 2, 'built for zh + en: ' + k);
});
