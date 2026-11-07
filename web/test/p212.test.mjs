// P212 (PROTOCOL §29): the phone's form rules (formwire.js) give the host's answers (forms.py) on the shared table
// protocol/vectors/forms.json; the page's turn.form is sanitised; snap.toPage carries it and the owner page's src.form.
import test from 'node:test';
import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import { holds, check, shown, sanitize, questions } from '../public/js/formwire.js';
import { toPage } from '../public/js/snap.js';

const V = JSON.parse(readFileSync(new URL('../../protocol/vectors/forms.json', import.meta.url), 'utf8'));
// the host's normalised spec of V.form (options as {value,label}, one section) — what form_get / turn.form carry
function norm(raw) {
  const opts = (o) => o.map((x) => (typeof x === 'string' ? { value: x, label: x } : x));
  return { title: raw.title, sections: raw.sections.map((s) => ({ id: s.id, questions: s.questions.map((q) => {
    const x = { ...q };
    if (x.options) x.options = opts(x.options);
    if (x.type === 'multi') { x.min ??= x.required ? 1 : 0; x.max ??= x.options.length + (x.other ? 1 : 0); }
    if (x.type === 'text') x.max_length ??= 500;
    if (x.type === 'rating') x.scale ??= 5;
    return x;
  }) })) };
}

test('show_if: the same table as forms.holds', () => {
  for (const [c, ans, want] of V.holds) assert.equal(holds(c, ans), want, JSON.stringify([c, ans]));
});

test('answers: the same verdicts and clean values as forms.check', () => {
  const spec = norm(V.form);
  for (const [ans, other, want, clean] of V.check) {
    const r = check(spec, ans, other);
    assert.equal(r.why, want, JSON.stringify(ans));
    if (clean) assert.deepEqual(r.clean, clean, JSON.stringify(ans));
  }
});

test('hidden questions are neither checked nor sent; drafts need nothing', () => {
  const spec = norm(V.form);
  const r = check(spec, { mode: 'online', people: 99 });
  assert.equal(r.why, 'ok');
  assert.ok(!('people' in r.clean) && r.skipped.includes('people'));
  assert.equal(check(spec, {}, {}, { draft: true }).why, 'ok');
  assert.ok(!shown(spec, { mode: 'online' }).has('people'));
  assert.ok(shown(spec, { mode: 'onsite' }).has('people'));
  assert.equal(questions(spec).length, 6);
});

test('every problem is reported per question (the page shows each one)', () => {
  const r = check(norm(V.form), { mail: 'x', rate: 9 });
  assert.equal(r.why, 'required');
  assert.deepEqual(Object.keys(r.errs).sort(), ['mail', 'mode', 'rate']);
  assert.equal(r.errs.mode.k, 'form.err.required');
  assert.equal(r.errs.mail.k, 'form.err.email');
});

test('attachments: only this submission\'s files, at most max_files', () => {
  const spec = { title: 't', sections: [{ id: 's', questions: [{ id: 'f', type: 'file', label: 'F', accept: ['image'], max_files: 2 }] }] };
  assert.equal(check(spec, { f: ['a'] }, {}, { files: new Set(['a']) }).why, 'ok');
  assert.equal(check(spec, { f: ['a', 'b', 'c'] }, {}, { files: new Set(['a', 'b', 'c']) }).why, 'answer');
  assert.equal(check(spec, { f: ['z'] }, {}, { files: new Set(['a']) }).why, 'answer');
  assert.equal(check(spec, { f: ['z'] }, {}, { draft: true }).why, 'ok', 'a draft never carries files');
});

test('turn.form is sanitised; a Deep Dive never carries its spec on the page', () => {
  assert.equal(sanitize({ id: 'nope' }), null);
  assert.equal(sanitize(null), null);
  const l = sanitize({ id: 'f_0123456789abcdef', mode: 'light', title: 'x'.repeat(200), n: 1, state: 'weird', tap: true, spec: { sections: [] } });
  assert.equal(l.title.length, 80);
  assert.equal(l.state, 'open');
  assert.ok(l.spec && l.tap);
  const d = sanitize({ id: 'f_0123456789abcdef', mode: 'deep', state: 'answered', spec: { sections: [] }, rows: [['a', 'b', true], 'junk', ['c', 1]], at: 5, by: 'iPhone' });
  assert.equal(d.spec, undefined);
  assert.deepEqual(d.rows, [['a', 'b', true], ['c', '1', false]]);
});

test('snap.toPage carries the form and the owner page\'s form tag', () => {
  const p = toPage({ id: 3, ts: 1, src: { k: 'agent', text: '' }, reply: { text: 'hi' }, end: 'done', form: { id: 'f_0123456789abcdef', mode: 'light', title: 'T', n: 1, state: 'open' } });
  assert.equal(p.form.id, 'f_0123456789abcdef');
  const o = toPage({ id: 4, ts: 1, src: { k: 'phone', text: '〔表单回复〕', form: { id: 'f_0123456789abcdef', title: 'T' } }, reply: { text: '' }, end: 'open' });
  assert.deepEqual(o.source.form, { id: 'f_0123456789abcdef', title: 'T' });
  assert.equal(toPage({ id: 5, ts: 1, src: { k: 'agent' }, reply: { text: '' }, end: 'done' }).form, null);
});
