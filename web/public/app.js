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
import { t, lang, fillText, plain, applyStatic, onLang } from './js/t.js';
import { el, toast, confirmSheet, closeConfirm } from './js/ui.js';
import { dbGet, dbPut, dbDel, deviceKey, signKey, wipeLocal, markPaired, wasPaired, askPersist } from './js/store.js';
import { b64u, unb64u } from './proto/wire.js';
import * as session from './js/session.js';
import * as snap from './js/snap.js';
import * as api from './js/api.js';
import * as blobs from './js/blobs.js';
import * as media from './js/media.js';       // F21: files the Agent shows (§13)
import * as controls from './js/controls.js';
import * as push from './js/push.js';
import * as relay from './js/relay.js';
import * as settings from './js/settings.js';
import * as elevate from './js/elevate.js';     // F17: sudo / secret cards (§11)
import * as passkey from './js/faceid.js';     // F20: the same phone without a second pairing (§12)

const $ = el;

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
function renderName() {
  $('brand-name').textContent = agentName ?? DEFAULT_NAME;
  $('agent').textContent = agentName ?? DEFAULT_NAME;
  document.title = agentName ?? t('meta.title');
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

// ---------------------------------------------------------------- state / views
let state = 'idle';
Object.defineProperty(window, '__ajState', { get: () => state, enumerable: false, configurable: false });
let statusKey = ['st.idle'];
function setStatus(s, key, vars) {
  state = s;
  statusKey = [key, vars];
  const st = $('status');
  st.dataset.state = s;
  st.textContent = plain(t(key, vars));
  const on = s === 'ready';
  snap.S.conn = on;
  $('meta').hidden = !on;
  relay.setConn(on, st.textContent);
  renderLogo();
  rerender();
}
let view = null;
const VIEWS = { pair: 'pair-view', sas: 'sas-view', mem: 'mem-view', act: 'act-view', tasks: 'tasks-view', revoked: 'revoked-view', error: 'error-view' };
function show(v) {
  view = v;
  document.body.dataset.view = v;
  for (const [k, id] of Object.entries(VIEWS)) $(id).hidden = k !== v;
  $('unpair').hidden = !['chat', 'mem', 'act', 'tasks'].includes(v);
  $('scan-repair').hidden = $('unpair').hidden;
  for (const id of ['open-mem', 'open-act', 'open-tasks']) $(id).hidden = !['chat', 'mem', 'act', 'tasks'].includes(v);
  push.renderA2hs(v);
  renderLogo();
}
// Outside the chat screen the header logo says where pairing stands (the chat screen's logo wears the Agent's state).
const LOGO = { mark: 'brand/img/logo-mark.png', question: 'brand/img/status/logo-question.png', offline: 'brand/img/status/logo-offline.png' };
function renderLogo() {
  if (view === 'chat') { rerender(); return; }
  let v = 'mark';
  if (state === 'pairing' || state === 'awaiting-approval') v = 'question';
  else if (state === 'waiting-host' || state === 'error' || state === 'revoked') v = 'offline';
  const img = $('orb');
  if (img.getAttribute('src') !== LOGO[v]) img.setAttribute('src', LOGO[v]);
  img.dataset.v = v;
}
let renderQueued = false;
function rerender() {
  if (renderQueued) return;
  renderQueued = true;
  queueMicrotask(() => { renderQueued = false; relay.render(snap.snapshot()); });
}

function deviceLabel() {
  const ua = navigator.userAgent;
  const os = /Android/.test(ua) ? 'Android' : /iPhone|iPad|iPod/.test(ua) ? 'iOS' : /Mac OS X/.test(ua) ? 'macOS'
    : /Windows/.test(ua) ? 'Windows' : /Linux|CrOS/.test(ua) ? 'Linux' : '';
  // 0.15.1: the Home Screen app and a Safari tab keep separate storage (= separate remotes): say which one it is
  const br = push.standalone() ? '主屏幕' : /Edg\//.test(ua) ? 'Edge' : /Firefox\/|FxiOS/.test(ua) ? 'Firefox' : /CriOS|Chrome\//.test(ua) ? 'Chrome'
    : /Safari\//.test(ua) ? 'Safari' : '浏览器';   // the device label is data for the host's device list, not UI text
  return ('网页 · ' + [os, br].filter(Boolean).join(' ')).slice(0, 64);
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
  elevate.clear();                                    // F17: the host re-sends open sudo / secret cards after ready
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
  onSas: (code) => { $('sas').textContent = code; show('sas'); setStatus('awaiting-approval', 'st.awaiting'); },
  async onApproved(ctx) {
    // dk = this browser's device public key: a later page that finds the pairing but another device key knows the browser
    // lost the key (0.15.1) instead of failing the resume and calling it "removed"
    const host = { relay: ctx.relay, channel: ctx.channel, hostPub: ctx.hostPub, approved: true, dk: b64u((await deviceKey()).pub) };
    await forgetLocal();                              // a new pairing starts empty: nothing of the previous computer goes to it
    await dbPut('host', host);
    markPaired(true);
    askPersist();
    $('pair-wiped').hidden = true;
    $('pair-error').hidden = true;
    setAgentName(null, false);                        // a new pairing: its name comes with the host's first status
    snap.synthReset(); snap.S.hist = { epoch: 0, first: 0, last: 0, count: 0 }; relay.historyReset(0);
    lastSeq = 0; controls.panel !== 'chat' && controls.openPanel('chat');
    return host;
  },
  onReady() {
    backfillPairing().catch(() => { /* only a hint for later */ });
    onHostGone();
    controls.openPanel(controls.panel);
    setStatus('ready', 'st.ready');
    api.myDeviceId().then(() => rerender());
    relay.onReady();
    blobs.onReconnect([]);
  },
  onApp,
  onUiError: (e, t) => { document.body.dataset.uiError = `${t}: ${e && e.message}`; },
  onHostDown: onHostGone,
  onRevoked: revoked,
  onPairFailed: pairFailed,
  onRestoreFailed: pkFailed,
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
controls.configure({ show, estopChanged: () => { relay.onEstop(); rerender(); } });

function onApp(m) {
  if (api.route(m) || blobs.handle(m) || media.handle(m)) return;
  switch (m.t) {
    case 'preferences': relay.applyPreferences(m.value); settings.setPrefs(m.value, m.host); if(m.problem)relay.configProblem(m.problem.error); return;
    case 'status': setAgentName(m.name); snap.setStatus(m); return;
    case 'ask': snap.addAsk(m); return;
    case 'ask_done': snap.askDone(m); return;
    case 'elev': elevate.add(m); return;             // F17 (§11)
    case 'elev_done': elevate.done(m); return;
    case 'elev_refused': elevate.refused(m); return;   // ADR-A163: Face ID not accepted, the card stays open
    case 'pk_offer': offerPasskey(m).catch(() => {}); return;    // F20 (§12)
    case 'pk_reg_res': pkSaved(m).catch(() => {}); return;
    case 'question': snap.addQuestion(m); return;
    case 'question_done': snap.questionDone(m); return;
    case 'auto': controls.showAuto(m); return;
    case 'grant': controls.setGrant(m); return;
    case 'grant_end': controls.endGrant(m.id, m.why); return;
    case 'push_key': push.gotPushKey(m.k); return;
    case 'estop_state': controls.setEstop(m); snap.S.estop = { ...controls.estop }; return;
    case 'hist_meta': snap.setMeta(m); return;
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
  markPaired(false);
  const replaced = why === 'replaced';
  setStatus('revoked', replaced ? 'st.replaced' : 'st.revoked');
  $('revoked-title').dataset.i18n = replaced ? 'revoked.replacedTitle' : 'revoked.title';
  $('revoked-lead').dataset.i18n = replaced ? 'revoked.replacedLead' : 'revoked.lead';
  applyStatic($('revoked-view'));
  show('revoked');
  $('revoked-draft').hidden = !hadDraft;
}
/** Unpair / re-pair / revoke / a new pairing (§10.14, P33-X02 / X13). In-memory first, in the same tick (no await before it):
 *  uploads, queued and in-flight sends, the tray, the quote, the field and ↑↓ history; then the sealed records and their key. */
async function forgetLocal() {
  relay.forgetLocal();
  blobs.forgetAll();
  elevate.clear();
  await wipeLocal();
}
function fatal(key) {
  session.closeSession();
  setStatus('error', 'st.unusable');
  showError(key, 'error.retry', 'reload');
}
async function resume() {
  const host = await dbGet('host');
  if (!host || !host.approved) return showIdle();
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
    err.textContent = t('pair.badLink');
    err.hidden = false;
    return;
  }
  $('pair-link').value = '';                          // the link carries the one-time PSK
  $('pair-error').hidden = true;
  show('pair');
  session.openSession('pair', p);
}
function showIdle() {
  session.closeSession();
  setAgentName(null, false);
  show('pair');
  setStatus('idle', 'st.idle');
  renderPk().catch(() => { $('pk-restore').hidden = true; });
}

// ---------------------------------------------------------------- F20 passkey (PROTOCOL §12): no second pairing
// Register: once, after a pairing (the host sends pk_offer only then), one sheet; 「以后再说」 changes nothing and the
// question never comes back for this pairing (host record `pkAsked`). The system Face ID sheet must follow the tap, so
// nothing slow runs between 「保存」 and navigator.credentials.create.
async function offerPasskey(m) {
  const h = await dbGet('host');
  if (!h || !h.approved || h.pkAsked || h.channel !== session.channel()) return;
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
  try { reg = await passkey.register(nonce, { relayIndex: ri, channel: h.channel, hostPub: h.hostPub, name, displaySuffix: t('pk.computer') }); } catch { return; }   // cancelled / no Face ID: nothing changes
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
async function pkRestore() {
  let prep = pkPrep;
  if (!prep || Date.now() - prep.ts > 4 * 60 * 1000) prep = await passkey.prepareRestore((await deviceKey()).pub, (await signKey()).pub);
  pkPrep = null;
  let a;
  try { a = await passkey.assert(prep.challenge); } catch (e) {                  // cancelled / no passkey here / not focused
    document.body.dataset.pkErr = (e && e.name) || 'error';                       // diagnostics only (tests read it)
    return renderPk();
  }
  let to;
  try {
    to = passkey.decodeHandle(a.uh, { testRelay: new URLSearchParams(location.search).get('relay') });
    if (!allowRelay(to.relay)) throw new Error('relay');
  } catch { return pkFailed('bad'); }
  pkCred = a.id;
  $('pk-restore').hidden = true;
  $('pair-error').hidden = true;
  session.openSession('restore', { relay: to.relay, channel: to.channel, hostPub: to.hostPub,
    pk: { id: a.id, cd: a.cd, ad: a.ad, sig: a.sig, uh: b64u(a.uh), ts: prep.ts, nonce: b64u(prep.nonce) } });
}
function pkFailed(why) {
  const dead = why === 'unknown' || why === 'revoked';
  if (dead && pkCred) passkey.forgetDead(pkCred);
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
  $('pair-wiped').hidden = true;
  snap.synthReset(); snap.S.hist = { epoch: 0, first: 0, last: 0, count: 0 }; relay.historyReset(0);
  $('input').value = '';
  showIdle();
}

// In-app scanner keeps the pairing keys in this standalone app's own storage.
let scanStream = null;
let scanGeneration = 0;
async function startScan() {
  stopScan();
  const generation = scanGeneration;
  const hint = $('scan-hint');
  hint.hidden = true;
  let det = null;
  try {
    if (window.BarcodeDetector && (await window.BarcodeDetector.getSupportedFormats()).includes('qr_code'))
      det = new window.BarcodeDetector({ formats: ['qr_code'] });
  } catch { /* bundled decoder below */ }
  if (!navigator.mediaDevices?.getUserMedia || (!det && !window.jsQR)) {
    fillText(hint, t('pair.scanHint')); hint.hidden = false; return;
  }
  let stream;
  try {
    stream = await navigator.mediaDevices.getUserMedia({ video: { facingMode: { ideal: 'environment' } }, audio: false });
  } catch {
    fillText(hint, t('pair.scanNoCam')); hint.hidden = false; return;
  }
  if (generation !== scanGeneration) { stream.getTracks().forEach(tr => tr.stop()); return; }
  scanStream = stream;
  const video = $('scan-video');
  video.srcObject = stream;
  $('scan-box').hidden = false;
  try { await video.play(); } catch { stopScan(); fillText(hint, t('pair.scanNoCam')); hint.hidden = false; return; }
  const canvas = document.createElement('canvas');
  const ctx = canvas.getContext('2d', { willReadFrequently: true });
  const tick = async () => {
    if (scanStream !== stream) return;
    let link;
    try {
      if (det) {
        const hits = await det.detect(video);
        link = hits.find(c => typeof c.rawValue === 'string' && c.rawValue.includes('#p='))?.rawValue;
      } else if (video.readyState >= 2 && video.videoWidth) {
        const scale = Math.min(1, 800 / video.videoWidth);
        canvas.width = Math.round(video.videoWidth * scale); canvas.height = Math.round(video.videoHeight * scale);
        ctx.drawImage(video, 0, 0, canvas.width, canvas.height);
        const frame = ctx.getImageData(0, 0, canvas.width, canvas.height);
        link = window.jsQR(frame.data, frame.width, frame.height)?.data;
      }
      if (scanStream !== stream) return;
      if (link) { parseLink(link); stopScan(); await startPairing(link); return; }
    } catch { /* an unreadable frame or unrelated QR: keep scanning */ }
    if (scanStream === stream) setTimeout(tick, 250);
  };
  tick();
}
function stopScan() {
  ++scanGeneration;
  if (scanStream) for (const tr of scanStream.getTracks()) tr.stop();
  scanStream = null;
  $('scan-video').srcObject = null;
  $('scan-box').hidden = true;
}
document.addEventListener('visibilitychange', () => { if (document.hidden) stopScan(); });
window.addEventListener('pagehide', stopScan);
function fitPairViewport() {
  const vv = window.visualViewport;
  if (vv) {
    document.documentElement.style.setProperty('--vvh', vv.height + 'px');
    document.documentElement.style.setProperty('--vvtop', vv.offsetTop + 'px');
  }
  document.body.dataset.kb = vv && window.innerHeight - vv.height > 120 ? '1' : '0';
  if (document.activeElement === $('pair-link')) $('pair-controls').scrollIntoView({ block: 'start' });
}
window.visualViewport?.addEventListener('resize', fitPairViewport);
window.visualViewport?.addEventListener('scroll', fitPairViewport);
window.addEventListener('resize', fitPairViewport);
document.addEventListener('focusin', () => { fitPairViewport(); setTimeout(fitPairViewport, 300); });
document.addEventListener('focusout', () => setTimeout(fitPairViewport, 300));
fitPairViewport();

// ---------------------------------------------------------------- language switch: re-render everything that is ours
function relang() {
  applyStatic();
  renderName();
  setStatus(state, statusKey[0], statusKey[1]);
  push.pushUi();
  controls.renderGrants();
  controls.renderEstop();
  renderError();
  push.renderA2hs(view);
  if (!$('scan-hint').hidden) fillText($('scan-hint'), t('pair.scanHint'));
  if (!$('pair-error').hidden) $('pair-error').textContent = t($('pair-error').dataset.k || 'pair.badLink');
  controls.reloadPanel();
  push.reregister();
}

// ---------------------------------------------------------------- wiring
function closeMenu() { $('aj-menu').open = false; }
function toggleBadge(open) {
  const p = $('badge-panel');
  p.hidden = !(open ?? p.hidden);
  if (p.hidden) delete p.dataset.modalOpen; else p.dataset.modalOpen = '1';
  $('badge').setAttribute('aria-expanded', String(!p.hidden));
  if (!p.hidden) $('badge-close').focus();
}
function wire() {
  $('version-hash').textContent = VERSION.combined;
  $('badge').addEventListener('click', () => toggleBadge());
  $('badge-close').addEventListener('click', () => toggleBadge(false));
  $('badge-panel').addEventListener('click', (e) => { if (e.target === $('badge-panel')) toggleBadge(false); });
  $('menu-about').addEventListener('click', () => { closeMenu(); toggleBadge(true); });
  document.addEventListener('click', (e) => { if ($('aj-menu').open && !e.target.closest('#aj-menu')) closeMenu(); });
  document.addEventListener('keydown', (e) => {
    if (e.key !== 'Escape') return;
    if ($('aj-menu').open) { closeMenu(); $('aj-menu').querySelector('summary').focus(); return; }
    if (!$('badge-panel').hidden) { toggleBadge(false); return; }
    closeConfirm();
  });
  for (const a of $('aj-menu').querySelectorAll('a')) a.addEventListener('click', closeMenu);
  $('a2hs-ok').addEventListener('click', () => push.dismissA2hs(view));
  $('scan').hidden = false;
  $('pair-link').addEventListener('focus', () => { stopScan(); fitPairViewport(); setTimeout(fitPairViewport, 300); });
  $('pair-go').addEventListener('click', () => startPairing($('pair-link').value));
  $('scan').addEventListener('click', () => { startScan(); });
  $('pk-restore').addEventListener('click', () => { stopScan(); pkRestore().catch(() => pkFailed('bad')); });
  $('scan-stop').addEventListener('click', stopScan);
  $('push-on').addEventListener('click', push.enablePush);
  $('grant-off').addEventListener('click', controls.revokeGrants);
  $('open-mem').addEventListener('click', () => { closeMenu(); controls.openPanel('mem'); });
  $('open-act').addEventListener('click', () => { closeMenu(); controls.openPanel('act'); });
  $('open-tasks').addEventListener('click', () => { closeMenu(); controls.openPanel('tasks'); });
  for (const b of document.querySelectorAll('[data-back]')) b.addEventListener('click', () => controls.openPanel('chat'));
  $('act-more').addEventListener('click', () => controls.loadActivity(false));
  $('resume').addEventListener('click', controls.onResume);
  const unpairAsk = async () => {
    closeMenu();
    if (await confirmSheet(t('menu.unpairTitle'), t('menu.unpairText'), t('menu.unpairYes'))) forgetHost();
  };
  $('unpair').addEventListener('click', unpairAsk);
  settings.configure({                                // F13: the gear, before relay.init so its Esc runs first
    agentName: () => agentName ?? DEFAULT_NAME,
    paired: () => !['idle', 'pairing', 'awaiting-approval', 'revoked'].includes(state),
    connected: () => state === 'ready',
    myId: api.myIdSync,
    unpair: unpairAsk,
    reload: () => location.reload(),
  });
  $('scan-repair').addEventListener('click', async () => {
    closeMenu();
    if (await confirmSheet(t('menu.unpairTitle'), t('menu.unpairText'), t('menu.unpairYes'))) { await forgetHost(); await startScan(); }
  });
  $('repair').addEventListener('click', forgetHost);   // keeps the device key
  $('retry').addEventListener('click', () => {
    const a = $('retry').dataset.action;
    if (a === 'resume') resume(); else if (a === 'reload') location.reload(); else showIdle();
  });
  addEventListener('hashchange', () => {             // a QR opened while this page is already open
    if (!location.hash.startsWith('#p=')) return;
    const l = location.hash;
    history.replaceState(null, '', location.pathname + location.search);
    startPairing(l);
  });
  // relay's revive: back online or back in the foreground → reconnect at once (no waiting out the backoff)
  addEventListener('online', session.reconnectNow);
  window.addEventListener('pageshow', (e) => { if (e.persisted) session.reconnectNow(); });
  document.addEventListener('visibilitychange', () => {
    const fg = document.visibilityState === 'visible';
    if (session.isReady()) session.sendApp({ t: 'vis', fg }).catch(() => {});   // the host pushes only while hidden
    if (fg) session.reconnectNow();
    if (fg && view === 'pair' && state === 'idle') renderPk().catch(() => {});   // a fresh challenge timestamp
  });
  onLang(relang);
}

async function main() {
  if (await leaveLegacyHost()) return;
  applyStatic();
  renderName();
  wire();
  if (!globalThis.crypto?.subtle || !globalThis.indexedDB || !globalThis.WebSocket) return fatal('error.missing');
  if (push.pushSupported()) push.registration().catch(() => {});
  try { await deviceKey(); } catch (e) {
    return fatal(e && e.name === 'NotSupportedError' ? 'error.noCrypto' : 'error.noStore');
  }
  await api.initSign();
  relay.init({
    api,
    myDev: api.myIdSync,
    agentName: () => agentName ?? DEFAULT_NAME,
    connected: () => state === 'ready',
    estopOn: () => controls.estop.on,
    estop: (stop) => (stop ? controls.onEstop() : controls.onResume()),
    canSign: api.canSign,
    openCount: snap.openCount,
    textMax: session.textMax,
    asr: () => session.peer.asr,
    p33: () => session.peer.p33,
    saidOld: (text) => snap.synthSaid(text, api.myIdSync()),
    settings: settings.toggleFromKey,
  });
  const host = await dbGet('host');
  // 0.15.1: the browser deleted this phone's pairing (Safari private tab closed, site data cleared, …) — say so, not "removed"
  let lost = !host && wasPaired();
  if (host && host.approved && typeof host.dk === 'string' && host.dk !== b64u((await deviceKey()).pub)) {
    await dbDel('host');                              // the pairing survived but its device key did not: it can never resume
    lost = true;
  }
  if (lost) { markPaired(false); $('pair-wiped').hidden = false; }
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
  if (host && host.approved && !lost) { askPersist(); show('chat'); return resume(); }
  if (host && !host.approved && ['replaced', 'revoked'].includes(host.removed)) return revoked(host.removed);
  showIdle();
}

main();
