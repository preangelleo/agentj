// 0.26.9 selfheal (CONTRACT §C, #461 #464) · the chat side of recovery, pure helpers (no DOM state of their own):
//  · loginOf(page)  the host's `login: {agent, why}` on a reply whose AI coding tool is signed out → 「重新登录」 under it, and
//                   the reply's own `/login` text turned into the same button (linkLogin). Plain prose that merely says
//                   "login" never gets one: only the host's field does.
//  · queueOf(page)  the host's `waited_s` (seconds since the running turn started) / `stuck: true` on the queued notice →
//                   「已等 N 分钟」 and, when stuck or waited ≥ END_AFTER_S, 「强制结束这一轮」 (signed rescue_do end_turn, rescue.js).
// Both are read from the turn's card (the history's extension point) and, for a host that puts them on the turn or on the
// reply itself, from there (snap.js carries them through as `login` / `queue`).
export const AGENTS = ['claude', 'codex', 'opencode'];
export const WHYS = ['not_logged_in', 'org_disabled', 'expired', 'device'];
export const END_AFTER_S = 120;
export const AGENT_NAME = { claude: 'Claude Code', codex: 'Codex', opencode: 'OpenCode' };

const obj = (v) => (v && typeof v === 'object' && !Array.isArray(v) ? v : null);

/** {agent, why} or null — the agent must be one we know; an unknown reason reads as 「not logged in」. */
export function loginOf(p) {
  if (!p) return null;
  const l = obj(p.card && p.card.login) || obj(p.login);
  if (!l || !AGENTS.includes(l.agent)) return null;
  return { agent: l.agent, why: WHYS.includes(l.why) ? l.why : 'not_logged_in' };
}

/** {waited, stuck} or null. `waited` = whole seconds ≥ 0 as the host counted them. */
export function queueOf(p) {
  if (!p) return null;
  const c = obj(p.card) || {}, q = obj(p.queue) || {};
  const w = [c.waited_s, q.waited_s].find((x) => Number.isFinite(x) && x >= 0 && x < 1e7);
  const stuck = c.stuck === true || q.stuck === true;
  if (w === undefined && !stuck) return null;
  return { waited: Math.floor(w || 0), stuck };
}

/** snap.js: what a turn carries for us outside its card (kept small and well-formed). */
export function carry(t) {
  const out = {};
  const l = obj(t && t.login) || obj(t && t.reply && t.reply.login);
  if (l && AGENTS.includes(l.agent)) out.login = { agent: l.agent, why: typeof l.why === 'string' ? l.why.slice(0, 24) : '' };
  const w = t && t.waited_s, s = t && t.stuck === true;
  if ((Number.isFinite(w) && w >= 0) || s) out.queue = { ...(Number.isFinite(w) && w >= 0 ? { waited_s: Math.floor(w) } : {}), ...(s ? { stuck: true } : {}) };
  return out;
}

// The waited time keeps counting on the phone between the host's updates: per turn, the host's value and when it came.
const seen = new Map();
/** Seconds waited now. `live` = the notice still describes the present (open page, or the newest one). */
export function waitedNow(id, q, live, now = Date.now()) {
  let s = seen.get(id);
  if (!s || s.w !== q.waited) { s = { w: q.waited, at: now }; seen.set(id, s); if (seen.size > 64) seen.delete(seen.keys().next().value); }
  return s.w + (live ? Math.max(0, Math.floor((now - s.at) / 1000)) : 0);
}
/** The dictionary key + vars of 「已等 N 分钟」. */
export function waitedText(sec) {
  return sec < 60 ? ['p0269.queue.waitedLess', {}] : ['p0269.queue.waited', { n: Math.floor(sec / 60) }];
}
/** 0.26.9 #471 (merged queued notice): 「上一件还在做，已等 N 分钟」. */
export function prevText(sec) {
  return sec < 60 ? ['p0269.queue.prevWaitedLess', {}] : ['p0269.queue.prevWaited', { n: Math.floor(sec / 60) }];
}
export const canEnd = (q, sec) => !!q && (q.stuck || sec >= END_AFTER_S);

let tick = 0;
/** Repaint while a waited line is on screen (every 15 s), stop by itself once none is. */
export function keepTicking(repaint) {
  if (tick) return;
  tick = setInterval(() => {
    if (!document.querySelector('[data-waited]')) { clearInterval(tick); tick = 0; return; }
    try { repaint(); } catch { /* the next tick tries again */ }
  }, 15000);
}

// `/login` as the reply wrote it — a word of its own (not part of a path or a longer command); in text or in inline code
const LOGIN = /(^|[^\w/.-])(\/login)(?![\w/-])/;
/** Turn every `/login` in the rendered reply into the 「重新登录」 button. Returns how many it replaced. */
export function linkLogin(box, label, onClick) {
  if (!box) return 0;
  let n = 0;
  for (const c of [...box.querySelectorAll('code')]) {
    if (c.closest('pre') || c.textContent.trim() !== '/login') continue;
    c.replaceWith(loginLink(label, onClick)); n++;
  }
  const walk = document.createTreeWalker(box, NodeFilter.SHOW_TEXT, {
    acceptNode: (x) => (x.parentElement && x.parentElement.closest('pre, a, button, code') ? NodeFilter.FILTER_REJECT : NodeFilter.FILTER_ACCEPT),
  });
  const hits = [];
  for (let x = walk.nextNode(); x; x = walk.nextNode()) if (LOGIN.test(x.nodeValue)) hits.push(x);
  for (let x of hits) {
    for (let m = LOGIN.exec(x.nodeValue); m && n < 8; m = LOGIN.exec(x.nodeValue)) {
      const at = m.index + m[1].length;
      const rest = x.splitText(at);
      const after = rest.splitText(m[2].length);
      rest.replaceWith(loginLink(label, onClick)); n++;
      x = after;
    }
  }
  return n;
}
function loginLink(label, onClick) {
  const b = document.createElement('button');
  b.type = 'button'; b.className = 'fp-link sh-loginlink'; b.dataset.login = '1';
  b.textContent = '/login'; b.title = label; b.setAttribute('aria-label', label);
  b.addEventListener('click', (e) => { e.stopPropagation(); onClick(); });
  return b;
}
