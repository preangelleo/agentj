// P426 #181-3 · skill health on the 技能 page (js/skillhealth.js, wired in five-pages.js) — node unit checks against a minimal
// DOM: the use line 「最近使用：x 天前 · y 次」, 「—」 when the computer saw no use (never a guess), 「很久没用了」 only on what the
// computer marked stale, 「归档」 only for the owner's own skills (never Agent J's), only on long-unused cards in the list and
// only when the computer answers sk1; an older computer (no skill_use) adds nothing; the tap's result lines; both languages.
import { test } from 'node:test';
import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';

class El {
  constructor(tag) { this.tagName = tag.toUpperCase(); this.kids = []; this.dataset = {}; this.className = ''; this.disabled = false; this.onclick = null; this._t = null; }
  append(...ns) { for (const n of ns) this.kids.push(typeof n === 'string' ? text(n) : n); }
  get textContent() { return this._t !== null ? this._t : this.kids.map((k) => k.textContent).join(''); }
  set textContent(v) { this.kids = []; this._t = String(v); }
  click() { if (!this.disabled && this.onclick) this.onclick({ target: this }); }
  find(cls) { const out = []; const walk = (n) => { for (const k of n.kids) { if (k instanceof El) { if (k.className.split(' ').includes(cls)) out.push(k); walk(k); } } }; walk(this); return out; }
}
const text = (s) => { const n = new El('#text'); n._t = String(s); return n; };
globalThis.window ??= globalThis;
globalThis.document = { createElement: (t) => new El(t), createTextNode: text, addEventListener() {}, querySelectorAll: () => [] };

const SH = await import('../public/js/skillhealth.js');
const FP = readFileSync(new URL('../public/js/five-pages.js', import.meta.url), 'utf8');
const SESSION = readFileSync(new URL('../public/js/session.js', import.meta.url), 'utf8');
const NOW = 1_800_000_000;
const INFO = { tracked: true, days: 30 };

test('use line: days ago and count; 「—」 when nothing was seen', () => {
  assert.equal(SH.ago(NOW - 60, NOW), '今天');
  assert.equal(SH.ago(NOW - 86400 - 5, NOW), '1 天前');
  assert.equal(SH.ago(NOW - 3 * 86400, NOW), '3 天前');
  assert.equal(SH.useLine({ use: { n: 5, last: NOW - 3 * 86400 } }, NOW), '最近使用：3 天前 · 5 次');
  assert.equal(SH.useLine({}, NOW), '最近使用：—');
  const c = new El('article');
  assert.ok(SH.decorate(c, { name: 'weekly', scope: 'global', arch: true }, INFO, { canAct: true, list: true, now: NOW }));
  assert.equal(c.find('fp-skill-use')[0].textContent, '最近使用：—');
  assert.equal(c.find('fp-stale').length, 0, 'no badge without the computer saying stale');
  assert.equal(c.find('fp-skill-act').length, 0, 'the list offers 归档 only on a long-unused card');
});

test('long unused: badge + 归档 for the owner\'s own skill; the tap asks for skill_archive', () => {
  const acts = [];
  const c = new El('article');
  SH.decorate(c, { name: 'old-report', scope: 'global', arch: true, stale: true, use: { n: 2, last: NOW - 90 * 86400 } }, INFO,
    { canAct: true, list: true, now: NOW, act: (type, name, box) => acts.push([type, name, box === c]) });
  assert.equal(c.dataset.stale, '1');
  assert.equal(c.find('fp-stale')[0].textContent, '很久没用了（超过 30 天）');
  assert.equal(c.find('fp-skill-use')[0].textContent, '最近使用：90 天前 · 2 次');
  const [b] = c.find('fp-skill-act');
  assert.equal(b.textContent, '归档');
  b.click();
  assert.deepEqual(acts, [['skill_archive', 'old-report', true]]);
});

test('Agent J\'s own skills are never offered for archive; no sk1 → no button; older computer → nothing', () => {
  const bundled = new El('article');
  SH.decorate(bundled, { name: 'agentj-commands', scope: 'bundled', stale: true }, INFO, { canAct: true, list: true, now: NOW });
  assert.equal(bundled.find('fp-skill-act').length, 0);
  const noCap = new El('article');
  SH.decorate(noCap, { name: 'x', scope: 'global', arch: true, stale: true }, INFO, { canAct: false, list: true, now: NOW });
  assert.equal(noCap.find('fp-skill-act').length, 0);
  const sheet = new El('section');
  SH.decorate(sheet, { name: 'x', scope: 'global', arch: true }, INFO, { canAct: true, list: false, now: NOW });
  assert.equal(sheet.find('fp-skill-act').length, 1, 'the detail sheet offers 归档 for any archivable skill');
  const old = new El('article');
  assert.equal(SH.decorate(old, { name: 'x', arch: true, stale: true }, undefined, { canAct: true }), false);
  assert.equal(old.kids.length, 0);
});

test('result lines, and the page wiring (archived group, restore, untracked note, sk1)', () => {
  assert.equal(SH.resultText('skill_archive', { ok: true }), '已归档。想用时在下面「已归档」里点「恢复」。');
  assert.equal(SH.resultText('skill_restore', { ok: true }), '已恢复，又能用了。');
  assert.equal(SH.resultText('skill_archive', { ok: false, why: 'official' }), 'Agent J 自带的技能不归档。');
  assert.equal(SH.resultText('skill_archive', { ok: false, why: 'weird' }), '没做成，请稍后再试。');
  assert.equal(SH.resultText('skill_archive', { t: 'timeout' }), '没做成，请稍后再试。');
  assert.match(FP, /pic\.skills_archived/);
  assert.match(FP, /skillAct\('skill_restore',a\.name,c\)/);
  assert.match(FP, /t\('p426\.untracked'\)/);
  assert.match(FP, /health\(sum,s,false\)/, 'the detail sheet carries the line too');
  assert.match(SESSION, /peer\.sk1 = peer\.wf1 && m\.caps\.includes\('sk1'\)/);
});

test('S6: 归档 / 恢复 are signed phone controls — same object text as controls.py, wire copies byte-identical', async () => {
  const w = await import('../../protocol/wire.js');
  for (const a of ['skill_archive', 'skill_restore']) assert.ok(w.CONTROL_ACTIONS.includes(a), a);
  assert.equal(w.controlObject('skill_archive', { op: 'archive', name: 'old-report' }), 'archive\nold-report');
  assert.equal(w.controlObject('skill_restore', { op: 'restore', name: 'old-report' }), 'restore\nold-report');
  assert.deepEqual(SH.signedArgs('skill_archive', 'old-report'),
    ['skill_archive', { op: 'archive', name: 'old-report' }, { t: 'skill_archive', name: 'old-report' }]);
  assert.deepEqual(SH.signedArgs('skill_restore', 'x'), ['skill_restore', { op: 'restore', name: 'x' }, { t: 'skill_restore', name: 'x' }]);
  const py = readFileSync(new URL('../../host/agentj/controls.py', import.meta.url), 'utf8');
  assert.match(py, /ACTIONS \+= \("skill_archive", "skill_restore"\)/);
  assert.match(py, /if action in \("skill_archive", "skill_restore"\):[^\n]*\n\s+return f"\{obj\['op'\]\}\\n\{obj\['name'\]\}"/);
  assert.equal(readFileSync(new URL('../public/proto/wire.js', import.meta.url), 'utf8'),
    readFileSync(new URL('../../protocol/wire.js', import.meta.url), 'utf8'));
  assert.match(FP, /signedWrite\(\.\.\.SH\.signedArgs\(type,name\)\)/, 'the tap is signed');
  assert.doesNotMatch(FP, /ask1\(\{t:type,name\}/, 'never the old unsigned request');
});

test('strings in both languages', () => {
  const en = JSON.parse(readFileSync(new URL('../i18n/web.en.json', import.meta.url), 'utf8'));
  for (const f of ['web.en.json', 'web.zh.json', 'web.zh.src.json', 'web.zh.override.json']) {
    const d = JSON.parse(readFileSync(new URL('../i18n/' + f, import.meta.url), 'utf8'));
    for (const k of Object.keys(en).filter((k) => k.startsWith('p426.'))) assert.ok(typeof d[k] === 'string' && d[k], `${f}: ${k}`);
  }
  for (const [k, v] of Object.entries(en)) if (k.startsWith('p426.')) assert.ok(!/[一-鿿]/.test(v), k);
  assert.ok(Object.keys(en).filter((k) => k.startsWith('p426.')).length >= 16);
});

test('Jarvis ruling 4: a symbolic-link skill has no 归档 anywhere; its detail sheet says why; a stale tap says why', () => {
  const card = new El('article');
  SH.decorate(card, { name: 'workflow-design-bible', scope: 'global', link: true, stale: true }, INFO, { canAct: true, list: true, now: NOW });
  assert.equal(card.find('fp-skill-act').length, 0);
  assert.equal(card.find('fp-skill-link').length, 0, 'the list stays short: the reason is in the sheet');
  const sheet = new El('section');
  SH.decorate(sheet, { name: 'workflow-design-bible', scope: 'global', link: true }, INFO, { canAct: true, list: false, now: NOW });
  assert.equal(sheet.find('fp-skill-act').length, 0);
  assert.match(sheet.find('fp-skill-link')[0].textContent, /符号链接/);
  assert.match(SH.resultText('skill_archive', { ok: false, why: 'link' }), /符号链接/);
});
