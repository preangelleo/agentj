// P154 追加 11: the robot library and the header limit — pure functions of js/busy.js and the generated js/robots.js.
import test from 'node:test';
import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
globalThis.window = {};
globalThis.document = { documentElement: { lang: 'zh' } };
const { assign, rowLimit, sanitizeCap, ROBOT_N, MAX_DOTS, MAX_WIDE, robotName } = await import('../public/js/busy.js');
const { ROBOTS } = await import('../public/js/robots.js');
const lib = JSON.parse(readFileSync(new URL('../public/assets/robots.json', import.meta.url), 'utf8'));

test('P154 robots: a library of ≥ 16, generated module in step with robots.json, distinct shapes', () => {
  assert.ok(ROBOT_N >= 16);
  assert.equal(ROBOTS.length, lib.robots.length);
  lib.robots.forEach((r, i) => { assert.equal(ROBOTS[i].id, r.id); assert.equal(ROBOTS[i].zh, r.zh); assert.equal(ROBOTS[i].en, r.en); });
  assert.equal(new Set(lib.robots.map((r) => [r.body, r.face, r.antenna, r.eyes, r.chest].join())).size, lib.robots.length);
  assert.equal(new Set(lib.robots.map((r) => [r.antenna, r.eyes, r.chest].join())).size, 16, 'every robot has its own shape');
  assert.equal(robotName(0), '小蓝');
  assert.ok(!/tengen/i.test(JSON.stringify(lib)));
});

test('P154 robots: one task keeps one robot; colour c first, then a free one of 9–15', () => {
  const t = (id, c, extra = {}) => ({ id, key: id, kind: 'temp', who: null, c, ...extra });
  const a = assign([t('a', 0), t('b', 0), t('c', 3)]);
  assert.equal(a[0].r, 0); assert.ok(a[1].r >= 9 && a[1].r < 16); assert.equal(a[2].r, 3);
  const b = assign([t('b', 0), t('c', 3), t('d', 0)]);      // a finished: b keeps its robot, d gets robot 0 (free again)
  assert.equal(b[0].r, a[1].r); assert.equal(b[1].r, 3); assert.equal(b[2].r, 0);
  const many = assign(Array.from({ length: 16 }, (_, i) => t('m' + i, i % 9)));
  assert.equal(new Set(many.map((x) => x.r)).size, 16, '16 at once: 16 different robots');
  const over = assign(Array.from({ length: 20 }, (_, i) => t('o' + i, i % 9)));
  assert.ok(over.every((x) => x.r >= 0 && x.r < 16));
  const ceo = assign([{ id: 'x1', key: 'x1', kind: 'ceo', who: '玄枢', c: 5 }]);
  const ceo2 = assign([{ id: 'x2', key: 'x2', kind: 'ceo', who: '玄枢', c: 5 }]);
  assert.equal(ceo[0].r, ceo2[0].r, 'a CEO keeps its robot across tasks');
});

test('P154 robots: the header limit — 6 by default, the capacity (≤ 12) on wide screens, never more than 6 on a phone', () => {
  assert.equal(MAX_DOTS, 6); assert.equal(MAX_WIDE, 12);
  assert.equal(rowLimit(null, true), 6); assert.equal(rowLimit(3, true), 6); assert.equal(rowLimit(10, true), 10);
  assert.equal(rowLimit(40, true), 12); assert.equal(rowLimit(10, false), 6);
  assert.equal(sanitizeCap({ cap: 9 }), 9); assert.equal(sanitizeCap({ cap: 0 }), null); assert.equal(sanitizeCap({ cap: 2.5 }), null);
  assert.equal(sanitizeCap({ cap: '9' }), null); assert.equal(sanitizeCap({}), null); assert.equal(sanitizeCap({ cap: 65 }), null);
});
