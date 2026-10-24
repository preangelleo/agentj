import * as wechat from './js/wechat.js';
import { floatAuthAsk } from './js/auth-overlay.js';
import { showPairGuide } from './js/pair-guide.js';
import { copyButton } from './js/copycode.js';

import {initControlGestures} from './js/control-gestures.js';
import {initPullRefresh} from './js/pull-refresh.js';
import {initRemote} from './js/tvremote.js';
import {longPress} from './js/longpress.js';
import { capturePage } from './js/pageshot.js';
import * as shotcopy from './js/shotcopy.js';   // P340 #27: the screenshot also goes to the clipboard
import * as fivePages from './js/five-pages.js';
import * as search from './js/search.js';
import * as pagefind from './js/pagefind.js';   // P363 (#70): the magnifier searches the page on screen
import { describeDevice } from './js/device-label.js';
// agentj web client. Spec: agentjarvis/protocol/PROTOCOL.md §2–§4, §8–§10; design agentjarvis/parity/DESIGN.md (PROMPT-33).
// The chat screen is relay's phone page (js/relay.js) on Agent J's end-to-end session; this module boots the page and owns
// Agent J's own screens: pairing (QR / link + 6-digit code typed on the computer), resume, removed / error, the Agent's
// name, the security badge, language, memory / activity / scheduled tasks, 全部停下, Web Push.
// PR1: every human-typed byte leaves this page only inside a Noise transport message; host / Agent text is rendered with
// textContent only (Markdown through js/md.js, DOM nodes only); the device's X25519 and Ed25519 private keys are
// non-extractable CryptoKeys in IndexedDB; drafts and input history are sealed (AES-GCM, non-extractable key); chat
// history is kept in memory only.
import { parsePairing as parseLink } from './proto/wire.js';
import VERSION from './version.js';
import {probeEnvironment, publicPhoneLink} from './js/phone-env.js';
import { t, lang, fillText, plain, applyStatic, onLang } from './js/t.js';
import { el, toast, executionToast, notify, noticeSheet, confirmSheet, closeConfirm } from './js/ui.js';
import { dbGet, dbPut, dbDel, deviceKey, signKey, wipeLocal, markPaired, wasPaired, askPersist } from './js/store.js';
import { b64u, unb64u } from './proto/wire.js';
import * as session from './js/session.js';
import * as snap from './js/snap.js';
import * as api from './js/api.js';
import { createAsrSetup } from './js/asr-setup.js';
import * as blobs from './js/blobs.js';
import * as media from './js/media.js';       // F21: files the Agent shows (§13)
import * as controls from './js/controls.js';
import * as push from './js/push.js';
import * as relay from './js/relay.js';
import * as settings from './js/settings.js';
import {initVersionNotice,setHostVersion} from './js/version-notice.js';
import {initShare, onShareReady, forgetShare} from './js/share.js';
import * as elevate from './js/elevate.js';     // F17: sudo / secret cards (§11)
import * as authrescue from './js/authrescue.js';   // P258: AI 登录救援 (red dot + pushes)
import * as webhooks from './js/webhooks.js';   // P366: Settings → Webhook re-reads on hook_changed
import * as qresume from './js/qresume.js';         // P314 (§32): 「额度用完，预计 HH:MM 自动继续」 banner
import * as rescue from './js/rescue.js';           // P332 (§33): Settings → 紧急修复 + the rescue-mode banner
import { registerMailForward } from './js/mailfwd.js';   // P189: file card → the dialog below
import * as contacts from './js/contacts.js';   // P184 (§25): contact cards + 「转发到邮箱」
import * as secretout from './js/secretout.js'; // F32: secret pickup cards (§18)
import * as cmdrun from './js/cmdrun.js';     // P361: Settings 「执行」
import * as termhook from './js/termhook.js'; // P408 (§38): Settings → 安全 → 网页终端 (the page itself loads on first open)
import * as bang from './js/bang.js';         // P414 (§38.12): 「!」 commands + their result card
import * as vaultrow from './js/vaultrow.js'; // P414 (§38.9): Settings → 安全 → 电脑密码
import * as keys from './js/keys.js';         // P352: 「模型和 API Key」 key manager
import * as longtask from './js/longtask.js';   // 0.18: opening card + 「我的 Agent 会什么」 (§20)
import * as logincard from './js/logincard.js'; // P114: sign-in QR cards (ADR-A194)
import * as helpcard from './js/helpcard.js'; // P411: screen-help cards (`agentj qr push` / `agentj screen ask`)
import * as passkey from './js/faceid.js';     // F20: the same phone without a second pairing (§12)
import * as renew from './js/renew.js';        // P122: …and after the browser deleted this page's storage (§12.1)
import * as bots from './js/bots.js';
import * as friends from './js/friends.js';    // §17.7: /friends + the friend cards
import * as setupcard from './js/setup.js';   // P115: the first-run checklist card
import * as devRename from './js/dev-rename.js';   // P179: long-press the source label → rename this remote
import * as busy from './js/busy.js';         // P150 (§22): 「在办事项」 dots + the centred dialog
import * as agentname from './js/agentname.js';   // P181 (§22.2): long-press the header name → rename
import { paintHeader, initHeaderLogo } from './js/logo.js';
import { applyTheme } from './js/theme.js';  // P169 (§23): the owner's theme (colours, logo, Send glyph, robots / dots)
import * as shotclip from './js/shotclip.js'; // P154: a screenshot copied on a computer → the attachment tray
import * as offline from './js/offline-card.js'; // P168: the disconnected card (blur + gdlg square, feedback@ after 30 min)
import { hintKey, hostKind, HINT_AFTER_MS } from './js/offhint.js';   // P287: the offline line under 「电脑离线」
import { createGate, DOWN_DEBOUNCE_MS } from './js/reconnect.js';   // P393: the five-second rule (one constant for every 「连不上」)
import { parseAgentId } from './proto/wire.js';
import { SCAN_CONSTRAINTS, SCAN_FALLBACK, SCAN_SLOW_MS, scanFrame } from './js/scan.js';   // F29 (ADR-A177)
import { classifyScan, RV_PREFIX } from './proto/wire.js';
import * as rvpair from './js/rvpair.js';   // P190 (§27): be scanned on the pairing screen; scan a new device from a paired one
import * as cardsync from './js/cardsync.js';   // P203 (§29.1): rebuild the cards from the computer's snapshot
import * as upgcard from './js/upgcard.js';     // P203 (§29.2): the upgrade confirmation card
import * as copycode from './js/copycode.js';   // P292: one copy behaviour for every code / pre
import * as pairpage from './js/pairpage.js'; // P210: the numbered method cards of 「连上你的电脑」

import { registerLayer, initLayers } from './js/layers.js';
import { initEdgeBack } from './js/edgeback.js';   // P302: left-edge swipe = back on every sub-page
import { wireTopFit } from './js/topfit.js';      // P340: header icon gaps follow the gear's margin on narrow screens
import { applyViewport, watchViewport } from './js/viewport.js';

const $ = el;
initControlGestures();

// ---------------------------------------------------------------- Agent display name (host → device {"t":"status",…,"name"})
// The name the owner gave this Agent: in the reply card's header (where relay named its target), in the menu
// (#brand-name) and as the tab title; "Agent J" when the host sends none. Untrusted display text: textContent only;
// format / surrogate / private-use / unassigned characters dropped, whitespace folded to one space, other control
// characters dropped, ≤ 32 code points. Cached in the host record (IndexedDB "host") for cold starts.
const DEFAULT_NAME = 'Agent J';
const NAME_MAX = 32;
let agentName = null;                                // null = the default
function cleanName(v) {
  if (typeof v !== 'string') return null;
  const s = v.slice(0, 512).replace(/[\p{Cf}\p{Cs}\p{Co}\p{Cn}]/gu, '').replace(/\s+/gu, ' ').replace(/\p{Cc}/gu, '').replace(/ {2,}/g, ' ').trim();
  const cps = Array.from(s);
  return (cps.length > NAME_MAX ? cps.slice(0, NAME_MAX).join('').trimEnd() : s) || null;
}
export function displayAgentName(name = agentName) {
  const raw = typeof name === 'string' ? name.trim() : '';
  if (!raw || raw.replace(/\s/gu, '').toLowerCase() === 'agentj') return DEFAULT_NAME;
  return raw.toLowerCase().startsWith('agent j - ') ? raw : `Agent J - ${raw}`;
}
function renderName() {
  $('brand-name').textContent = displayAgentName();
  $('agent').textContent = displayAgentName();
  document.title = agentName ? displayAgentName() : t('meta.title');
}
function setAgentName(v, save = true) {
  const n = cleanName(v);
  const changed = n !== agentName;
  agentName = n;
  renderName();
  if (save && changed && session.channel()) saveName(session.channel(), n).catch(() => { /* only a cache */ });
}
async function saveName(channel, name) {
  const h = await dbGet('host');
  if (!h || !h.approved || h.channel !== channel || (h.name ?? null) === name) return;
  await dbPut('host', { ...h, name });
}

// Read the pairing fragment first thing and strip it from the URL / history (it carries the one-time PSK).
let pendingLink = null;
if (location.hash.startsWith('#p=')) {
  pendingLink = location.hash;
  history.replaceState(null, '', location.pathname + location.search);
}
// §17.1 / §17.7: m.agentj.app/friends opens the friends page on this phone's paired session; `#add=AJ-…` (a friend's share
// link or QR) prefills 「加好友」 and is stripped from the address bar at once. Nothing of it is sent anywhere until the owner taps.
const FRIENDS_PATH = '/friends';
let wantFriends = location.pathname === FRIENDS_PATH;
let pendingAdd = null;
let pendingCard = false;           // P73: `/friends#card` (the /my-agent-id answer's link) opens 「我的名片」
function takeAddHash() {
  if (location.hash === '#card') { pendingCard = true; history.replaceState(null, '', location.pathname + location.search); return true; }
  if (!location.hash.startsWith('#add=')) return false;
  let raw = '';
  try { raw = decodeURIComponent(location.hash.slice(5)); } catch { /* a broken escape: keep it empty */ }
  pendingAdd = parseAgentId(raw) || raw.slice(0, 40);
  history.replaceState(null, '', location.pathname + location.search);
  return true;
}
if (takeAddHash()) wantFriends = true;
// P190 (§27): a new device's code opened by the system camera (`#j=…`): this page approves it once it is paired and ready.
let pendingRv = null;
function takeRvHash() {
  if (!location.hash.startsWith(RV_PREFIX)) return false;
  pendingRv = location.origin + '/' + location.hash;
  history.replaceState(null, '', location.pathname + location.search);
  return true;
}
takeRvHash();
function openFriendsWanted() {
  if (!wantFriends) return false;
  wantFriends = false;
  $('pair-friends').hidden = true;
  const add = pendingAdd; pendingAdd = null;
  const card = pendingCard; pendingCard = false;
  friends.open(add ? 'add' : card ? 'card' : 'list', add);
  return true;
}
// P73: a link to this page's own friends screens inside a reply (the /my-agent-id answer: …/friends#add=… · …/friends#card)
// opens the screen in place — no reload, no new tab, nothing sent.
const OWN_FRIENDS = /^https:\/\/m\.agentj\.app\/friends(?:\?[^#]*)?#(card|add=[A-Za-z0-9-]{1,40})$/;   // brand/lang.js may add ?lang=
document.addEventListener('click', (e) => {
  const a = e.target instanceof Element ? e.target.closest('a[href]') : null;
  if (!a || state !== 'ready') return;
  const href = a.getAttribute('href') || '';
  const own = OWN_FRIENDS.exec(href) || (href.startsWith(location.origin + '/friends#') ? OWN_FRIENDS.exec('https://m.agentj.app' + href.slice(location.origin.length)) : null);
  if (!own) return;
  e.preventDefault();
  if (own[1] === 'card') friends.open('card');
  else { let raw = own[1].slice(4); try { raw = decodeURIComponent(raw); } catch { /* keep */ } friends.open('add', parseAgentId(raw) || raw.slice(0, 40)); }
}, true);

// ---------------------------------------------------------------- state / views
let state = 'idle';
Object.defineProperty(window, '__ajState', { get: () => state, enumerable: false, configurable: false });
let statusKey = ['st.idle'];
// P129: a drop from a ready session is first retried silently. For up to QUIET_MS the screen keeps looking connected (no
// grey page, no 「正在恢复连接…」, no stale line) while the session reconnects underneath; sends already go to the offline
// queue (connected() reads `state`, which is updated at once). Only a reconnect that takes longer is shown.
// P393: the window is the link gate (js/reconnect.js): DOWN_DEBOUNCE_MS of foreground time, started again when the page
// comes back to the front; the relay's 「Agent J 主机离线」 waits it out too. During it only the dots say so (data-relink).
const QUIET_MS = DOWN_DEBOUNCE_MS;
let quietPaint = false, painted = 'idle';
const link = createGate({ ms: QUIET_MS, onChange: (v) => {
  document.body.toggleAttribute('data-relink', v === 'quiet');
  if (v === 'quiet') offline.hush();
  if (v === 'down') { quietPaint = false; paintStatus(); rescue.onLinkDown(); }
  else settings.connectionChanged(state === 'ready', v === 'quiet');
} });
/** Foreground / resume / network change: a drop that is not over gets a fresh silent window (no flash on unlock). */
function wakeLink() { link.restart(); }
let downSince = 0, hintTimer = 0, knownHost = { plat: null, boot: null };   // P287: see paintOffHint
function setStatus(s, key, vars) {
  state = s;
  statusKey = [key, vars];
  if (s === 'ready' || s === 'idle' || s === 'revoked') downSince = 0;
  else if (s === 'waiting-host' && key === 'st.hostDown' && !downSince) downSince = Date.now();
  // P393: the relay saying the computer itself is offline (st.hostDown) waits out the window like our own drop does (a
  // relay deploy, a phone back from the lock screen: the computer is back a second later).
  const down = s === 'connecting' || s === 'waiting-host';
  if (down && (painted === 'ready' || quietPaint)) {
    if (link.drop()) { quietPaint = true; relay.connLost(); offline.dropped(); }
    if (quietPaint) return;
  }
  if (down && PAIRED_VIEWS.includes(view) && link.drop()) offline.dropped();   // a paired page starting without a link
  if (!down) { quietPaint = false; link.up(); }
  paintStatus();
}
function paintStatus() {
  const s = state, key = statusKey[0], vars = statusKey[1];
  painted = s;
  const st = $('status');
  st.dataset.state = s;
  st.textContent = plain(t(key, vars));
  const on = s === 'ready';
  onShareReady(on);
  snap.S.conn = on;
  $('meta').hidden = !on;
  relay.setConn(on, st.textContent);
  settings.connectionChanged(on, !on && link.state() === 'quiet');
  paintOffHint();
  renderLogo();
  rerender();
  const m = offMode();
  if (m) offline.update(m, key);
}
// P168: what the disconnected card should do for the painted state + view (offline-card.js update()).
function offMode() {
  if (link.state() === 'quiet' || (painted === 'idle' && PAIRED_VIEWS.includes(view))) return null;   // quiet window / boot: no news yet
  if (painted === 'ready') return 'ready';
  if (['idle', 'pairing', 'awaiting-approval', 'revoked'].includes(painted)) return 'gone';
  return (painted === 'connecting' || painted === 'waiting-host') && PAIRED_VIEWS.includes(view) ? 'down' : 'other';
}
// P287: 「电脑离线」 for a while → one line in the page's own words on why and what to do (js/offhint.js). Only the relay's
// "computer offline" counts (our own network drop is 「正在恢复连接…」), only on the chat screen, never while connected.
function paintOffHint() {
  clearTimeout(hintTimer); hintTimer = 0;
  const box = $('offHint');
  const down = state === 'waiting-host' && statusKey[0] === 'st.hostDown' && view === 'chat' && downSince;
  const key = down ? hintKey(knownHost, Date.now() - downSince) : null;
  box.hidden = !key;
  box.textContent = key ? plain(t(key)) : '';
  if (down && !key) hintTimer = setTimeout(paintOffHint, Math.max(250, downSince + HINT_AFTER_MS - Date.now()));
}
async function learnHost() {
  const k = hostKind(session.peer);
  if (!k.plat) return;                                 // an older computer says nothing: keep what we knew
  knownHost = k;
  const h = await dbGet('host');
  if (h && h.approved && h.channel === session.channel() && (h.plat !== k.plat || h.boot !== k.boot)) await dbPut('host', { ...h, ...k });
}
dbGet('host').then((h) => { if (h && h.approved && !knownHost.plat) knownHost = hostKind(h); }).catch(() => { /* no storage: the general line */ });
onLang(() => paintOffHint());
let view = null;
let backToSettings = false;   // P187: see leavePage()
const VIEWS = { pair: 'pair-view', scan: 'scan-view', models: 'models-view', sas: 'sas-view', mem: 'mem-view', act: 'act-view', tasks: 'tasks-view', friends: 'friends-view', bots: 'bots-view', setup: 'setup-view', revoked: 'revoked-view', error: 'error-view' };
const PAIRED_VIEWS = ['chat', 'mem', 'act', 'tasks', 'models', 'friends', 'bots', 'setup'];
function show(v) {
  view = v;
  pagefind.close();
  if (v !== 'pair') rvpair.stopCode();
  if (v === 'chat') backToSettings = false;
  document.body.dataset.view = v;
  for (const [k, id] of Object.entries(VIEWS)) $(id).hidden = k !== v;
  $('unpair').hidden = !PAIRED_VIEWS.includes(v);
  $('scan-repair').hidden = $('unpair').hidden;
  for (const id of ['open-mem', 'open-act', 'open-tasks', 'open-models', 'open-friends', 'open-bots', 'open-setup']) $(id).hidden = !PAIRED_VIEWS.includes(v);
  // the address follows the friends page (m.agentj.app/friends can be bookmarked); leaving it for another paired view goes back to /
  if (v === 'bots') history.replaceState(history.state, '', '/bots'+location.search);
  else if (v !== 'bots' && PAIRED_VIEWS.includes(v) && location.pathname === '/bots') history.replaceState(history.state, '', '/'+location.search);
  if (v === 'friends' && location.pathname !== FRIENDS_PATH) history.replaceState(history.state, '', FRIENDS_PATH + location.search);
  else if (v !== 'friends' && PAIRED_VIEWS.includes(v) && location.pathname === FRIENDS_PATH) history.replaceState(history.state, '', '/' + location.search);
  push.renderA2hs(v);
  renderPhoneEnvironment();
  renderLogo();
  const m = offMode();
  if (m) offline.update(m, statusKey[0]);
}
function renderLogo() { paintHeader(); if (view === "chat") rerender(); }
let renderQueued = false;
function rerender() {
  if (renderQueued) return;
  renderQueued = true;
  queueMicrotask(() => { renderQueued = false; relay.render(snap.snapshot()); });
}

function deviceLabel() {
  return describeDevice(navigator.userAgent, push.standalone());
}

// The client only ever talks to our relay: relay.agentj.app, or the legacy alpha-relay.agentjarvis.net that pairing links
// from not-yet-updated hosts still carry (one version cycle). A pasted link cannot move this device to somebody else's
// relay + host (CSP connect-src enforces the same on this origin). A page served from 127.0.0.1 exists only in tests.
const RELAY_HOSTS = ['relay.agentj.app', 'alpha-relay.agentjarvis.net'];
function allowRelay(url) {
  const u = new URL(url);
  const ours = u.protocol === 'wss:' && !u.port && RELAY_HOSTS.includes(u.hostname);
  // localhost too (F20): WebAuthn refuses an IP address as rp id, so the passkey tests serve the page as localhost
  if (['127.0.0.1', 'localhost'].includes(location.hostname)) return ours || (u.protocol === 'ws:' && u.hostname === '127.0.0.1');
  return ours;
}

// The legacy web host (alpha-web.agentjarvis.net) still serves this page for one version cycle: a phone paired there keeps
// its keys in that origin's IndexedDB and keeps working there. A visitor with no pairing there goes to the new origin with
// path + query + fragment intact (a #p= pairing link arrives whole; it was only stripped from this page's history).
const LEGACY_WEB_HOST = 'alpha-web.agentjarvis.net';
const WEB_ORIGIN = 'https://m.agentj.app';
async function leaveLegacyHost() {
  if (location.hostname !== LEGACY_WEB_HOST) return false;
  let paired = false;
  try { const h = await dbGet('host'); paired = !!(h && h.approved); } catch { /* no storage → nothing to keep here */ }
  if (paired) return false;
  location.replace(WEB_ORIGIN + location.pathname + location.search + (pendingLink ?? location.hash));
  return true;
}
const parsePairing = (input) => parseLink(input, undefined, allowRelay);

// ---------------------------------------------------------------- session hooks
let lastSeq = 0;
function onHostGone() {
  snap.S.asks.clear(); snap.S.qs.clear(); snap.S.order = [];   // the host re-sends every request still open after ready
  controls.clearGrants();
  // P392: retain pending/running ids until authoritative cards sync or a terminal receipt.
  contacts.clear({ keepForward: true });              // P184: open contact cards come again after ready
  secretout.clear({ keepOpened: true });              // F32: and open pickup cards (a value on screen stays)
  longtask.clear();                                   // 0.18: the host re-sends open opening cards after ready
  logincard.clear();
  wechat.reset();                                  // P114: the host re-sends open sign-in cards after ready
  helpcard.clear();                                   // P411: … and open screen-help cards
  termhook.gone();                                    // P408: the terminal rode that session — the host has closed it
}
session.configure({
  setStatus,
  deviceLabel,
  resume,
  helloExtra: () => {
    const x = { since: lastSeq };
    if (snap.S.hist.last) x.hist = { epoch: snap.S.hist.epoch, last: snap.S.hist.last };
    return x;
  },
  onSas: (code) => { $('sas').textContent = code; delete $('sas-view').dataset.mode; sasWords(reversePairing); show('sas'); setStatus('awaiting-approval', 'st.awaiting'); },
  // P153 (§3.3): the host put a card on the paired remotes — nothing to type; the code stays as the one to compare
  onPairWait: () => { $('sas-view').dataset.mode = 'card'; setStatus('awaiting-approval', 'st.awaitingRemote'); },
  async onApproved(ctx) {
    knownHost = { plat: null, boot: null };           // P287: a new computer; its ready says what it is
    // dk = this browser's device public key: a later page that finds the pairing but another device key knows the browser
    // lost the key (0.15.1) instead of failing the resume and calling it "removed"
    const host = { relay: ctx.relay, channel: ctx.channel, hostPub: ctx.hostPub, approved: true, dk: b64u((await deviceKey()).pub) };
    if (restoreKind === 'pk') host.pk = true;         // P198: restored with a passkey, so the taken-over page can offer it again
    $('pair-guide').hidden = true;
    await forgetLocal();                              // a new pairing starts empty: nothing of the previous computer goes to it
    await dbPut('host', host);
    markPaired(true);
    ticketComing = true; ticketQueue(renew.clear);   // P122: the host sends this pairing's ticket right after (an old host: none)
    // Persistence permission is optional: never hold the pairing handshake on it.
    askPersist().then(() => checkPhoneEnvironment(true)).catch(() => {});
    $('pair-wiped').hidden = true;
    $('pair-error').hidden = true;
    setAgentName(null, false);                        // a new pairing: its name comes with the host's first status
    snap.synthReset(); snap.S.hist = { epoch: 0, first: 0, last: 0, count: 0 }; relay.historyReset(0);
    fivePages.clear();
    lastSeq = 0; controls.panel !== 'chat' && controls.openPanel('chat');
    return host;
  },
  onReady() {
    backfillPairing().catch(() => { /* only a hint for later */ });
    learnHost().catch(() => { /* only a hint for later */ });
    restoreKind = null; rtRetries = 0; pkTakeover = false;
    if (!ticketComing) ticketCheck().catch(() => {});
    ticketComing = false;
    onHostGone();
    controls.openPanel(controls.panel);
    setStatus('ready', 'st.ready');
    if(location.pathname==='/bots') bots.open();
    openFriendsWanted();
    reversePairing = false;
    if (pendingRv) { const c = pendingRv; pendingRv = null; rvpair.approve(c).catch(() => {}); }
    api.myDeviceId().then(() => rerender());
    relay.onReady();
    cmdrun.onReady();                                 // P393: 「连不上」 on the command card turns into 「已恢复连接」
    rescue.onReady();                                 // P332: rescue mode on / off; a repair that dropped the link and came back
    if (!session.peer.rescue) {                       // P332: the rescue watchdog answers nothing else — ask it nothing else
      fivePages.refresh();
      setTimeout(() => cardsync.sync(true), 1200);    // P203: after the host's own replay — fills gaps, drops cards that ended
    }
    // P189: the file cards' 「转发到邮箱」 icon (P183, mailfwd.js) opens P184's dialog — only on a host with `contacts`
    registerMailForward(contacts.forwardAvailable() ? (req) => contacts.openForward({ mid: req.mid, name: req.name }) : null);
    blobs.onReconnect([]);
  },
  onApp,
  onUiError: (e, t) => { document.body.dataset.uiError = `${t}: ${e && e.message}`; },
  onHostDown: onHostGone,
  onRevoked: revoked,
  onPairFailed: pairFailed,
  onRestoreFailed: restoreFailed,
  onProtocolFail(wasPair, e) {
    if (e && e.name === 'NotSupportedError') return fatal('error.noCrypto');
    setStatus('error', 'st.error');
    showError(wasPair ? 'error.pairBad' : 'error.dataBad', wasPair ? 'error.back' : 'error.reconnect', wasPair ? 'idle' : 'resume');
  },
});
snap.configure({
  onChange: (reset) => { if (reset) relay.historyReset(snap.S.hist.epoch); rerender(); },
  onTurn: (p) => relay.upsertTurn(p),
});
api.onState((m) => relay.onSayState(m));
controls.configure({ faceId: settings.faceIdStep, show, estopChanged: () => { relay.onEstop(); rerender(); }, friends: () => friends.refresh(), bots: () => bots.refresh() });
bots.configure({openPanel:controls.openPanel});
setupcard.configure({ openPanel: controls.openPanel, view: () => view });   // P131: the checklist page + chat banner
friends.configure({ openPanel: controls.openPanel, leave: () => leavePage(), agentName: () => displayAgentName() });
elevate.configure({ onSecretSaved: keys.saved, recover: () => cardsync.sync() }); // P392: no circular module dependency
contacts.configure({ openSettingsMail: () => settings.openMail() });   // P184: no mailbox yet → Settings → 邮件通道
upgcard.configure({ upgrade: () => api.upgradeNow() });   // P203: 「现在升级」 = this phone's signed /update
authrescue.configure({ openAuth: () => settings.openAuth() });   // P288: the chat's 「现在续期」 banner → Settings → AI 登录救援
qresume.configure();                                           // P314: ✕ on the quota banner = qr_cancel
rescue.configure({                                             // P332: 紧急修复 — the block, ≡ 重置连接, /reset, the banner
  openSettings: () => settings.openRescue(),
  linkState: () => (session.isReady() ? 'up' : link.state() === 'quiet' ? 'quiet' : 'down'),   // P393: wait out a blink
  toChat: () => { settings.close(); if (controls.panel !== 'chat') controls.openPanel('chat'); },
});
api.setUpgradeFlow(() => upgcard.start());                 // ≡ /update and Settings ask the computer first

function onApp(m) {
  if (api.route(m) || blobs.handle(m) || media.handle(m)) return;
  switch (m.t) {
    case 'pair_guide': showPairGuide(m.kind); return;
    case 'preferences': setHostVersion(m.host?.version); relay.hostVersionIs(m.host?.version); relay.applyPreferences(m.value); settings.setPrefs(m.value, m.host); rerender(); if(m.problem)relay.configProblem(m.problem.error); return;
    case 'status': setAgentName(m.name); snap.setStatus(m); renderTg(); return;
    case 'busy': busy.setBusy(m); return;            // P150 (§22): who works in the background (display only)
    case 'res': busy.setRes(m); return;              // P181 (§22.1): the load card's five resources (only while it is open)
    case 'theme': applyTheme(m); rerender(); relay.paintChrome(); return;   // P169 (§23): repaint now (logo, status bar)
    case 'ask':
      if (snap.addAsk(m)) frontAsk(m);
      return;
    case 'ask_done': friends.answerDone(m); snap.askDone(m); return;
    case 'answer_refused': relay.answerRefused(m); friends.answerRefused(m); snap.answerRefused(m); toast(t('fr.ask.failed'), 5000); return;
    case 'pair_ev': settings.onPairEv(m); return;    // P153 (§3.3): how the pairing this remote opened went
    case 'bots_tool_request': bots.toolRequest(m); return;
    case 'bots_proposal': bots.proposal(m); return;
    case 'jcard_open': fivePages.pushOpen(m.id, m.at); return;   // P360 (§34): `agentj card open` — go to that card / place
    case 'fr_changed': friends.changed(m); return;   // §17.7: something changed on the computer → the friends page re-reads
    case 'hook_changed': webhooks.changed(); return;   // P366: a webhook changed on the computer → the open Webhook page re-reads
    case 'elev': if (m.kind === 'login') termhook.msg(m); else elevate.add(m); return;   // F17 (§11) · P408: the terminal's login card stays in its page
    case 'auth_rescue': authrescue.onPush(m); return;   // P258 (§31)
    case 'quota_resume': qresume.onPush(m); return;     // P314 (§32)
    case 'rescue': rescue.onRescue(m); return;          // P332 (§33): the rescue watchdog answers instead of Agent J
    case 'ark_plan': rescue.onArkPlan(m); return;
    case 'reset_done': rescue.onDone(m); return;        // P332: a repair's end that reached this remote without its request
    case 'reset_step': return;                          // (steps of another remote's repair: its own card shows them)
    case 'elev_progress': elevate.progress(m); return;
    case 'elev_out': elevate.output(m); return;        // P406 (§11.2): a running sudo command's masked output (the strip)
    case 'elev_done': termhook.msg(m); elevate.done(m); return;
    case 'elev_refused': termhook.msg(m); elevate.refused(m); return;   // ADR-A163: Face ID not accepted, the card stays open
    case 'term_out': case 'term_confirm': case 'term_sudo': case 'term_close': termhook.msg(m); return;   // P408 (§38)
    case 'term_bang': case 'term_go': termhook.msg(m); return;   // P414 (§38.12 / §38.13): a 「!」 line's answer · the Agent opens the terminal
    case 'elev_grants': elevate.grants(m); return;     // #116 (§11.1): 10-minute same-class approvals + 「收回授权」
    case 'elev_auto': elevate.auto(m); return;         // #116: ran under that approval, no card
    case 'ct_card': contacts.card(m); return;         // P184 (§25): save / complete a contact (the owner's tap)
    case 'ct_card_done': contacts.cardDone(m); return;
    case 'ct_card_err': contacts.cardErr(m); return;
    case 'pk_offer': offerPasskey(m).catch(() => {}); return;    // F20 (§12)
    case 'setup_card': setupcard.card(m); return;     // P115: the first-run checklist (host snapshot)
    case 'devs': case 'dev_pending': case 'dev_pair_end': if (!rvpair.onRemotes(m)) settings.onRemotes(m); return;   // P171 (§24) · P190 (§27)
    case 'secret_out': secretout.add(m); return;       // F32 (§18): a pickup card (no value)
    case 'secret_out_val': secretout.value(m); return; // the value, after Face ID, to this session only
    case 'secret_out_done': secretout.done(m); return;
    case 'secret_out_err': secretout.failed(m); return;
    case 'wechat_state': wechat.route(m); return;
    case 'login_qr': logincard.add(m); return;          // P114: a sign-in QR card (only the QR, never chat history)
    case 'login_qr_done': logincard.done(m); return;
    case 'login_qr_state': logincard.state(m); return;
    case 'login_qr_err': logincard.failed(m); return;
    case 'help_card': helpcard.add(m); return;          // P411: a screen-help card (QR / screenshot, never chat history)
    case 'help_end': helpcard.end(m); return;
    case 'help_err': helpcard.failed(m); return;
    case 'pk_reg_res': pkSaved(m).catch(() => {}); return;
    case 'lt_card': longtask.add(m); return;          // 0.18 (§20): the one opening card
    case 'lt_done': longtask.done(m); return;
    case 'lt_res': longtask.res(m); return;
    case 'lt_caps': longtask.showCaps(m); return;      // 「我的 Agent 会什么」
    case 'rt': gotTicket(m); return;                  // P122 (§12.1): this phone's renewal ticket → the HttpOnly cookie
    case 'question': snap.addQuestion(m); return;
    case 'upg_card': upgcard.add(m); return;         // P203 (§29.2): what an upgrade now would interrupt — the owner decides
    case 'upg_card_done': upgcard.done(m); return;
    case 'question_done': snap.questionDone(m); return;
    case 'auto': controls.showAuto(m); return;
    case 'grant': controls.setGrant(m); return;
    case 'grant_end': controls.endGrant(m.id, m.why); return;
    case 'push_key': push.gotPushKey(m.k); return;
    case 'estop_state': controls.setEstop(m); snap.S.estop = { ...controls.estop }; return;
    case 'hist_read': relay.ownerRead(m); return;
    case 'hist_meta': snap.setMeta(m); relay.ownerRead(m); return;
    case 'hist_turn': snap.pushTurn(m); return;
    case 'meter': snap.setMeter(m); return;
    case 'models': snap.setModels(m); return;
    case 'msg':                                       // a host without p33 (§8): chat entries become in-memory pages
      if (Number.isInteger(m.seq)) lastSeq = m.seq;
      snap.synthMsg(m, api.myIdSync());
      return;
    default:                                          // unknown app message types are ignored (forward compatible)
  }
}

// error view: texts are keys so a language switch re-renders them
let errorKeys = null;
function renderError() {
  if (!errorKeys) return;
  fillText($('error-text'), t(errorKeys[0]));
  $('retry').textContent = t(errorKeys[1]);
}
function showError(textKey, btnKey, action) {
  errorKeys = [textKey, btnKey];
  renderError();
  $('retry').dataset.action = action;
  show('error');
}
function pairFailed() {
  setStatus('error', 'st.pairFail');
  showError('st.pairFail', 'error.back', 'idle');
}
// Removed by the computer (§10.14, P33-X13): the session ends, and the drafts, the input history, the key that sealed them,
// the tray and anything queued are deleted from this phone at once; the screen says so when there was an unsent draft.
/** A phone paired before 0.15.1 has neither the marker nor `dk`: the first ready resume proved its key, record both. */
async function backfillPairing() {
  if (!wasPaired()) markPaired(true);
  const h = await dbGet('host');
  if (h && h.approved && typeof h.dk !== 'string' && h.channel === session.channel()) await dbPut('host', { ...h, dk: b64u((await deviceKey()).pub) });
}
// why (0.15.1, PROTOCOL §3 `removed`): 'replaced' = a newer pairing took this phone's place on a full computer.
async function revoked(why = 'revoked') {
  session.closeSession();
  const hadDraft = !!$('input').value.trim() || !!(await dbGet('draft').catch(() => null));
  await forgetLocal();
  // this computer no longer knows the phone: the record stays only to say so after a reload (no resume, no network);
  // a new QR for the same computer pairs again (main() resumes instead only for a still-approved pairing)
  const h = await dbGet('host').catch(() => null);
  if (h && (h.approved || h.removed !== why)) await dbPut('host', { ...h, approved: false, removed: why }).catch(() => {});
  takeoverWith = null;
  markPaired(false);
  ticketQueue(renew.clear);                           // P122: a removed / replaced phone's ticket is dead on the host anyway
  const replaced = why === 'replaced';
  $('repair').hidden = replaced; // F36: continue in the other app; do not offer a takeover loop.
  setStatus('revoked', replaced ? 'st.replaced' : 'st.revoked');
  $('revoked-title').dataset.i18n = replaced ? 'revoked.replacedTitle' : 'revoked.title';
  $('revoked-lead').dataset.i18n = replaced ? 'revoked.replacedLead' : 'revoked.lead';
  applyStatic($('revoked-view'));
  show('revoked');
  $('revoked-draft').hidden = !hadDraft;
  $('takeover-note').hidden = true;
  $('takeover-pk').hidden = true;
  if (replaced) renderTakeover(h && { ...h, approved: false, removed: why }).catch(() => { $('takeover-pk').hidden = true; });
}
// P198 (F20 over F36, Jarvis's decision): Safari / the Home Screen app / a private tab share one Face ID record, and the side
// that was taken over may take the connection back here — one button on this page, never a prompt. It shows only when this
// pairing has (or may have) a passkey: `pk` true = saved or restored with one here; false = the computer offered to save
// one, i.e. it has none (a record from before P198 never said: the button shows, and a refused tap says why).
// A successful restore stores a new approved record (onApproved), so the next launch no longer opens this page.
let takeoverWith = null;
async function renderTakeover(h) {
  const b = $('takeover-pk'), lead = $('revoked-lead');
  const ok = !!h && h.removed === 'replaced' && h.pk !== false && !!(await signKey()) && (await passkey.supported());
  if (view !== 'revoked' || state !== 'revoked') return;
  takeoverWith = ok ? h : null;
  if (ok) pkPrep = await passkey.prepareRestore((await deviceKey()).pub, (await signKey()).pub);
  b.hidden = !ok;
  lead.dataset.i18n = ok ? 'revoked.takeoverLead' : 'revoked.replacedLead';
  fillText(lead, t(lead.dataset.i18n));
}
function takeoverNote(key) {
  const n = $('takeover-note');
  n.dataset.k = key;
  n.textContent = t(key);
  n.hidden = false;
}
/** Unpair / re-pair / revoke / a new pairing (§10.14, P33-X02 / X13). In-memory first, in the same tick (no await before it):
 *  uploads, queued and in-flight sends, the tray, the quote, the field and ↑↓ history; then the sealed records and their key. */
async function forgetLocal() {
  forgetShare();
  relay.forgetLocal();
  friends.forget();
  snap.S.touched.clear(); snap.S.handledWaiting = false;
  blobs.forgetAll();
  elevate.clear();
  contacts.clear();
  secretout.clear();
  longtask.clear();
  logincard.clear();
  wechat.reset();
  helpcard.clear();
  setupcard.clear();
  await wipeLocal();
}
function fatal(key) {
  session.closeSession();
  setStatus('error', 'st.unusable');
  showError(key, 'error.retry', 'reload');
}
async function resume() {
  const host = await dbGet('host');
  if (!host || !host.approved) { if (!(await tryTicket())) showIdle(); return; }
  setAgentName(host.name, false);                     // the cached name, shown before the connection is up
  show(controls.panel === 'chat' ? 'chat' : controls.panel);
  session.openSession('resume', { relay: host.relay, channel: host.channel, hostPub: new Uint8Array(host.hostPub) });
}

// ---------------------------------------------------------------- pairing input
function startPairing(input) {
  let p;
  try { p = parsePairing(input); } catch {
    showIdle();
    const err = $('pair-error');
    delete err.dataset.k;
    fillText(err, t('pair.badLink'));   // P292: `agentj pair` → code + ⧉
    err.hidden = false;
    return;
  }
  $('pair-link').value = '';                          // the link carries the one-time PSK
  $('pair-error').hidden = true;
  rvpair.stopCode();
  show('pair');
  session.openSession('pair', p);
}
// P190 (§27): this page was scanned by a paired remote, which handed it the pairing link: same pairing, other words on the
// code screen (compare the 6 digits with the remote's card instead of typing them on the computer).
let reversePairing = false;
function sasWords(rv) {
  for (const [id, k] of [['sas-title', 'title'], ['sas-lead', 'lead'], ['sas-note', 'note']]) {
    const n = $(id);
    n.dataset.i18n = (rv ? 'sas.rv' : 'sas.') + (rv ? k[0].toUpperCase() + k.slice(1) : k);
    fillText(n, t(n.dataset.i18n));
  }
}
function pairFromRemote(link) {
  reversePairing = true;
  startPairing(link);
}
function showIdle() {
  session.closeSession();
  setAgentName(null, false);
  show('pair');
  setStatus('idle', 'st.idle');
  reversePairing = false;
  rvpair.showCode().catch(() => {});
  $('pair-rv').hidden = !pendingRv;
  $('pair-friends').hidden = !wantFriends;
  renderPk().catch(() => { $('pk-restore').hidden = true; });
}

// ---------------------------------------------------------------- F20 passkey (PROTOCOL §12): no second pairing
// Register: once, after a pairing (the host sends pk_offer only then), one sheet; 「以后再说」 changes nothing and the
// question never comes back for this pairing (host record `pkAsked`). The system Face ID sheet must follow the tap, so
// nothing slow runs between 「保存」 and navigator.credentials.create.
// F32: 「设置 Face ID」 on a pickup card asks for an offer even after 「以后再说」 (pkForce, one offer).
let pkForce = false;
async function setupFaceId() {
  if (!(await passkey.supported())) { await checkPhoneEnvironment(); renderPhoneEnvironment(true); toast(t('sout.noFaceId'), 5000); return; }
  pkForce = true;
  session.sendApp({ t: 'pk_offer_req' }).catch(() => { pkForce = false; toast(t('elev.fail'), 3000); });
}
secretout.configure({ setupFaceId });
keys.configure({ setupFaceId });   // P352: 拷贝 / 删除 on a phone without a passkey offer the same F20 setup
cmdrun.configure({ setupFaceId, openTerminal: (cmd) => termhook.open(cmd) }); // P361: 「执行」 on a phone without a passkey — the same F20 offer · P408: 「在终端里打开」
termhook.configure({ setupFaceId, openSettings: () => { if (!settings.isOpen()) settings.open(); }, openRescue: () => settings.openRescue(), changed: () => cmdrun.paint(),
  attach: (f) => relay.attachShot(f, true), insert: (s) => relay.insertText(s), goChat: () => { if (settings.isOpen()) settings.close(); } });   // P408 · P414: 截屏发到聊天 / 放回输入框
vaultrow.configure({ setupFaceId });   // P414
let phoneEnvironment = null, phoneHintAfterPair = false, phoneHintDismissed = false;
function renderPhoneEnvironment(force = false) {
  const e = phoneEnvironment;
  const hint = $('phone-env');
  hint.hidden = !(e?.warn && (view === 'pair' || view === 'error' || force || (phoneHintAfterPair && !phoneHintDismissed && state === 'ready')));
  if (e) $('phone-env-text').textContent = t('phoneEnv.' + e.platform) + (e.platform === 'ios' && !e.platformAuth ? '\n\n' + t('phoneEnv.iosAuth') : '');
  $('phone-env-dismiss').hidden = view === 'pair' || view === 'error';
  $('setup-faceid').hidden = !PAIRED_VIEWS.includes(view);
}
async function checkPhoneEnvironment(afterPair = false) {
  phoneEnvironment = await probeEnvironment({ua: navigator.userAgent, touch: navigator.maxTouchPoints,
    storage: navigator.storage, supported: passkey.supported, probe: async () => {
      const key = 'p98-storage-probe';
      try { await dbPut(key, true); return await dbGet(key) === true; }
      finally { await dbDel(key).catch(() => {}); }
    }});
  if (afterPair) { phoneHintAfterPair = true; phoneHintDismissed = false; }
  renderPhoneEnvironment();
}
async function offerPasskey(m) {
  let h = await dbGet('host');
  const forced = pkForce; pkForce = false;
  // P198: the computer offers this only to a device without a passkey (after a pairing, a ticket restore): remember that
  if (h && h.approved && !forced && h.pk !== false && h.channel === session.channel()) await dbPut('host', (h = { ...h, pk: false }));
  if (!h || !h.approved || (h.pkAsked && !forced) || h.channel !== session.channel()) return;
  const ri = passkey.relayIndex(h.relay);
  const nonce = unb64u(m.n);
  if (ri < 0 || nonce.length !== 32 || !(await passkey.supported())) return;
  await dbPut('host', { ...h, pkAsked: true });
  const g = session.gen();
  const name = agentName ?? DEFAULT_NAME;
  const no = $('confirm-no');
  no.dataset.i18n = 'pk.later'; no.textContent = t('pk.later');
  let yes;
  try { yes = await confirmSheet(t('pk.askTitle'), t('pk.askText'), t('pk.save')); } finally { no.dataset.i18n = 'sheet.cancel'; no.textContent = t('sheet.cancel'); }
  if (!yes) return;
  let reg;
  // cancelled / refused by the system (iPhone: no iCloud Keychain → no passkey can be saved): nothing changes; say why once
  try { reg = await passkey.register(nonce, { relayIndex: ri, channel: h.channel, hostPub: h.hostPub, name, displaySuffix: t('pk.computer'), replace: m.replace || null }); } catch { toast(t('pk.notSaved'), 8000); return; }
  session.sendApp(reg, g).catch(() => toast(t('pk.saveFail')));
}
async function pkSaved(m) {
  toast(t(m.ok ? 'pk.saved' : 'pk.saveFail'));
  const h = await dbGet('host');
  if (m.ok && h && h.approved && h.channel === session.channel()) await dbPut('host', { ...h, pk: true });
}
// Restore: on the empty pairing screen, one button when this browser has Face ID / a platform authenticator. The new
// device key + approval key exist already (main() made them); the challenge binds the passkey's answer to them.
let pkPrep = null;
let pkCred = null;
async function renderPk() {
  const b = $('pk-restore');
  const sk = await signKey();
  if (view !== 'pair' || state !== 'idle' || !sk || !(await passkey.supported())) { b.hidden = true; return; }
  pkPrep = await passkey.prepareRestore((await deviceKey()).pub, sk.pub);
  b.hidden = view !== 'pair' || state !== 'idle';
}
async function pkRestore(auto = false, takeover = false) {
  pkTakeover = takeover;
  let prep = pkPrep;
  if (!prep || Date.now() - prep.ts > 4 * 60 * 1000) prep = await passkey.prepareRestore((await deviceKey()).pub, (await signKey()).pub);
  pkPrep = null;
  let a;
  try { a = await passkey.assert(prep.challenge); } catch (e) {                  // cancelled / no passkey here / not focused
    document.body.dataset.pkErr = (e && e.name) || 'error';                       // diagnostics only (tests read it)
    if (takeover) {                                   // P198: stay on the taken-over page; the button stays for another tap
      if (e && e.name === 'NotAllowedError') takeoverNote('revoked.takeoverNone');
      return renderTakeover(takeoverWith);
    }
    // P122: a tap that found nothing says why (no passkey saved — on iPhone usually iCloud Keychain off — or cancelled);
    // an automatic try (iOS may refuse it without a tap) stays silent: the button is still there
    if (!auto && e && e.name === 'NotAllowedError') pairNote('pk.none');
    return renderPk();
  }
  let to;
  try {
    to = passkey.decodeHandle(a.uh, { testRelay: new URLSearchParams(location.search).get('relay') });
    if (!allowRelay(to.relay)) throw new Error('relay');
  } catch { return pkFailed('bad'); }
  pkCred = a.id;
  restoreKind = 'pk';
  $('pk-restore').hidden = true;
  $('takeover-pk').hidden = true;
  $('takeover-note').hidden = true;
  $('pair-error').hidden = true;
  session.openSession('restore', { relay: to.relay, channel: to.channel, hostPub: to.hostPub,
    pk: { id: a.id, cd: a.cd, ad: a.ad, sig: a.sig, uh: b64u(a.uh), ts: prep.ts, nonce: b64u(prep.nonce) } });
}
/** One plain line under the pairing field (pair-error), by dictionary key. */
function pairNote(key) {
  const err = $('pair-error');
  err.dataset.k = key;
  err.textContent = t(key);
  err.hidden = false;
}

// ---------------------------------------------------------------- P122 renewal ticket (PROTOCOL §12.1): stay signed in
// The host issues one ticket per pairing / restore (and on rt_req); renew.save() turns it into the HttpOnly cookie at once.
// A page with no pairing first asks the origin for a proof over its NEW keys and, when there is one, reconnects as the same
// phone with no tap. Only the host's own answer ends that: unknown / revoked → the cookie goes and the screen says so.
const RT_REFRESH_MS = 30 * 86400 * 1000;              // a ready phone renews a ticket older than this (rt_req)
let ticketComing = false;                             // a pairing / restore: the host sends the ticket by itself
let restoreKind = null;                               // 'rt' | 'pk': which restore the session in flight is
let rtRetries = 0;                                    // consecutive `expired` ticket restores (the host was offline > 5 min)
let rtChain = Promise.resolve();
const ticketQueue = (fn) => { rtChain = rtChain.then(fn).catch(() => {}); return rtChain; };   // saves / clears in order
async function ticketCheck() {
  const h = await dbGet('host');
  if (!h || !h.approved || h.channel !== session.channel() || passkey.relayIndex(h.relay) < 0) return;
  if (typeof h.rtAt === 'number' && Date.now() - h.rtAt < RT_REFRESH_MS && (await renew.has()) !== false) return;
  await session.sendApp({ t: 'rt_req' });
}
function gotTicket(m) {
  const ch = session.channel();
  ticketQueue(async () => {
    const h = await dbGet('host');
    const ri = h ? passkey.relayIndex(h.relay) : -1;
    if (!h || !h.approved || h.channel !== ch || ri < 0) return;
    if (!(await renew.save({ relayIndex: ri, channel: h.channel, hostPub: new Uint8Array(h.hostPub) }, m.i, m.k))) return;
    const cur = await dbGet('host');
    if (cur && cur.approved && cur.channel === ch) await dbPut('host', { ...cur, rtAt: Date.now() });
  });
}
/** → true when a ticket restore is under way (the chat screen shows "connecting"); false = no ticket here / unreachable. */
async function tryTicket() {
  const sk = await signKey();
  if (!sk) return false;
  let pr;
  try { await rtChain; pr = await renew.prove((await deviceKey()).pub, sk.pub); } catch { return false; }
  if (!pr) return false;
  let to;
  try {
    to = passkey.decodeHandle(pr.uh, { testRelay: new URLSearchParams(location.search).get('relay') });
    if (!allowRelay(to.relay)) throw new Error('relay');
  } catch { ticketQueue(renew.clear); return false; }
  restoreKind = 'rt';
  $('pair-wiped').hidden = true;
  show('chat');
  session.openSession('restore', { relay: to.relay, channel: to.channel, hostPub: to.hostPub,
    rt: { i: pr.i, p: pr.p, uh: b64u(pr.uh), ts: pr.ts, nonce: pr.nonce } });
  return true;
}
function restoreFailed(why) {
  const kind = restoreKind; restoreKind = null;
  if (kind !== 'rt') return pkFailed(why);
  if (why === 'expired' && rtRetries++ < 3) {         // the proof aged while the computer was offline: a fresh one
    tryTicket().then((ok) => { if (!ok) showIdle(); }, () => showIdle());
    return;
  }
  rtRetries = 0;
  showIdle();
  if (why === 'unknown' || why === 'revoked') { ticketQueue(renew.clear); pairNote('rt.dead'); return; }
  rtGaveUp = true;                                    // `bad`: keep the cookie, but no automatic retry on this page load
  autoPasskey().catch(() => {});                      // a passkey may still get this phone back
}
let rtGaveUp = false;
/** Back online / back in the foreground on the empty pairing screen (a ticket restore could not reach the origin). */
function ticketWhenIdle() {
  if (view !== 'pair' || state !== 'idle' || rtGaveUp || session.current()) return;
  tryTicket().catch(() => {});
}
/** The phone was paired here before (the browser lost the pairing, or the ticket did not work): try its passkey once without
 *  waiting for a tap. Chromium allows it; iOS Safari may want a tap first — then nothing shows and the button stays. */
async function autoPasskey() {
  if (view !== 'pair' || state !== 'idle' || !(await signKey()) || !(await passkey.supported())) return;
  await pkRestore(true);
}

let pkTakeover = false;                                // P198: the restore in flight started on the taken-over page
function pkFailed(why) {
  const dead = why === 'unknown' || why === 'revoked';
  if (dead && pkCred) passkey.forgetDead(pkCred);
  if (pkTakeover) {
    pkTakeover = false;
    // the computer unreachable / busy: still taken over here, the button stays; a passkey the computer no longer knows can
    // never bring this pairing back — forget the record (no more taken-over page) and say so on the pairing screen
    if (!dead) { setStatus('revoked', 'st.replaced'); show('revoked'); takeoverNote('pk.failed'); return renderTakeover(takeoverWith).catch(() => {}); }
    dbDel('host').catch(() => {});
  }
  showIdle();
  const err = $('pair-error');
  err.dataset.k = dead ? 'pk.dead' : 'pk.failed';
  err.textContent = t(err.dataset.k);
  err.hidden = false;
}
/** 重新配对 / 解除配对: forget the computer, and with it the sealed drafts and input history. Keeps the device key. */
async function forgetHost() {
  session.closeSession();
  const wiped = forgetLocal();
  await dbDel('host');
  await wiped;
  markPaired(false);
  ticketQueue(renew.clear);                           // P122: unpaired here = no automatic sign-in either
  $('pair-wiped').hidden = true;
  snap.synthReset(); snap.S.hist = { epoch: 0, first: 0, last: 0, count: 0 }; relay.historyReset(0);
  $('input').value = '';
  showIdle();
}

// In-app scanner keeps the pairing keys in this standalone app's own storage.
// P190: three modes. 'pair' = the unpaired page scans a computer's code (as before: tap 「扫描」). 'any' = a paired page's scan
// icon / the camera button held: scanning starts at once; a new device's code → the add-remote card, a friend's link → 加好友,
// a computer's pairing link → a hint (it carries a secret: never into the chat), anything else → the message field as text
// (never opened, never sent). 'rv' = 遥控器管理's 「扫码添加遥控器」: only a new device's code counts.
let scanStream = null;
let scanGeneration = 0;
let scanActive = false;
let scanMode = 'pair';
let scanBack = null;          // where a paired scan returns: 'chat' | 'remotes'
async function startScan(mode = 'pair') {
  if (view === "scan" && scanStream) return;
  stopScan(false);
  scanMode = mode;
  const generation = scanGeneration;
  if (mode !== 'pair') { scanBack = settings.isOpen() ? 'remotes' : (scanBack || 'chat'); settings.close(); }
  show("scan");
  document.body.dataset.scan = mode;
  if (!history.state?.ajScan) history.pushState({ajScan:true}, "");
  const hint = $('camera-hint');
  fillText(hint, t(mode === 'pair' ? 'camera.aim' : mode === 'rv' ? 'scan.aimRv' : 'scan.aim')); hint.hidden = false;
  scanActive = true; paintScanTrigger();   // P207 §3: every mode scans at once (no 「扫描」 tap first)
  let det = null;
  try {
    if (window.BarcodeDetector && (await window.BarcodeDetector.getSupportedFormats()).includes('qr_code'))
      det = new window.BarcodeDetector({ formats: ['qr_code'] });
  } catch { /* bundled decoder below */ }
  if (!navigator.mediaDevices?.getUserMedia || (!det && !window.jsQR)) {
    fillText(hint, t('pair.scanHint')); hint.hidden = false; stopScan(false); return;
  }
  if (generation !== scanGeneration) return;
  let stream;
  try {
    // F29: ask for 1080p (iOS gives ~640×480 otherwise); a camera that fails on it (not a refusal) gets the old request
    try { stream = await navigator.mediaDevices.getUserMedia(SCAN_CONSTRAINTS); }
    catch (e) { if (e?.name === 'NotAllowedError' || e?.name === 'SecurityError') throw e; stream = await navigator.mediaDevices.getUserMedia(SCAN_FALLBACK); }
  } catch {
    if (generation !== scanGeneration) return;
    fillText(hint, t('pair.scanNoCam')); hint.hidden = false; stopScan(false); return;
  }
  if (generation !== scanGeneration) { stream.getTracks().forEach(tr => tr.stop()); return; }
  scanStream = stream; paintScanTrigger();
  const video = $('scan-video');
  video.srcObject = stream;
  $('scan-box').hidden = false;
  try { await video.play(); } catch { if(generation !== scanGeneration) return; stopScan(false); fillText(hint, t('pair.scanNoCam')); hint.hidden = false; return; }
  const canvas = document.createElement('canvas');
  const ctx = canvas.getContext('2d', { willReadFrequently: true });
  let attempt = 0, since = 0, slowShown = false;
  const tick = async () => {
    if (scanStream !== stream) return;
    if (!scanActive) { setTimeout(tick, 250); return; }
    // F29: 10 s of scanning without a result → say what else works (the home-screen app pairs from a pasted link; a Safari
    // tab can use the system camera, which opens the pairing page itself)
    if (!since) since = Date.now();
    else if (!slowShown && Date.now() - since >= SCAN_SLOW_MS) {
      slowShown = true;
      fillText(hint, t(push.standalone() ? 'camera.slowApp' : 'camera.slow')); hint.hidden = false;
    }
    let link;
    try {
      if (det) {
        const hits = await det.detect(video);
        const want = scanMode === 'pair' ? (c) => c.rawValue.includes('#p=') : () => true;
        link = hits.find(c => typeof c.rawValue === 'string' && c.rawValue && want(c))?.rawValue;
        if (!link && scanMode === 'pair' && hits.some(c => classifyScan(c.rawValue) === 'reverse')) fillText(hint, t('rv.scanUnpaired'));
      } else if (video.readyState >= 2 && video.videoWidth) {
        link = scanFrame(ctx, video, video.videoWidth, video.videoHeight, attempt++, window.jsQR);
      }
      if (scanStream !== stream) return;
      if (link && scanMode !== 'pair') { stopScan(); scanned(link, generation); return; }
      if (link && scanMode === 'pair' && classifyScan(link) === 'reverse') { fillText(hint, t('rv.scanUnpaired')); link = null; }
      if (link) { parseLink(link); stopScan(); await startPairing(link); return; }
    } catch { /* an unreadable frame or unrelated QR: keep scanning */ }
    if (scanStream === stream) setTimeout(tick, 250);
  };
  tick();
}
/** P207 §3: the round pause / resume icon (aria-pressed = paused; the word lives in aria-label + title). No stream = retry. */
function paintScanTrigger() {
  const b = $('scan-trigger'), paused = !scanActive;
  const label = t(scanStream && scanActive ? 'camera.pause' : 'camera.scan');
  b.setAttribute('aria-pressed', String(!!scanStream && paused));
  b.setAttribute('aria-label', label); b.title = label;
  if (scanStream && paused) document.body.dataset.scanPaused = '1'; else delete document.body.dataset.scanPaused;
}
function stopScan(leave = true) {
  ++scanGeneration;
  if (scanStream) for (const tr of scanStream.getTracks()) tr.stop();
  scanStream = null;
  $('scan-video').srcObject = null;
  $('scan-box').hidden = true;
  scanActive = false;
  paintScanTrigger();
  delete document.body.dataset.scan;
  if (leave) {
    if (view === 'scan') {
      if (scanMode === 'pair') show('pair');
      else { const back = scanBack; scanBack = null; controls.openPanel('chat'); if (back === 'remotes') settings.open('remotes'); }
    }
    // Avoid a queued popstate overriding a successful QR pairing approval page.
    if (history.state?.ajScan) history.replaceState(null, '');
  }
}
/** P190: what a paired scan found. Scanned text is data: shown or inserted, never opened, never sent by itself. */
function scanned(text, generation) {
  const kind = classifyScan(text);
  if (kind === 'reverse') { rvpair.approve(text).catch(() => {}); return; }
  if (scanMode === 'rv') { toast(t(kind === 'pair' ? 'scan.pairLink' : 'rv.notCode'), 5000); return; }
  if (kind === 'pair') { toast(t('scan.pairLink'), 6000); return; }
  if (kind === 'friend') {
    let raw = text.slice(text.indexOf('#add=') + 5);
    try { raw = decodeURIComponent(raw); } catch { /* keep */ }
    friends.open('add', parseAgentId(raw) || raw.slice(0, 40));
    return;
  }
  const clean = String(text).replace(/\r\n?/g, '\n').replace(/[\u0000-\u0008\u000B-\u001F\u007F]/g, '').slice(0, 4000);
  if (!clean.trim()) return;
  relay.insertText(clean);
  toast(t('scan.inserted'), 3500);
}
/** The header scan icon: paired → scan anything; on the pairing screen → a computer's code. */
function scanIcon() {
  if (state === 'ready' || PAIRED_VIEWS.includes(view)) { scanBack = null; startScan('any'); }
  else startScan('pair');
}
document.addEventListener('visibilitychange', () => { if (document.hidden) stopScan(); });
window.addEventListener('pagehide', () => stopScan());
window.addEventListener('popstate', () => { if(view === 'scan') stopScan(); });
function fitPairViewport() {
  applyViewport();          // P142: the same rule as the chat (keyboard up → visual viewport, else full height)
  // P218: since P210 the link box sits under a title, a note and the command, so the block's top no longer keeps the box
  // and 「开始配对」 above a 300 px keyboard on a 360×640 phone — start the scroll at the box's own label instead
  if (document.activeElement === $('pair-link')) ($('pair-link').labels?.[0] || $('pair-link')).scrollIntoView({ block: 'start' });
}
document.addEventListener('focusin', () => { fitPairViewport(); setTimeout(fitPairViewport, 300); });
document.addEventListener('focusout', () => setTimeout(fitPairViewport, 300));
watchViewport(fitPairViewport);

// ---------------------------------------------------------------- language switch: re-render everything that is ours
// P129: the host's Telegram channel is really down (≥3 failures / ≥2 min): one line in the chat's status area, in the page's
// language; it disappears with the next status that does not say so.
function renderTg() {
  const n = $('tgdown');
  if (!n) return;
  n.hidden = !snap.S.tg;
  n.textContent = snap.S.tg ? t('r.channelDown', { name: 'Telegram' }) : '';   // the channel's name is data, not dictionary copy
}
function relang() {
  applyStatic();
  renderTg();
  renderName();
  setStatus(state, statusKey[0], statusKey[1]);
  push.pushUi();
  controls.renderGrants();
  controls.renderEstop();
  renderError();
  push.renderA2hs(view);
  if (!$('scan-hint').hidden) fillText($('scan-hint'), t('pair.scanHint'));
  if (!$('pair-error').hidden) fillText($('pair-error'), t($('pair-error').dataset.k || 'pair.badLink'));
  if (!$('takeover-note').hidden) $('takeover-note').textContent = t($('takeover-note').dataset.k);
  controls.reloadPanel();
  friends.relang();
  push.reregister();
}

async function openModels() {
  closeMenu(); show('models');
  const modelBox = $('model-options');
  modelBox.replaceChildren();
  keys.load($('keys-box')).catch(() => {});   // P352: the key manager (names / states only; values never on the page)
  const catalog = await api.models();
  if (view !== 'models') return;
  if (catalog) snap.setModels(catalog);
  const c = snap.snapshot().switch;
  for (const m of c?.models || []) {
    const b = document.createElement('button'); b.type = 'button'; b.className = 'aj-btn'; b.dataset.modelId = m.id; b.textContent = m.name && m.name !== m.id ? `${m.name} · ${m.id}` : m.id;
    b.addEventListener('click', async () => {
      b.disabled = true;
      const result = await api.model({model:m.id, effort:m.efforts?.[0] || null});
      b.disabled = false; toast(t(result.ok ? 'models.changed' : 'models.failed'));
    }); modelBox.append(b);
  }
  if (!c?.models?.length) modelBox.textContent = t('r.pill.unsupported');
}
const asrSetup = createAsrSetup({t, notify,
  confirm: () => confirmSheet(t('asrFix.title'), t('asrFix.note'), t('asrFix.install')),
  install: api.asrInstall,
  ready: () => { session.peer.asr = 'ready'; },
  repaint: () => relay.paintVoiceReadiness(),
});
const installAsr = () => asrSetup.run();

// ---------------------------------------------------------------- wiring
// P171: the ≡ menu is gone — its rows live in the full-screen Settings page; leaving it for a sub-view closes it first.
// P187: a page opened from a Settings row is its second level — its back icon and Esc return to Settings, not the chat.
const closeMenu = () => settings.close();
let backFocus = null;
const fromSettings = (fn) => (e) => { backToSettings = settings.isOpen(); backFocus = e?.currentTarget?.id || null; settings.close(); fn(); };
// P165: a new remote asks to pair (P153 card) while this remote's own 遥控器管理 is open — step aside so the card
// (hold to confirm, no typing) is in front; the page's code field stays one tap away.
// P417 #186: every NEW 「请你点头」 card does the same — Settings never hides one. A Same-owner card (its six-digit code)
// also leaves the friends page / any other page for the chat, where the card is.
function frontAsk(m) {
  if (m.tool === 'AuthRescue' && settings.isOpen() && !$('set-auth').hidden) { floatAuthAsk(); return; }
  relay.remoteReader(false);
  if (m.tool === 'pair_device' && settings.isOpen()) settings.onPairCard();
  if (settings.isOpen()) settings.close();
  if (view !== 'chat' && PAIRED_VIEWS.includes(view)) controls.openPanel('chat');
}
function leavePage() {
  const again = backToSettings;
  backToSettings = false;
  controls.openPanel('chat');
  if (!again) return;
  settings.open();
  const row = backFocus && $(backFocus);
  if (row && !row.closest('[hidden]')) row.focus();         // back on the row that opened the page
}
function toggleBadge(open) {
  const p = $('badge-panel');
  p.hidden = !(open ?? p.hidden);
  if (p.hidden) delete p.dataset.modalOpen; else p.dataset.modalOpen = '1';
  $('menu-about').setAttribute('aria-expanded', String(!p.hidden));
  if (!p.hidden) $('badge-close').focus();
}
function wire() {
  $('version-hash').textContent = VERSION.combined;
  $('badge-close').addEventListener('click', () => toggleBadge(false));
  $('badge-panel').addEventListener('click', (e) => { if (e.target === $('badge-panel')) toggleBadge(false); });
  $('setup-faceid').addEventListener('click', () => { closeMenu(); setupFaceId().catch(() => {}); });
  $('phone-env-dismiss').addEventListener('click', () => { phoneHintDismissed = true; renderPhoneEnvironment(); });
  $('phone-env-copy').addEventListener('click', async () => {
    const link = publicPhoneLink(location.href);
    if (!await copyButton(link, $('phone-env-copy'))) { $('phone-env-link').value = link; $('phone-env-link').hidden = false; $('phone-env-link').select(); }
  });
  onLang(() => renderPhoneEnvironment());
  $('menu-about').addEventListener('click', () => toggleBadge(true));   // above Settings; closing it returns there
  initRemote({card:fivePages.remoteStep,message:relay.remoteMessage,reader:relay.remoteReader,reading:relay.remoteReading,scroll:relay.remoteScroll});
  initLayers({onEscape:relay.resetEscape});
  initEdgeBack();
  wireTopFit();
  const layer = (id, close, fallback, rank = 20, trap = true) => registerLayer({element: () => $(id), open: () => !$(id).hidden, close, fallback: () => $(fallback), rank, trap});
  layer('confirm', closeConfirm, 'setBtn', 60);
  layer('badge-panel', () => toggleBadge(false), 'menu-about', 40);
  layer('settings', settings.back, 'setBtn', 40);
  // P363 (#70): one magnifier, the page on screen as its scope — a five page / document (five-pages.js), a menu page (记忆 /
  // 活动 / 后台任务 / 模型和 API Key / 好友 / Bot / 首次准备: in-text find), else the chat's 搜索聊天记录
  search.setScope(() => fivePages.find() || (['mem','act','tasks','models','friends','bots','setup'].includes(view) && pagefind.openIn($(VIEWS[view]))));
  pagefind.initLayer(registerLayer, () => $('srchBtn'));
  registerLayer({element: () => $(VIEWS[view]), open: () => ['mem','act','tasks','models','friends','bots','setup','scan'].includes(view),
    close: () => { if(view === 'scan') stopScan(); else if(view === 'friends') friends.back(); else leavePage(); },
    trigger: () => $('setBtn'), fallback: () => $('setBtn'), rank: 1, trap: false});
  $('a2hs-ok').addEventListener('click', () => push.dismissA2hs(view));
  $('scan').hidden = false;
  $('pair-link').addEventListener('focus', () => { stopScan(); fitPairViewport(); setTimeout(fitPairViewport, 300); });
  $('pair-go').addEventListener('click', () => startPairing($('pair-link').value));
  pairpage.init(() => startScan());
  $('scanBtn').addEventListener('click', () => { settings.close(); closeMenu(); scanIcon(); });
  $('rm-scan-icon').addEventListener('click', () => startScan('rv'));   // P207 §3: the scan icon right of 「添加遥控器」
  $('pk-restore').addEventListener('click', () => { stopScan(); pkRestore().catch(() => pkFailed('bad')); });
  $('takeover-pk').addEventListener('click', () => { pkRestore(false, true).catch(() => pkFailed('bad')); });   // P198
  $('scan-stop').addEventListener('click', () => stopScan());
  $('scan-back').addEventListener('click', () => stopScan());
  $('scan-trigger').addEventListener('click', () => { if (view !== 'scan') return; if (!scanStream) { startScan(scanMode); return; } scanActive = !scanActive; paintScanTrigger(); });
  $('open-models').addEventListener('click', fromSettings(openModels));
  $('models-back').addEventListener('click', leavePage);
  $('grant-off').addEventListener('click', controls.revokeGrants);
  $('open-mem').addEventListener('click', fromSettings(() => controls.openPanel('mem')));
  $('open-act').addEventListener('click', fromSettings(() => controls.openPanel('act')));
  $('open-tasks').addEventListener('click', fromSettings(() => controls.openPanel('tasks')));
  $('open-friends').addEventListener('click', fromSettings(() => friends.open('list')));
  $('open-bots').addEventListener('click', fromSettings(() => bots.open()));
  $('open-setup').addEventListener('click', fromSettings(() => setupcard.open(true)));
  friends.wire();
  for (const b of document.querySelectorAll('[data-back]')) b.addEventListener('click', leavePage);
  $('act-more').addEventListener('click', () => controls.loadActivity(false));
  $('resume').addEventListener('click', controls.onResume);
  const unpairAsk = async () => {
    closeMenu();
    if (await confirmSheet(t('menu.unpairTitle'), t('menu.unpairText'), t('menu.unpairYes'), true)) forgetHost();
  };
  $('unpair').addEventListener('click', unpairAsk);
  initVersionNotice(settings.refreshApp, {blocked: relay.refreshBlocked, save: relay.saveDraft});
  busy.init({ name: () => displayAgentName(), prompt: (k) => relay.sayPreset(k), detail: api.busyDetail });   // P160: a row → its read-only card
  agentname.init({ name: () => agentName ?? DEFAULT_NAME, canRename: () => session.isReady() });
  shotclip.init({ attach: (f) => relay.attachShot(f, false, true), say: () => toast(t('set.shotDenied'), 4000) });   // P398: auto
  offline.configure({
    retry: () => { if (session.retryInfo().nextAt) session.reconnectNow('manual'); else resume().catch(() => {}); },
    retryInfo: session.retryInfo,
    version: VERSION.version,
    device: deviceLabel,
    // the outage start survives a reload in the host record (a number, nothing else); written in order with the ticket
    persist: (at) => ticketQueue(async () => {
      const h = await dbGet('host');
      if (!h || !h.approved) return;
      if (at === null) { if (h.downAt === undefined) return; const { downAt, ...rest } = h; await dbPut('host', rest); }
      else if (h.downAt !== at) await dbPut('host', { ...h, downAt: at });
    }),
  });
  offline.init();
  $('offRescue').addEventListener('click', () => { offline.later(); rescue.openBlock(); });   // P332: the card's 「紧急修复」
  devRename.init({ myDev: api.myIdSync });
  let shotBusy = false;
  // P340 #27: the screenshot goes into the attachments AND the system clipboard. Synchronous up to copyImage(): it must
  // run inside the gesture (the gear's release / Alt+Shift key), with the picture still a promise (Safari's rule).
  const screenshot = () => {
    if (shotBusy || !PAIRED_VIEWS.includes(view)) return;
    shotBusy = true;
    const shot = capturePage();
    const clip = shotcopy.copyImage(shot);
    shot.then(async (file) => {
      settings.close(); show('chat');
      const tray = await relay.attachShot(file, true);
      const r = await clip;
      if (r === 'ok') shotclip.remember();
      shotNotice(file, r, tray);
    }, () => toast(t('p264.shotFail'), 2400)).finally(() => { shotBusy = false; });
  };
  // the notice is the screenshot's preview: its picture, what happened, and the fallbacks (each a fresh tap = a fresh
  // user activation, so 「复制图片」 can succeed where the first try was refused)
  const shotNotice = (file, r, tray) => {
    const plan = shotcopy.shotNoticePlan(r, { added: tray.added, canClip: shotcopy.clipSupport(), canShare: shotcopy.canShareFile(file) });
    const url = URL.createObjectURL(file), img = document.createElement('img');
    img.src = url; img.alt = ''; setTimeout(() => URL.revokeObjectURL(url), 60_000);
    const actions = plan.actions.map((a) => a === 'copy'
      ? { label: t('p340a.copyImg'), fn: (button) => copyButton(file, button, async value => { const x = await shotcopy.copyImage(value); if (x === 'ok') shotclip.remember(); return x === 'ok'; }) }
      : { label: t('p340a.share'), fn: () => shotcopy.shareFile(file).then((x) => { if (x === 'failed') toast(t('p340a.shareFail'), 3000, 'warn'); }) });
    notify({ kind: plan.kind, text: t(plan.key, { n: tray.n, max: tray.max }), media: img, actions, ms: actions.length ? 8000 : 3000 });
  };
  const assist = kind => {
    settings.close(); show('chat');
    relay.prepareSetupDraft(t('p264.' + kind));
  };
  for (const kind of ['friends', 'bots']) $('assist-' + kind).addEventListener('click', () => assist(kind));
  // P387 #98: same sealed-draft and busy guards as automatic refresh; a hold suppresses its tap.
  const guardedRefresh=async()=>{
    const ready=async()=>!relay.refreshBlocked()&&await relay.saveDraft()&&!relay.refreshBlocked();
    if(!await ready()){toast(t('p387.refreshBusy'),4000);return false;}
    return settings.refreshApp({silent:true,beforeReload:ready});
  };
  longPress($('srchBtn'), guardedRefresh); // @ix app.refresh_hold
  initPullRefresh($('main'), {blocked:relay.refreshBlocked,refresh:guardedRefresh}); // @ix app.refresh_pull
  $('srchBtn').addEventListener('click', () => { if (settings.isOpen()) settings.toggleSearch(); });
  document.addEventListener('keydown', e => {
    if (e.isComposing || e.repeat || !PAIRED_VIEWS.includes(view)) return;
    // @ix app.screenshot app.friends app.bots
    if (e.altKey && e.shiftKey && !e.metaKey && !e.ctrlKey && ['KeyY','KeyG','KeyB'].includes(e.code)) {
      e.preventDefault(); e.stopImmediatePropagation();
      if (e.code === 'KeyY') screenshot();
      else { settings.close(); if (e.code === 'KeyG') friends.open('list'); else bots.open(); }
    }
    if (settings.isOpen() && (e.metaKey || e.ctrlKey) && !e.altKey && !e.shiftKey && e.code === 'KeyK') {
      e.preventDefault(); e.stopImmediatePropagation(); settings.toggleSearch();
    }
  }, true);
  settings.configure({                                // F13: the gear, before relay.init so its Esc runs first
    screenshot,
    gearHelp: () => noticeSheet(t('set.btn'), t('p264.gearHelp')),
    agentName: () => displayAgentName(),
    paired: () => !['idle', 'pairing', 'awaiting-approval', 'revoked'].includes(state),
    connected: () => state === 'ready',
    myId: api.myIdSync,
    unpair: unpairAsk,
    reload: () => location.reload(),
    view: () => view,
    forget: forgetHost,                               // P171: this remote unbound itself from 遥控器管理
    assist: (text) => { show('chat'); relay.insertText(text); },   // P274: 「让 Agent J 帮我改」 → a draft, never sent
    upgradeHost: async () => {                        // P203: Settings → 升级电脑上的 Agent J (the same path as ≡ /update)
      if (!session.isReady()) { toast(t('set.offline'), 2400); return; }
      if (await api.slash('update')) { settings.close(); executionToast(t('upg.started'), 2600); }
    },
  });
  $('scan-repair').addEventListener('click', async () => {
    closeMenu();
    if (await confirmSheet(t('menu.unpairTitle'), t('menu.unpairText'), t('menu.unpairYes'), true)) { await forgetHost(); await startScan(); }
  });
  $('repair').addEventListener('click', forgetHost);   // keeps the device key
  $('retry').addEventListener('click', () => {
    const a = $('retry').dataset.action;
    if (a === 'resume') resume(); else if (a === 'reload') location.reload(); else showIdle();
  });
  addEventListener('hashchange', () => {             // a QR opened while this page is already open
    if (takeAddHash()) {                              // a friend's link opened while the page is open
      wantFriends = true;
      if (state === 'ready') openFriendsWanted(); else if (view === 'pair') $('pair-friends').hidden = false;
      return;
    }
    if (takeRvHash()) {                               // P190: a new device's code opened while this page is open
      if (state === 'ready') { const c = pendingRv; pendingRv = null; rvpair.approve(c).catch(() => {}); }
      else if (view === 'pair') $('pair-rv').hidden = false;
      return;
    }
    if (!location.hash.startsWith('#p=')) return;
    const l = location.hash;
    history.replaceState(null, '', location.pathname + location.search);
    startPairing(l);
  });
  // relay's revive: back online or back in the foreground → reconnect at once (no waiting out the backoff)
  addEventListener('online', () => { wakeLink(); session.reconnectNow(); });
  addEventListener('online', ticketWhenIdle);         // P122: a ticket restore that could not reach the origin, again
  window.addEventListener('pageshow', (e) => { wakeLink(); if (e.persisted) session.reconnectNow(); });
  document.addEventListener('resume', () => { wakeLink(); session.reconnectNow(); });   // P393: a frozen page thawed
  navigator.connection?.addEventListener?.('change', () => { if (!document.hidden) { wakeLink(); session.reconnectNow(); } });   // P393: Wi-Fi ↔ mobile data
  document.addEventListener('visibilitychange', () => {
    const fg = document.visibilityState === 'visible';
    if (fg) wakeLink();                                // P393: back in front — reconnect silently for a fresh five seconds
    if (session.isReady()) session.sendApp({ t: 'vis', fg }).catch(() => {});   // the host pushes only while hidden
    if (fg) session.reconnectNow();
    if (fg && session.isReady()) cardsync.sync();   // P203: back in front on a live session — cards sent while frozen
    if (fg && view === 'pair' && state === 'idle') renderPk().catch(() => {});   // a fresh challenge timestamp
    if (fg) ticketWhenIdle();
  });
  onLang(relang);
  onLang(() => paintScanTrigger());   // P207: the scan icon's words follow the language
}

async function main() {
  if (await leaveLegacyHost()) return;
  copycode.init();   // P292: every code / pre on the page can be copied (the static ones in index.html, then all later)
  applyStatic();
  renderName();
  wire();
  checkPhoneEnvironment().catch(() => {});
  if (!globalThis.crypto?.subtle || !globalThis.indexedDB || !globalThis.WebSocket) return fatal('error.missing');
  if ('serviceWorker' in navigator && window.isSecureContext) push.registration().catch(() => {});
  try { await deviceKey(); } catch (e) {
    return fatal(e && e.name === 'NotSupportedError' ? 'error.noCrypto' : 'error.noStore');
  }
  await api.initSign();
  rvpair.configure({ allowRelay, pair: pairFromRemote,
    testRelay: () => (['127.0.0.1', 'localhost'].includes(location.hostname) ? new URLSearchParams(location.search).get('relay') : null) });
  relay.init({
    paired: () => !['idle', 'pairing', 'awaiting-approval', 'revoked'].includes(state),
    pairAdd: settings.pairFromKey,
    screenshot: () => settings.screenshotFromKey(),
    friends: () => { settings.close(); friends.open('list'); },
    bots: () => { settings.close(); bots.open(); },
    setup: () => { settings.close(); setupcard.open(true); },
    menuPages: fivePages.toggleMenu,
    goChat: () => fivePages.go('chat'),
    scan: () => { scanBack = null; startScan('any'); },
    api,
    myDev: api.myIdSync,
    agentName: () => displayAgentName(),
    // P424 (#145): the rescue watchdog answers no `say` — a message sent while it holds the line waits in the offline queue
    // and goes out once Agent J itself is back (the next ready)
    connected: () => state === 'ready' && !session.peer.rescue,
    estopOn: () => controls.estop.on,
    estop: (stop) => (stop ? controls.onEstop() : controls.onResume()),
    canSign: api.canSign,
    openCount: snap.openCount,
    textMax: session.textMax,
    asr: () => session.peer.asr,
    p33: () => session.peer.p33,
    saidOld: (text) => snap.synthSaid(text, api.myIdSync()),
    settings: settings.toggleFromKey,
    openSettings: () => settings.open(),
    openShotSetting: () => settings.openShot(),     // P398 (#156): the 「自动放入 · 设置里可关」 chip
    resetConn: rescue.resetFromCommand,              // P332: ≡ 重置连接 and /reset
    upgradeHost: settings.upgradeFromKey,
    refreshApp: settings.refreshApp,
    models: openModels,
    asrInstall: installAsr,
    bang: bang.take,                                // P414 (§37.12): 「!」 lines go to the web terminal
    termCard: bang.paintCard,
  });
  fivePages.init({ chat: () => controls.openPanel('chat'), memory: () => controls.openPanel('mem'), insert: relay.insertText, send: () => $('send').click() });
  const host = await dbGet('host');
  // 0.15.1: the browser deleted this phone's pairing (Safari private tab closed, site data cleared, …) — say so, not "removed"
  let lost = !host && wasPaired();
  if (host && host.approved && typeof host.dk === 'string' && host.dk !== b64u((await deviceKey()).pub)) {
    await dbDel('host');                              // the pairing survived but its device key did not: it can never resume
    lost = true;
  }
  if (lost) { markPaired(false); $('pair-wiped').hidden = false; }
  await initShare(!!host?.approved && !lost);
  if (pendingLink) {
    const l = pendingLink; pendingLink = null;
    // 0.15.1: a QR link of the computer this phone is already paired with (reopened from history / a bookmark, or the
    // camera again): the link is one-time and probably spent — keep the pairing and resume. A removed phone has no host
    // record any more (revoked()), so scanning a new QR after a removal still pairs.
    let same = false;
    try {
      const q = parseLink(l, 0, allowRelay);
      same = !!host && host.approved && !lost && q.channel === host.channel && b64u(q.hostPub) === b64u(new Uint8Array(host.hostPub));
    } catch { /* a bad link: startPairing says so */ }
    if (!same) return startPairing(l);
    toast(t('pair.already'));
  }
  if (host && host.approved && !lost) { offline.restore(host.downAt); askPersist(); show('chat'); resume(); openFriendsWanted(); return; }
  if (host && !host.approved && ['replaced', 'revoked'].includes(host.removed)) return revoked(host.removed);
  if (await tryTicket()) { openFriendsWanted(); return; }   // P122: no pairing here, but this phone's ticket cookie is
  showIdle();
  if (lost) autoPasskey().catch(() => {});
}

main();

initHeaderLogo();
