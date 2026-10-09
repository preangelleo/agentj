// Small page helpers shared by the modules: element lookup, relay's toast (one line, optional action button), the
// two-step confirm dialog of Agent J's signed writes.
export const el = (id) => document.getElementById(id);
export function mk(tag, cls, text) { const e = document.createElement(tag); if (cls) e.className = cls; if (text !== undefined) e.textContent = text; return e; }

// ms defaults to a glance; an upload holds its progress toast until it finishes.
export function toast(t, ms) {
  const n = el('toast'); delete n.dataset.sharedFollow; n.textContent = t; n.classList.remove('act'); n.classList.add('on');
  clearTimeout(n._t); n._t = setTimeout(() => n.classList.remove('on'), ms || 1800);
}
export function toastOff() { const n = el('toast'); clearTimeout(n._t); n.classList.remove('on', 'act'); }
// A toast with one action button (undo, restore). Any later toast() replaces it.
export function toastAction(t, label, fn, ms) {
  const n = el('toast'); delete n.dataset.sharedFollow; n.textContent = t;
  const b = document.createElement('button'); b.type = 'button'; b.className = 'undo'; b.textContent = label;
  b.addEventListener('pointerdown', (e) => e.preventDefault());          // keep the field's focus
  b.addEventListener('click', () => { toastOff(); fn(); });
  n.appendChild(b); n.classList.add('on', 'act');
  clearTimeout(n._t); n._t = setTimeout(() => n.classList.remove('on', 'act'), ms || 5000);
}

/** Two-step confirmation for every write (and /clear): → true / false. */
export function confirmSheet(title, text, yes) {
  return new Promise((resolve) => {
    el('confirm-title').textContent = title; el('confirm-text').textContent = text; el('confirm-yes').textContent = yes;
    const box = el('confirm');
    box.hidden = false; box.dataset.modalOpen = '1';
    el('confirm-yes').focus();
    const done = (v) => { box.hidden = true; delete box.dataset.modalOpen; el('confirm-yes').onclick = null; el('confirm-no').onclick = null; resolve(v); };
    el('confirm-yes').onclick = () => done(true);
    el('confirm-no').onclick = () => done(false);
  });
}
export const confirmOpen = () => !el('confirm').hidden;
export function closeConfirm() { if (!el('confirm').hidden) el('confirm-no').click(); }

export function human(n) {
  return n < 1024 ? n + ' B' : n < 1048576 ? (n / 1024).toFixed(0) + ' KB' : (n / 1048576).toFixed(1) + ' MB';
}
export function stamp() { const d = new Date(); return [d.getHours(), d.getMinutes(), d.getSeconds()].map((n) => String(n).padStart(2, '0')).join(''); }
export function mmss(s) { s = Math.round(s); return Math.floor(s / 60) + ':' + String(s % 60).padStart(2, '0'); }
