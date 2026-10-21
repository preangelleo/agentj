// P426 #181-3 (PROTOCOL §39) · skill health on the 技能 page: 「最近使用：3 天前 · 5 次」 from the computer's own count, 「—」
// when it saw none (never a guess); a long-unused skill says 「很久没用了」, and one the owner may archive (their own, never
// Agent J's, never a symbolic link — its sheet says why) carries 「归档」 — on the list only when it is long unused, always in its
// detail sheet. DOM only, no HTML.
import { t } from './t.js';

export const DAY = 86400;
export const WHY = ['official', 'not_found', 'exists', 'link'];

function el(tag, text, cls) { const n = document.createElement(tag); if (text !== undefined) n.textContent = text; if (cls) n.className = cls; return n; }

/** Days since a unix time → 今天 / 1 天前 / n 天前 (today / 1 day ago / n days ago). */
export function ago(ts, now = Date.now() / 1000) {
  const d = Math.max(0, Math.floor((now - (ts || 0)) / DAY));
  return d === 0 ? t('p426.today') : d === 1 ? t('p426.dayAgo') : t('p426.daysAgo', { n: d });
}

export const useLine = (s, now) => (s.use ? t('p426.used', { when: ago(s.use.last, now), n: s.use.n }) : t('p426.unused'));

/** Adds the use line, the long-unused badge and (when allowed) 「归档」 to a skill card or sheet. `info` = the tail's
 *  skill_use; absent (an older computer) → nothing is added. o = {canAct, list, act(type, name, box), now}. */
export function decorate(box, s, info, o = {}) {
  if (!info) return false;
  box.append(el('small', useLine(s, o.now), 'fp-skill-use'));
  if (s.stale) { box.dataset.stale = '1'; box.append(el('small', t('p426.stale', { days: info.days }), 'fp-unreg fp-stale')); }
  if (s.arch && o.canAct && (s.stale || !o.list)) {
    const b = el('button', t('p426.archive'), 'fp-action fp-secondary fp-skill-act'); b.type = 'button';
    b.onclick = () => o.act?.('skill_archive', s.name, box); box.append(b);
  }
  // Jarvis ruling 4: a skill reached through a symbolic link is never archivable — no button; its detail sheet says why
  if (s.link && !o.list) box.append(el('small', t('p426.link'), 'fp-skill-link'));
  return true;
}

/** The tap as a signed phone control (§8, S6): [action, target, message] for controls.signedWrite — the signature covers
 *  `op \n name`, so an unsigned or re-pointed tap changes nothing on the computer. */
export function signedArgs(type, name) {
  const op = type === 'skill_archive' ? 'archive' : 'restore';
  return [type, { op, name }, { t: type, name }];
}

/** The line after a tap: done, or why not (one of WHY, else a generic retry). */
export function resultText(type, m) {
  if (m && m.ok) return t(type === 'skill_archive' ? 'p426.archivedOk' : 'p426.restoredOk');
  return t('p426.why.' + (m && WHY.includes(m.why) ? m.why : 'other'));
}
