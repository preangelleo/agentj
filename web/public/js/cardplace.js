// 0.26.9 #472 (CONTRACT §E6, chairman 11-04 17:1x): every card the phone can pop has ONE place — under the current reply card,
// in the reply's notice slot (#reply-notices, above the 阅读 · 朗读 · 转发 row), where P440 already put the sudo strip, the
// grants and the banners. Before this the approval / question sheet slid up from the bottom of the chat stage and the
// password, key, sign-in, help, goal and contact cards were centred dialogs over the whole screen — with the keyboard up they
// sat right on the composer ("从我的最底下…消息发送那一条弹出来"). Now:
//   · `CARDS` declares, for every card kind, its placement and the host messages that raise it. web/test/p0269-cards.test.mjs
//     fails when a host message in app.js's switch, or a card surface the modules build, is not declared here (a new card kind
//     cannot ship without saying where it goes).
//   · `sync()` (wired by ontop.js and its own observer) moves an open reply-placed card into #reply-notices while the chat is in
//     front, and back to its home when something else covers the chat (Settings, the reader, a page, a modal <dialog>): there the
//     P551 on-top behaviour keeps it visible over whatever the owner opened — the card follows the owner, never hides.
//   · Registered exceptions (`place: 'exception'`) are only what the platform or the owner forces full screen: the passkey /
//     Face ID sheet (drawn by the operating system, WebAuthn), the 「全屏」 reading of a revealed key the owner tapped, and the
//     disconnected state (not a card from the Agent J host — the page itself cannot reach it). `place: 'page'` = a card that
//     belongs to the page the owner is working in (the terminal's own login / confirm lines) or a dialog he opened there.
// #500 ROLLBACK (chairman 11-05 22:5x): the docking above is rolled back for every card the owner acts on (`place: 'own'`) —
// they are drawn where 0.26.8a2 drew them again; only the short notices and banners (`place: 'reply'`) live in the slot.
// Card text (#472 ② / §E1): `scopeLine()` draws the card's class and the host's one-line "why your task scope does not cover
// this" when the host sends `cls` / `why_scope` (optional; nothing extra when absent).

import { SVG } from './icons.js';   // the one SVG namespace constant (static.test: no URLs in shipped files)

// #500 rollback (chairman 11-05): `own` = the card is drawn where 0.26.8a2 drew it (the approval / question sheet from the bottom of
// the chat stage across the screen, the other action cards as centred dialogs) — #472's move under the reply card is rolled back.
export const PLACE = Object.freeze({ REPLY: 'reply', PAGE: 'page', EXCEPTION: 'exception', OWN: 'own' });
const OWN_WHY = '#500 rollback (chairman 11-05): a card the owner acts on is drawn where 0.26.8a2 drew it, not under the reply card';

/** kind → { place, surface (CSS selector of its root, null = drawn by the platform), msgs (host → phone types), was, why? }.
 *  `was` = where it was drawn before #472 (the inventory the report lists). */
export const CARDS = Object.freeze({
  approval:      { place: 'own', surface: '#sheet', msgs: ['ask', 'ask_done', 'answer_refused', 'approval_review_result', 'approval_questioned'], was: 'sheet rising from the bottom of the chat stage over a scrim', why: OWN_WHY },
  question:      { place: 'own', surface: '#sheet', msgs: ['question', 'question_done'], was: 'sheet rising from the bottom of the chat stage over a scrim', why: OWN_WHY },
  sudo:          { place: 'own', surface: '#elev', msgs: ['elev'], was: 'centred dialog over the whole screen (on the composer with the keyboard up)', why: OWN_WHY },   // kind sudo · secret · vshare
  sudo_run:      { place: 'reply', surface: '#elev-run', msgs: ['elev_progress', 'elev_out', 'elev_done', 'elev_refused'], was: 'reply notice slot (P440)' },
  sudo_grants:   { place: 'reply', surface: '#elev-grant', msgs: ['elev_grants', 'elev_auto'], was: 'reply notice slot (P440)' },
  mail_servers:  { place: 'own', surface: '#mail-servers', msgs: [], was: 'centred dialog over the whole screen', why: OWN_WHY },
  secret_out:    { place: 'own', surface: '.ajmodal.sout', msgs: ['secret_out', 'secret_out_val', 'secret_out_done', 'secret_out_err'], was: 'centred dialog over the whole screen', why: OWN_WHY },
  login_qr:      { place: 'own', surface: '.ajmodal.lqr', msgs: ['login_qr', 'login_qr_done', 'login_qr_state', 'login_qr_err'], was: 'centred dialog over the whole screen', why: OWN_WHY },
  login_sheet:   { place: 'own', surface: '.ajmodal.lgs', msgs: ['auth_rescue'], was: 'centred dialog over the chat', why: OWN_WHY },
  help_card:     { place: 'own', surface: '.ajmodal.hlp', msgs: ['help_card', 'help_end', 'help_err'], was: 'centred dialog over the whole screen', why: OWN_WHY },
  goal_opening:  { place: 'own', surface: '.ajmodal.lt:not(.lt-caps)', msgs: ['lt_card', 'lt_done', 'lt_res'], was: 'centred dialog over the whole screen', why: OWN_WHY },
  agent_caps:    { place: 'own', surface: '.ajmodal.lt-caps', msgs: ['lt_caps'], was: 'centred dialog over the whole screen', why: OWN_WHY },
  peer_approval: { place: 'own', surface: '.ajmodal.pkp', msgs: ['pk_peer_ask', 'pk_peer_done', 'pk_peer_close', 'pk_peer_answer_res'], was: 'centred dialog over the whole screen', why: OWN_WHY },
  contact_card:  { place: 'own', surface: '#ctCard', msgs: ['ct_card', 'ct_card_done', 'ct_card_err'], was: 'centred square dialog over the whole screen', why: OWN_WHY },
  confirm:       { place: 'own', surface: '#confirm', msgs: ['upg_card', 'upg_card_done', 'pk_offer', 'roster_bind_offer', 'roster_bind_res'], was: 'centred dialog over the whole screen', why: OWN_WHY },
  version_notice:{ place: 'exception', surface: '#version-notice', msgs: [], was: 'centred square over the whole screen',
                   why: 'the page\'s own 「我升级了 · 刷新」 notice about this web page (P150), not a card from the Agent J host; it never takes focus' },
  notice:        { place: 'reply', surface: '#toast', msgs: ['auto', 'approval_resign', 'owner_grant_notice'], was: 'reply notice slot (P440 rect); short status = centred circle (P461, not a card)' },
  banner:        { place: 'reply', surface: '#setup-banner, #qr-banner, #rescue-banner, #estop-banner, #grant-bar, #ar-renew, #shared-status, #version-later, #share-notice, #assist-banner',
                   msgs: ['setup_card', 'quota_resume', 'rescue', 'ark_plan', 'reset_done', 'estop_state', 'grant', 'grant_end', 'assist_state', 'assist_notice'], was: 'reply notice slot (P440)' },
  usage: { place: 'reply', surface: '#useBox', msgs: ['meter'], was: 'usage details in the existing reply header' },
  approval_details: { place: 'page', surface: '#full541', msgs: [], was: 'owner-opened full-screen command details', why: 'the owner opens the existing full-screen reader from a pending approval; it cannot execute the action' },
  form_draft:    { place: 'reply', surface: '.fstrip, .fdeep', msgs: ['form_draft_res'], was: 'existing reply form and owner-opened deep form; draft acknowledgement updates its state' },
  assist_terminal: { place: 'page', surface: '#assist-terminal-view', msgs: ['assist_term_event', 'assist_report'], was: 'customer assistant terminal page', why: 'read-only terminal events and reports belong to the separately opened assistant terminal page' },
  private_file:  { place: 'page', surface: '#private-file-locator', msgs: [], was: 'owner-opened private file locator', why: 'the locator opens a read-only download sheet; each transfer still needs this paired device and an explicit save tap' },
  terminal:      { place: 'page', surface: '#term', msgs: ['term_out', 'term_confirm', 'term_sudo', 'term_close', 'term_bang', 'term_go'],
                   was: 'inside the terminal page', why: 'the terminal\'s login-password and dangerous-line cards belong to the terminal page the owner is typing in' },
  bot_request:   { place: 'page', surface: '#bots-main', msgs: ['bots_tool_request', 'bots_proposal'],
                   was: 'the Bots page', why: 'a website / Telegram bot\'s write request is answered on that bot\'s page (its log and settings are there)' },
  settings_dialog: { place: 'page', surface: '.ajmodal.sxf, #wechat-panel', msgs: ['seat_xfer_state', 'wechat_state'],
                   was: 'centred dialog over Settings', why: 'opened by the owner from Settings (seat transfer code, WeChat binding); it stays with Settings' },
  faceid:        { place: 'exception', surface: null, msgs: [], was: 'operating-system sheet',
                   why: 'the passkey / Face ID / fingerprint sheet is drawn by the operating system (WebAuthn) over the whole screen; a web page cannot place it' },
  secret_full:   { place: 'exception', surface: '.ajmodal.sout.sout-full', msgs: [], was: 'full screen',
                   why: 'the owner tapped 「全屏」 to read a key he already unlocked with Face ID — a reader he opened, not a card that popped' },
  offline:       { place: 'exception', surface: '#offDlg', msgs: [], was: 'centred square over the greyed page',
                   why: 'the disconnected state is the page\'s own (no host is reachable to have sent a card); the whole page is unusable until it reconnects' },
});

/** host → phone message types in app.js's switch that raise no card (state, data, replies). The test requires every case
 *  to be either here or in some CARDS[kind].msgs. */
export const NOT_CARDS = Object.freeze([
  'term_prefill_res', // Terminal draft acknowledgement; no standalone card or content surface.
  'pair_guide', 'cmds_state', 'preferences', 'status', 'busy', 'res', 'theme', 'pair_ev', 'jcard_open', 'fr_changed', 'hook_changed',
  'reset_step', 'assist_task', 'msg_steered', 'devs', 'dev_pending', 'dev_pair_end', 'host_moved', 'pk_reg_res', 'pk_state', 'preauth',
  'draft_restore', 'rt', 'push_key', 'hist_read', 'hist_meta', 'hist_turn', 'models', 'msg', 'budget_state',
]);

/** Dialog-shaped roots that are pages, not cards (the test's surface scan skips exactly these). */
export const NOT_CARD_SURFACES = Object.freeze({ '#settings': 'the Settings page itself (full screen, opened by the owner)' });

const SURFACES = Object.entries(CARDS);
/** The placement of a host message type (null = not a card / undeclared). */
export function placementOfMsg(type) {
  for (const [kind, c] of SURFACES) if (c.msgs.includes(type)) return { kind, place: c.place };
  return null;
}
/** The declared kind of a card root (exceptions win: a full-screen key reader is still a .sout). */
export function kindOf(node) {
  if (!node || typeof node.matches !== 'function') return null;
  let hit = null;
  for (const [kind, c] of SURFACES) {
    if (!c.surface) continue;
    let m = false;
    try { m = node.matches(c.surface); } catch { m = false; }
    if (!m) continue;
    if (c.place === PLACE.EXCEPTION) return kind;
    hit = hit || kind;
  }
  return hit;
}

// overlay cards that move into the slot (the in-flow ones — sudo strip, grants, banners, notices — are built there)
export const DOCKABLE = Object.freeze(['#sheet', '#elev', '#mail-servers', '.ajmodal.sout', '.ajmodal.lqr', '.ajmodal.lgs', '.ajmodal.hlp',
  '.ajmodal.lt', '.ajmodal.pkp', '#ctCard', '#confirm']);
const DOCK_SEL = DOCKABLE.join(',');
const SKIP_SEL = DOCK_SEL + ',.scrim,.toast,#toast';

const $ = (id) => document.getElementById(id);
const homes = new Map();      // node → comment marker where it lived before it was docked
let busy = false;

const shown = (n) => !!n && n.isConnected && !n.hidden && !n.closest('[hidden]');
const modalDialog = () => [...document.querySelectorAll('dialog')].some((d) => { try { return d.matches(':modal'); } catch { return d.open; } });
/** Is the chat the screen the owner is looking at? (a card under the reply card would be visible there) */
export function chatInFront() {
  if (typeof document === 'undefined' || document.body?.dataset.view !== 'chat' || modalDialog()) return false;
  const stage = $('stage'), slot = $('reply-notices');
  if (!stage || !slot || !slot.getClientRects().length) return false;
  const r = stage.getBoundingClientRect();
  if (r.width < 2 || r.height < 2) return false;
  // probe the stage, looking through the cards themselves (a centred card's own scrim is not "something else")
  for (const [fx, fy] of [[0.5, 0.35], [0.5, 0.8], [0.15, 0.5]]) {
    const pile = document.elementsFromPoint(r.left + r.width * fx, r.top + r.height * fy);
    const first = pile.find((n) => !n.closest(SKIP_SEL));
    if (!first || !stage.contains(first)) return false;
  }
  return true;
}
// security r4 F8: in the slot a host card sits right under the model's words — every docked card carries a host-only badge
// 「来自 Agent J 主机」 built here from DOM calls (never from reply text: md.js sets only its own fixed classes, never a `cp-host`)
let tr = (k) => k;
const SHIELD = 'M12 3l7 3v5c0 4.5-3 8.3-7 10-4-1.7-7-5.5-7-10V6z';
export function badge(node) {
  if (typeof document === 'undefined' || !node) return null;
  const frame = node.matches('.ajmodal,.gdlg') ? node.querySelector(':scope>.box,:scope>.sq') || node : node;
  let b = frame.querySelector(':scope>.cp-host');
  if (b) return b;
  b = document.createElement('p'); b.className = 'cp-host'; b.dataset.cpHost = '1';
  const svg = document.createElementNS(SVG, 'svg'); svg.setAttribute('viewBox', '0 0 24 24'); svg.setAttribute('aria-hidden', 'true');
  const path = document.createElementNS(SVG, 'path'); path.setAttribute('d', SHIELD); svg.append(path);
  const span = document.createElement('span'); span.dataset.i18n = 'p0269c.fromHost'; span.textContent = tr('p0269c.fromHost');
  b.append(svg, span);
  frame.prepend(b);
  return b;
}
function undock(node, slot) {
  if (node.classList.contains('cp-docked')) node.classList.remove('cp-docked');
  if (node.dataset.cpModal) { node.setAttribute('aria-modal', 'true'); delete node.dataset.cpModal; }
  if (node.parentNode !== slot) return;                       // ontop.js lifted it into a dialog: that home is ontop's
  const mark = homes.get(node);
  homes.delete(node);
  if (mark && mark.isConnected) mark.replaceWith(node); else document.body.append(node);
}
// a question card has no such row: it stays at its start, the question first
/** Scroll ONLY #reply-notices so `el` is visible: 'start' = its top at the slot's top; 'nearest' = the smallest move. */
export function slotShow(el, mode = 'nearest') {
  const slot = $('reply-notices');
  if (!slot || !el || !slot.contains(el)) return;
  const q = slot.getBoundingClientRect(), r = el.getBoundingClientRect();
  let d = 0;
  if (mode === 'start') d = r.top - q.top;
  else if (r.bottom > q.bottom) d = Math.min(r.bottom - q.bottom, r.top - q.top);
  else if (r.top < q.top) d = r.top - q.top;
  if (d) slot.scrollTop += d;
}
/** #500 rollback (chairman 11-05 22:5x, 「这个不是重新设计，这个是roll back，因为以前的就很好」): the cards the owner acts on
 *  (approval / question sheet, confirm, password / key / sign-in / help / goal / contact / peer Face ID cards) are back where
 *  0.26.8a2 drew them — the approval / question sheet rises from the bottom of the chat stage across the whole screen width,
 *  the others are the centred dialogs P551 (ontop.js) keeps on top. Nothing is moved into #reply-notices any more; the short
 *  notices (#toast, banners, sudo strip, grants) stay in that slot as before (P440). One pass: a card left docked by an older
 *  build of this page goes home, and every open host card carries the security r4 F8 host-only badge (55ca571c5f). */
export function sync() {
  if (busy || typeof document === 'undefined') return;
  const slot = $('reply-notices');
  busy = true;
  try {
    for (const node of document.querySelectorAll(DOCK_SEL)) {
      // the sheet's badge is built by initCardPlace (ontop.js calls this pass before the dictionary function is handed over)
      if (node.id !== 'sheet' && shown(node) && kindOf(node)) badge(node);
      if (slot && (node.classList.contains('cp-docked') || (node.id !== 'sheet' && node.parentNode === slot))) undock(node, slot);
    }
  } finally { busy = false; }
}
/** #500 rollback: the approval / question sheet stays in its 0.26.8a2 home (the chat stage); only the F8 badge is added. */
export function initCardPlace(t) {
  if (typeof t === 'function') tr = t;
  const sheet = $('sheet');
  if (sheet) badge(sheet);
  const go = () => sync();
  if (typeof MutationObserver !== 'undefined') new MutationObserver(go).observe(document.body, { subtree: true, childList: true, attributes: true,
    attributeFilter: ['hidden', 'open', 'class', 'data-view', 'data-sheet'] });
  addEventListener('resize', go);
  sync();
}

// ------------------------------------------------------------------ #472 ②: the card's class + why the task scope does not cover it
/** Classes the host policy names (§E1). An unknown machine word shows no class label (never a raw identifier). */
export const CLASSES = Object.freeze(['spend_over_budget', 'secret_reveal', 'new_recipient', 'public_publish', 'permanent_delete', 'irreversible']);
const ONE = (v, n) => (typeof v === 'string' ? v.replace(/[\u0000-\u001f\u007f-\u009f\u2028\u2029]+/g, ' ').replace(/\s+/g, ' ').trim().slice(0, n) : '');
/** Pure: the (label, why) a card shows — label '' when the class is absent / unknown, why '' when absent. */
export function scopeText(card, tr) {
  const cls = ONE(card && card.cls, 60), why = ONE(card && card.why_scope, 300);
  const label = CLASSES.includes(cls) ? tr('p0269c.cls.' + cls) : '';
  return { label, why };
}
/** Fill (or hide) one `<p>` with 「需要你确认：…」 + why. Returns true when something is shown. */
export function paintScope(node, card, tr) {
  if (!node) return false;
  const { label, why } = scopeText(card, tr);
  node.replaceChildren();
  if (!label && !why) { node.hidden = true; return false; }
  if (label) { const b = document.createElement('b'); b.className = 'cp-cls'; b.textContent = label; node.append(b); }
  if (why) { const s = document.createElement('span'); s.className = 'cp-why'; s.textContent = tr('p0269c.whyScope', { why }); node.append(s); }
  node.hidden = false;
  return true;
}
