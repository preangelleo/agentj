if (typeof document !== 'undefined' && !document.querySelector('link[data-agentj-forms]')) {
  const css = document.createElement('link'); css.rel = 'stylesheet';
  css.href = new URL('../forms.css', import.meta.url).href; css.dataset.agentjForms = '1'; document.head.appendChild(css);
}
// forms.js — P212 (PROTOCOL §29): the Agent's forms in the reply card, from one schema (agentj.form/v1, host forms.py).
//   快选 / Quick Pick (turn.form.mode light): drawn under the reply's words. A lone single-choice / button question = one tap
//     submits (turn.form.tap); several questions = fill in, then 提交. Never pre-selected (a slip would submit).
//   深聊卡 / Deep Dive (deep): an entry block in the card → the full-screen page (#fdeep): sections, progress, recommendations
//     (pre-selected when the form says so), conditional questions, attachments (uploaded with the submission through the
//     say's own blob channel, 200 MB each), a review screen, then one submission. Drafts go to the computer (form_draft,
//     every 2 s while typing, at once on close) and stay sealed on this phone too (putSealed 'forms'), so another remote
//     continues where this one stopped.
// The answers go out as the owner's own message (say + form) and are re-checked by the host; first submission wins. A
// queued answer is withdrawn with the composer's × like any message (the form opens again). Every string of a form is text:
// textContent only, never HTML. The checks below mirror forms.py (shared table: protocol/vectors/forms.json).
import { t, onLang, lang } from './t.js';
import { mk as nativeMk, toast } from './ui.js';
// Exact factory for this schema/view; ordinary aj-action controls remain independent actors.
function mk(tag, cls, text) {
  cls=cls?.replace(/\baj-btn(?=\b|--)/g,'aj-action');
  const action=typeof cls==='string'&&cls.split(/\s+/).includes('aj-action');
  return nativeMk(tag,cls,text,action?'aj-action':'aj-inline-component',action?'dialog':undefined);
}
import { registerLayer } from './layers.js';

import { OTHER, empty, holds, questions, value, check, shown, DATES } from './formwire.js';
export { OTHER, empty, holds, questions, value, check, shown };
const ACCEPT = { image: 'image/*', audio: 'audio/*', video: 'video/*', file: '' };
const DRAFT_MS = 2000;

function labelOf(q, v, other) {
  const opts = Object.fromEntries((q.options || []).map((o) => [o.value, o.label]));
  const oth = t('form.other') + (other[q.id] ? t('form.colon') + other[q.id] : '');
  const one = (x) => (x === OTHER ? oth : opts[x] ?? String(x));
  if (['single', 'buttons', 'dropdown'].includes(q.type)) return one(v);
  if (q.type === 'multi') return v.map(one).join(t('form.sep'));
  if (q.type === 'rank') return v.map(one).join(' > ');
  if (q.type === 'rating') return `${v} / ${q.scale}`;
  if (q.type === 'file') return t('form.nFiles', { n: v.length });
  return String(v) + (q.unit ? ' ' + q.unit : '');
}
/** The readable text that goes with the answers (the host writes its own for the Agent; this is the phone's copy). */
export function summary(spec, clean) {
  const other = clean._other || {};
  const lines = [t('form.sumHead', { title: spec.title })];
  for (const q of questions(spec)) if (q.id in clean) lines.push(`${q.label} → ${labelOf(q, clean[q.id], other)}`);
  return lines.join('\n');
}
export { sanitize } from './formwire.js';

// ---------------------------------------------------------------- page state
const local = new Map();           // form id → {answers, other, files: {qid: [entry]}, sec, at, sending, sent}
const MAX_BYTES = 209715200;       // = blobs.MAX_BYTES (200 MiB a file)
const anyMime = (type) => { const t = String(type || '').split(';')[0].trim().toLowerCase();
  return /^[a-z0-9][a-z0-9!#$&^_.+-]{0,63}\/[a-z0-9][a-z0-9!#$&^_.+-]{0,126}$/.test(t) ? t : 'application/octet-stream'; };   // = blobs.anyMime
// The phone hands over everything that talks to the computer (relay.js init): say = sayForm (the composer's queue /
// withdraw / rocket), ask1 / isReady / sendApp (session.js), Upload (blobs.js), getSealed / putSealed (store.js).
// demo = the website's template gallery: the same renderer, no computer, nothing sent or stored.
let hooks = { say: null, demo: false, ask1: null, isReady: () => false, sendApp: null, Upload: null, getSealed: null, putSealed: null };
export function configure(h) { hooks = { ...hooks, ...h }; }
const isReady = () => !hooks.demo && hooks.isReady();
const sendApp = (m) => (hooks.sendApp ? hooks.sendApp(m) : Promise.reject(new Error('offline')));
let sealed = null;
async function sealedAll() { if (sealed) return sealed; sealed = (hooks.demo || !hooks.getSealed ? null : await hooks.getSealed('forms').catch(() => null)) || {}; return sealed; }
function sealLater(id) {
  if (hooks.demo) return;
  const s = local.get(id);
  sealedAll().then((all) => {
    if (s && !s.sent) all[id] = { answers: s.answers, other: s.other, sec: s.sec, at: s.at };
    else delete all[id];
    const keys = Object.keys(all);
    for (const k of keys.slice(0, Math.max(0, keys.length - 20))) delete all[k];
    hooks.putSealed?.('forms', all)?.catch(() => {});
  });
}
function stateOf(id, spec, draft) {
  let s = local.get(id);
  if (!s) {
    s = { answers: {}, other: {}, files: {}, sec: 0, at: 0 };
    for (const q of questions(spec)) {
      if (spec.preselect && q.rec !== undefined && q.type !== 'file') s.answers[q.id] = q.rec;   // Grill me: agree = keep it
      else if (q.default !== undefined) s.answers[q.id] = q.default;
    }
    local.set(id, s);
  }
  if (draft && Number(draft.at) > s.at) {
    s.answers = { ...s.answers, ...(draft.answers || {}) }; s.other = { ...(draft.other || {}) };
    s.sec = Number.isInteger(draft.sec) ? draft.sec : s.sec; s.at = Number(draft.at); s.draftBy = draft.by || '';
  }
  return s;
}
export function forgetAll() { local.clear(); seen.clear(); sealed = {}; closeDeep(true); }

// ---------------------------------------------------------------- controls
function opt(o, cls, role) {
  const b = mk('button', 'fopt ' + cls); b.type = 'button'; b.setAttribute('role', role); b.dataset.v = o.value;
  const mark = mk('span', 'fmark'); mark.setAttribute('aria-hidden', 'true');
  const txt = mk('span', 'ftxt'); txt.append(mk('span', 'fl', o.label));
  if (o.hint) txt.append(mk('small', 'fh', o.hint));
  b.append(mark, txt);
  return b;
}
function recTag(b) { const r = mk('span', 'frec', t('form.rec')); b.append(r); }
/** One question's control. st = the form's state; onChange(id) after every edit; ctx = {tap, submit, deep, fid}. */
function control(q, st, onChange, ctx) {
  const box = mk('div', 'fctl');
  const set = (v) => { if (empty(v)) delete st.answers[q.id]; else st.answers[q.id] = v; onChange(q.id); };
  const T = q.type;
  if (T === 'single' || T === 'buttons' || T === 'multi') {
    const many = T === 'multi', row = mk('div', T === 'buttons' ? 'fbtns' : 'fopts');
    row.setAttribute('role', many ? 'group' : 'radiogroup');
    const list = q.options.concat(q.other ? [{ value: OTHER, label: t('form.other') }] : []);
    const otherIn = q.other ? mk('input', 'fin fother') : null;
    const paint = () => {
      const v = st.answers[q.id], on = many ? v || [] : [v];
      for (const b of row.children) {
        const sel = on.includes(b.dataset.v);
        b.classList.toggle('on', sel); b.setAttribute('aria-checked', String(sel));
        if (many) b.disabled = !sel && q.max && on.length >= q.max;
      }
      if (otherIn) otherIn.hidden = !on.includes(OTHER);
    };
    for (const o of list) {
      const b = opt(o, many ? 'chk' : T === 'buttons' ? 'btn' : 'radio', many ? 'checkbox' : 'radio');
      if (q.rec !== undefined && (many ? (q.rec || []).includes(o.value) : q.rec === o.value)) recTag(b);
      b.addEventListener('click', () => {
        if (many) { const cur = (st.answers[q.id] || []).slice(), i = cur.indexOf(o.value); if (i >= 0) cur.splice(i, 1); else cur.push(o.value); set(cur); }
        else set(o.value);
        paint();
        if (ctx.tap && !many && o.value !== OTHER) ctx.submit();
      });
      row.append(b);
    }
    box.append(row);
    if (otherIn) {
      otherIn.type = 'text'; otherIn.maxLength = 200; otherIn.placeholder = t('form.otherHint'); otherIn.value = st.other[q.id] || '';
      otherIn.setAttribute('aria-label', t('form.other'));
      otherIn.addEventListener('input', () => { st.other[q.id] = otherIn.value; onChange(q.id); });
      box.append(otherIn);
    }
    paint();
    return box;
  }
  if (T === 'dropdown') {
    const s = mk('select', 'fin');
    s.append(new Option(t('form.choose'), ''));
    for (const o of q.options) s.append(new Option(o.label + (q.rec === o.value ? ' · ' + t('form.rec') : ''), o.value));
    s.value = st.answers[q.id] || '';
    s.addEventListener('change', () => set(s.value || undefined));
    box.append(s);
    return box;
  }
  if (T === 'text' || T === 'longtext') {
    const i = mk(T === 'text' ? 'input' : 'textarea', 'fin');
    if (T === 'text') i.type = { email: 'email', phone: 'tel', url: 'url' }[q.pattern] || 'text';
    if (q.pattern === 'phone') i.inputMode = 'tel';
    i.maxLength = q.max_length; i.value = st.answers[q.id] || '';
    if (q.placeholder) i.placeholder = q.placeholder;
    if (T === 'longtext') { i.rows = ctx.deep ? 4 : 3; }
    i.addEventListener('input', () => set(i.value));
    box.append(i);
    return box;
  }
  if (T === 'number') {
    const row = mk('div', 'fnum'), i = mk('input', 'fin');
    i.type = 'number'; i.inputMode = 'decimal';
    for (const k of ['min', 'max', 'step']) if (k in q) i[k] = String(q[k]);
    if (!('step' in q)) i.step = 'any';
    i.value = st.answers[q.id] ?? '';
    i.addEventListener('input', () => set(i.value === '' ? undefined : Number(i.value)));
    row.append(i);
    if (q.unit) row.append(mk('span', 'funit', q.unit));
    box.append(row);
    return box;
  }
  if (T === 'slider') {
    const row = mk('div', 'fslide'), i = mk('input'), out = mk('output', 'fval');
    i.type = 'range'; i.min = String(q.min); i.max = String(q.max); i.step = String(q.step ?? 1);
    const v = st.answers[q.id];
    i.value = String(v ?? (q.rec ?? (q.min + q.max) / 2));
    const paint = () => { const a = st.answers[q.id]; out.textContent = a === undefined ? '—' : a + (q.unit ? ' ' + q.unit : ''); row.classList.toggle('unset', a === undefined); };
    i.addEventListener('input', () => { set(Number(i.value)); paint(); });
    i.setAttribute('aria-label', q.label);
    row.append(i, out);
    box.append(row);
    if (q.labels) { const sc = mk('div', 'fscale'); sc.append(mk('span', '', q.labels[0]), mk('span', '', q.labels[1])); box.append(sc); }
    paint();
    return box;
  }
  if (DATES[T]) {
    const i = mk('input', 'fin'); i.type = T === 'datetime' ? 'datetime-local' : T;
    if (q.min) i.min = q.min; if (q.max) i.max = q.max;
    i.value = st.answers[q.id] || '';
    i.addEventListener('input', () => set(i.value || undefined));
    box.append(i);
    return box;
  }
  if (T === 'rating') {
    const row = mk('div', 'fstars' + (q.scale > 5 ? ' ten' : '')); row.setAttribute('role', 'radiogroup');
    const paint = () => { const v = st.answers[q.id] || 0; [...row.children].forEach((b, k) => { b.classList.toggle('on', k < v); b.setAttribute('aria-checked', String(k + 1 === v)); }); };
    for (let k = 1; k <= q.scale; k++) {
      const b = mk('button', 'fstar', q.scale > 5 ? String(k) : '★'); b.type = 'button'; b.setAttribute('role', 'radio');
      b.setAttribute('aria-label', t('form.stars', { n: k, of: q.scale }));
      b.addEventListener('click', () => { set(st.answers[q.id] === k ? undefined : k); paint(); });
      row.append(b);
    }
    box.append(row); paint();
    return box;
  }
  if (T === 'rank') {
    const list = mk('ol', 'frank');
    const labels = Object.fromEntries(q.options.map((o) => [o.value, o.label]));
    const order = () => st.answers[q.id] || q.options.map((o) => o.value);
    const paint = () => {
      list.replaceChildren();
      const cur = order();
      cur.forEach((v, k) => {
        const li = mk('li', 'frow'); li.dataset.v = v;
        li.append(mk('span', 'fn', String(k + 1)), mk('span', 'fl', labels[v]));
        const up = mk('button', 'fmv', '↑'), dn = mk('button', 'fmv', '↓');
        up.type = dn.type = 'button'; up.disabled = k === 0; dn.disabled = k === cur.length - 1;
        up.setAttribute('aria-label', t('form.up', { x: labels[v] })); dn.setAttribute('aria-label', t('form.down', { x: labels[v] }));
        const mv = (d) => { const c = order().slice(); [c[k], c[k + d]] = [c[k + d], c[k]]; set(c); paint(); (d < 0 ? list.children[k - 1] : list.children[k + 1])?.querySelector(d < 0 ? '.fmv' : '.fmv:last-child')?.focus(); };
        up.addEventListener('click', () => mv(-1)); dn.addEventListener('click', () => mv(1));
        li.append(up, dn); list.append(li);
      });
      keep.hidden = !!st.answers[q.id];
    };
    const keep = mk('button', 'flink', t('form.rankKeep')); keep.type = 'button';
    keep.addEventListener('click', () => { set(order().slice()); paint(); });
    box.append(list, keep); paint();
    return box;
  }
  if (T === 'file') {
    const chips = mk('div', 'ffiles'), add = mk('button', 'fadd', '＋ ' + t('form.addFile')); add.type = 'button';
    const pick = mk('input'); pick.type = 'file'; pick.hidden = true; pick.multiple = q.max_files > 1;
    pick.accept = q.accept.map((a) => ACCEPT[a]).filter(Boolean).join(',') + (q.accept.includes('file') ? '' : '');
    if (q.accept.includes('file')) pick.removeAttribute('accept');
    const list = () => (st.files[q.id] ||= []);
    const paint = () => {
      chips.replaceChildren();
      for (const f of list()) {
        const c = mk('span', 'fchip'); c.append(mk('span', 'fl', f.name), mk('small', '', human(f.size)));
        const x = mk('button', 'fx', '×'); x.type = 'button'; x.setAttribute('aria-label', t('form.removeFile', { x: f.name }));
        x.addEventListener('click', () => { st.files[q.id] = list().filter((y) => y !== f); f.up?.drop?.(); sync(); paint(); });
        c.append(x); chips.append(c);
      }
      add.hidden = list().length >= q.max_files;
    };
    const sync = () => { const fs = list(); if (fs.length) st.answers[q.id] = fs.map((f) => f.key); else delete st.answers[q.id]; onChange(q.id); };
    pick.addEventListener('change', () => {
      for (const file of [...pick.files]) {
        if (list().length >= q.max_files) { toast(t('form.err.files', { n: q.max_files }), 2400); break; }
        if (file.size > MAX_BYTES) { toast(t('form.tooBig', { x: file.name }), 3000); continue; }
        if (!file.size) continue;
        list().push({ file, name: file.name.slice(0, 128), size: file.size, mime: anyMime(file.type), key: 'k' + Math.random().toString(36).slice(2, 10) });
      }
      pick.value = ''; sync(); paint();
    });
    add.addEventListener('click', () => pick.click());
    box.append(chips, add, pick);
    if (q.accept.length && !q.accept.includes('file')) box.append(mk('div', 'fhint', t('form.accept', { x: q.accept.map((a) => t('form.kind.' + a)).join(t('form.sep')) })));
    paint();
    return box;
  }
  return box;
}
const human = (n) => (n < 1048576 ? Math.max(1, Math.round(n / 1024)) + ' KB' : (n / 1048576).toFixed(1) + ' MB');

function question(q, st, onChange, ctx, n) {
  const w = mk('div', 'fq'); w.dataset.q = q.id; w.dataset.type = q.type;
  if (q.type === 'note') { w.classList.add('fnote'); w.append(mk('p', '', q.text)); return w; }
  const id = `fq-${ctx.fid}-${q.id}`;
  const lab = mk('div', 'fqt'); lab.id = id;
  if (n) lab.append(mk('span', 'fqn', n + '. '));
  lab.append(document.createTextNode(q.label));
  if (q.required) { const r = mk('span', 'freq', '*'); r.setAttribute('aria-label', t('form.required')); lab.append(r); }
  w.append(lab);
  if (q.hint) w.append(mk('div', 'fqh', q.hint));
  if (ctx.deep && q.rec !== undefined && q.type !== 'file') {
    w.append(mk('div', 'frecline', t('form.myRec', { x: labelOf(q, q.rec, {}) })));
  }
  if (q.show_if) { w.classList.add('fcond'); }
  const c = control(q, st, onChange, ctx);
  c.setAttribute('aria-labelledby', id);
  for (const x of c.querySelectorAll('input,select,textarea')) if (!x.getAttribute('aria-label')) x.setAttribute('aria-labelledby', id);
  w.append(c, mk('div', 'ferr'));
  return w;
}
function paintErrs(root, errs, touched) {
  for (const w of root.querySelectorAll('.fq[data-q]')) {
    const e = errs[w.dataset.q], show = e && (touched === true || touched.has(w.dataset.q) || e.k !== 'form.err.required');
    const box = w.querySelector('.ferr');
    if (box) box.textContent = show ? t(e.k, e.vars) : '';
    w.classList.toggle('bad', !!show);
  }
}

// ---------------------------------------------------------------- submitting
const WHY = ['form_answered', 'form_expired', 'form_cancelled', 'form_gone', 'form_required', 'form_answer', 'stopped', 'offline', 'seat', 'too_many', 'att_gone'];
async function submit(fid, spec, st, view, onState) {
  if (st.sending || st.sent) return false;
  if (hooks.demo) { toast(t('form.demo'), 2600); return false; }
  const bids = new Set(), keyBid = {};
  for (const [, fs] of Object.entries(st.files)) for (const f of fs) { keyBid[f.key] = null; }
  const pre = check(spec, st.answers, st.other, { files: new Set(Object.keys(keyBid)) });
  if (pre.why !== 'ok') { onState('errs', pre.errs); return false; }
  st.sending = true; onState('sending');
  try {
    const att = [];
    const fileQs = questions(spec).filter((q) => q.type === 'file' && q.id in pre.clean);
    const total = fileQs.reduce((n, q) => n + st.files[q.id].length, 0);
    let done = 0;
    for (const q of fileQs) for (const f of st.files[q.id]) {
      if (!f.bid) {
        f.up ||= new hooks.Upload(f.file, { purpose: 'att', origin: 'file', name: f.name, mime: f.mime, onProgress: (p) => onState('upload', { done, total, p }) });
        const r = await f.up.start();
        if (!r.ok) { onState('fail', r.why || 'net'); toast(t('form.upFail', { x: f.name }), 3200); f.up = null; return false; }
        f.bid = f.up.bid;
      }
      done++; att.push(f.bid); bids.add(f.bid); onState('upload', { done, total, p: 0 });
    }
    const all = { ...st.answers };
    for (const q of fileQs) all[q.id] = st.files[q.id].map((f) => f.bid);
    const { clean } = check(spec, all, st.other, { files: bids });
    const answers = {};                         // only what is shown and valid (hidden questions never go)
    for (const q of questions(spec)) if (q.id in clean) answers[q.id] = clean[q.id];
    const other = clean._other || {};
    const r = await hooks.say({ text: summary(spec, clean), att, form: { id: fid, answers, ...(Object.keys(other).length ? { other } : {}) } });
    if (!r || !r.ok) {
      const why = r && WHY.includes(r.why) ? r.why : 'other';
      toast(t('form.why.' + why), 3600);
      if (why === 'att_gone') for (const fs of Object.values(st.files)) for (const f of fs) { f.bid = null; f.up = null; }
      onState('fail', why);
      return false;
    }
    st.sent = true; sealLater(fid);
    if (view) { view.state = 'answered'; view.at = Date.now(); view.rows = rowsOf(spec, clean); view.by = t('form.thisDevice'); }
    onState('sent');
    return true;
  } finally { st.sending = false; }
}
function rowsOf(spec, clean) {
  const other = clean._other || {};
  return questions(spec).filter((q) => q.id in clean).map((q) => [q.label, labelOf(q, clean[q.id], other), q.rec !== undefined && JSON.stringify(q.rec) === JSON.stringify(clean[q.id])]);
}
/** A withdrawn answer (the host re-opened the form): this phone may fill it again. */
export function reopened(fid) { const s = local.get(fid); if (s) { s.sent = false; for (const fs of Object.values(s.files)) for (const f of fs) { f.bid = null; f.up = null; } } }

// ---------------------------------------------------------------- the reply card
function hhmm(ms) { const d = new Date(ms); return String(d.getHours()).padStart(2, '0') + ':' + String(d.getMinutes()).padStart(2, '0'); }
function doneBlock(f) {
  const b = mk('div', 'fdone');
  const head = mk('div', 'fdh');
  head.append(mk('span', 'ftick', '✓'), mk('span', '', t('form.submitted', { at: f.at ? hhmm(f.at) : '', by: f.by || '' })));
  b.append(head);
  const rows = f.rows || [];
  for (const [k, v, rec] of rows.slice(0, f.mode === 'light' ? 60 : 3)) {
    const r = mk('div', 'frowd'); r.append(mk('span', 'fk', k), mk('b', '', v));
    if (rec) r.append(mk('span', 'frec', t('form.rec')));
    b.append(r);
  }
  if (f.mode === 'deep' && rows.length > 3) b.append(mk('div', 'fmore', t('form.moreRows', { n: rows.length - 3 })));
  return b;
}
function closedBlock(f) {
  const b = mk('div', 'fclosed');
  b.append(mk('span', '', t(f.state === 'cancelled' ? 'form.cancelled' : 'form.expired')), mk('small', '', f.title));
  return b;
}
const isExpired = (f) => f.state === 'open' && f.exp && f.exp < Date.now();
function lightBlock(f, rootEl) {
  const spec = f.spec, st = stateOf(f.id, spec), box = mk('div', 'fbox flight');
  box.dataset.fid = f.id;
  const touched = new Set();
  let note, go;
  const refresh = () => {
    const vis = shown(spec, st.answers);
    for (const w of box.querySelectorAll('.fq[data-q]')) w.hidden = !vis.has(w.dataset.q);
    const res = check(spec, st.answers, st.other, { files: new Set() });
    paintErrs(box, res.errs, touched);
    if (go) {
      const miss = Object.values(res.errs).filter((e) => e.k === 'form.err.required' || e.k === 'form.err.other').length;
      go.disabled = res.why !== 'ok' || st.sending;
      note.textContent = st.sending ? t('form.sending') : miss ? t('form.missing', { n: miss }) : res.why !== 'ok' ? t('form.fix') : '';
    }
  };
  const onState = (k, x) => {
    if (k === 'errs') { paintErrs(box, x, true); }
    if (k === 'sent') { rootEl._key = null; renderInto(rootEl, f); return; }
    refresh();
  };
  const ctx = { tap: f.tap, deep: false, fid: f.id, submit: () => submit(f.id, spec, st, f, onState) };
  if (spec.intro) box.append(mk('p', 'fintro', spec.intro));
  const qs = questions(spec);
  for (const q of qs) {
    const w = question(q, st, (id) => { touched.add(id); st.at = Date.now(); refresh(); }, ctx, 0);
    if (qs.length === 1 && q.label === spec.title) w.querySelector('.fqt')?.classList.add('fsr');   // the head already says it
    box.append(w);
  }
  if (!f.tap) {
    const row = mk('div', 'fsub'); note = mk('span', 'fsubnote'); note.setAttribute('aria-live', 'polite');
    go = mk('button', 'aj-btn aj-btn--primary fgo', spec.submit_label || t('form.submit')); go.type = 'button';
    go.addEventListener('click', () => ctx.submit());
    row.append(note, go); box.append(row);
  } else box.append(mk('div', 'ftapnote', t('form.tapNote')));
  refresh();
  return box;
}
function deepEntry(f) {
  const b = mk('div', 'fbox fdeepin'); b.dataset.fid = f.id;
  const ic = mk('span', 'fic'); ic.setAttribute('aria-hidden', 'true'); ic.textContent = '✎';
  const mid = mk('div', 'fmid');
  mid.append(mk('b', 'ftitle', f.title), mk('small', 'fmeta', t('form.deepMeta', { s: f.secs, n: f.n })));
  const s = local.get(f.id), answered = f.state === 'answered';
  const got = answered ? f.n : s ? Object.keys(s.answers).filter((k) => !empty(s.answers[k])).length : 0;
  const bar = mk('div', 'fprog'), fill = mk('i'); fill.style.width = Math.min(100, Math.round((got / Math.max(1, f.n)) * 100)) + '%'; bar.append(fill);
  mid.append(bar);
  const go = mk('button', 'aj-btn ' + (answered ? '' : 'aj-btn--primary ') + 'fgo', t(answered ? 'form.view' : got ? 'form.continue' : 'form.start'));
  go.type = 'button'; go.addEventListener('click', () => openDeep(f, go));
  b.append(ic, mid, go);
  if (answered) b.append(doneBlock(f));
  return b;
}
function renderInto(root, f) {
  root.replaceChildren();
  if (!f) { root.hidden = true; return; }
  root.hidden = false;
  const head = mk('div', 'fhead'); head.append(mk('span', 'fkind', t(f.mode === 'deep' ? 'form.kindDeep' : 'form.kindLight')));
  if (f.mode === 'light') head.append(mk('span', 'ftitle', f.title));
  root.append(head);
  const st = local.get(f.id);
  if (f.state === 'open' && st && st.sent) { root.append(mk('div', 'fsubnote', t('form.sentWait'))); return; }
  if (f.state === 'open' && isExpired(f)) { root.append(closedBlock({ ...f, state: 'expired' })); return; }
  if (f.state === 'expired' || f.state === 'cancelled') { root.append(closedBlock(f)); return; }
  if (f.mode === 'deep') { root.append(deepEntry(f)); return; }
  if (f.state === 'answered') { root.append(doneBlock(f)); return; }
  if (!f.spec) { root.append(mk('div', 'fsubnote', t('form.unavailable'))); return; }
  root.append(lightBlock(f, root));
}
function strip(words) {
  let s = document.getElementById('fstrip');
  if (!s) { s = mk('div', 'fstrip'); s.id = 'fstrip'; s.hidden = true; }
  const after = document.getElementById('mstrip');
  const anchor = after && after.previousElementSibling === words ? after : words;
  if (s.previousElementSibling !== anchor) anchor.after(s);
  return s;
}
/** After the page's words (and media.js's strip): the page's form, if it carries one. Idempotent for the same state. */
export function renderPage(words, p) {
  const f = p && p.form ? p.form : null;
  const root = strip(words);
  const key = f ? [p.id, f.id, f.state, f.rows ? f.rows.length : 0, local.get(f.id)?.sent ? 1 : 0, lang()].join('|') : null;
  if (f) {                                    // answered → open again = the answer was withdrawn: fill it in again
    if (seen.get(f.id) === 'answered' && f.state === 'open') reopened(f.id);
    seen.set(f.id, f.state);
  }
  if (root._key === key && (key === null || root.childElementCount)) return;
  root._key = key;
  renderInto(root, f);
}
const seen = new Map();
onLang(() => { const r = document.getElementById('fstrip'); if (r) r._key = null; });

// ---------------------------------------------------------------- 深聊卡: the full-screen page
let D = null;     // {f, spec, st, sec, review, opener, timer, root, ...}
function deepRoot() {
  let r = document.getElementById('fdeep');
  if (r) return r;
  r = mk('div', 'fdeep'); r.id = 'fdeep'; r.hidden = true;
  r.setAttribute('role', 'dialog'); r.setAttribute('aria-modal', 'true'); r.setAttribute('aria-labelledby', 'fdeep-title');
  document.body.append(r);
  registerLayer({ element: () => r, open: () => !r.hidden, close: () => closeDeep(), rank: 6, fallback: () => document.getElementById('input') });
  r.addEventListener('keydown', (e) => {
    if (!D || e.isComposing) return;
    const typing = /^(INPUT|TEXTAREA|SELECT)$/.test(e.target.tagName);
    if (e.key === 'Enter' && (e.ctrlKey || e.metaKey)) { e.preventDefault(); if (D.review) D.doSubmit(); else go(+1, true); return; }
    if (typing || e.ctrlKey || e.metaKey || e.altKey) return;
    if (e.key === 'ArrowRight') { e.preventDefault(); go(+1); } else if (e.key === 'ArrowLeft') { e.preventDefault(); go(-1); }
  });
  return r;
}
export const deepOpen = () => !!document.getElementById('fdeep') && !document.getElementById('fdeep').hidden;
async function openDeep(f, opener) {
  const r = deepRoot();
  let spec = f.spec, draft = null, rows = f.rows, state = f.state;
  if (!hooks.demo) {
    if (!isReady()) { toast(t('form.offline'), 2600); return; }
    const m = await hooks.ask1({ t: 'form_get', id: f.id }, 'form_res', 15000);
    if (m.t !== 'form_res') { toast(t('form.offline'), 2600); return; }
    if (!m.ok) { toast(t('form.why.form_gone'), 2600); return; }
    spec = m.spec; draft = m.draft || null; state = m.state; rows = m.rows || rows;
    f = { ...f, state, rows, at: m.at || f.at, by: m.by || f.by };
  }
  if (!spec) return;
  if (state === 'open' && !hooks.demo) {
    const loc = (await sealedAll())[f.id];
    if (loc && (!draft || Number(loc.at) > Number(draft.at))) draft = loc;
  }
  const st = stateOf(f.id, spec, draft);
  D = { f, spec, st, sec: Math.min(Math.max(0, st.sec || 0), spec.sections.length - 1), review: false, opener, touched: new Set(), saved: st.at || 0,
    readonly: state !== 'open' || !!st.sent };
  r.hidden = false; document.body.dataset.fdeep = '1';
  paintDeep();
  r.querySelector('.fdx')?.focus();
}
export function closeDeep(silent) {
  const r = document.getElementById('fdeep');
  if (!r || r.hidden) return;
  if (D && !D.readonly && !silent) saveDraft(true);
  r.hidden = true; delete document.body.dataset.fdeep;
  const back = D && D.opener;
  D = null;
  r.replaceChildren();
  if (back?.isConnected) back.focus({ preventScroll: true });
  const fs = document.getElementById('fstrip'); if (fs) fs._key = null;
  document.dispatchEvent(new CustomEvent('aj:formclosed'));
}
function saveDraft(now) {
  if (!D || D.readonly) return;
  clearTimeout(D.timer);
  const fire = () => {
    if (!D) return;
    const { f, st } = D;
    st.sec = D.sec; st.at = Date.now(); sealLater(f.id);
    if (hooks.demo || !isReady()) { paintSaved(t('form.savedLocal')); return; }
    const answers = Object.fromEntries(Object.entries(st.answers).filter(([k]) => D.spec && questions(D.spec).find((q) => q.id === k)?.type !== 'file'));
    sendApp({ t: 'form_draft', id: f.id, answers, other: st.other, sec: D.sec }).catch(() => {});
    D.saved = Date.now(); paintSaved(t('form.saved', { at: hhmm(D.saved) }));
  };
  if (now) fire(); else D.timer = setTimeout(fire, DRAFT_MS);
}
function paintSaved(text) { const s = document.getElementById('fdeep-saved'); if (s) s.textContent = text; }
/** form_draft_res: the computer kept it (or the form closed meanwhile). */
export function onDraftRes(m) {
  if (!D || m.id !== D.f.id) return;
  if (!m.ok && m.why && m.why !== 'answer') { D.readonly = true; paintDeep(); toast(t('form.why.form_' + m.why) || t('form.why.other'), 3000); }
}
function counts() {
  const vis = shown(D.spec, D.st.answers);
  const qs = questions(D.spec).filter((q) => q.type !== 'note' && vis.has(q.id));
  return { n: qs.length, got: qs.filter((q) => !empty(D.st.answers[q.id])).length, vis };
}
function secErrs(k) {
  const all = check(D.spec, D.st.answers, D.st.other, { files: new Set(Object.values(D.st.files).flat().map((f) => f.key)) });
  const ids = new Set(D.spec.sections[k].questions.map((q) => q.id));
  return Object.fromEntries(Object.entries(all.errs).filter(([id]) => ids.has(id)));
}
function go(d, strict) {
  if (!D) return;
  if (D.review) { if (d < 0) { D.review = false; paintDeep(); } return; }
  if (d > 0 && !D.readonly) {
    const e = secErrs(D.sec);
    if (Object.keys(e).length) { paintErrs(document.getElementById('fdeep'), e, true); if (strict !== false) focusFirstBad(); return; }
  }
  const n = D.spec.sections.length;
  if (d > 0 && D.sec === n - 1) { if (!D.readonly) { D.review = true; paintDeep(); } return; }
  const to = Math.min(n - 1, Math.max(0, D.sec + d));
  if (to === D.sec) return;
  D.sec = to; saveDraft(); paintDeep();
  document.querySelector('#fdeep .fdbody')?.scrollTo(0, 0);
}
function focusFirstBad() { document.querySelector('#fdeep .fq.bad input, #fdeep .fq.bad button, #fdeep .fq.bad select, #fdeep .fq.bad textarea')?.focus(); }
function paintDeep() {
  const r = document.getElementById('fdeep');
  if (!D || !r) return;
  const { f, spec, st } = D;
  r.replaceChildren();
  r.dataset.ro = D.readonly ? '1' : '0';
  // header
  const hd = mk('div', 'fdhd'), x = mk('button', 'fdx', '✕'); x.type = 'button'; x.setAttribute('aria-label', t('form.close'));
  x.addEventListener('click', () => closeDeep());
  const tt = mk('div', 'fdtt'), title = mk('div', 'fdtitle', spec.title); title.id = 'fdeep-title';
  const c = counts();
  const status = mk('div', 'fdstat', D.readonly ? t(f.state === 'answered' || st.sent ? 'form.roAnswered' : 'form.roClosed', { at: f.at ? hhmm(f.at) : '', by: f.by || '' })
    : D.review ? t('form.reviewHead', { got: c.got, n: c.n }) : t('form.progress', { k: D.sec + 1, s: spec.sections.length, got: c.got, n: c.n }));
  tt.append(title, status);
  const saved = mk('div', 'fdsaved'); saved.id = 'fdeep-saved'; saved.setAttribute('aria-live', 'polite');
  if (!D.readonly && D.saved) saved.textContent = st.draftBy && D.saved === st.at ? t('form.draftFrom', { by: st.draftBy }) : t('form.saved', { at: hhmm(D.saved) });
  hd.append(x, tt, saved);
  const pg = mk('div', 'fdpg'), pgi = mk('i'); pgi.style.width = Math.round((c.got / Math.max(1, c.n)) * 100) + '%'; pg.append(pgi);
  pg.setAttribute('role', 'progressbar'); pg.setAttribute('aria-valuemin', '0'); pg.setAttribute('aria-valuemax', String(c.n)); pg.setAttribute('aria-valuenow', String(c.got));
  const steps = mk('nav', 'fdsteps'); steps.setAttribute('aria-label', t('form.sections'));
  spec.sections.forEach((s, k) => {
    const done = s.questions.filter((q) => q.type !== 'note' && c.vis.has(q.id)).every((q) => !q.required || !empty(st.answers[q.id]));
    const b = mk('button', 'fdstep' + (k === D.sec && !D.review ? ' on' : done ? ' ok' : ''), s.title || t('form.secN', { k: k + 1 }));
    b.type = 'button'; if (k === D.sec && !D.review) b.setAttribute('aria-current', 'step');
    b.addEventListener('click', () => { D.review = false; D.sec = k; saveDraft(); paintDeep(); });
    steps.append(b);
  });
  const body = mk('div', 'fdbody'), inner = mk('div', 'fdinner');
  body.append(inner);
  if (D.readonly) {
    inner.append(mk('h2', 'fdsec', t('form.answers')));
    const rows = f.rows || (st.sent ? rowsOf(spec, check(spec, st.answers, st.other, { draft: true }).clean) : []);
    if (!rows.length) inner.append(mk('p', 'fdsecd', t(f.state === 'answered' ? 'form.noRows' : 'form.roClosedLong')));
    for (const [k, v, rec] of rows) { const rw = mk('div', 'fdrow'); rw.append(mk('div', 'fk', k), mk('div', 'fv', v)); if (rec) rw.append(mk('span', 'frec', t('form.rec'))); inner.append(rw); }
  } else if (D.review) {
    inner.append(mk('h2', 'fdsec', t('form.review')), mk('p', 'fdsecd', t('form.reviewHint')));
    const res = check(spec, st.answers, st.other, { draft: true });
    const fileN = Object.values(st.files).reduce((n, fs) => n + fs.length, 0);
    spec.sections.forEach((s, k) => {
      const blk = mk('div', 'fdrev'), h = mk('div', 'fdrevh');
      h.append(mk('b', '', s.title || t('form.secN', { k: k + 1 })));
      const ed = mk('button', 'flink', t('form.edit')); ed.type = 'button'; ed.addEventListener('click', () => { D.review = false; D.sec = k; paintDeep(); });
      h.append(ed); blk.append(h);
      for (const q of s.questions) {
        if (q.type === 'note' || !c.vis.has(q.id)) continue;
        const v = q.type === 'file' ? (st.files[q.id] || []).map((x) => x.name).join(t('form.sep')) : q.id in res.clean ? labelOf(q, res.clean[q.id], res.clean._other || {}) : '';
        const rw = mk('div', 'fdrow'); rw.append(mk('div', 'fk', q.label), mk('div', 'fv' + (v ? '' : ' none'), v || t('form.unanswered'))); blk.append(rw);
      }
      inner.append(blk);
    });
    if (fileN) inner.append(mk('p', 'fdsecd', t('form.filesGo', { n: fileN })));
  } else {
    const s = spec.sections[D.sec];
    if (s.title) inner.append(mk('h2', 'fdsec', s.title));
    if (s.intro || (D.sec === 0 && spec.intro)) inner.append(mk('p', 'fdsecd', [D.sec === 0 ? spec.intro : '', s.intro].filter(Boolean).join('\n')));
    let n = spec.sections.slice(0, D.sec).reduce((k, x) => k + x.questions.filter((q) => q.type !== 'note').length, 0);
    for (const q of s.questions) {
      if (q.type !== 'note') n++;
      const w = question(q, st, (id) => { D.touched.add(id); st.at = Date.now(); saveDraft(); refreshDeep(); }, { deep: true, fid: f.id, tap: false }, q.type === 'note' ? 0 : n);
      if (q.show_if) { const why = q.show_if.q && questions(spec).find((x) => x.id === q.show_if.q); if (why) w.insertBefore(mk('div', 'fcondtag', t('form.because', { x: why.label })), w.firstChild); }
      inner.append(w);
    }
  }
  // footer
  const ft = mk('div', 'fdft'), fin = mk('div', 'fdinner fdftin');
  const back = mk('button', 'aj-btn fdback', t('form.prev')); back.type = 'button';
  back.disabled = !D.review && D.sec === 0; back.addEventListener('click', () => go(-1));
  const mid = mk('span', 'fdmid'); mid.id = 'fdeep-mid'; mid.setAttribute('aria-live', 'polite');
  const next = mk('button', 'aj-btn aj-btn--primary fdnext'); next.type = 'button';
  if (D.readonly) { next.textContent = t('form.close'); next.addEventListener('click', () => closeDeep()); back.hidden = true; }
  else if (D.review) {
    next.textContent = spec.submit_label || t('form.submit');
    D.doSubmit = async () => {
      const ok = await submit(f.id, spec, st, null, (k, x) => {
        if (k === 'sending') { next.disabled = true; mid.textContent = t('form.sending'); }
        if (k === 'upload') mid.textContent = t('form.uploading', { k: Math.min(x.total, x.done + 1), n: x.total, p: Math.round((x.p || 0) * 100) });
        if (k === 'errs') { const bad = spec.sections.findIndex((sx) => sx.questions.some((q) => q.id in x)); D.review = false; D.sec = Math.max(0, bad); paintDeep(); paintErrs(document.getElementById('fdeep'), x, true); }
        if (k === 'fail') { next.disabled = false; mid.textContent = ''; }
      });
      if (ok) { D.f = { ...D.f, state: 'answered' }; D.readonly = true; closeDeep(true); toast(t('form.thanks'), 2600); }
    };
    next.addEventListener('click', () => D.doSubmit());
  } else {
    const last = D.sec === spec.sections.length - 1;
    next.textContent = t(last ? 'form.toReview' : 'form.next');
    next.addEventListener('click', () => go(+1));
  }
  fin.append(back, mid, next); ft.append(fin);
  r.append(hd, pg, steps, body, ft);
  if (!D.readonly && !D.review) refreshDeep();
}
function refreshDeep() {
  if (!D) return;
  const r = document.getElementById('fdeep'), c = counts();
  for (const w of r.querySelectorAll('.fdbody .fq[data-q]')) w.hidden = !c.vis.has(w.dataset.q);
  const e = secErrs(D.sec);
  paintErrs(r, e, D.touched);
  const miss = Object.values(e).filter((x) => x.k === 'form.err.required' || x.k === 'form.err.other').length;
  const mid = document.getElementById('fdeep-mid');
  if (mid) mid.textContent = miss ? t('form.missing', { n: miss }) : '';
  const stat = r.querySelector('.fdstat');
  if (stat) stat.textContent = t('form.progress', { k: D.sec + 1, s: D.spec.sections.length, got: c.got, n: c.n });
  const pgi = r.querySelector('.fdpg i'); if (pgi) pgi.style.width = Math.round((c.got / Math.max(1, c.n)) * 100) + '%';
  r.querySelector('.fdpg')?.setAttribute('aria-valuenow', String(c.got));
}
/** Test / gallery hook: open a form object directly (demo pages have no host). */
export function openForm(f, opener) { return f.mode === 'deep' ? openDeep(f, opener) : null; }
export const debug = () => ({ open: deepOpen(), sec: D ? D.sec : null, review: D ? D.review : false, local: [...local.keys()] });
