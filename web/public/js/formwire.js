// formwire.js — P212 (PROTOCOL §29): the wire side of a page's form (turn.form, host forms.view) → a safe plain object.
// No DOM here (snap.js and the node tests import it); the renderer is forms.js. The rules mirror host forms.py
// (shared table: protocol/vectors/forms.json).
export const OTHER = '_other';
const PATTERNS = { email: /^[^@\s]{1,64}@[^@\s]{1,190}\.[^@\s]{1,63}$/, phone: /^\+?[0-9][0-9 ()-]{5,24}$/, url: /^https?:\/\/\S{3,500}$/ };
export const DATES = { date: /^\d{4}-\d{2}-\d{2}$/, time: /^\d{2}:\d{2}$/, datetime: /^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}$/ };

// ---------------------------------------------------------------- the rules (same as host forms.py)
export const empty = (v) => v === undefined || v === null || v === '' || (Array.isArray(v) && !v.length) || (typeof v === 'object' && !Array.isArray(v) && !Object.keys(v).length);
export function holds(c, ans) {
  if (!c) return true;
  if (c.all) return c.all.every((x) => holds(x, ans));
  if (c.any) return c.any.some((x) => holds(x, ans));
  const v = ans[c.q];
  const many = Array.isArray(v) ? v : empty(v) ? [] : [v];
  if ('answered' in c) return !empty(v) === c.answered;
  if (empty(v)) return 'ne' in c || 'not_in' in c;
  if ('eq' in c) return many.includes(c.eq);
  if ('ne' in c) return !many.includes(c.ne);
  if ('in' in c) return many.some((x) => c.in.includes(x));
  if ('not_in' in c) return !many.some((x) => c.not_in.includes(x));
  if (typeof v !== 'number') return false;
  return 'gt' in c ? v > c.gt : v < c.lt;
}
export const questions = (spec) => spec.sections.flatMap((s) => s.questions);
const vals = (q) => (q.options || []).map((o) => o.value).concat(q.other ? [OTHER] : []);
const isNum = (v) => typeof v === 'number' && Number.isFinite(v);
function validDate(v, typ) {
  if (typeof v !== 'string' || !DATES[typ].test(v)) return false;
  if (typ === 'time') { const [h, m] = v.split(':').map(Number); return h < 24 && m < 60; }
  const [y, mo, d] = v.slice(0, 10).split('-').map(Number), dt = new Date(Date.UTC(y, mo - 1, d));
  if (dt.getUTCFullYear() !== y || dt.getUTCMonth() !== mo - 1 || dt.getUTCDate() !== d) return false;
  if (typ === 'datetime') { const [h, m] = v.slice(11).split(':').map(Number); return h < 24 && m < 60; }
  return true;
}
/** One answer → [clean value, null] or [null, {k: message key, vars}]. files: bid set of this submission (null = none here). */
export function value(q, v, files = null) {
  const bad = (k, vars) => [null, { k, vars }];
  const T = q.type;
  if (['single', 'buttons', 'dropdown'].includes(T)) return vals(q).includes(v) ? [v, null] : bad('form.err.choice');
  if (T === 'multi') {
    const all = vals(q);
    if (!Array.isArray(v) || new Set(v).size !== v.length || v.some((x) => !all.includes(x))) return bad('form.err.choice');
    if (v.length && !((q.min || 0) <= v.length && v.length <= (q.max ?? all.length))) return bad('form.err.pick', { min: q.min || 0, max: q.max });
    return [all.filter((x) => v.includes(x)), null];
  }
  if (T === 'text' || T === 'longtext') {
    if (typeof v !== 'string') return bad('form.err.choice');
    const s = v.replace(/\r\n?/g, '\n').trim();
    if (s.length > q.max_length) return bad('form.err.long', { n: q.max_length });
    if (s && q.pattern && !PATTERNS[q.pattern].test(s)) return bad('form.err.' + q.pattern);
    return [s, null];
  }
  if (T === 'number' || T === 'slider') {
    if (!isNum(v)) return bad('form.err.number');
    if (('min' in q && v < q.min) || ('max' in q && v > q.max)) return bad('form.err.range', { min: q.min ?? '…', max: q.max ?? '…' });
    return [v, null];
  }
  if (DATES[T]) {
    if (!validDate(v, T)) return bad('form.err.date');
    if (('min' in q && v < q.min) || ('max' in q && v > q.max)) return bad('form.err.range', { min: q.min ?? '…', max: q.max ?? '…' });
    return [v, null];
  }
  if (T === 'rating') return Number.isInteger(v) && v >= 1 && v <= q.scale ? [v, null] : bad('form.err.range', { min: 1, max: q.scale });
  if (T === 'rank') {
    const all = q.options.map((o) => o.value);
    return Array.isArray(v) && v.length === all.length && [...v].sort().join('\u0000') === [...all].sort().join('\u0000') ? [v.slice(), null] : bad('form.err.choice');
  }
  if (T === 'file') {
    if (files === null || !Array.isArray(v)) return bad('form.err.choice');
    if (v.length > q.max_files) return bad('form.err.files', { n: q.max_files });
    return v.every((b) => files.has(b)) ? [v.slice(), null] : bad('form.err.choice');
  }
  return bad('form.err.choice');
}
/** → {why: 'ok' | 'required' | 'answer', clean, skipped, errs: {qid: {k, vars}}} — every problem, not just the first. */
export function check(spec, answers, other = {}, { draft = false, files = null } = {}) {
  const clean = {}, skipped = [], errs = {}, oth = {};
  let why = 'ok';
  const qs = questions(spec), ids = new Set(qs.map((q) => q.id));
  if (Object.keys(answers).some((k) => !ids.has(k))) return { why: 'answer', clean, skipped, errs };
  for (const q of qs) {
    if (q.type === 'note') continue;
    if (!holds(q.show_if, clean)) { skipped.push(q.id); continue; }
    const v = answers[q.id];
    if (empty(v)) {
      if (q.required && !draft) { errs[q.id] = { k: 'form.err.required' }; if (why === 'ok') why = 'required'; }
      skipped.push(q.id); continue;
    }
    if (q.type === 'file' && draft) continue;
    const [c, e] = value(q, v, files);
    if (e) { errs[q.id] = e; if (why === 'ok') why = 'answer'; continue; }
    clean[q.id] = c;
    const picked = Array.isArray(c) ? c : [c];
    if (q.other && picked.includes(OTHER)) {
      const o = typeof other[q.id] === 'string' ? other[q.id].trim() : '';
      if (!o) { if (!draft) { errs[q.id] = { k: 'form.err.other' }; if (why === 'ok') why = 'required'; } }
      else if (o.length > 200) { errs[q.id] = { k: 'form.err.long', vars: { n: 200 } }; if (why === 'ok') why = 'answer'; }
      else oth[q.id] = o;
    }
    if (q.type === 'multi' && !draft && c.length < (q.min || 0)) { errs[q.id] = { k: 'form.err.pick', vars: { min: q.min, max: q.max } }; if (why === 'ok') why = 'answer'; }
  }
  if (Object.keys(oth).length) clean._other = oth;
  return { why, clean, skipped, errs };
}
/** The questions shown now (show_if against the valid answers so far). */
export function shown(spec, answers) {
  const { clean } = check(spec, answers, {}, { draft: true });
  const ok = {};
  const out = new Set();
  for (const q of questions(spec)) {
    if (!holds(q.show_if, ok)) continue;
    out.add(q.id);
    if (q.id in clean) ok[q.id] = clean[q.id];
  }
  return out;
}

export function sanitize(f) {
  if (!f || typeof f !== 'object' || typeof f.id !== 'string' || !/^f_[0-9a-f]{16}$/.test(f.id)) return null;
  const st = ['open', 'answered', 'expired', 'cancelled'].includes(f.state) ? f.state : 'open';
  const out = { id: f.id, mode: f.mode === 'deep' ? 'deep' : 'light', title: String(f.title || '').slice(0, 80), n: Number(f.n) || 0,
    state: st, exp: Number(f.exp) || 0, secs: Number(f.secs) || 1, tap: f.tap === true, files: Number(f.files) || 0 };
  if (out.mode === 'light' && f.spec && Array.isArray(f.spec.sections)) out.spec = f.spec;
  if (st === 'answered') {
    out.at = Number(f.at) || 0; out.by = String(f.by || '').slice(0, 64);
    out.rows = Array.isArray(f.rows) ? f.rows.slice(0, 60).filter((r) => Array.isArray(r) && r.length >= 2).map((r) => [String(r[0]), String(r[1]), r[2] === true]) : [];
  }
  return out;
}
