// P115 (P92, ADR-A196) + P131: the first-run checklist. The host owns the journal and every check; this page only shows the
// host's snapshot (same revision on the phone and this computer's browser) and sends the owner's own taps:
//   Use / I won't use this / Later / Start the leaving check / install the browser / use the browser already running here /
//   open the sign-in page on the computer → a signed `setup_mark` (agentjarvis-control-v1 over item, choice, revision — a
//   stale revision comes back `changed` and the page re-reads); "Check" → `setup_get` with check:true (a read-only re-probe:
//   the owner's word is a request to check, never proof). Nothing but the ✕ flag below is stored here; no secrets, QR codes or links ever appear.
// P131 (Leo 10-11): it never covers the chat. A host snapshot only shows a thin banner above the chat (「首次准备 3/12 · 继续」);
// the checklist is its own page (banner or menu → page, ← back to chat). The banner's ✕ / the page's 「收起清单」 closes it at
// once (`setup_close`: every page hides it and it no longer comes back by itself); the menu reopens it (`setup_get` open).
// Every row says where it is done; a row that is not done shows the host's fact / effect / next step (`explain`).
// P131 追加2 (Leo 10-11 09:31 「关闭后还在反复弹」): ✕ on this device is permanent for this device (localStorage "aj.setup.hidden"):
// no snapshot, revision, check, resume or restart brings the banner back here — only the owner's own menu tap reopens it.
import { ask1, isReady, sendApp, randHex } from './session.js';
import { signedWrite, whyText } from './controls.js';
import { t, lang, onLang } from './t.js';
import { mk, toast } from './ui.js';

const pick = (o) => (o && typeof o === 'object' ? String((lang() === 'en' ? o.en : o.zh) ?? '') : '');
const GROUPS = ['core', 'machine', 'browser', 'google', 'privacy', 'optional'];
const WHERE = ['auto', 'local', 'phone'];
const STATES = ['verified', 'pending', 'waiting_local', 'attention', 'deferred', 'unsupported', 'not_applicable', 'stale'];
const NEXT = ['install_browser', 'adopt_browser', 'open_login', 'check'];
const stateText = (s) => (STATES.includes(s) ? t('setup.st.' + s) : String(s));
const el = (id) => document.getElementById(id);
let H = { openPanel: () => {}, view: () => null };
let S = null, busy = false, wired = false, pendingClose = false;
const HIDE_KEY = 'aj.setup.hidden';
function hiddenHere() { try { return localStorage.getItem(HIDE_KEY) === '1'; } catch { return false; } }
function hideHere(on) { try { if (on) localStorage.setItem(HIDE_KEY, '1'); else localStorage.removeItem(HIDE_KEY); } catch { /* blocked storage: host flag only */ } }

export function configure(hooks) { H = { ...H, ...hooks }; }

function valid(m) {
  return m && Number.isInteger(m.revision) && m.revision > 0 && Array.isArray(m.items) && m.items.length <= 64 &&
    m.items.every((i) => i && typeof i.id === 'string' && /^[a-z]{2,20}$/.test(i.id)) && m.counts && typeof m.counts === 'object';
}

function wire() {
  if (wired) return;
  wired = true;
  el('setup-banner-open')?.addEventListener('click', () => open(false));
  el('setup-banner-x')?.addEventListener('click', () => closeList());
  el('setup-close')?.addEventListener('click', () => { closeList(); H.openPanel('chat'); });
  onLang(() => render());
}

/** A host snapshot (`setup_card`). It never opens the page by itself: it only (re)draws the banner and an open page. */
export function card(m) {
  if (!valid(m)) return;
  if (S && m.revision < S.revision) return;           // an older snapshot never replaces a newer one
  S = m;
  wire();
  if (pendingClose && !S.closed && isReady()) { pendingClose = false; sendClose(); S = { ...S, closed: true }; }
  render();
}

export function clear() { S = null; busy = false; pendingClose = false; render(); }
export const isOpen = () => H.view() === 'setup';

/** The page: from the banner (already have the snapshot) or the menu (reopen: a closed checklist shows again). */
export async function open(reopen) {
  wire();
  if (reopen) hideHere(false);                        // the owner's own tap: this device shows it again
  H.openPanel('setup');
  render();
  if (!isReady()) { if (!S) toast(t('setup.offline'), 3000); return; }
  const m = await ask1({ t: 'setup_get', check: false, ...(reopen ? { open: true } : {}) }, 'setup_card', 30000);
  if (m && m.t === 'setup_card' && valid(m)) { S = m; pendingClose = false; }
  else if (m && m.t === 'setup_card' && m.result === 'needs_setup') { S = null; }
  render();
}

function sendClose() { sendApp({ t: 'setup_close', r: randHex(8) }); }

/** ✕: hidden at once on this page; the host hides it everywhere and stops showing it by itself. Offline: sent on reconnect. */
export function closeList() {
  hideHere(true);
  if (S) S = { ...S, closed: true };
  if (isReady()) sendClose(); else pendingClose = true;
  render();
}

const btn = (cls, label, fn, id) => {
  const b = mk('button', 'aj-btn ' + cls, label); b.type = 'button'; if (id) b.id = id;
  b.disabled = busy; b.addEventListener('click', () => { if (!busy) fn(); }); return b;
};

async function mark(item, choice) {
  if (!S || busy) return;
  busy = true; render();
  const r = await signedWrite('setup_mark', { item, choice, rev: S.revision }, { t: 'setup_mark', item, choice, rev: S.revision });
  busy = false;
  if (!r.ok) {
    if (r.why === 'changed') await refresh(false);
    else toast(t('setup.notSaved', { why: whyText(r.why) }), 4000);
  }
  render();
}

function netHint() {
  const c = navigator.connection;
  const type = c && typeof c.type === 'string' ? c.type : '';
  return type === 'cellular' ? 'cellular' : type === 'wifi' ? 'wifi' : 'unknown';
}

async function micState() {
  try { const p = await navigator.permissions.query({ name: 'microphone' }); return ['granted', 'denied', 'prompt'].includes(p.state) ? p.state : null; } catch { return null; }
}

/** Ask the host for the snapshot; with check, a live read-only re-probe (optionally one item). */
export async function refresh(check, item) {
  if (!isReady()) { toast(t('setup.offline'), 3000); return; }
  busy = true; render();
  const req = { t: 'setup_get', check: !!check, net: netHint() };
  if (item) req.item = item;
  const mic = item === 'phonepermissions' || !item ? await micState() : null;
  if (mic) req.perm = { mic };
  const m = await ask1(req, 'setup_card', 90000);
  busy = false;
  if (m && m.t === 'setup_card' && valid(m)) { S = m; }
  else if (m && m.t === 'setup_card' && m.result === 'needs_setup') toast(t('setup.needsSetup'), 4000);
  else if (m && m.t !== 'setup_card') toast(t('setup.unreachable'), 3000);
  render();
}

// ---------------------------------------------------------------- the chat banner
function renderBanner() {
  const b = el('setup-banner');
  if (!b) return;
  const show = !!S && !S.closed && !hiddenHere();
  b.hidden = !show;
  if (!show) return;
  const p = S.progress && Number.isInteger(S.progress.total) ? S.progress : { done: S.counts.verified, total: S.items.length };
  el('setup-banner-open').textContent = S.remote_ready ? t('setup.bannerReady') : t('setup.banner', { d: p.done, n: p.total });
}

// ---------------------------------------------------------------- the page
function whereTag(i) {
  if (i.state === 'unsupported') return t('setup.where.unsupported');
  const w = i.state === 'waiting_local' ? 'local' : i.where;   // "waiting for you" is never "I handle it"
  return t('setup.where.' + (WHERE.includes(w) ? w : 'local'));
}

function sourceLine() {
  const s = S.browser_source;
  if (!s || typeof s !== 'object') return '';
  if (s.kind === 'adopted') return t(s.running === false ? 'setup.src.adoptedOff' : 'setup.src.adopted', { port: s.port });
  if (s.kind === 'own') return t('setup.src.own');
  if (s.kind === 'offer') return t('setup.src.offer', { port: s.port });
  return t('setup.src.none');
}

function actionNote() {
  const a = S.action;
  if (!a || typeof a !== 'object' || !NEXT.includes(a.choice)) return '';
  const port = S.browser_source?.port ?? '';
  if (a.state === 'running') return t('setup.act.running.' + a.choice);
  if (a.state === 'failed') return t('setup.act.failed', { why: String(a.reason || a.result || '') });
  if (a.choice === 'open_login') {
    if (a.adopted) return t(a.raised ? 'setup.act.openedAdopted' : 'setup.act.openedAdoptedNoRaise', { port });
    return t(a.raised ? 'setup.act.opened' : 'setup.act.openedNoRaise');
  }
  return t('setup.act.done.' + a.choice, { port });
}

function nextButtons(row, i) {
  const ex = i.explain;
  const list = (ex && Array.isArray(ex.next) ? ex.next : []).filter((n) => NEXT.includes(n));
  list.forEach((n, k) => {
    const cls = k === 0 ? 'aj-btn--primary' : '';
    if (n === 'check') row.append(btn(cls, t('setup.check'), () => refresh(true, i.id), 'setup-check'));
    else row.append(btn(cls, t('setup.next.' + n), () => mark(i.id, n), 'setup-' + n.replace('_', '-')));
  });
  return list.length > 0;
}

function actions(box, i) {
  const row = mk('div', 'ajrow setup-actions');
  const optional = i.need !== 'required';
  const unused = () => row.append(btn('', t('setup.unused'), () => mark(i.id, 'unused'), 'setup-unused'));
  if (i.id === 'exit') {
    if (S.exit_armed) {
      box.appendChild(mk('p', 'setup-note', t('setup.exitNote')));
      row.append(btn('aj-btn--primary', t('setup.exitSent'), () => refresh(true, 'exit'), 'setup-check'));
    } else {
      row.append(btn('aj-btn--primary', t('setup.exitStart'), () => mark('exit', 'start_exit'), 'setup-exit'));
    }
  } else if (i.state === 'deferred') {
    row.append(btn('aj-btn--primary', t('setup.continue'), () => mark(i.id, 'selected'), 'setup-use'));
    if (optional) unused();
  } else if (nextButtons(row, i)) {
    if (optional) unused();
  } else if (i.choice === 'undecided') {
    row.append(btn('aj-btn--primary', t('setup.use'), () => mark(i.id, 'selected'), 'setup-use'));
    if (optional) unused();
  } else {
    if (i.state !== 'unsupported') row.append(btn('aj-btn--primary', t('setup.check'), () => refresh(true, i.id), 'setup-check'));
    if (optional) unused();
  }
  if (i.id !== 'exit' && i.state !== 'deferred') row.append(btn('aj-btn--quiet', t('setup.later'), () => mark(i.id, 'later'), 'setup-later'));
  box.appendChild(row);
}

function overview(box) {
  const wrap = mk('div', 'setup-all');
  for (const g of GROUPS) {
    const rows = S.items.filter((i) => i.group === g);
    if (g === 'optional') rows.sort((a,b) => { const order = lang() === 'en' ? ['tg','wx'] : ['wx','tg']; return (order.includes(a.id) ? order.indexOf(a.id) : 2) - (order.includes(b.id) ? order.indexOf(b.id) : 2); });
    if (!rows.length) continue;
    const gt = (S.groups || []).find((x) => x.id === g);
    wrap.appendChild(mk('h3', 'setup-group', pick(gt?.title) || g));
    if (g === 'browser' && sourceLine()) { const p = mk('p', 'setup-src', sourceLine()); p.id = 'setup-src'; wrap.appendChild(p); }
    const ul = mk('ul', 'setup-list');
    for (const i of rows) {
      const li = mk('li', 'setup-row');
      const st = i.choice === 'unused' ? t('setup.st.unused')
        : i.choice === 'undecided' && i.state !== 'verified' && i.state !== 'not_applicable'
          ? t('setup.st.undecided') + (i.state === 'unsupported' ? ' · ' + stateText('unsupported') : '')
          : stateText(i.state);
      li.dataset.state = i.choice === 'unused' ? 'unused' : i.state;
      li.dataset.id = i.id;
      const name = mk('span', 'setup-name', pick(i.title));
      if (i.choice !== 'unused' && i.state !== 'verified' && i.state !== 'not_applicable') name.appendChild(mk('span', 'setup-tag', whereTag(i)));
      li.append(name, mk('span', 'setup-st', st));
      ul.appendChild(li);
    }
    wrap.appendChild(ul);
  }
  box.appendChild(wrap);
}

function renderPage() {
  const box = el('setup-main');
  if (!box) return;
  box.replaceChildren();
  if (!S) { box.appendChild(mk('p', 'setup-note', t('setup.needsSetup'))); return; }
  const cur = S.items.find((i) => i.id === S.current);
  const kicker = S.remote_ready ? t('setup.kickerReady') : t('setup.kicker', { g: S.group, n: GROUPS.length });
  box.append(mk('p', 'setup-kicker', kicker));
  const h = mk('h2', '', S.remote_ready ? t('setup.titleReady') : cur ? pick(cur.title) : t('setup.titleStart'));
  h.id = 'setup-title';
  box.appendChild(h);
  if (!isReady() && Number.isInteger(S.updated_at)) {     // the host is the authority: offline = its last known revision
    const at = new Date(S.updated_at * 1000).toLocaleTimeString(lang() === 'en' ? 'en-GB' : 'zh-CN', { hour: '2-digit', minute: '2-digit' });
    box.appendChild(mk('p', 'setup-note', t('setup.offlineAt', { at })));
  }
  if (S.remote_ready) {
    box.appendChild(mk('p', 'setup-note', t('setup.readyNote')));
    const un = S.items.filter((i) => i.choice === 'unused').map((i) => pick(i.title));
    if (un.length) box.appendChild(mk('p', 'setup-note', t('setup.notEnabled', { list: un.join(lang() === 'en' ? ', ' : '、') })));
  } else if (cur) {
    const where = mk('p', 'setup-where', whereTag(cur) + ' · ' + stateText(cur.state)); where.id = 'setup-where';
    box.append(where, mk('p', 'setup-hint', pick(cur.hint)));
    if (cur.explain) {
      const f = mk('p', 'setup-fact', pick(cur.explain.fact)); f.id = 'setup-fact';
      const e = mk('p', 'setup-effect', pick(cur.explain.effect)); e.id = 'setup-effect';
      box.append(f, e);
    }
    const note = actionNote();
    if (note && (S.action?.item === cur.id || (S.action?.item === 'browser' && cur.group === 'browser'))) {
      const n = mk('p', 'setup-act', note); n.id = 'setup-act'; box.appendChild(n);
    }
    actions(box, cur);
  } else {
    box.appendChild(mk('p', 'setup-note', t('setup.nothing')));
  }
  box.appendChild(mk('p', 'setup-counts', t('setup.counts', { v: S.counts.verified, u: S.counts.unused, t: S.counts.todo, a: S.counts.auto, d: S.counts.undecided })));
  overview(box);
  box.dataset.revision = String(S.revision);
}

function render() {
  renderBanner();
  if (H.view() === 'setup') renderPage();
}
