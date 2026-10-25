import { paintReportActions } from './report-actions.js';
import {reopenSession} from './rescue.js';
import { QueueWatch } from './queue-watch.js';
import { paintIOSChrome } from './ios-chrome.js';
import { createMicrophone } from './microphone.js';
import { copyButton } from './copycode.js';
import {phoneConfirmation} from './tvremote.js';
import {focusDraft, releaseDraft, touchComposer} from './composer-focus.js';
import {pasteRoute, CLIP_TEXT_MAX} from './paste-route.js';
import { termKey, backslashEnter, killed, forget as forgetKill } from './termkeys.js';   // P412 (#174)
import * as fsed from './fsedit.js';   // P412 (#174): the full-screen editor
import { bindReaderSwipe } from './reader-swipe.js';
import { DOUBLE_MS as RD_DTAP_MS, TAP_MS as RD_TAP_MS, TAP_PX as RD_TAP_PX, PAIR_PX as RD_PAIR_PX } from './file-gestures.js';
import { linkify, cardKeys } from './five-pages.js';
import {onChange as onCardsChange, refresh as refreshCards} from './jcards.js';
import { capAbove, sheetCeil, wireCeil } from './fit.js';
import { registerLayer, activeLayer } from './layers.js';
import { applyViewport, watchViewport } from './viewport.js';
import { historyDoubleTap, longPress as cornerLongPress } from './history-corners.js';   // P179 (P165: renamed — P181's longpress.js owns longPress)
import { open as openRename } from './dev-rename.js';
import { deviceInfo, refreshApp, upgradeFromKey } from './settings.js';
import { SharedNotice } from './shared-notice.js';
import { quotaWindows } from './dashboard.js';
import { openUsage, initUsage } from './usagedlg.js';   // P207 §1: Weekly / 5H → the centred dark dialog
import { cycle, tier } from './quotapace.js';   // P303: day of the reset cycle + the faint pace / amount glow
// The chat screen = relay's phone page (pwa/index.html <script>, ADR-022 … ADR-052 there), ported section by section.
// What changed is only the transport and the wording: relay's HTTP calls (requests, the event stream, raw uploads) go through the
// adapter `ctx.api` (Noise session, PROTOCOL §10), every string comes from the i18n dictionaries, Telegram forwarding is
// the phone's share sheet, the cloud voice is the phone's own speechSynthesis, drafts and input history are sealed in
// IndexedDB. The sections keep relay's order and `// ----` markers; they share ~40 bindings (cur, hist, atts, ptt, …),
// which is why they stay one module (the leaf parts live in md.js, blobs.js, wav.js, speak.js, ui.js; padding, frag and signatures come from proto/wire.js).
import { RelayMD } from './md.js';
import { copyable } from './copycode.js';   // P292: every code / pre can be copied
import { upgrade as upgradeWords, forget as forgetRendered } from './render.js';   // P57: lazy code / math / diagrams
import { cmdGist } from './cmdgist.js';   // P341: the approval card's plain line when the Agent gave none
import { t, lang, onLang, fillText, both } from './t.js';
import { el, toast, executionToast, toastOff, toastAction, human, stamp, mmss, confirmSheet } from './ui.js';
import * as sfx from './sfx.js';
import * as sendvol from './sendvol.js';   // P154 追加 12: hold the grey Send → the send-sound slider
import * as shotclip from './shotclip.js';
import * as attkeys from './attkeys.js';   // P398 (#156): the same content twice in the tray → once
import { Upload, TYPES as DROP_TYPES, MAX_BYTES as UPLOAD_MAX, MAX_ATT, ASR_MAX_BYTES, anyMime } from './blobs.js';
import { renderPage as renderMedia, forgetAll as forgetMedia, renderReader as renderReaderMedia, clearReader as clearReaderMedia, closeViewer as closeMediaViewer, viewerOpen as mediaViewerOpen } from './media.js';   // F21: files the reply shows (§13); P59: in the reader too
import { toWav, GAP_MIN_MS, GAP_SKIP_MS } from './wav.js';
import * as LR from './longrec.js';   // P182: long takes kept on the phone until the computer has them
import { Speaker, configureSpeech } from './speak.js';
import { isReady, sendApp, hostId, peer } from './session.js';
import { getSealed, putSealed, dbDel } from './store.js';
import { Outbox, paint as paintOutbox, MAX as OUT_MAX, isEcho } from './outbox.js';
import { onTheme } from './theme.js';   // P169 (§23)
import { paintHeader } from './logo.js';   // P263: the five-state header logo
import { waitRobot } from './busy.js';   // P281: every wait line starts with the theme's dancing robot
import { longPress } from './longpress.js';   // P181: long-press shortcuts on the composer buttons
import { initHot, setHot, openHot, kindOf as hotKind, hotName } from './hotkey.js';   // P285: the reply card's bottom-left hot key
import * as search from './search.js';   // P188 (§28): 搜索聊天记录
import { lookup as ixLookup, ROWS as IX_ROWS, GROUPS as IX_GROUPS, dispatchFunctional, functionalLookup, kbdTokens } from './interactions.gen.js';   // P207: keys from interactions.json
import { createUnread } from './unread.js';   // P207 (追加 2): this device's unread mark
import { open as openAgentName } from './agentname.js';
import { askKind as friendAsk, renderAsk as renderFriendAsk, addFriendCmd, openFromCmd } from './friends.js';   // §17.7: friend request / friend's question cards
import { wireComponents } from './components.js';
import * as replyBtn from './replybtn.js';   // P368 (#78): [派](reply:派) one-tap answers + #tags in the reply

let C = null;                        // ctx from app.js: api, myDev(), estopOn(), canSign(), panels …
const VERBS = ['Bash', 'Write', 'Edit', 'MultiEdit', 'NotebookEdit', 'Read', 'WebFetch', 'WebSearch', 'Delete', 'ExternalDirectory', 'CodexPermissions'];
const verb = (tool) => (VERBS.includes(tool) ? t('r.verb.' + tool) : t('r.verb.other', { tool }));
let lastStatus = null;
let cur = null;          // last rendered snapshot
const skew = 0;          // host clock = phone clock for display (turn ts come from the host, ms)
let lastSync = null;     // phone-local time of the last frame we actually received
let input = null, mainEl = null, deck = null, tray = null, menu = null, menuList = null, sug = null, mic = null;

// ---- the Agent's words (Markdown, DOM nodes only) -------------------------------------
let wordsSrc = null, cardReplyKey = null;
let historySync = true;   // a refreshed page must fetch the current head before claiming delivery is still pending
let historyGeneration = 0;
// P135: an open page the computer stays quiet about (idle / stopped / down for NO_ECHO_MS) says so and offers a refresh;
// stopped says so at once. The timer redraws at the mark; leaving the page or a new state clears it.
const NO_ECHO_MS = 30000, QUIET = ["idle", "stopped", "down"];
const NO_ECHO_SRC = "\u0000pending:r.replyNoEcho";
let quiet = null;        // {k: page|hostStatus, at, timer}
function waitKey(s, syncing, quietMs){
  if (syncing) return "st.resuming";
  if (s?.compacting === true) return "r.replyCompacting";
  if (s?.status === "working") return "r.replyWorking";
  if (s?.hostStatus === "stopped") return "r.replyStopped";
  if (QUIET.includes(s?.hostStatus) && quietMs >= NO_ECHO_MS) return "r.replyNoEcho";
  return "r.replyPending";
}
// P205: the working line speaks to the owner — 「董事长，您的最新指示已经收到…」 / "Chairman, your latest instruction is in…".
// The address is the host's `owner.address` preference (≤ 16 characters; "" = 董事长 / Chairman). Text only: control
// characters go, whitespace collapses, and every wait line is drawn as plain text nodes (never Markdown / HTML).
const ADDRESS_MAX = 16;
let ownerAddr = "";
function cleanAddress(v){
  if (typeof v !== "string") return "";
  return Array.from(v.replace(/[\u0000-\u001f\u007f-\u009f\u2028\u2029]/g, " ").replace(/\s+/g, " ").trim()).slice(0, ADDRESS_MAX).join("").trim();
}
function waitText(key){ return t(key, {address: ownerAddr || t("r.ownerAddress")}); }
function noteQuiet(k){
  if (k === (quiet && quiet.k)) return;
  if (quiet) clearTimeout(quiet.timer);
  quiet = k ? {k, at: Date.now(), timer: setTimeout(() => { wordsSrc = null; showPage(); }, NO_ECHO_MS)} : null;
}
const STREAM_MS = 150;   // P193 (B10): an open page's growing reply is redrawn at most this often
let wordsAt = 0, wordsTimer = null;
// pending: an open page whose reply has not come yet — no words at all (the thinking dots say it), never the
// empty-history line 「已连上你的Agent J 主机…」 (relay never showed a page before its reply; Agent J shows the say's page at once)
// P281: wait words that are not a wait — 「已停下」 is over, 「Agent J 主机没有回音」 needs the owner — get no robot
const NOT_WAITING = ["r.replyStopped", "r.replyNoEcho"];
let themeGen = 0;          // P281: a new theme redraws the wait robot (its sheet) on the page shown
function renderWords(text, pending = false, label = "", waitLine = false, sys = false, dataOnly = false){
  const tx = (text || "").trim();
  // P127: a compaction in flight (any mode: PreCompact on a shared Claude, system/status on our own) says so at once;
  // a page-specific label (queued behind a busy session) wins over the session-wide wait words
  noteQuiet(pending && !tx && !label && QUIET.includes(cur?.hostStatus) ? shownKey + "|" + cur.hostStatus : null);
  const wait = waitKey(cur, historySync, quiet ? Date.now() - quiet.at : 0);
  // P281: the page's words are a wait line (the host's own wait / queue / switch lines included) → the robot leads them
  const robot = pending && !tx ? !!label || !NOT_WAITING.includes(wait) : !!waitLine && !!tx;
  const key = (dataOnly ? "\u0000data:" : "") + (sys ? "\u0000sys:" : "") + (pending && !tx ? "\u0000pending:" + (label || wait) + (!label && wait === "r.replyWorking" && ownerAddr ? "|" + ownerAddr : "") : tx)
    + (robot ? "\u0000robot:" + themeGen : "");
  // P519: refresh once for each settled Agent reply, so host-side card installs/switches need no menu visit.
  const cardKey = shownKey + "\u0000" + tx;
  if (tx && !pending && !sys && !dataOnly && cardReplyKey !== cardKey){ cardReplyKey = cardKey; refreshCards(); }
  if (key === wordsSrc) return;
  // P193 (B10): a streaming reply grows chunk by chunk and every redraw re-parses the whole Markdown (O(n²) over a long
  // answer). While the open page only grows, redraw at most every STREAM_MS; the trailing timer draws the latest text.
  const now = Date.now();
  if (pending && tx && wordsSrc && !wordsSrc.startsWith("\u0000") && tx.startsWith(wordsSrc) && now - wordsAt < STREAM_MS){
    if (!wordsTimer) wordsTimer = setTimeout(() => { wordsTimer = null; showPage(); }, STREAM_MS - (now - wordsAt));
    return;
  }
  if (wordsTimer){ clearTimeout(wordsTimer); wordsTimer = null; }
  wordsAt = now;
  wordsSrc = key;
  const w = el("words");
  w.className = "words" + (!tx ? " empty" : tx.length > 600 ? " l" : tx.length > 240 ? " m" : "");
  fill(w, pending && !tx ? (label || waitText(wait)) : tx, dataOnly || pending && !tx, sys);
  afterWords(robot);
  rdFollow(tx, sys, dataOnly);
}
// P341 (#19): a host-written system page (k "sys": goal results, notices) quotes commands — "$(…)" / "$HOME" — so it
// renders Markdown without TeX; the Agent's own replies keep math.
// P365: an automation's message (k "auto", `agentj notify`) is the owner's own script text — prices and $VARS, no TeX either
const sysPage = (p) => !!(p && p.source && (p.source.k === "sys" || p.source.k === "auto"));
function fill(w, tx, plain = false, sys = false){
  if (tx && !plain){
    try{
      const detail = tx.match(/^([\s\S]*)\n\n(\u8be6\u60c5|Details):\n```text\n([\s\S]*)\n```$/);
      tx = detail ? detail[1] : tx;
      w.replaceChildren(RelayMD.toDOM(RelayMD.parse(tx, {math: !sys}), document)); upgradeWords(w); linkify(w, false, !sys);
      if (detail){
        const d = document.createElement("details"), label = document.createElement("summary"), raw = document.createElement("pre");
        label.textContent = detail[2]; raw.textContent = detail[3]; d.append(label, copyable(raw, {click: false})); w.appendChild(d);
      }
      return;
    }catch(_){}
  }
  w.replaceChildren();
  const paras = tx ? tx.split(/\n\s*\n/) : [emptyText()];
  for (const p of paras){ const n = document.createElement("p"); n.textContent = p.trim(); w.appendChild(n); }
}
const emptyText = () => (C && C.connected() ? t('r.emptyReady') : t('r.empty'));
/** P281: the robot goes in front of the first words (inside the first paragraph, so it sits on the text's line). */
function leadRobot(w){
  const first = w.firstElementChild;
  if (first && first.tagName === "P") first.prepend(waitRobot());
  else w.prepend(waitRobot());
}
/** P281: the line under a reply still being written — the robot alone, or with what a /switch-harness is doing right now
    (card.progress, host text). Shown while the Agent works (CSS) or while the open page has a progress line. */
let thinkingSrc = null;
function paintThinking(progress){
  const key = (progress || "") + "\u0000" + themeGen;
  document.body.dataset.pageProgress = progress ? "1" : "0";
  if (key === thinkingSrc) return;
  thinkingSrc = key;
  const box = el("thinking");
  if (!box) return;
  box.replaceChildren(waitRobot());
  if (progress){ const s = document.createElement("span"); s.textContent = progress; box.appendChild(s); }
}
onCardsChange(() => { wordsSrc = null; showPage(); if(rdAt){const p=currentPage();if(p){fill(el("rdWords"),rdAt.text,p.source?.k === "friend_report",sysPage(p));renderReaderMedia(el("rdWords"),p);}} });
onTheme(() => { themeGen++; wordsSrc = null; thinkingSrc = null; showPage(); });

// ---- the reading area scrolls; the page does not ---------------------------------
const STICK_PX = 24, NEW_AT = "start";
let stick = true;
function atBottom(){ return mainEl.scrollHeight - mainEl.scrollTop - mainEl.clientHeight <= STICK_PX; }
function afterWords(robot = false){
  document.body.dataset.waitLine = robot ? "1" : "0";     // P281: the words lead with the robot → no second one under them
  if (robot) leadRobot(el("words"));
  if (stick) mainEl.scrollTop = NEW_AT === "end" ? mainEl.scrollHeight : 0;
  paintMore();
}
function paintMore(){
  el("rm").classList.toggle("more", mainEl.scrollHeight - mainEl.scrollTop - mainEl.clientHeight > 1);
  el("rm").classList.toggle("under", mainEl.scrollTop > 1);   // P181 追加 2: words under the pinned header → its soft edge
}
function copyCodeClick(e){
  // P154 追加: inline `code` — one click (a tap on a phone) copies it; no icon, the shade flashes and a ≤ 1 s note.
  // P292: that click now lives in copycode.js (data-cc="bare", set by md.js; a press that selected text is not a click to
  // copy, a code inside a link stays a link); it bubbles `cc:copied` and the note is said here (copiedNote). A click on a
  // code block's code presses its 拷贝 below (data-cc="ext").
  const b = e.target.closest && e.target.closest(".copy");
  if (!b) return;
  const code = b.closest(".codeblock").querySelector("pre > code");
  copyButton(code ? code.textContent : "", b);
}

function copiedNote(e){
  // copycode owns both the in-place and centred feedback, including bare inline code.
}

// "已等 N 分钟" from the request's arrival on this page.
function renderSub(){
  let sub = "";
  const card = cur && cur.card;
  if (document.body.dataset.conn === "on" && cur && cur.status === "waiting" && card && card.received_at){
    const mins = Math.floor((Date.now() / 1000 - skew - card.received_at) / 60);
    if (mins >= 1) sub = t('r.waited', {n: mins});
  }
  el("capSub").textContent = sub;
}

function summarize(sum){
  if (sum == null) return {main:"", why:""};
  if (typeof sum === "string") return {main:sum, why:""};
  const main = sum.command || sum.file_path || sum.notebook_path || sum.url || sum.query || sum.pattern || JSON.stringify(sum, null, 2);
  return {main:String(main), why: typeof sum.description === "string" ? sum.description : ""};
}

// Which card was folded away. A different card opens again on its own; the same one stays folded until the tag is tapped.
let foldedCard = null;
const cardKey = c => c ? [c.kind, c.id, c.received_at].join("|") : null;

// ---- questions answered from the phone (§10.7; relay M2-ask) -------------------------
// The phone sends option NUMBERS, signed; the labels never come from here. After 确认 the sheet says 「已发送，等待生效」
// until the host's question_done.
const ASK_FINAL = ["answered", "cancelled", "terminal", "timeout", "expired", "ended", "stopped"];   // P203: expired = timed out while away
const ASK_RESULT_S = 6;
let askSel = {id: null, picks: []};
let askSent = {id: null, state: null};
let askBusy = false, askArmUntil = 0, askToasted = null, askResultTimer = null, qsKey = null, askDismissed = null;

function askState(a){
  if (!a) return null;
  return a.state === "open" && askSent.id === a.id ? askSent.state : a.state;
}
function askLabels(a){
  return (a.choices || []).map((pick, i) => typeof pick === "string" ? pick : pick.map(n => {
    const o = ((a.questions[i] || {}).options || [])[n - 1]; return o ? o.label : String(n);
  }).join(t('r.list.sep'))).join(t('r.list.sep2'));
}
function askText(a){
  const st = askState(a);
  if (st === "answered") return t('r.q.st.answered', {labels: askLabels(a)});
  return ["sent", "cancel_sent", "cancelled", "terminal", "timeout", "expired", "ended", "detached", "stopped"].includes(st) ? t('r.q.st.' + st) : "";
}
function askComplete(a){
  return askSel.id === a.id && a.questions.every((_, i) => (askSel.picks[i] || []).length > 0);
}

// P368 (#79): the words under an option show 2 lines; when they are longer, a 「…展开」 button under the option (a sibling —
// a tap on it never picks the option) opens / folds them. Shown only when the clamp actually cut something.
function qMore(d){
  const m = document.createElement("button");
  m.type = "button"; m.className = "qmore"; m.hidden = true; m.textContent = t('p368.qMore');
  m.addEventListener("click", (e) => { e.stopPropagation(); const on = d.classList.toggle("open"); m.textContent = t(on ? 'p368.qLess' : 'p368.qMore'); m.setAttribute("aria-expanded", on ? "true" : "false"); });
  return m;
}
function fitQMore(box, tries = 0){
  let unseen = false;
  for (const m of box.querySelectorAll(".qmore")){
    const d = m.previousElementSibling && m.previousElementSibling.querySelector(".d");
    if (!d || d.classList.contains("open")) continue;
    if (!d.clientHeight){ unseen = true; continue; }
    m.hidden = d.scrollHeight <= d.clientHeight + 1;
  }
  if (unseen && tries < 20) requestAnimationFrame(() => fitQMore(box, tries + 1));   // the sheet is still sliding in
}
function renderQuestion(card, a){
  const qs = a ? a.questions : [];
  const st = askState(a);
  const open = st === "open";
  const live = open && !askBusy && C.canSign();
  el("sheetTitle").textContent = qs.length > 1 ? t('r.q.titleN', {n: qs.length}) : ((qs[0] && qs[0].question) || t('r.q.title1'));
  const key = (a ? a.id : cardKey(card)) + "|" + st + "|" + askBusy + "|" + lang();
  const box = el("qs");
  if (key !== qsKey){
    qsKey = key;
    box.replaceChildren();
    qs.forEach((item, qi) => {
      if (qs.length > 1 || item.header){
        const h = document.createElement("h3");
        h.textContent = qs.length > 1 ? item.question : item.header;
        if (qs.length > 1 && item.header){ const s = document.createElement("span"); s.className = "qhdr"; s.textContent = item.header; h.prepend(s); }
        box.appendChild(h);
      }
      const ol = document.createElement("ol");
      (item.options || []).forEach((o, k) => {
        const li = document.createElement("li");
        li.className = "optli";
        const b = document.createElement("button");
        b.type = "button"; b.className = "opt" + (item.multi ? " multi" : "");
        b.dataset.q = String(qi); b.dataset.n = String(k + 1);
        const n = document.createElement("span"); n.className = "n"; n.textContent = item.multi ? "" : String(k + 1);
        const tt = document.createElement("span"); tt.className = "t";
        const l = document.createElement("span"); l.textContent = o.label; tt.appendChild(l);
        let d = null;
        if (o.description){ d = document.createElement("span"); d.className = "d"; d.textContent = o.description; tt.appendChild(d); }
        b.append(n, tt); b.disabled = !live;
        li.appendChild(b);
        if (d) li.appendChild(qMore(d));   // P368 (#79): 2 lines, then 「…展开」
        ol.appendChild(li);
      });
      box.appendChild(ol);
      if (a.textMax && open){
        const label = document.createElement("label"); label.className = "qfree";
        const title = document.createElement("span"); title.textContent = t('p425.free');
        const field = document.createElement("textarea"); field.rows = 2; field.maxLength = a.textMax;
        field.setAttribute("aria-label", t('p425.free')); field.placeholder = t('p425.freeHint'); field.disabled = !live;
        const saved = askSel.id === a.id ? askSel.picks[qi] : null;
        field.value = typeof saved === "string" ? saved : "";
        field.addEventListener("input", () => {
          if (askSel.id !== a.id) askSel = {id: a.id, picks: a.questions.map(() => [])};
          askSel.picks[qi] = field.value.trim() ? field.value : [];
          for (const button of ol.querySelectorAll('.opt')) button.setAttribute('aria-pressed', 'false');
          el('askSend').disabled = !askComplete(a);
        });
        label.append(title, field); box.appendChild(label);
      }

    });
  }
  fitQMore(box);
  const picks = a && askSel.id === a.id ? askSel.picks : (a && !open ? (a.choices || []) : []);
  box.querySelectorAll(".opt").forEach(b => {
    const pick = picks[+b.dataset.q];
    const on = Array.isArray(pick) && pick.includes(+b.dataset.n);
    b.setAttribute("aria-pressed", on ? "true" : "false");
    if (b.classList.contains("multi")) b.querySelector(".n").textContent = on ? "✓" : "";
  });
  el("askSend").hidden = !open;
  el("askSend").disabled = !live || !askComplete(a);
  el("askSend").textContent = askBusy ? t('r.q.sending') : t('r.q.confirm');
  const armed = open && Date.now() < askArmUntil;
  el("askCancel").hidden = !open;
  el("askCancel").disabled = askBusy || !C.canSign();
  el("askCancel").classList.toggle("armed", armed);
  el("askCancel").textContent = armed ? t('r.q.cancelSure') : t('r.q.cancel');
  const nosign = open && !C.canSign();
  el("askState").hidden = !(a && !open) && !nosign;
  el("askState").textContent = nosign ? t('ask.noSign') : (a ? askText(a) : "");
  el("elsewhereText").textContent = a && open ? t('r.q.elsewhereLive') : t('r.q.elsewhere');
  paintTags(a);
}

// Agent J's extras inside relay's sheet: danger chips (or 低风险), the scheduled task it comes from, how long is left,
// and how many more requests wait behind this one.
// P128: a credential that is only SHOWN is labelled 查看密码或密钥, a change 改密码或密钥; the reason in the phone's language.
const catKey = (c, item) => (c === 'credentials' && item && item.cred === 'read' ? 'credentials_read' : c);
const askWhy = (p) => (lang() === 'en' && p.why_en ? p.why_en : p.why);
function paintTags(item){
  const box = el("askTags"); box.replaceChildren();
  if (item && item.kind === "permission"){
    if (item.tool === "pair_device") { const s = document.createElement("span"); s.className = "atag danger"; s.textContent = t(item.passkey_required ? "pair.card.tagFace" : "pair.card.tag"); box.appendChild(s); }   // P153
    else if (item.tool === "passphrase_reset") { const s = document.createElement("span"); s.className = "atag danger"; s.textContent = t(item.passkey_required ? "ppReset.tagFace" : "ppReset.tag"); box.appendChild(s); }   // #118
    else if (item.tool === "wechat_confirm") { const s = document.createElement("span"); s.className = "atag"; s.textContent = t("wc.confirmTag"); box.appendChild(s); }
    else if (item.tool === "AuthRescue") { const s = document.createElement("span"); s.className = "atag danger"; s.textContent = t("ar.confirmTag"); box.appendChild(s); }
    else if (item.passkey_required) { const s = document.createElement("span"); s.className = "atag danger"; s.textContent = t(item.tool === "SandboxOnce" ? "ask.sandboxOnce" : "p336.faTitle"); box.appendChild(s); }
    else if (item.cats && item.cats.length) for (const c of item.cats){ const s = document.createElement("span"); s.className = "atag danger"; s.dataset.cat = c; s.textContent = t('ask.cat.' + catKey(c, item)); box.appendChild(s); }
    else if (item.goal !== "grant" && item.goal !== "attack") { const s = document.createElement("span"); s.className = "atag low"; s.textContent = t('ask.low'); box.appendChild(s); }   // P339: an attack card is never tagged low
  }
  if (item && item.task){ const s = document.createElement("span"); s.className = "atag task"; s.textContent = t('ask.task', {task: item.task}); box.appendChild(s); }
  if (item && item.goal){   // P158 goal mode: countdown (no answer = approved) · waits for you · possible attack (red)
    const s = document.createElement("span"); s.className = "atag goal " + (item.goal === "attack" ? "danger" : item.goal === "auto" ? "low" : "task");
    s.dataset.goal = item.goal; s.textContent = t('ask.goal.' + item.goal) + (item.goal_why && !goalHuman(item) ? " · " + item.goal_why : ""); box.appendChild(s);   // P339: the reason has its own line
  }
  box.hidden = !box.childElementCount;
  const more = C.openCount() - 1;
  el("askMore").textContent = item && item.state === "open" && more > 0 ? t('r.ask.more', {n: more}) : "";
  paintLeft(item);
}
let leftTimer = 0;
function paintLeft(item){
  clearTimeout(leftTimer);
  const p = el("askLeft");
  if (!item || item.state !== "open" || !item.deadline || friendAsk(item)){ p.hidden = true; return; }   // friend cards say their own validity
  const left = Math.max(0, Math.ceil((item.deadline - Date.now()) / 1000));
  p.hidden = false;
  p.textContent = item.goal === "auto" ? (left > 0 ? t('ask.goalLeft', {n: left}) : t('ask.goalUp')) : left > 0 ? t('ask.left', {n: left}) : t('ask.timeUp');
  if (item.tool === "wechat_confirm" && left > 0) p.textContent = t(left > 3600 ? "wc.leftHours" : "wc.leftMins", {n: Math.ceil(left / (left > 3600 ? 3600 : 60))});
  if (left > 0) leftTimer = setTimeout(() => paintLeft(item), 1000);
}

function renderCard(s){
  const card = s.card, a = s.ask || null, p = s.approval || null;
  const pending = !!card;   // P396: a retry card remains usable after its owner cleared the waiting colour
  const folded = pending && foldedCard === cardKey(card);
  if (!pending) foldedCard = null;
  const fin = !!a && ASK_FINAL.includes(a.state);
  const age = fin && a.final_at ? Date.now() / 1000 - skew - a.final_at : Infinity;
  if (fin && age < ASK_RESULT_S && askToasted !== a.id){ askToasted = a.id; toast(askText(a), 2600, askState(a) === 'answered' ? 'success' : 'info'); }
  const result = fin && age < ASK_RESULT_S && !pending && askDismissed !== a.id;
  const pfin = !!p && APPR_FINAL.includes(p.state);
  const page = pfin && p.final_at ? Date.now() / 1000 - skew - p.final_at : Infinity;
  if (pfin && page < ASK_RESULT_S && apprToasted !== p.id){ apprToasted = p.id; executionToast(apprText(p), 2600); }
  if (apprOpt && p && apprOpt.id === p.id && pfin){
    // P154: the host's verdict on a card this remote put away at once. The one we sent → done, quietly. Anything else
    // (timed out, ended, stopped first) → the result card comes back once to say so.
    if (p.state !== (apprOpt.action === "deny" ? "denied" : "approved")) apprDismissed = null;
    apprOpt = null;
  }
  const presult = pfin && page < ASK_RESULT_S && !pending && !result && apprDismissed !== p.id;
  clearTimeout(askResultTimer);
  const left = result ? ASK_RESULT_S - age : presult ? ASK_RESULT_S - page : 0;
  if (left > 0) askResultTimer = setTimeout(() => { if (cur) renderCard(cur); }, left * 1000 + 50);
  document.body.dataset.sheet = (pending && !folded) || result || presult ? "1" : "0";
  el("sheet").hidden = document.body.dataset.sheet !== "1";
  el("pendTag").hidden = !folded || (!!apprOpt && apprOpt.key === foldedCard);   // P154: a card answered here leaves no tag
  el("apprBtns").hidden = true; el("apprBatch").hidden = true; el("apprGoalAdd").hidden = true;
  el("goalInfo").hidden = true; el("cmdToggle").hidden = true;   // P339: only a goal card in plain words shows them
  el("sheet").classList.remove("danger");
  el("frAsk").hidden = true;
  if (result){
    el("askText").textContent = t('r.ask.answer');
    el("cmdBox").hidden = true; el("qs").hidden = false; el("why").hidden = true; el("cmdCopy").hidden = true;
    renderQuestion(null, a);
    return;
  }
  if (presult){ renderPermission(null, p); return; }
  if (!pending){ el("askCancel").hidden = true; paintLeft(null); return; }
  el("pendText").textContent = card.kind === "question" ? t('r.pend.question') : t('r.pend.permission');
  const q = card.kind === "question";
  el("askText").textContent = q ? t('r.ask.answer') : t('r.ask.nod');
  el("cmdBox").hidden = q; el("qs").hidden = !q; el("cmdCopy").hidden = true;
  if (q){
    renderQuestion(card, a && !ASK_FINAL.includes(a.state) ? a : null);
    el("why").hidden = true;
  } else renderPermission(card, p && p.state === "open" ? p : null);
}

// ---- tool approval from the phone (§8, §10.8; relay M2-approve) ---------------------
// 拒绝 is one tap; 批准 needs a HOLD of APPR_HOLD_MS — a tap alone never approves. Both are Ed25519-signed by this
// device's non-extractable key over exactly what the sheet shows (tool + summary; the batch scope for 批量).
const APPR_FINAL = ["approved", "denied", "terminal", "timeout", "expired", "ended", "stopped"];
const APPR_HOLD_MS = 1200;
let apprSent = {id: null, state: null}, apprBusy = false, apprToasted = null, apprDismissed = null;
// P154 追加: the remote that sends the answer puts the card away at once (other remotes close when the host says so).
// apprOpt = {id, key, action} while the host has not confirmed; apprFail = the reason the card came back (shown on it).
let apprOpt = null, apprFail = null;
let apprHold = null;

function apprState(p){
  if (!p) return null;
  return p.state === "open" && apprSent.id === p.id ? apprSent.state : p.state;
}
const FR_DONE = {approved: "allow", denied: "deny", timeout: "timeout", expired: "timeout", ended: "ended", stopped: "stopped", terminal: "ended"};
function apprText(p){
  const st = apprState(p);
  if (friendAsk(p) && p.pq && p.pq.pairing === true && FR_DONE[st] === "ended") return t('p417.so.closed');   // #186
  if (friendAsk(p) && FR_DONE[st]) return t('fr.ask.done.' + (p.tool === "friend_request" ? "fr_" : "pq_") + FR_DONE[st]);
  if (st === "open") return p.local_only ? t('r.appr.st.localOnly') : "";
  return ["allow_sent", "deny_sent", "batch_sent", "approved", "denied", "terminal", "timeout", "expired", "ended", "stopped"].includes(st) ? t('r.appr.st.' + st) : "";
}
function renderPermission(card, p){
  el("askText").textContent = t('r.ask.nod');
  if (friendAsk(p)){
    el("sheet").classList.toggle("danger", !!(p.pq && p.pq.high_risk));
    el("sheet").classList.toggle("friend-neutral", !(p.pq && p.pq.high_risk));                                    // §17.7: friends.js draws the card into #frAsk; relay keeps the sheet
    for (const id of ["cmdBox", "cmdCopy", "why", "qs", "askCancel", "askSend", "apprBtns", "apprBatch", "apprGoalAdd", "goalInfo", "cmdToggle", "askState"]) el(id).hidden = true;
    el("frAsk").hidden = false;
    renderFriendAsk(p, {open: apprState(p) === "open", sign: C.canSign(), repaint: () => { if (cur) renderCard(cur); },
      dismiss: () => { apprDismissed = p.id; if (cur && cur.card) foldedCard = cardKey(cur.card); if (cur) renderCard(cur); }});
    el("elsewhereText").textContent = t(p.tool === "friend_request" ? 'fr.ask.where' : 'fr.q.where');
    paintTags({...p, kind: "friend"});
    return;
  }
  el("sheet").classList.remove("friend-neutral");
  el("cmdBox").hidden = false; el("qs").hidden = true;
  el("askCancel").hidden = true; el("askSend").hidden = true;
  const tool = (p && p.tool) || (card && card.tool_name) || "?";
  const rescueAsk = !!(p && p.tool === "AuthRescue");   // Login rescue has its own copy; passkey requirement is not a sandbox action.
  const handoffAsk = !!(p && p.tool === "wechat_confirm");
  const ppAsk = !!(p && p.tool === "passphrase_reset");   // #118: reset the remote pairing password; the host wrote the text
  const pairAsk = !!(p && p.tool === "pair_device") || ppAsk;   // P153 (§3.3): a new remote waits; the host wrote the card text
  const danger = !!(p && (p.passkey_required || pairAsk || (p.cats && p.cats.length) || p.goal === "attack"));
  el("sheet").classList.toggle("danger", danger);
  el("sheetTitle").textContent = handoffAsk ? t("wc.confirmTitle") : ppAsk ? t("ppReset.title") : pairAsk ? t("pair.card.title") : rescueAsk ? t("ar.confirmTitle") : p && p.passkey_required ? t(p.tool === "SandboxOnce" ? "ask.sandboxTitle" : "p336.faTitle") : danger ? t('r.verb.danger', {verb: verb(tool)}) : verb(tool);
  // P339: a goal card the host put in plain words — 「目标进行中：{who} 想{what}…」, the three lines under it, and the raw
  // command (tool tag, 展开全部, description) folded behind 「详情」 (closed for every new card).
  const human = goalHuman(p);
  if (human){
    el("sheetTitle").textContent = human;
    // review #3: a card that looks like an attack shows its raw command in full from the start (never folded away)
    if (goalOpen.id !== p.id){ goalOpen = {id: p.id, open: p.goal === "attack"}; if (p.goal === "attack") cmdOpenKey = cardKey(card); }
    el("cmdBox").hidden = !goalOpen.open;
    el("cmdToggle").hidden = false;
    el("cmdToggle").setAttribute("aria-expanded", String(goalOpen.open));
  }
  el("tool").textContent = handoffAsk ? t("wc.title") : pairAsk || (p && p.passkey_required) ? "Agent J" : tool;
  const {main} = summarize(p ? p.summary : card.summary);
  el("cmd").classList.toggle("sandbox", !!(p && (p.passkey_required || pairAsk || handoffAsk)));
  paintCmd(p && (p.passkey_required || pairAsk || handoffAsk) ? p.summary : main, !(p && (p.passkey_required || pairAsk || handoffAsk)), cardKey(card), tool);
  const why = p && p.why ? t('ask.why', {why: askWhy(p)}) : "";
  el("why").textContent = why; el("why").hidden = !why;
  const st = apprState(p), open = st === "open";
  const sign = C.canSign();
  paintMailAllow(p, open && sign);
  if (human) paintGoalInfo(p, open, open && sign);
  const goalAdd = !!(human && open && sign && p.goal === "hold" && p.goal_add && !p.local_only);
  el("apprGoalAdd").hidden = !goalAdd;
  el("apprGoalAdd").disabled = apprBusy;
  if (goalAdd) el("apprGoalAddText").textContent = p.goal_add;
  el("apprBtns").hidden = !open || !sign;
  el("apprDeny").disabled = apprBusy;
  el("apprAllow").hidden = !p || p.local_only;
  el("apprAllow").disabled = apprBusy;
  el("apprAllowLbl").textContent = handoffAsk ? t("wc.confirm") : ppAsk ? t(p.passkey_required ? 'ppReset.allowFace' : 'ppReset.allow') : pairAsk ? t(p.passkey_required ? 'pair.card.allowFace' : 'pair.card.allow') : rescueAsk ? t('ar.confirmStart') : p && p.passkey_required ? t(p.tool === 'SandboxOnce' ? 'ask.sandboxOnce' : 'p336.faYes') : t('r.appr.hold');   // P154 追加 9: 「长按批准」 on every card, ⚠ cards too — the room goes to the ⌘↩ hint
  el("apprDeny").firstElementChild.textContent = ppAsk ? t('r.appr.deny') : pairAsk ? t('pair.card.deny') : t('r.appr.deny');
  const kbd = apprKbd();                                       // P154 追加 2: ⌘↩ / Ctrl↩ on a computer only
  el("apprKbd").textContent = kbd; el("apprKbd").hidden = !kbd;
  if (kbd) el("apprAllow").setAttribute("aria-keyshortcuts", IS_MAC ? "Meta+Enter" : "Control+Enter");
  const batch = open && sign && p && p.scope && !danger;
  el("apprBatch").hidden = !batch;
  if (batch){
    el("apprBatchLbl").textContent = t('r.appr.batchHold');
    el("apprBatchScope").textContent = t('ask.batchScope', {scope: p.scope, max: p.batch_max, mins: Math.round(p.batch_secs / 60)});
  }
  if (!open || apprBusy) apprCancelHold();
  const text = p ? (open && !sign ? t('ask.noSign') : open && apprFail && apprFail.id === p.id ? apprFail.text : apprText(p)) : "";
  el("askState").hidden = !text;
  el("askState").textContent = text;
  el("elsewhereText").textContent = handoffAsk ? t("wc.confirmNote") : ppAsk && open ? t('ppReset.where') : pairAsk && open ? t('pair.card.where') : !p ? t('r.appr.elsewhere') : p.local_only ? t('r.appr.st.localOnlyShort') : open ? t('r.appr.elsewhereLive') : t('r.appr.elsewhere');
  paintTags(p || null);
}
// P339: the goal card's plain title (null = an older host's card: it renders exactly as before).
let goalOpen = {id: null, open: false};
function goalHuman(p){
  if (!p || !["auto", "hold", "attack"].includes(p.goal) || !p.goal_what) return null;
  return t(p.goal === "auto" ? 'p339.titleAuto' : 'p339.title', {who: p.goal_who || t('p339.agent'), what: p.goal_what});
}
// Plain lines, text only: why it was not approved automatically · what happens if you don't tap · how to approve (and, with
// a goal_add offer, how to let the same kind through from now on). A card already decided keeps only the reason.
function paintGoalInfo(p, open, live){
  const lines = [];
  if (p.goal_why) lines.push(t('p339.why', {why: p.goal_why}));
  if (open) lines.push(t(p.goal === "auto" ? 'p339.ifNotAuto' : 'p339.ifNot'));
  if (live && p.goal !== "auto") lines.push(t('p339.how') + (p.goal === "hold" && p.goal_add ? t('p339.howAdd') : ""));
  const box = el("goalInfo");
  box.replaceChildren(...lines.map(x => { const n = document.createElement("p"); n.textContent = x; return n; }));
  box.hidden = !lines.length;
}
function toggleGoalCmd(){
  const p = cur && cur.approval;
  if (!p) return;
  goalOpen = {id: p.id, open: !(goalOpen.id === p.id && goalOpen.open)};
  if (cur) renderCard(cur);
}
// P296: a mail card to someone not yet on the mail allowlist offers one unticked box per such recipient — 「以后发给 X 不用再
//确认（加入白名单）」. Ticked ones go out with 长按批准, inside the same signature (api.approve → mailAllowMessage).
let mailPicks = {id: null, set: new Set()};
function paintMailAllow(p, live){
  const box = el("apprMailAllow");
  const offer = live && p && p.tool === "agentj mail send" && !p.goal_add && Array.isArray(p.allow_offer) ? p.allow_offer : [];   // P339: a goal_add card offers that one line instead
  if (!offer.length){ box.hidden = true; box.replaceChildren(); return; }
  if (mailPicks.id !== p.id) mailPicks = {id: p.id, set: new Set()};
  if (box.dataset.id !== p.id || box.childElementCount !== offer.length + 1){
    box.dataset.id = p.id;
    const rows = offer.map((a) => {
      const lab = document.createElement("label"), cb = document.createElement("input"), sp = document.createElement("span");
      cb.type = "checkbox"; cb.checked = mailPicks.set.has(a);
      cb.addEventListener("change", () => { if (cb.checked) mailPicks.set.add(a); else mailPicks.set.delete(a); });
      sp.textContent = t('r.appr.mailAllow', {a});
      lab.append(cb, sp);
      return lab;
    });
    const hint = document.createElement("small"); hint.textContent = t('r.appr.mailAllowHint');
    box.replaceChildren(...rows, hint);
  }
  box.hidden = false;
}
// P154: a long command used to push 长按批准 below the fold. It now shows 2 lines (the sandbox explanation stays whole);
// 展开全部 / 收起 (beside the tool tag) appears only when there is more, and the copy icon (the reply bar's, at the right of
// the title 「运行一条命令」) copies the whole command — to read it, paste it elsewhere.
let cmdOpenKey = null, cmdFull = "";
function paintCmd(text, fold, key, tool){
  const pre = el("cmd");
  // A Bash summary is "<command>\n— <description>" (host agent.summarize): the description is the line people can read,
  // so it stands above the folded command; the copy icon takes the command alone.
  let body = String(text || ""), desc = "";
  const cut = fold && tool === "Bash" ? body.lastIndexOf("\n— ") : -1;
  if (cut > 0){ desc = body.slice(cut + 3).trim(); body = body.slice(0, cut); }
  const gist = !desc && fold && tool === "Bash" ? cmdGist(body) : "";   // P341 (#19): no description → a plain line of what it does
  el("cmdDesc").textContent = desc || gist; el("cmdDesc").hidden = !(desc || gist);
  el("cmdDesc").classList.toggle("gist", !!gist);
  cmdFull = body;
  if (pre.textContent !== cmdFull) pre.textContent = cmdFull;
  if (cmdOpenKey !== null && cmdOpenKey !== key) cmdOpenKey = null;
  const open = !fold || cmdOpenKey === key;
  pre.classList.toggle("fold", !open);
  pre.dataset.key = key || "";
  el("cmdCopy").hidden = !cmdFull;
  measureCmd();
}
function measureCmd(){
  const pre = el("cmd"), more = el("cmdMore");
  const folded = pre.classList.contains("fold");
  const longer = folded ? pre.scrollHeight > pre.clientHeight + 2 : cmdOpenKey !== null;
  more.hidden = !longer;
  pre.classList.toggle("clip", folded && longer);
  more.setAttribute("aria-expanded", String(!folded));
  el("cmdMoreLbl").textContent = t(folded ? 'r.appr.cmdMore' : 'r.appr.cmdLess');
}
function toggleCmd(){
  const pre = el("cmd");
  cmdOpenKey = pre.classList.contains("fold") ? pre.dataset.key : null;
  pre.classList.toggle("fold", cmdOpenKey === null);
  pre.classList.remove("clip");
  measureCmd();
}
async function apprPost(p, action){
  if(action!=="deny"&&p.passkey_required&&phoneConfirmation(!p.fa))return;
  // A passkey approval waits for the passkey sheet, so only that one stays up until it is signed.
  const now = !(p.passkey_required && action !== "deny") && cur && cur.card;
  apprFail = null;
  if (now){ apprOpt = {id: p.id, key: cardKey(cur.card), action}; foldedCard = apprOpt.key; apprDismissed = p.id; }
  apprBusy = true; if (cur) renderCard(cur);
  // P339: 「长按：批准，并加进目标授权」 = an allow whose signature also covers [goal_add] (the P296 allow_add line)
  const r = action === "goaladd" ? await C.api.approve(p, "allow", [p.goal_add])
    : await C.api.approve(p, action, action === "allow" && mailPicks.id === p.id ? [...mailPicks.set] : undefined);
  apprBusy = false;
  if (r.ok){ apprSent = {id: p.id, state: action === "batch" ? "batch_sent" : action === "allow" || action === "goaladd" ? "allow_sent" : "deny_sent"}; }
  else {
    const why = r.why === 'passkey_required' ? t('ask.passkeyRequired') : r.why === "offline" ? t('r.appr.net') : t('r.appr.fail');
    if (apprOpt && apprOpt.id === p.id){                        // it did not leave this phone: the card comes back, saying why
      if (foldedCard === apprOpt.key) foldedCard = null;
      apprOpt = null; apprDismissed = null; apprFail = {id: p.id, text: why};
    }
    toast(why, 3000);
  }
  if (cur) renderCard(cur);
}
// P396: refusal is a retryable failure, never a terminal decision and never proof of approval.
export function answerRefused(m) {
  if (apprSent.id === m.id) apprSent = {id: null, state: null};
  if (apprOpt && apprOpt.id === m.id) apprOpt = null;
  apprDismissed = null; foldedCard = null;
  apprFail = {id: m.id, text: t('fr.ask.failed')};
}
// P154 追加 2 (owner, 2026-10-12): on a computer ⌘+Enter (Mac) / Ctrl+Enter = 长按批准 finished — the same apprPost, so the
// same device signature and passkey check; never a way around them. Only while the card is up; it wins over the text
// box's Enter-to-send. Esc puts the card away (layers.js). Phones (no fine pointer) show no hint.
const IS_MAC = /Mac|iPhone|iPad|iPod/.test((navigator.userAgentData && navigator.userAgentData.platform) || navigator.platform || "");
function apprKbd(){
  return isDesktop() ? (IS_MAC ? "⌘↩" : "Ctrl↩") : "";
}
function apprHotkey(e){
  if (e.key !== "Enter" || !(e.metaKey || e.ctrlKey) || e.altKey || e.shiftKey || e.isComposing || e.repeat) return;
  if (document.body.dataset.sheet !== "1" || el("apprBtns").hidden || el("apprAllow").hidden || el("apprAllow").disabled || dialogOpen()) return;
  const p = apprLive();
  if (!p || p.local_only || friendAsk(p)) return;
  e.preventDefault(); e.stopImmediatePropagation();
  apprCancelHold();
  apprPost(p, "allow");
}
function apprLive(){
  const p = cur && cur.approval;
  return p && apprState(p) === "open" && !apprBusy ? p : null;
}
function apprCancelHold(){
  if (!apprHold) return;
  clearTimeout(apprHold.timer);
  apprHold.btn.classList.remove("holding");
  apprHold = null;
}
// The hold gesture, for 批准 and for 批量批准 (both approve, so neither may fire on a tap).
function holdToApprove(btn, action){
  btn.style.setProperty("--hold-ms", APPR_HOLD_MS + "ms");
  btn.addEventListener("pointerdown", e => {
    const p = apprLive();
    if (!p || p.local_only || apprHold || e.button > 0) return;
    e.preventDefault();
    if (p.passkey_required && action === "allow") { apprPost(p, action); return; }
    btn.classList.add("holding");
    apprHold = {id: p.id, btn, t0: Date.now(), timer: setTimeout(() => {
      const q = apprLive(), id = apprHold && apprHold.id;
      apprCancelHold();
      if (q && q.id === id && !q.local_only) apprPost(q, action);
    }, APPR_HOLD_MS)};
  });
  ["pointerup", "pointercancel", "pointerleave"].forEach(ev => btn.addEventListener(ev, () => {
    if (!apprHold || apprHold.btn !== btn) return;
    const short = Date.now() - apprHold.t0 < APPR_HOLD_MS;
    apprCancelHold();
    if (short && ev === "pointerup") toast(t('r.appr.holdHint'), 1800);
  }));
  btn.addEventListener("contextmenu", e => e.preventDefault());
}
async function askPost(a, cancel){
  askBusy = true; renderCard(cur);
  const r = await C.api.answerQ(a, cancel ? null : askSel.picks.map(p => p.slice()));
  askBusy = false;
  if (r.ok){ askSent = {id: a.id, state: cancel ? "cancel_sent" : "sent"}; if (!cancel) a.choices = askSel.picks.map(p => p.slice()); }
  else toast(r.why === "offline" ? t('r.q.net') : t('r.q.fail'), 3000);
  if (cur) renderCard(cur);
}

// ---- the phone's status bar follows the page colour (relay ADR-022) ----------------
// P422: first paint and every repaint keep system chrome Sooty.
function chromeTarget(){
  return "#141414"; // P422 #191/#86: system chrome never follows a state or custom theme.
}
function setThemeColor(hex){
  const old = [...document.querySelectorAll('meta[name="theme-color"]')];
  if (old.length === 1 && old[0].getAttribute("content") === hex && !old[0].media) return;
  const m = document.createElement("meta");
  m.name = "theme-color"; m.content = hex;
  for (const o of old) o.remove();
  document.head.prepend(m);
}
export function paintChrome(force){
  if (document.documentElement.dataset.ios === "1") {
    const color = paintIOSChrome();
    // All iPhone browsers and Home Screen keep the same fixed Noir metadata.
    if (color) setThemeColor(color);
    return;
  }
  const hex = chromeTarget();
  if (!hex) return;
  document.documentElement.style.backgroundColor = hex;
  setThemeColor(hex);
}

// ---- dashboard numbers: "—" / an empty bar unless the host has an exact source --------
function paintUsage(u){
  u = u || {};
  paintPill(u);
  el("meta").title = u.source ? t('r.usage.age', {s: Math.round(u.age_s || 0)}) : t('r.usage.none');
  const windows = quotaWindows(u);
  // P340 D (#42, Leo 10-16): both rules stay on screen as page dividers whatever the harness / provider — no data hides only
  // the label (no 「—」, no explanation), so switching harness never moves the page. Provider windows: the 5 h one goes to the
  // bottom rule, the first longer one (weekly / monthly / daily) to the top rule.
  const longer = windows.filter(w => w.window !== '5h');
  for (const id of ['mWeek', 'm5h']) {
    const bar = el(id), x = Array.isArray(u.quota_windows)
      ? (id === 'mWeek' ? longer[0] : windows.find(w => w.window === '5h') || longer[1])
      : windows.find(w => w.window === (id === 'm5h' ? '5h' : 'weekly'));
    bar.hidden = false; bar.dataset.known = x ? '1' : '0';
    bar.dataset.zero = x && x.pct === 0 ? '1' : '0';
    bar.firstElementChild.style.width = x ? x.pct + '%' : '0';
    // P303: WEEKLY n%（第 N 天）, and a faint glow — fast (weekly only), ≥ 80 % Mellow Marrow, ≥ 90 % Peachy Feeling
    const c = x ? cycle(x.window, x.reset || quotaReset(u, x.window)) : null, day = c && x.window === 'weekly' ? t('p303.day', {n: c.step}) : '';
    bar.dataset.glow = x ? tier(x.pct, c, {pace: x.window === 'weekly'}) : '';
    bar.querySelector('.lab').textContent = x ? `${x.window} ${Math.round(x.pct)}%${day}${readAge(x)}` : '';
    const label = x ? t('r.meter.' + ({weekly:'week',daily:'daily',monthly:'monthly','5h':'h5'}[x.window])) : '';
    bar.dataset.stale = x && x.stale ? '1' : '0';
    bar.setAttribute('aria-label', x ? t('r.meter.used', {label, pct:x.pct}) + (day ? ' ' + t('p303.dayAria', {n: c.step, of: c.steps}) : '')
      + (x.stale ? ' · ' + t('p433.staleWhy') : x.at ? ' · ' + t('p433.updated', {t: hm(x.at)}) : '') : '');
    bar.title = bar.getAttribute('aria-label');
  }
  const w = el("water"), ctx = u.context_pct;
  const post = !!postCompact && typeof ctx !== "number" && !Number.isFinite(u.context_used);
  w.dataset.post = post ? "1" : "0";
  w.dataset.known = post || typeof ctx === "number" ? "1" : "0";
  w.style.setProperty("--lvl", post ? POST_COMPACT_LEVEL : typeof ctx === "number" ? ctx / 100 : 0);
  w.dataset.lvl = typeof ctx === "number" ? String(ctx) : "";
  w.setAttribute('aria-label', typeof ctx === 'number' ? t('r.usage.ctx', {p:ctx}) : t('r.usage.none'));
  w.title = Number.isFinite(u.context_used) ? (Number.isFinite(u.context_limit) && u.context_limit > 0 ? `${u.context_used} / ${u.context_limit}` : `${u.context_used} tokens`) : w.getAttribute('aria-label');
  if (Number.isFinite(u.context_used) && typeof ctx !== 'number') w.setAttribute('aria-label', w.title);
}
// P433 (#196): beside a reading — 「· 读数可能过期」 when a confirmed limit contradicted it, 「· 更新于 HH:MM」 once it is older
// than READ_OLD (a fresh one needs no time), nothing when the host did not say when it was measured
const READ_OLD = 30 * 60;
const hm = (at) => { const d = new Date(at * 1000); return String(d.getHours()).padStart(2, '0') + ':' + String(d.getMinutes()).padStart(2, '0'); };
function readAge(x, now = Date.now() / 1000) {
  if (x.stale) return ' · ' + t('p433.stale');
  return x.at && now - x.at > READ_OLD ? ' · ' + t('p433.updated', {t: hm(x.at)}) : '';
}
const POST_COMPACT_LEVEL = 0.03;
let postCompact = false;
function paintCompacting(on){
  const w = el("water"), was = w.dataset.compacting === "1";
  if (on === was) return;
  if (on){ w.style.transform = ""; w.dataset.compacting = "1"; postCompact = true; return; }
  if (w.dataset.post === "1" && matchMedia("(prefers-reduced-motion: reduce)").matches){
    w.style.transition = "none"; w.dataset.compacting = "0"; w.style.transform = "";
    void w.offsetHeight; w.style.transition = "";
    return;
  }
  const tr = getComputedStyle(w).transform;
  w.style.transform = tr === "none" ? "" : tr;
  w.dataset.compacting = "0";
  void w.offsetHeight;
  w.style.transform = "";
}
// P303: the reset time the harness reported for a window (unix s; Claude Code / Codex), or null
function quotaReset(u, w){
  const r = w === 'weekly' ? u.week_reset : w === '5h' ? u.h5_reset : null;
  return Number.isFinite(r) && r > 0 ? r : null;
}
// P207 §1: the numbers open the global centred dark dialog (js/usagedlg.js), not a toast pill at the top
function meterToast(e){
  const u = (cur && cur.usage) || {};
  const windows = u.source ? quotaWindows(u).map(x => ({window: x.window, reset: x.reset || quotaReset(u, x.window), label: t('r.meter.' + ({weekly:'week',daily:'daily',monthly:'monthly','5h':'h5'}[x.window])), pct: x.pct,
    ...(x.at ? {at: x.at} : {}), ...(x.stale ? {stale: true} : {})})) : [];
  const ctx = typeof u.context_pct === 'number' ? u.context_pct : null;
  const ctxText = ctx != null ? null : Number.isFinite(u.context_used) ? `${u.context_used} tokens` : postCompact ? t('p207set.usage.ctxPost') : null;
  openUsage({source: !!u.source, windows, ctx, ctxText, age: u.age_s != null ? u.age_s : null}, e && e.currentTarget);
}

/** The single entry: a snapshot from snap.js (relay's render(s)). */
export function render(s){
  if (!C) return;
  cur = s;
  lastSync = new Date();
  const st = s.status || "unknown";
  document.body.dataset.status = st;
  document.body.dataset.agent = s.hostStatus || "none";
  kindSeen(s.agentKind);   // P412 (#175)
  if (st === "waiting" && s.kind) document.body.dataset.kind = s.kind;
  else delete document.body.dataset.kind;
  paintChrome();
  el("capText").textContent = document.body.dataset.conn !== "on" ? el("capText").textContent
    : st === "waiting" ? t('r.capt.' + (s.kind || 'permission')) : t('r.capt.' + (["working", "idle"].includes(st) ? st : "unknown"));
  renderSub();
  paintHeader();
  noteHistory(s);
  renderCard(s);
  paintUsage(s.usage);
  paintCompacting(s.compacting === true);
  paintSayCancel();                                    // P328: blue → the × stops the turn
  if (s.usage && typeof s.usage.context_pct === "number" && !s.compacting) postCompact = false;
  if (lastStatus !== null && s.status !== lastStatus && navigator.vibrate && document.body.dataset.conn === "on"){
    try{ navigator.vibrate(s.status === "waiting" ? [90,60,90] : 35); }catch(_){}
  }
  lastStatus = s.status;
}

// ---- the header pill switches the model / effort (relay ADR-035; §10.11) ---------------
// Tap the model half = the next model, tap the effort half = the next effort, hold ≈ 0.6 s = the default.
// P239 (Leo 10-13: 「点着玩」): a tap only changes what the label shows. Each half keeps its own clock — it commits once it
// has been left alone PILL_GATHER_MS (5 s); another tap on that half restarts it. A pick equal to what the Agent already
// runs is never sent. While a half waits it is faded with a thin line running out under it (data-sw-model / -effort);
// once sent the pill is dashed until the next meter reports it; a refusal puts the label back with one toast.
// Hold = the default at once (a deliberate gesture, not play): it drops any waiting tap. Leaving the page sends a waiting
// pick at once (pillFlush). Typed `/model …` and the /model card's buttons are immediate and drop a waiting tap (pillDrop).
const PILL_GATHER_MS = 5000, HOLD_MS = 600, PILL_SETTLE_MS = 45000, HOLD_SLOP_PX = 10;
const pill = {want: null, sent: null, sentAt: 0, wait: {model: 0, effort: 0}};
function swCat(){ return cur && cur.switch && Array.isArray(cur.switch.models) && cur.switch.models.length ? cur.switch : null; }
function realPick(){
  const u = (cur && cur.usage) || {}, c = swCat();
  const m = c && (c.models.find(x => x.id === u.model_id) || c.models.find(x => x.name === u.model));
  return {model: m ? m.id : null, effort: u.effort || null};
}
const effortsOf = (c, id) => { const m = c && c.models.find(x => x.id === id); return m && Array.isArray(m.efforts) ? m.efforts : []; };
const samePick = (a, b) => !!a && !!b && a.model === b.model && (a.effort || null) === (b.effort || null);
// P239: a model with no effort levels (Haiku) is settled whatever effort the meter still reports.
const sameRun = (a, b) => samePick(a, b) || (!!a && !!b && !!a.model && a.model === b.model && !effortsOf(swCat(), a.model).length);
const pillWaiting = () => !!(pill.wait.model || pill.wait.effort);
const sharedNotice = new SharedNotice();
function paintPill(u){
  u = u || (cur && cur.usage) || {};
  const shared = el('shared-status');
  if (shared) {
    const state = C && C.connected() && u.shared_status;
    // Only an actionable read-only condition owns permanent space. Informational
    // following notices use the existing fading toast, keyed to the native session.
    if (state === 'desktop_writer' && el('toast').dataset.sharedFollow === '1') toastOff();
    shared.hidden = state !== 'desktop_writer';
    shared.textContent = state === 'desktop_writer' ? u.shared_writer?.[lang()] || t('r.shared.desktop_writer') : '';
    const follow = C.connected() && (u.shared_follow?.agent !== 'codex' || state) ? u.shared_follow : null;
    sharedNotice.update(hostId(), follow, state, (agent) => {
      // A result/error/action toast takes priority over this optional information.
      if (!el('toast').classList.contains('on') || el('toast').dataset.sharedFollow === '1') {
        toast(t(agent === 'codex' ? 'r.shared.following' : 'r.shared.following.' + agent), 3500);
        el('toast').dataset.sharedFollow = '1';
      }
    });
  }
  const c = swCat(), real = realPick();
  if (!pillWaiting()){
    if (pill.want && sameRun(pill.want, real)){ pill.want = null; pill.sent = null; pill.sentAt = 0; }
    if (pill.want && pill.sentAt && Date.now() - pill.sentAt > PILL_SETTLE_MS){ pill.want = null; pill.sent = null; pill.sentAt = 0; }
  }
  if (pill.sent && sameRun(pill.sent, real)) pill.sent = null;
  const show = pill.want || null;
  const name = show ? ((c && c.models.find(x => x.id === show.model)) || {}).name || (show.isDefault ? u.model : "") || "—" : (u.model || "—");
  const noEffort = !!(c && real.model && !effortsOf(c, real.model).length);   // P239: e.g. Haiku — the meter's effort means nothing
  const effort = show ? show.effort : (u.model && !noEffort ? u.effort : null);
  el("metaModel").textContent = name;
  el("metaSep").textContent = effort ? " · " : "";
  el("metaEffort").textContent = effort || "";
  el("metaStale").textContent = "";
  const m = el("meta");
  if (show) m.dataset.sw = pillWaiting() ? "wait" : pill.sentAt && cur && cur.status !== "idle" ? "queued" : "pending";
  else delete m.dataset.sw;
  for (const h of ["model", "effort"]){
    const k = h === "model" ? "swModel" : "swEffort";
    if (show && pill.wait[h]) m.dataset[k] = "wait"; else delete m.dataset[k];
  }
}
// P142: no catalogue (shared Claude before its picker is known, an older host): the tap asks the host for `/model` — the
// same self-slash path as typing it; its card lists the models as buttons. Only an offline page still says "unsupported".
function pillList(){ if (!C.api.slash("model")) toast(t('r.pill.unsupported'), 2200); }
function pillStep(half){
  const c = swCat();
  if (!c){ pillList(); return; }
  const b = pill.want && pill.want.model ? pill.want : (pill.sent || realPick());
  let model = b.model || c.default.model, effort = b.effort || c.default.effort;
  if (half === "model"){
    const available = c.models.filter(x => !x.disabled);
    if (!available.length) { C.models?.(); return; }
    const i = available.findIndex(x => x.id === model);
    model = available[(i + 1) % available.length].id;
    const efs = effortsOf(c, model);
    if (!efs.includes(effort)) effort = efs.length ? (efs.includes(c.default.effort) ? c.default.effort : efs[0]) : null;
  } else {
    const efs = effortsOf(c, model);
    if (!efs.length){ toast(t('r.pill.noEffort'), 2200); return; }
    effort = efs[(efs.indexOf(effort) + 1) % efs.length];
  }
  pill.want = {model, effort}; pill.sentAt = 0;
  const m = el("meta"), k = half === "model" ? "swModel" : "swEffort";
  delete m.dataset[k]; void m.offsetWidth;                         // a new tap restarts the running-out line
  clearTimeout(pill.wait[half]);
  pill.wait[half] = setTimeout(() => { pill.wait[half] = 0; pillCommit(); }, PILL_GATHER_MS) || 1;
  paintPill();
}
// The pick to send now: halves whose clock is still running keep what the Agent runs (sent / real), the others take the
// shown target. An effort that does not fit the committed model takes the shown one (the model step already fitted it).
function pillCommit(){
  const want = pill.want; if (!want || want.isDefault) return;
  const base = pill.sent || realPick(), c = swCat();
  const model = pill.wait.model ? base.model || want.model : want.model;
  let effort = pill.wait.effort ? base.effort : want.effort;
  if (pill.wait.effort && c && !effortsOf(c, model).includes(effort)) effort = want.effort;
  const pick = {model, effort};
  if (sameRun(pick, base)){                                     // tapped back to what runs: nothing to send
    if (pill.sent) pill.sentAt = Date.now();                     // still settling an earlier send: its clock runs again
    paintPill(); return;
  }
  pillSend(pick);
}
// Leaving the page (hidden / pagehide): a waiting pick is sent at once rather than lost.
function pillFlush(){
  if (!pillWaiting()) return;
  clearTimeout(pill.wait.model); clearTimeout(pill.wait.effort);
  pill.wait.model = pill.wait.effort = 0;
  pillCommit();
}
// An immediate switch from elsewhere (typed /model, the /model card): the waiting taps are dropped, the label shows the
// real reading again.
function pillDrop(){
  clearTimeout(pill.wait.model); clearTimeout(pill.wait.effort);
  pill.wait.model = pill.wait.effort = 0;
  pill.want = null; pill.sent = null; pill.sentAt = 0;
  paintPill();
}
function pillDefault(){
  const c = swCat();
  clearTimeout(pill.wait.model); clearTimeout(pill.wait.effort);
  pill.wait.model = pill.wait.effort = 0;
  if (!c){ pill.want = {isDefault: true}; pill.sentAt = 0; pillSend(pill.want); return; }   // P142: hold = default, catalogue or not
  pill.want = {...c.default, isDefault: true}; pill.sentAt = 0;
  paintPill();
  toast(t('r.pill.default', {name: ((c.models.find(x => x.id === c.default.model) || {}).name || ""), effort: c.default.effort || "—"}), 1800);
  pillSend(pill.want);
}
async function pillSend(pick){
  const want = pill.want; if (!want) return;
  const r = await C.api.model(pick.isDefault ? {default: true} : {model: pick.model, effort: pick.effort});
  if (r.ok && r.superseded){ if (pill.want === want && !pillWaiting()) pillDrop(); return; }   // another remote's later pick won
  if (!r.ok){
    toast(t('r.pill.fail', {why: t('r.pill.why.' + (["unknown_model", "unknown_effort", "busy", "unsupported", "stopped", "offline", "timeout", "unconfirmed", "blocked", "failed"].includes(r.why) ? r.why : "other"))}), 2600);
    pillDrop(); return;                                            // the label goes back to what the Agent runs
  }
  if (pick.model) pill.sent = {model: pick.model, effort: pick.effort || null};   // the default too, when the catalogue names it
  if (pill.want !== want) return;
  pill.sentAt = Date.now();
  if (r.queued || (cur && cur.status !== "idle")) toast(t('r.pill.queued'), 2000);
  paintPill();
}
// One press handler for both hidden controls: a tap (released before HOLD_MS, finger still) or a hold.
function pressable(node, onTap, onHold){
  node.setAttribute('data-no-select', '');
  let p = null;
  const end = () => { if (p){ clearTimeout(p.t); p = null; } };
  node.addEventListener("pointerdown", e => {
    if (e.button > 0) return;
    end();
    p = {x: e.clientX, y: e.clientY, e, held: false};
    p.t = setTimeout(() => { if (p){ p.held = true; try{ if (navigator.vibrate) navigator.vibrate(30); }catch(_){} onHold(p.e); } }, HOLD_MS);
  });
  node.addEventListener("pointermove", e => { if (p && Math.hypot(e.clientX - p.x, e.clientY - p.y) > HOLD_SLOP_PX) end(); });
  node.addEventListener("pointerup", () => { if (p && !p.held) onTap(p.e); end(); });
  node.addEventListener("pointercancel", end);
  node.addEventListener("pointerleave", end);
  node.addEventListener("contextmenu", e => e.preventDefault());
  node.addEventListener("dragstart", e => e.preventDefault());
}
function pillHalf(e){
  if (!el("metaEffort").textContent) return "model";             // P239: no effort shown (e.g. Haiku) → the whole pill is the model
  const sep = el("metaSep").getBoundingClientRect(), m = el("meta").getBoundingClientRect();
  const cut = sep.width ? sep.left + sep.width / 2 : m.left + m.width / 2;
  return e.clientX < cut ? "model" : "effort";
}

// ---- the logo steps the page's type size: 中 → 大 → 中 → 小 → 中; hold = 中 ------------------------
// P154 追加 13 (Leo, 2026-10-12: 「每次刷新以后就回到了初始默认值」): the step is phone-local and remembered in
// localStorage beside the other look settings (aj.fontScale, aj.sfxVolume) — a reload, a reopen or the iPhone
// home-screen app comes back at the same size. Applied at load, before the first paint of the chat screen.
// Not the same state as Settings' 「回复字号」 (--rs, reply text only); the two multiply and stay independent.
export const FZ_KEY = "aj.pageFz";
const FZ_STEPS = ["m", "l", "m", "s"];
let fzAt = 0;
function paintFz(){
  const v = FZ_STEPS[fzAt], root = document.documentElement;
  if (v === "m") delete root.dataset.fz; else root.dataset.fz = v;
}
try { const v = localStorage.getItem(FZ_KEY); const i = v && v !== "m" ? FZ_STEPS.indexOf(v) : -1; if (i > 0) fzAt = i; } catch (_) { /* blocked storage: default */ }
paintFz();
function setFz(i, say){
  fzAt = i;
  const v = FZ_STEPS[i];
  paintFz();
  try { if (v === "m") localStorage.removeItem(FZ_KEY); else localStorage.setItem(FZ_KEY, v); } catch (_) { /* blocked storage: this visit only */ }
  paintPlaceholder();
  if (say) toast(t('r.fz.' + v), 1000);
}

// ---- the logo wears the state colour ----------------------------------------------

// ---- pages: every reply with the input it answered (relay ADR-033 / 036) -----------------
// `hist` = the loaded turns of the host's history (§10.5); pageAt = index; `follow` = on the newest page. P132 (Leo,
// 2026-10-11): on the newest page a new page takes the owner to it — however far he scrolled the reply he was reading;
// paging history (or the full-screen reader) is never interrupted, the page capsule flashes instead. `tailWait` = he is on
// the newest page whose turn has no visible reply yet (a pending input stays behind the reply he reads, P87) — the intent
// survives, so that reply, when it lands, still takes him there.
const hist = {turns: [], count: 0, firstId: 0, lastId: 0, epoch: 0, route: true, busy: false, want: 0, older: false};
let histSettled = () => false;   // P398: settleQueued (the queue section sets it) — a page the host updated may end a queued send
let pageAt = -1, follow = true, tailWait = false, pagesCache = null, shownKey = null, unreadReply = false;
const noticeReads = new Set();
const isSilent = text => typeof text === "string" && text.trim() === "\u3014\u4e0d\u56de\u7fa4\u3015";
const tailSeen = {primed: false, id: 0, reply: "", virt: false};
function newTurnArrived(ps){
  const tt = ps.findLast(p => visibleReply(p)) || {id: null, reply: ""};
  let arrived = false;
  if (tailSeen.primed && tt.id !== null) arrived = tt.id > tailSeen.id;
  if (tt.id !== null) tailSeen.id = Math.max(tailSeen.id, tt.id);
  tailSeen.reply = tt.reply; tailSeen.virt = tt.id === null; tailSeen.primed = true;
  return arrived && !isSilent(tt.reply);
}
function visibleReply(p){ return typeof p?.reply === "string" && !!p.reply.trim() && !isSilent(p.reply); }
function visiblePage(p){
  const src = p.source;
  // P127 (追加6): a withdrawn send never reached the Agent and its words are back in the field — as if never sent, so its
  // page leaves the history instead of standing there with 「（无文字回复）」 (and a second copy once it is sent again).
  if (p.card && p.card.withdrawn === true) return false;
  return !isSilent(p.reply) && (visibleReply(p) || !!src?.text?.trim() || !!src?.att?.length || !!p.card);
}
function toNewest(){ follow = true; tailWait = false; shownKey = null; stick = true; showPage(); }
// P340 (Leo 10-16 #21): a message sent while he reads an older page leaves him there — only a send from the newest page
// (or the page still waiting for its reply) follows it to the new page. His own page arriving does the same (upsertTurn);
// a reply that lands while he is back in history lights the page number as any new reply does (P102 / P333).
function onNewestPage(){ return follow || tailWait || pageAt < 0 || pageAt >= pages().length - 1; }
let sentStay = false;
function sendFollow(){ sentStay = !onNewestPage(); if (!sentStay) toNewest(); }
const SRC_KINDS = ["leo", "dev", "host", "agent", "sys", "task", "cmd"];
// P424 (#87): a page without the owner's input whose reply is the page before it, word for word (an older host ended one
// turn twice: 「后台任务完成」 + the same reply) — not a second page; history already on disk keeps it, the phone folds it.
function echoPage(p, prev){
  const s = p.source;
  return !!prev && !p.card && !!s && (s.k === "agent" || s.k === "sys") && !s.text?.trim() && !s.att?.length && !s.quote
    && visibleReply(p) && typeof prev.reply === "string" && prev.reply.trim() === p.reply.trim();
}
function pages(){
  if (pagesCache) return pagesCache;
  const out = [];
  for (const p of hist.turns) if (visiblePage(p) && !echoPage(p, out[out.length - 1])) out.push(p);
  if (!out.length) out.push({id: null, reply: "", source: null, ts: null});
  return (pagesCache = out);
}
function totalPages(){ return pages().length + Math.max(0, hist.count - hist.turns.length); }
function noteHistory(s){
  const h = s.history;
  if (h && typeof h.last_id === "number"){
    hist.count = Math.max(h.count || 0, hist.turns.length);
    const ep = typeof h.epoch === "number" ? h.epoch : 0;
    if (ep !== hist.epoch || h.last_id < hist.lastId) resetHistory(ep);
    if (h.last_id > hist.lastId) syncHistory(h.last_id);
  }
  showPage();
}
function resetHistory(ep){
  search.onEpoch(ep);                                // P188: a /clear empties the search cache
  hist.count = 0; hist.epoch = ep; hist.turns = []; hist.lastId = 0; hist.firstId = 0; hist.want = 0;
  pagesCache = null; follow = true; tailWait = false; pageAt = -1; shownKey = null; tailSeen.id = 0; tailSeen.primed = false; unreadReply = false;
}
/** A turn pushed by the host (hist_turn): new, a reply part appended, or its end. */
export function upsertTurn(p){
  const previous=hist.turns.find(x=>x.id===p.id);
  if(previous?.end==='open' && p.end==='done' && !isSilent(p.reply)) announceTurn(p,true);
  const i = hist.turns.findIndex(x => x.id === p.id);
  if (i >= 0) hist.turns[i] = p;
  else if (!hist.turns.length || p.id > hist.turns[hist.turns.length - 1].id){
    if (hist.lastId && p.id > hist.lastId + 1 && hist.turns.length){ syncHistory(p.id); return; }   // a gap: fetch it in order
    hist.turns.push(p); hist.lastId = p.id; hist.count = Math.max(hist.count, hist.turns.length);
    if (!hist.firstId) hist.firstId = p.id;
  } else return;
  pagesCache = null;
  if (p.source && ["phone","cmd"].includes(p.source.k) && p.source.dev === C.myDev() && p.end === "open" && !previous && onNewestPage()){ follow = true; shownKey = null; }
  if (histSettled()) return;             // P398: dequeue → paintQueued → showPage
  showPage();
}
export function historyReset(ep){ resetHistory(ep); showPage(); }
// P135: a head fetch that fails or times out retries after 5 s, doubling to 60 s, one at a time; success or a newer
// generation (a reconnect, a fresh refresh) ends it — otherwise the page would say 「正在恢复…」 forever.
const RETRY_MS = 5000, RETRY_MAX_MS = 60000;
let historyRetry = null, historyDelay = 0;
async function refreshNewest(retry = false){
  if (historyRetry){ clearTimeout(historyRetry); historyRetry = null; }
  if (!retry) historyDelay = 0;
  unread.bind(hostId());
  const generation = ++historyGeneration;
  historySync = true; wordsSrc = null; showPage();
  const before = new Map(hist.turns.map(x => [x.id, x]));
  try{
    const j = await C.api.history({limit: 50});
    if (generation !== historyGeneration) return;
    if (!j || j.epoch < hist.epoch) throw new Error("stale head");
    if (j.epoch > hist.epoch) resetHistory(j.epoch);
    const byId = new Map(hist.turns.map(x => [x.id, x]));
    // A live update received after this request is newer than its snapshot.
    for (const p of j.turns){
      if (!byId.has(p.id) || byId.get(p.id) === before.get(p.id)) byId.set(p.id, p);
    }
    hist.turns = [...byId.values()].sort((a,b) => a.id - b.id);
    hist.lastId = hist.turns.at(-1)?.id || 0;
    hist.count = Math.max(j.count || 0, hist.turns.length);
    hist.firstId = j.first_id || hist.turns[0]?.id || 0;
    if (Number.isSafeInteger(j.read)) {
      unread.owner(j.epoch, j.read);
      if (unread.mark() > j.read) sendRead();
    }
    pagesCache = null; historySync = false; historyDelay = 0;
    unread.rebase(hist.turns.filter(visibleReply).map(x => x.id));   // P333: what the head brought (reload, foreground, reconnect) is not a live arrival
  }catch(_){
    if (generation === historyGeneration){   // keep the syncing wording and try again later
      historyDelay = Math.min(historyDelay ? historyDelay * 2 : RETRY_MS, RETRY_MAX_MS);
      historyRetry = setTimeout(() => { historyRetry = null; if (generation === historyGeneration) refreshNewest(true); }, historyDelay);
    }
  }
  finally{ if (generation === historyGeneration){ wordsSrc = null; if (!histSettled()) showPage(); } }
}
async function syncHistory(want){
  hist.want = Math.max(hist.want, want || 0);
  if (hist.busy) return;
  hist.busy = true;
  try{
    for (let n = 0; n < 4; n++){
      const reset = hist.want < hist.lastId;
      const q = hist.lastId && !reset ? {after: hist.lastId} : {limit: 50};
      const j = await C.api.history(q);
      if (!j) break;
      const fresh = j.turns.filter(x => x && typeof x.id === "number");
      const ep = typeof j.epoch === "number" ? j.epoch : 0;
      if (ep < hist.epoch) continue;
      if (ep > hist.epoch){ resetHistory(ep); hist.turns = fresh; }
      else if (reset || !hist.lastId) hist.turns = fresh;
      else {
        const byId = new Map(hist.turns.map(x => [x.id, x]));
        for (const x of fresh) byId.set(x.id, x);
        hist.turns = [...byId.values()].sort((a, b) => a.id - b.id);
      }
      hist.count = Math.max(j.count || 0, hist.turns.length); hist.firstId = j.first_id || (hist.turns[0] ? hist.turns[0].id : 0);
      hist.lastId = hist.turns.length ? hist.turns[hist.turns.length - 1].id : 0;
      pagesCache = null;
      if (hist.lastId >= hist.want) break;
    }
  }catch(_){}
  hist.busy = false;
  showPage();
  // P193 (B10): not the whole epoch — only until the newest page and the one before it are real (silent-only batches are
  // skipped); everything older loads on demand (goPage / goEdge / showTurn / the silent panel's 「更早」).
  while (olderLeft() && pages().filter(p => p.id !== null).length < PREFETCH_PAGES){ const b = await loadOlderBatch(); if (!b || !b.all) break; }
  showPage();
}
const PREFETCH_PAGES = 2;
function olderLeft(){ return hist.turns.length > 0 && hist.turns[0].id > hist.firstId; }
// One batch just older than the oldest loaded turn → {all, shown} (shown = how many
// of them are pages), or null when there is nothing more / it failed.
async function loadOlderBatch(){
  if (hist.older || !olderLeft()) return null;
  hist.older = true;
  try{
    const j = await C.api.history({before: hist.turns[0].id, limit: 50});
    // Older pages of a session that has since been cleared are not this session's.
    if (j && j.turns.length && (typeof j.epoch === "number" ? j.epoch : 0) === hist.epoch){
      const older = j.turns.filter(t => t && typeof t.id === "number" && t.id < hist.turns[0].id);
      hist.turns = older.concat(hist.turns); hist.firstId = j.first_id || hist.firstId;
      if (!older.length) hist.firstId = hist.turns[0].id;
      pagesCache = null;
      const shown = older.filter(visiblePage).length;
      if (!follow) pageAt += shown;
      hist.older = false;
      return {all: older.length, shown};
    }
  }catch(_){}
  hist.older = false;
  return null;
}
// Paging back: a batch of nothing but silent turns adds no page, so keep going.
async function loadOlder(){
  for (;;){
    const b = await loadOlderBatch();
    if (!b || !b.all) return false;
    if (b.shown) return true;
  }
}
function hhmm(ts){
  if (typeof ts !== "number") return "";
  const d = new Date((ts + skew) * 1000);
  return String(d.getHours()).padStart(2, "0") + ":" + String(d.getMinutes()).padStart(2, "0");
}
function currentPage(){ const ps = pages(); return ps[Math.min(ps.length - 1, Math.max(0, follow ? ps.length - 1 : pageAt))]; }
/** Who spoke, as the source card says it: the colour key and the label. */
function sourceOf(src){
  if (!src) return {kind: "leo", label: t('r.src.you')};
  // P179: another remote's label is its current one (the owner may have renamed it since), else the name the page was stamped with
  if (src.k === "phone") return src.dev && src.dev === C.myDev() ? {kind: "leo", label: t('r.src.you')}
    : {kind: "dev", label: (src.dev && deviceInfo(src.dev)?.label) || src.name || t('r.src.otherDevice')};
  if (src.k === "telegram") return {kind:"tg",label:"Telegram"};
  if (src.k === "email") return {kind:"tg",label:t('r.src.email', {name: src.name || ''})};   // P170: same quiet colour as Telegram
  if (src.k === "friend_report") return {kind:"sys", label:t('p498.friend', {name:src.name || ''})};
  if (src.k === "host") return {kind: "host", label: t('r.src.host')};
  // P132: a page the Agent started itself says why — a /loop or wake-up check, a background task, or just reporting in
  // P156: the host's own heartbeat check (its prompt is not shown — a folded label; a quiet beat has no page at all)
  if (src.k === "agent") return {kind: "agent", label: src.via === "heartbeat" ? t('r.src.heartbeat') : src.via === "quota" ? t('p314.src') : src.via === "hook" ? t('p366.src') : src.via === "loop" ? t('r.src.loop') : src.via === "task" ? t('r.src.bgTask')
    : src.via === "auto" ? t('p365.src.about', {name: src.name || ''})   // P365: the main Agent's own follow-up about an automation's message
    : t(src.text?.trim() ? 'r.src.agent' : 'r.src.agentReport', {name: C.agentName()})};
  if (src.k === "task") return {kind: "task", label: src.name ? t('r.src.taskNamed', {name: src.name}) : t('r.src.task')};
  if (src.k === "cmd") return {kind: "cmd", label: t('r.src.cmd')};
  // P365: the owner's own script / timer (`agentj notify --from <name>`): the quiet system colour, its registered name
  if (src.k === "auto") return {kind: "sys", label: t('p365.src.auto', {name: src.name || ''})};
  return {kind: "sys", label: src.local ? t('r.src.local') : t('r.src.sys')};
}
function showPage(){
  if (!C) return;
  const ps = pages();
  const arrived = newTurnArrived(ps);
  const oldId = shownKey === null ? null : shownKey.split("|")[0];
  const pinned = ps.findIndex(p => String(p.id === null ? "live" : p.id) === oldId);
  const tail = ps[ps.length - 1];
  if (pinned >= 0 && pinned < ps.length - 1){
    const onNewest = (follow || tailWait) && !rdAt;
    if (!onNewest){ pageAt = pinned; follow = false; tailWait = false; }               // paging history / reading full-screen
    else if (!visibleReply(tail)){ pageAt = pinned; follow = false; tailWait = true; }  // its reply is not here yet
    else { follow = true; tailWait = false; }                                           // P132: newest page → go to the new one
  }
  if (arrived && !follow) unreadReply = true;
  if (follow || pageAt < 0 || pageAt > ps.length - 1) {
    pageAt = ps.length - 1;
  }
  follow = pageAt === ps.length - 1;
  if (follow){ unreadReply = false; tailWait = false; }
  el("pgLatest").setAttribute("aria-label", t(unreadReply ? "r.newReply" : "r.pg.latest"));
  el("pgLatest").title = t(unreadReply ? "r.newReply" : "r.pg.latestHint");
  el("pgCorner").dataset.unread = unreadReply ? "1" : "0";
  const p = ps[pageAt];
  paintUnread(ps, p);
  const key = (p.id === null ? "live" : p.id) + "|" + pageAt;
  const om = el("om"), src = p.source;
  if (src?.notice_id && !document.hidden && isReady()) {
    const receiptKey = hostId() + ':' + src.notice_id;
    if (!noticeReads.has(receiptKey)) {
      noticeReads.add(receiptKey);
      sendApp({t:'notice_read', id:src.notice_id}).catch(() => noticeReads.delete(receiptKey));
    }
  }
  const blank = p.id === null && !p.reply;
  om.hidden = blank;
  const who = sourceOf(src);
  om.dataset.src = who.kind;
  const ic = el("srcIcons").content.querySelector(`[data-kind="${who.kind}"]`);
  el("omIcon").replaceChildren(ic ? ic.cloneNode(true) : "");
  el("omLabel").textContent = who.label;
  el("omTime").textContent = p.ts ? "· " + hhmm(p.ts) : "";
  const tx = src && typeof src.text === "string" ? src.text.trim() : "";
  const ot = el("omText");
  // P363 (#72): a voice-only message shows what was said (the computer's transcript) instead of 「（只有附件）」
  const heard = tx ? "" : transcriptOf(src), failed = !tx && !heard ? asrFailOf(src) : "";
  ot.textContent = tx || heard || failed || (src && src.att && src.att.length ? t('r.om.attOnly') : t('r.om.none'));
  ot.classList.toggle("none", !tx && !heard);
  ot.classList.toggle("asr", !tx && !!heard);
  if (tx) linkify(ot, true);   // P360: card links also in the owner's input
  // P132: no input of the owner's (the Agent reported in, a check or a background task) → the same card as a page header:
  // same place and size, the source label, the page capsule, both corner gestures and the new-message flash.
  const header = !tx && !src?.att?.length && !src?.quote;
  om.dataset.header = header ? "1" : "0";
  if (header) ot.textContent = t('r.om.noInput');
  paintOmAttachments(src);
  const q = src && src.quote ? src.quote : null;
  el("omQuote").hidden = !q;
  el("omQuote").dataset.id = q ? String(q.id) : "";
  el("omqWho").textContent = q ? (q.who || C.agentName()) : "";
  const qx = !!q && q.excerpt === true;
  el("omqMeta").textContent = q ? [q.ts ? hhmm(q.ts / 1000) : "", qx ? t('r.quote.excerptTag') : ""].filter(Boolean).map(x => " · " + x).join("") : "";
  el("omqText").textContent = q && typeof q.text === "string" ? (qx ? t('r.quote.wrap', {text: q.text}) : q.text) : "";
  // P286: quotes 2…5 of the same message, one row each (same look; a tap jumps to that page)
  const more = q && Array.isArray(q.more) ? q.more : [], box = el("omQuoteMore");
  box.hidden = !more.length;
  box.replaceChildren(...more.map(m => {
    const r = el("omQuote").cloneNode(true), mx = m.excerpt === true;
    r.removeAttribute("id"); r.hidden = false; r.dataset.id = String(m.id);
    for (const n of r.querySelectorAll("[id]")) n.removeAttribute("id");
    const [who, meta] = r.querySelectorAll(".qh>*");
    who.textContent = m.who || C.agentName();
    meta.textContent = [m.ts ? hhmm(m.ts / 1000) : "", mx ? t('r.quote.excerptTag') : ""].filter(Boolean).map(x => " · " + x).join("");
    r.querySelector(".qt").textContent = mx ? t('r.quote.wrap', {text: m.text}) : m.text;
    r.addEventListener("click", () => jumpToTurn(m.id));
    r.addEventListener("keydown", e => { if (e.key === "Enter" || e.key === " "){ e.preventDefault(); e.stopPropagation(); jumpToTurn(m.id); } });
    return r;
  }));
  paintQFold(p);
  const total = totalPages(), n = total - (ps.length - 1 - pageAt);
  el("pg").textContent = blank && total <= 1 ? "0 / 0" : n + " / " + total;
  el("rmTime").textContent = p.ts ? "· " + hhmm(p.ts) : "";
  el("rmPart").textContent = p.part ? t('r.part', {k: p.part[0], n: p.part[1]}) : "";
  el("pgPrev").disabled = pageAt === 0 && !(hist.turns.length && hist.turns[0].id > hist.firstId);
  el("pgNext").disabled = pageAt >= ps.length - 1;
  el("localNote").hidden = !(src && src.local);
  const queueList = queuedWatchList();
  queueStuck = queueWatch.check(queueList, hist.turns);
  paintCmdx(p);
  C.termCard?.(p);                                       // P414 (§37.12): a 「!」 page's terminal result card (js/bang.js)
  if (key !== shownKey){
    const moved = shownKey !== null && shownKey.split("|")[0] !== key.split("|")[0];
    shownKey = key;
    setOm(false);
    if (moved) stick = true;
  }
  const qd = p.end === "open" && queuedTurn(p.id);     // P127: accepted, waiting behind the running turn — say so
  document.body.dataset.pageQueued = qd ? "1" : "0";
  // P282 (追加): the working dots belong to the page being worked on. A closed page shown while another page is still open
  // (e.g. the Agent's 「切换已完成」 report landed after the owner's queued message) does not look busy itself.
  document.body.dataset.workElsewhere = p.end !== "open" && Number.isInteger(activeTurn) && activeTurn !== p.id
    && hist.turns.some(x => x.id === activeTurn && x.end === "open") ? "1" : "0";
  const ahead = qd ? queuedAhead(p.id) : 0;              // P152 (P149 plan B): 「前面还有 N 条」 when this device queued more than one
  // P281: what a running /switch-harness is doing (host card.progress on the open page) — the wait line itself while no
  // words came yet, else the robot line under them; the host's own wait lines (card.wait: 「正在等待 …」, card.queued: the
  // busy session's queue line) and the newest switch page (card.switch: 正在切换 / 已切回 …) start with the robot too
  const open = p.end === "open" && p.id !== null, c = p.card || {};
  const progress = open && typeof c.progress === "string" ? c.progress.trim().slice(0, 300) : "";
  const hostWait = open && (c.wait === true || c.queued === true) || (typeof c.switch === "string" && p === ps[ps.length - 1]);
  renderWords((isSilent(p.reply) ? "" : p.reply?.trim()) || (p.end === "open" || p.id === null ? "" : t('r.noReply')), open,
    qd ? t(queueStuck ? 'p481.queueStuck' : 'r.queue.card') + (ahead ? " · " + t('r.queue.ahead', {n: ahead}) : "") : progress, hostWait, sysPage(p), p.source?.k === "friend_report");
  paintThinking(visibleReply(p) && !hostWait ? progress : "");
  el("words").classList.toggle("empty", !visibleReply(p));
  renderMedia(el("words"), p);
  wireComponents(el("words"), replyBtn.agentWords(p) && p.end !== "open", text => appendText("\n" + text + "\n"));
  replyBtn.paint(el("words"), p, {pages, say: sayReply, find: search.find});   // P368
  if (rdAt && rdAt.key === shownKey) renderReaderMedia(el("rdWords"), p);   // P59: the reader's slots, same Blobs
  if (rdOpen()) rdSync();                                                   // P273: the reader follows every jump
  document.body.dataset.pageOpen = p.end === "open" ? "1" : "0";
  paintActs();
  if (!el("silent").hidden) renderSilent();
}
// A command result page (§8 cmd card → turn.card): the model buttons and 「撤销清空」 sit under the words.
function paintCmdx(p){
  const box = el("cmdx"), c = p.card;
  if (paintReportActions(box, p)) return;
  box.replaceChildren();
  if (p.source?.k === 'sys' && c?.recovery === 'restart_session') {
    box.hidden = false;
    const b = document.createElement('button'); b.type = 'button'; b.className = 'cmdundo';
    b.dataset.recovery = 'restart_session'; b.textContent = t('p435.session');
    b.addEventListener('click', reopenSession); box.append(b); return;
  }
  const isCmd = !!c && p.source && p.source.k === "cmd";
  const resume = isCmd && c.cmd === "clear" && c.resume && typeof c.resume.title === "string" && typeof c.resume.command === "string" ? c.resume : null;
  const cancelWait = isCmd && c.cancel_wait === true && p.end === "open" && p.source.dev === C.myDev();
  const openBtn = isCmd && typeof c.open === "string" && /^fr_(card|add)(:[A-Z0-9-]{1,40})?$/.test(c.open);   // P73
  // P151b: a phone turn the shared Claude has not ended after 5 min — 「继续等」 (another round) or 「中断」 (/stop)
  if (p.end === "open" && queuedTurn(p.id) && document.body.dataset.conn === "on" && !C.estopOn()){
    box.hidden = false;
    const stop = document.createElement("button"); stop.type = "button"; stop.className = "cmdundo";
    stop.dataset.queueStop = String(p.id); stop.textContent = t('p481.stopPrevious');
    stop.addEventListener("click", () => {
      if (queuedTurn(p.id) && document.body.dataset.conn === "on" && !C.estopOn()) stopTurn();
    });
    box.append(stop); return;
  }
  const stall = !isCmd && !!c && c.wait === true && c.stall === true && p.end === "open" && p.id !== null;
  if (stall){
    box.hidden = false;
    const more = document.createElement("button"); more.type = "button"; more.className = "cmdundo"; more.dataset.waitMore = String(p.id);
    more.textContent = t('r.wait.more');
    more.addEventListener("click", () => { more.disabled = true; sendApp({t: 'turn_wait', turn: p.id}).catch(() => { more.disabled = false; }); });
    const stop = document.createElement("button"); stop.type = "button"; stop.className = "cmdundo"; stop.dataset.waitStop = String(p.id);
    stop.textContent = t('r.wait.stop');
    stop.addEventListener("click", () => { stop.disabled = more.disabled = true; C.api.slash('stop'); });
    box.append(more, stop);
    return;
  }
  // P424 #75: the 「尚未接收」 notice sits on the owner's own page (host card.held); the settings diagnosis behind 「详情」
  // (collapsed, never in the words), then one 「已送达」 mark once the message reached the Agent after all
  const held = !isCmd && !!c && (c.held === "pending" || c.held === "delivered");
  if (held){
    if (c.held === "delivered"){
      const m = document.createElement("span"); m.className = "heldok"; m.dataset.held = "delivered"; m.textContent = t('p398.delivered');
      box.appendChild(m);
    } else if (typeof c.detail === "string" && c.detail.trim()){
      const d = document.createElement("details"); d.className = "cmdresume heldx";
      const summary = document.createElement("summary"); summary.textContent = t('p339.details');
      const body = document.createElement("p"); body.textContent = c.detail.trim().slice(0, 2000);
      d.append(summary, body); box.appendChild(d);
    }
    box.hidden = !box.children.length;
    return;
  }
  box.hidden = !isCmd || !((Array.isArray(c.models) && c.models.length) || c.undo === true || openBtn || cancelWait || resume);
  if (box.hidden) return;
  if (cancelWait){
    const b = document.createElement("button"); b.type = "button"; b.className = "cmdundo";
    b.textContent = t('sheet.cancel'); b.dataset.cmdCancel = String(p.id);
    b.addEventListener("click", () => { b.disabled = true; sendApp({t: 'cmd_cancel', turn: p.id}).catch(() => { b.disabled = false; }); });
    box.appendChild(b);
  }
  if (openBtn){
    const o = document.createElement("button"); o.type = "button"; o.className = "cmdopen"; o.dataset.open = c.open;
    o.textContent = t(c.open === "fr_card" ? 'cmd.openCard' : 'cmd.openAdd');
    o.addEventListener("click", () => openFromCmd(c.open));
    box.appendChild(o);
  }
  if (Array.isArray(c.models)){
    for (const x of c.models.slice(0, 40)){
      if (!x || typeof x.id !== "string") continue;
      const b = document.createElement("button"); b.type = "button"; b.className = "cmdmodel"; b.dataset.model = x.id;
      b.disabled = x.disabled === true;
      if (x.cur) b.setAttribute("aria-current", "true");
      const n = document.createElement("b"); n.textContent = (typeof x.name === "string" && x.name ? x.name : x.id) + (x.cur ? t('cmd.current') : "");
      const d = document.createElement("span"); d.textContent = [x.id, typeof x.desc === "string" ? x.desc : ""].filter(Boolean).join(" · ");
      b.append(n, d);
      b.addEventListener("click", () => { for (const o of box.querySelectorAll("button")) o.disabled = true; pillDrop(); C.api.slash("model", x.id); });
      box.appendChild(b);
    }
  }
  if (c.undo === true && p.id === newestUndoId()){
    if (undoExpired(p.id)){            // P280: the new conversation has begun — no button that can no longer work
      const g = document.createElement("span"); g.className = "cmdundogone"; g.textContent = t('cmd.undoGone');
      box.appendChild(g);
    } else {
      const u = document.createElement("button"); u.type = "button"; u.className = "cmdundo"; u.textContent = t('cmd.undo');
      u.addEventListener("click", () => { u.disabled = true; C.api.slash("undo_clear"); });
      box.appendChild(u);
    }
  }
  if (resume){
    const d = document.createElement("details"); d.className = "cmdresume";
    const summary = document.createElement("summary"); summary.textContent = resume.title;
    const pre = document.createElement("pre"), code = document.createElement("code"); code.textContent = resume.command;
    pre.appendChild(code); d.append(summary, copyable(pre)); box.appendChild(d);
  }
}
const newestUndoId = () => { for (let i = hist.turns.length - 1; i >= 0; i--){ const x = hist.turns[i]; if (x.card && (x.card.cmd === "clear" || x.card.cmd === "undo_clear")) return x.card.undo ? x.id : -1; } return -1; };
// P280: 「撤销清空」 lasts until the new conversation starts — the first message after the clear (phone, Telegram, desktop or the
// Agent's own) opens it, and the host refuses an undo from then on.
const NEW_TALK = new Set(["phone", "telegram", "host", "agent", "auto"]);   // P365: an automation's message reaches the Agent too
const undoExpired = (id) => hist.turns.some(x => x.id > id && x.source && NEW_TALK.has(x.source.k));
// P261: folded source cards show only a count; attachment cards exist only while open.
function paintOmAttachments(src = currentPage()?.source){
  const list = src?.att || [], open = el("om").classList.contains("open");
  const count = el("omAttCount"), att = el("omAtt");
  count.hidden = !list.length || open;
  count.querySelector("span").textContent = String(list.length);
  count.setAttribute("aria-label", t('r.om.attachments', {n: list.length}));
  el("om").classList.toggle("has-att", !!list.length);
  att.replaceChildren();
  if (open) for (const a of list){
    const s = document.createElement("span"); s.className = "omchip"; s.dataset.kind = a.kind; s.textContent = a.name; att.appendChild(s);
    if (a.asr) att.appendChild(asrBlock(a.asr));
  }
  att.hidden = !open || !list.length;
}
// P363 (#72): under a voice attachment of the open card, the computer's transcript — folded / unfolded with its own line
// (open by default), selectable (long press copies); a failed one says why. Every remote and every read-back get it (the
// host updates the page, PROTOCOL §10.5 Src.att[].asr).
const asrTexts = (src) => (src?.att || []).map(a => a.asr && a.asr.ok ? a.asr.text : "").filter(Boolean);
function transcriptOf(src){ return asrTexts(src).join("\n\n"); }
function asrFailOf(src){
  const bad = (src?.att || []).find(a => a.asr && !a.asr.ok);
  return bad ? t('p363.asrFail', {why: asrWhy(bad.asr.why)}) : "";
}
const asrWhy = (w) => { const k = 'p363.why.' + w, v = t(k); return v === k ? t('p363.why.broken') : v; };
function asrBlock(a){
  const d = document.createElement("details"); d.className = "omasr"; d.open = true;
  const sm = document.createElement("summary"); sm.textContent = t(a.ok ? 'p363.asrTitle' : 'p363.asrFailTitle');
  const p = document.createElement("p"); p.className = "omasr-text";
  p.textContent = a.ok ? a.text + (a.cut ? "\n" + t('p363.asrCut') : "") : t('p363.asrFail', {why: asrWhy(a.why)});
  d.append(sm, p);
  return d;
}
function setOm(open){
  const om = el("om");
  om.classList.toggle("open", open);
  el("deck").classList.toggle("open", open);
  el("omMore").setAttribute("aria-expanded", open ? "true" : "false");
  el("omMore").setAttribute("aria-label", open ? t('r.om.collapse') : t('r.om.expand'));
  el("omMore").firstElementChild.textContent = open ? t('r.om.collapseShort') : "…";
  paintOmAttachments();
  if (!open) el("deck").scrollTop = 0;
}
async function goPage(delta){
  let ps = pages();
  let at = (follow ? ps.length - 1 : pageAt) + delta;
  if (at < 0){
    follow = false; pageAt = 0;
    if (!(await loadOlder())){ if (ps.length === 1) follow = true; showPage(); return false; }
    ps = pages(); at = pageAt - 1;
  }
  if (at < 0 || at > ps.length - 1) return false;
  pageAt = at; follow = at === ps.length - 1; tailWait = false; shownKey = null;
  showPage();
  return true;
}
async function goEdge(newest){
  if (newest){ toNewest(); return; }
  follow = false; tailWait = false; pageAt = 0;
  while (await loadOlder()) {}
  pageAt = 0; shownKey = null; showPage();
}

// ---- P207 (追加 2): 未读 — replies that came after the last one this device saw in the foreground ----------------------
// The input card's header is three equal tap zones: left = earliest (double tap), middle = first unread (tap; a round
// badge with the count, flashing like the page number's new-message pulse), right = newest (double tap / tap while it
// flashes). Logo hold and `u` do the same as the middle. Seeing a page = it is on screen while the page is visible.
const unread = createUnread();
export function ownerRead(m) {
  unread.bind(hostId());
  if (m.epoch === hist.epoch && unread.owner(m.epoch, m.read)) showPage();
}
// P321: every remote's count follows the host's mark; a receipt the host has not confirmed is sent again (a few times
// per id, at most every READ_RETRY_MS) so one lost frame cannot leave the other remotes a reply behind.
const READ_RETRY_MS = 4000;
let readSentAt = 0, readTimer = null;
function sendRead(){
  const m = unread.pending();
  if (m === null) return;
  readSentAt = Date.now(); C.api.readHistory(hist.epoch, m);
  if (!readTimer) readTimer = setTimeout(() => { readTimer = null; if (unread.isOwner()) sendRead(); }, READ_RETRY_MS);
}
function retryRead(){ if (!readTimer && Date.now() - readSentAt >= READ_RETRY_MS) sendRead(); }
const UNREAD_FILL_BATCHES = 10;
let unreadFilling = false;
const replyIds = ps => ps.filter(x => x.id !== null && visibleReply(x)).map(x => x.id);
function unreadCovered(){
  const m = unread.mark();
  return !olderLeft() || (m !== null && hist.turns.length > 0 && hist.turns[0].id <= m + 1);
}
function paintUnread(ps, p){
  const ids = replyIds(ps);
  if (!historySync) unread.sync(hist.epoch, ids);
  unread.arrivals(ids, document.visibilityState === "visible");   // P333: also while the head is loading (a live turn is live)
  const covered = unreadCovered();
  if (!historySync && p && p.id !== null && visibleReply(p) && document.visibilityState === "visible" && document.body.dataset.view === "chat")
    { unread.look(p.id); if (unread.see(p.id, ids, covered) && unread.isOwner()) sendRead(); }
  else unread.advance(ids, covered);
  if (!historySync && unread.isOwner()) retryRead();
  // P333: another remote's receipt clears what it read, never this remote's own new-message alert (pulse + badge)
  const al = historySync ? [] : unread.alerts(ids), n = al.length;
  // P333: a return to the foreground (the head fetch) put the pulse out; a newer reply still new here lights it again
  if (!historySync && !follow && p && p.id !== null && unread.newer(ids, p.id)) unreadReply = true;
  if (historySync || (unread.isOwner() && !n)) unreadReply = false;
  el("pgCorner").dataset.unread = unreadReply ? "1" : "0";
  el("pgLatest").setAttribute("aria-label", t(unreadReply ? "r.newReply" : "r.pg.latest"));
  el("pgLatest").title = t(unreadReply ? "r.newReply" : "r.pg.latestHint");
  const b = el("omUnread");
  el("omUnreadN").textContent = n > 99 ? "99+" : String(n);
  b.dataset.n = String(n);
  el("om").dataset.unread = n ? "1" : "0";
  b.setAttribute("aria-label", n ? t('r.unread.badge', {n}) : t('r.unread.none'));
  b.title = n ? t('r.unread.badge', {n}) : "";
  b.setAttribute("aria-disabled", n ? "false" : "true");
  b.tabIndex = n ? 0 : -1;
  if (!covered && !unreadFilling && unread.mark() && !historySync) fillUnread();
}
// The mark is older than what is loaded: fetch older batches (≤ 10 × 50 turns) so the count is whole.
async function fillUnread(){
  unreadFilling = true;
  try { for (let i = 0; i < UNREAD_FILL_BATCHES && !unreadCovered(); i++){ const b = await loadOlderBatch(); if (!b || !b.all) break; } }
  finally { unreadFilling = false; }
  showPage();
}
async function goFirstUnread(tell){
  for (let i = 0; i < UNREAD_FILL_BATCHES * 2 && !unreadCovered(); i++){ const b = await loadOlderBatch(); if (!b || !b.all) break; }
  const ps = pages(), first = unread.alerts(replyIds(ps))[0];
  const at = first === undefined ? -1 : ps.findIndex(x => x.id === first);
  if (at < 0){ if (tell) toast(t('r.unread.none'), 1600); return false; }
  pageAt = at; follow = at === ps.length - 1; tailWait = false; shownKey = null; stick = true;
  showPage();
  return true;
}

// Copy: y the reply, Y the input. What is copied is the stored text, not the rendering.
function copyOut(text, doneMsg, button){
  if (!text){ toast(t('r.copy.nothing'), 1600); return; }
  copyButton(text, button);
}
function copyReply(){ copyOut(currentPage().reply || "", t('r.copy.reply'), el('copyReply')); }
// P363 (#73): the input card's copy button and Y — the owner's words; a voice note's transcript after them (voice only: the transcript)
function copySource(){ const p = currentPage(), s = p && p.source; copyOut([(s && s.text || "").trim(), transcriptOf(s)].filter(Boolean).join("\n\n"), t('r.copy.source'), el('omCopy')); }

// ---- reply actions: 阅读 · 朗读 · 分享 · 复制 · 回复 (relay ADR-037) ---------------------
// Each acts on a turn by its id — never a page number.
// P286 (Leo 10-14): up to five quotes above the field, oldest first — each selection adds one, 回复 adds the whole page;
// the sixth is refused (never drops an older one). A new array on every change: a send compares identity.
const QMAX = 5;
let quotes = [];             // [{id, kind, speaker, ts, line[, excerpt, copied]}]
const quoted = (id, ex) => quotes.some(q => q.id === id && (q.excerpt ?? null) === (ex ?? null));
function addQuote(q){
  if (quoted(q.id, q.excerpt)) return "dup";
  if (quotes.length >= QMAX) return "full";
  quotes = [...quotes, q]; paintReply(); paintActs();
  return "ok";
}
const pageQuote = (p, excerpt) => ({id: p.id, kind: sourceOf(p.source).kind, speaker: C.agentName(), ts: p.ts, line: plainLine(p.reply),
  ...(excerpt == null ? {} : {excerpt, copied: false})});
function paintActs(){
  const p = currentPage(), id = p.id;
  el("readBtn").disabled = !(p.reply || "").trim();
  el("replyBtn").disabled = id === null || !C.connected();
  el("replyBtn").classList.toggle("on", quotes.some(q => q.id === id));
  el("copyReply").disabled = !(p.reply || "").trim();
  // P285: a draft (web report / a skill) needs the reply's words, like 阅读 / 复制; a command needs the connection
  el("webReportBtn").disabled = ["report", "skill"].includes(hotKind()) ? !(p.reply || "").trim() : !C.connected();
  paintSpeak(); paintFwd(); paintMail();
  fitBar();
}
// P340 (Leo 10-16 #35): a sent message with two to five quotes shows them folded into one row — 「引用 5 段」, the first
// quote's words, 「+4」 — the look of the quote strip above the field before sending. A tap opens them all (each row still
// jumps to its page), another tap folds them again. One quote stays as it was. Every page opens folded.
let qOpen = null;
export function qFoldWords(q, more){
  if (!q) return null;
  const n = 1 + (Array.isArray(more) ? more.length : 0);
  if (n < 2) return null;
  return {n, head: t('p340b.qfold.head', {n}), text: String(q.text || "").replace(/\s+/g, " ").trim(), more: "+" + (n - 1)};
}
function paintQFold(p){
  const om = el("om"), b = el("omQuoteFold");
  const q = p && p.source && p.source.quote ? p.source.quote : null, f = qFoldWords(q, q && q.more);
  b.hidden = !f;
  if (!f){ delete om.dataset.qfold; return; }
  const open = p.id !== null && qOpen === p.id;
  om.dataset.qfold = open ? "0" : "1";
  b.setAttribute("aria-expanded", open ? "true" : "false");
  b.setAttribute("aria-label", t(open ? 'p340b.qfold.close' : 'p340b.qfold.open'));
  b.title = b.getAttribute("aria-label");
  el("omqfHead").textContent = open ? f.head + " · " + t('p340b.qfold.close') : f.head;
  el("omqfText").textContent = f.text;
  el("omqfN").textContent = open ? "" : f.more;
}
function toggleQFold(){
  const p = currentPage();
  if (!p || p.id === null) return;
  qOpen = qOpen === p.id ? null : p.id;
  paintQFold(p);
}
// P251: equal-width hit regions; compress empty space before shrinking every glyph together.
// P340 (#38): the group is right-packed, glyph gap = the last glyph's right margin (--rm-gap, app.css). Only when the row is
// too narrow for that does this narrow the gap (to 12 px), then the glyphs (21 → 18 px), then the gap again (to 6 px).
export function barFit(room, n, base){
  const I = 21, need = (i, g) => n * (i + g) + g;
  if (!(room > 0) || n < 1 || need(I, base) <= room) return {icon: I, gap: base};
  let g = (room - n * I) / (n + 1);
  if (g >= 12) return {icon: I, gap: g};
  const i = (room - 12 - 12 * n) / n;
  if (i >= 18) return {icon: i, gap: 12};
  return {icon: 18, gap: Math.max(6, (room - 18 * n) / (n + 1))};
}
function fitBar(){
  const group = el("rmIcons"), bar = el("rmbar");
  if (!group || !bar) return;
  const count = [...group.querySelectorAll(".act")].filter(b => !b.hidden).length;
  group.style.setProperty("--rm-count", count);
  const br = bar.getBoundingClientRect();
  if (!br.width) return;
  const wr = el("webReportBtn"), cs = getComputedStyle(bar);
  // from the bar's own box, not wr's current place: an overflowing row (before this fit lands) pushes wr left in WebKit
  const wcs = wr && !wr.hidden && wr.getClientRects().length ? getComputedStyle(wr) : null;
  const from = br.left + parseFloat(cs.paddingLeft || "0") + (wcs ? wr.offsetWidth + parseFloat(wcs.marginLeft || "0") + parseFloat(wcs.marginRight || "0") : 0);
  bar.style.removeProperty("--rm-gap");
  const base = 2 * parseFloat(getComputedStyle(bar).paddingRight) || 16;   // the padding is half the gap, resolved to px
  const f = barFit(br.right - from, count, base);
  if (f.gap !== base) bar.style.setProperty("--rm-gap", f.gap.toFixed(2) + "px");
  group.style.setProperty("--rm-icon", f.icon.toFixed(2) + "px");
}
// P216 (Leo 10-13): 分享成网页报告 — puts the web-page-report skill's command at the front of the field, cursor after it,
// never sends. The owner may add which part / who should see it, or send it bare and the Agent asks (the skill's own
// 「先问」 step). A card that is not the newest also becomes the quote (the same reply_to as 回复), so the Agent knows
// where the share starts. An unknown slash reaches the Agent as text (Claude Code, measured), so a host without the
// package still gets the request and offers to install it from the plaza.
const WR_CMD = "/web-page-report";
function webReport(p){ slashOn(p, WR_CMD, t('r.wr.hint'), t('r.wr.hintFrom')); }
// P285: the same for any skill the hot key is bound to — `/<skill> ` at the front of the field (never doubled), an older card
// quoted, never sent: the owner adds a line, and the Agent runs it on that reply.
function slashOn(p, cmd, hint, hintFrom){
  if (!p || !(p.reply || "").trim()) return;
  const ps = pages(), i = ps.indexOf(p);
  const older = p.id !== null && i >= 0 && ps.slice(i + 1).some(x => x.id !== null);
  if (older && !quotes.some(q => q.id === p.id) && addQuote(pageQuote(p)) === "full") toast(t('r.quote.max'), 2600);
  const re = new RegExp("^[/\\uff0f]" + cmd.slice(1).replace(/[.*+?^${}()|[\]\\]/g, "\\$&") + "(?:[ \\t]+|$)", "i");
  const rest = input.value.replace(re, "").replace(/^\s+/, "");
  input.value = cmd + " " + rest;
  paintActs();
  focusComposer();
  input.setSelectionRange(input.value.length, input.value.length);
  autosize(); refreshSend(); saveDraft();
  toast(older ? hintFrom : hint, 5200);
}
// P285: one tap on the hot key. A command does exactly what its row in the ≡ list does (/clear: confirmSheet first, the same
// danger confirmation as typing it — a hot key never skips it); /customize and /switch-harness are sent as their rows send them.
export async function hotTap(p = currentPage()){
  const n = hotName(), k = hotKind(n);
  if (k === "report") return webReport(p);
  if (k === "run" || k === "clear"){ if (await runCmd(n)){ toNewest(); executionToast(t('r.run.sent', {cmd: "/" + n}), 1600); } return; }
  if (n === "customize") return sayPreset("cmds");
  if (n === "switch-harness") return saySwitch();
  const cmd = "/" + n;
  slashOn(p, cmd, t('p285.hint', {cmd}), t('p285.hintFrom', {cmd}));
}
export { openHot };
// P320: 「重新加载配置」 — the host re-reads its configuration and key file without restarting serve (≡ row, /reload, Alt+Shift+L,
// the settings page's Reload notice). The answer is the /reload card in the chat.
export async function reloadHost(){
  if (hostMenu && !hostMenu.reload){ toast(t('p320.reloadOld'), 4000); return false; }
  const ok = await runCmd("reload");
  if (ok){ toNewest(); executionToast(t('r.run.sent', {cmd: "/reload"}), 1600); }
  return ok;
}
function plainLine(md){
  for (let ln of String(md || "").split("\n")){
    ln = ln.trim();
    if (!ln || /^(```|~~~)/.test(ln) || /^([-*_]\s*){3,}$/.test(ln) || /^\|?[\s:|-]+\|[\s:|-]*$/.test(ln)) continue;
    ln = ln.replace(/^#{1,6}\s+/, "").replace(/^(>\s?)+/, "").replace(/^([-*+]|\d+[.)])\s+(\[[ xX]\]\s+)?/, "")
           .replace(/!?\[([^\]]*)\]\([^)]*\)/g, "$1").replace(/\*\*|__|~~|`/g, "").replace(/\*([^*\s][^*]*)\*/g, "$1")
           .replace(/^\||\|$/g, "").replace(/\s*\|\s*/g, "  ").trim();
    if (ln) return ln;
  }
  return "";
}
function startReply(p){
  if (!p || p.id === null) return;
  if (addQuote(pageQuote(p)) === "full") toast(t('r.quote.max'), 2600);
  focusComposer();
  input.setSelectionRange(input.value.length, input.value.length);
}
// a chip's source tag: time · Agent, just the time from three chips on (the words need the room); no 「已复制」 here
const chipTag = q => (quotes.length > 2 ? [hhmm(q.ts) || q.speaker] : [hhmm(q.ts), q.speaker]).filter(Boolean).join(" · ");
const quoteWords = q => typeof q.excerpt === "string" ? excerptLine(q.excerpt) : (q.line || t('r.quote.noText'));
function paintReply(){
  const bar = el("qbar"), n = quotes.length, q = quotes[0];
  bar.hidden = !n;
  const old = [...bar.querySelectorAll(".qc")], key = q => q.id + "\n" + (q.excerpt ?? "");
  // the same quotes (only 「已复制」 changed, e.g. a clipboard write landing during a tap): update in place, never swap the
  // chip under the finger
  if (n > 1 && old.length === n && old.every((c, i) => c.dataset.k === key(quotes[i]))){
    old.forEach((c, i) => { c._q = quotes[i]; c.firstChild.textContent = chipTag(quotes[i]); });
    return;
  }
  for (const c of old) c.remove();
  if (n > 1) bar.dataset.n = String(n); else delete bar.dataset.n;
  const x1 = el("qbX");
  x1.setAttribute("aria-label", t(n > 1 ? 'r.quote.clearAll' : 'r.quote.cancel')); x1.title = x1.getAttribute("aria-label");
  if (!n) return;
  bar.style.setProperty("--qc", "var(--src-" + q.kind + ")");
  if (n > 1){                                       // P286: one chip each — source tag (jump) · first words (full text) · ×
    const list = el("qbList");
    quotes.forEach((q, i) => {
      const c = document.createElement("div"), tag = document.createElement("button"), w = document.createElement("button"), x = document.createElement("button");
      c.className = "qc"; c.style.setProperty("--qc", "var(--src-" + q.kind + ")"); c.dataset.k = key(q); c._q = q;
      tag.type = w.type = x.type = "button";
      tag.className = "qs"; tag.textContent = chipTag(q);
      tag.setAttribute("aria-label", t('r.quote.jumpSrc') + " · " + [hhmm(q.ts), q.speaker].filter(Boolean).join(" · "));
      w.className = "qw"; w.textContent = typeof q.excerpt === "string" ? q.excerpt.replace(/\s+/g, " ").trim() : (q.line || t('r.quote.noText')); w.setAttribute("aria-label", t('r.quote.open'));
      x.className = "qcx"; x.setAttribute("aria-label", t('r.quote.remove')); x.title = t('r.quote.remove');
      x.append(el("qbX").querySelector("svg").cloneNode(true));
      for (const b of [tag, w, x]) b.addEventListener("pointerdown", e => e.preventDefault());   // the field keeps the focus
      tag.addEventListener("click", () => jumpToQuote(c._q));   // @ix compose.quote_chip
      w.addEventListener("click", () => openQuote(c._q));
      x.addEventListener("click", () => removeQuote(c._q));
      c.append(tag, w, x); list.appendChild(c);
    });
    return;
  }
  const tm = hhmm(q.ts), head = el("qbHead"), x = typeof q.excerpt === "string";
  head.replaceChildren(t(x ? 'r.quote.headEx' : 'r.quote.head', {who: q.speaker}));
  const meta = (tm ? " · " + tm : "") + (x && q.copied ? " · " + t('r.quote.copied') : "");
  if (meta){ const s = document.createElement("span"); s.textContent = meta; head.appendChild(s); }
  el("qbText").textContent = quoteWords(q);
}
function removeQuote(q){ quotes = quotes.filter(x => !(x.id === q.id && (x.excerpt ?? null) === (q.excerpt ?? null))); paintReply(); paintActs(); if (!quotes.length) focusComposer({preventScroll: true}); }
function cancelReply(){ if (!quotes.length) return; quotes = []; paintReply(); paintActs(); }
// The full words of one quote (the chips only show the first characters), its source, 跳到原文 / 删掉
let qdlgAt = null, qdlgFrom = null;
function openQuote(q){
  qdlgAt = q; qdlgFrom = document.activeElement;
  el("quoteDlgSrc").textContent = [hhmm(q.ts), q.speaker].filter(Boolean).join(" · ");
  el("quoteDlgText").textContent = typeof q.excerpt === "string" ? q.excerpt : (q.line || t('r.quote.noText'));
  el("quoteDlg").hidden = false;
  el("quoteDlgJump").focus({preventScroll: true});
}
function closeQuote(){
  if (el("quoteDlg").hidden) return;
  el("quoteDlg").hidden = true; qdlgAt = null;
  if (qdlgFrom && qdlgFrom.isConnected) focusDraft(qdlgFrom, {preventScroll: true}); else focusComposer({preventScroll: true});
}
// 跳回原卡并高亮该段: the page by its id, then the excerpt marked with the CSS Highlight API (a selection would quote it again)
async function jumpToQuote(q){
  if (rdOpen()) closeReader();
  await jumpToTurn(q.id);
  if (typeof q.excerpt !== "string" || currentPage().id !== q.id) return;
  requestAnimationFrame(() => markExcerpt(el("words"), q.excerpt));
}
let markT = 0;
function markExcerpt(root, ex){
  const H = window.CSS && CSS.highlights, nodes = [], w = document.createTreeWalker(root, NodeFilter.SHOW_TEXT);
  let all = "";
  for (let n; (n = w.nextNode());){ if (n.parentElement && n.parentElement.closest("button")) continue; nodes.push([n, all.length]); all += n.data; }
  // whitespace differs between the rendering and the selection's toString(): match on the words, map back to node offsets
  const sq = [], map = [];
  for (let i = 0; i < all.length; i++) if (!/\s/.test(all[i])){ sq.push(all[i]); map.push(i); }
  const needle = ex.replace(/\s+/g, ""), at = needle ? sq.join("").indexOf(needle) : -1;
  if (at < 0) return false;
  const pos = i => { for (let k = nodes.length - 1; k >= 0; k--) if (nodes[k][1] <= i) return [nodes[k][0], i - nodes[k][1]]; return null; };
  const a = pos(map[at]), b = pos(map[at + needle.length - 1]);
  if (!a || !b) return false;
  const r = document.createRange();
  r.setStart(a[0], a[1]); r.setEnd(b[0], b[1] + 1);
  const box = r.getBoundingClientRect(), mr = mainEl.getBoundingClientRect();
  if (box.height && (box.top < mr.top || box.bottom > mr.bottom)) mainEl.scrollTop += box.top - mr.top - mr.height / 3;
  if (!H || typeof Highlight !== "function") return true;
  H.set("aj-quote", new Highlight(r));
  clearTimeout(markT); markT = setTimeout(() => H.delete("aj-quote"), 2600);
  return true;
}
async function jumpToTurn(id){
  if (typeof id !== "number" || !(id > 0)) return;
  while (!hist.turns.some(p => p.id === id) && olderLeft() && hist.turns[0].id > id){
    const b = await loadOlderBatch(); if (!b || !b.all) break;
  }
  if (hist.turns.some(p => p.id === id && isSilent(p.reply))){ openSilent(id); return; }
  const i = pages().findIndex(p => p.id === id);
  if (i < 0){ toast(t('r.quote.gone'), 2200); return; }
  pageAt = i; follow = i === pages().length - 1; tailWait = false; shownKey = null; showPage();
}

// ---- select to quote (relay ADR-046; §10.6 excerpt) ---------------------------------------
const SEL_IDLE_MS = 450, EXCERPT_MAX = 2000;
let selPtr = false, selTimer = 0, selLast = null, selPending = null;
function wordsSelection(){
  const s = document.getSelection ? document.getSelection() : null;
  if (!s || !s.rangeCount || s.isCollapsed) return null;
  const w = rdOpen() ? el("rdWords") : el("words");     // P286: selecting in the full-screen reader quotes too
  const inside = n => { const e = n && (n.nodeType === 1 ? n : n.parentElement); return !!e && w.contains(e) && !e.closest("button"); };
  if (!inside(s.anchorNode) || !inside(s.focusNode)) return null;
  const tx = s.toString().trim();
  return tx ? tx.slice(0, EXCERPT_MAX) : null;
}
function excerptLine(x){
  const ls = String(x).split("\n").map(l => l.trim()).filter(Boolean);
  const l = ls[0] || "";
  return t('r.quote.wrap', {text: l.length > 80 ? l.slice(0, 80) + "…" : l + (ls.length > 1 ? "…" : "")});
}
function selCopied(text){
  if (selPending === text) selPending = null;
  const i = quotes.findIndex(q => q.excerpt === text && !q.copied);
  if (i >= 0){ quotes = quotes.map((q, k) => k === i ? {...q, copied: true} : q); paintReply(); }
}
async function selCopy(text){
  const cb = navigator.clipboard, ua = navigator.userActivation;
  if (ua && !ua.isActive){ selPending = text; return false; }
  let ok = false;
  if (cb && typeof cb.writeText === "function"){ try{ await cb.writeText(text); ok = true; }catch(_){} }
  if (!ok && wordsSelection() === text){ try{ ok = !!document.execCommand("copy"); }catch(_){ ok = false; } }
  if (ok) selCopied(text); else selPending = text;
  return ok;
}
const selFocus = type => type ? type === "mouse" : matchMedia("(hover: hover) and (pointer: fine)").matches;
async function selCommit(type){
  clearTimeout(selTimer); selTimer = 0;
  if (performance.now() < rdMuteUntil) return;
  const text = wordsSelection();
  if (!text) return;
  const p = currentPage(), id = p ? p.id : null;
  if (selLast && selLast.id === id && selLast.text === text) return;
  if (id !== null && quoted(id, text)) return;
  selLast = {id, text};
  // P286: added, never replacing; the clipboard still gets this latest selection (also when the five are full)
  const r = id !== null ? addQuote(pageQuote(p, text)) : null;
  if (r === "full") toast(t('r.quote.max'), 2600);
  else if (r === "ok" && rdOpen()) toast(t('r.quote.added', {i: quotes.length, n: QMAX}), 1600);   // the strip is under the reader
  await selCopy(text);
  if (r === "ok" && !rdOpen() && quoted(id, text) && selFocus(type)){
    focusComposer({preventScroll: true});
    input.setSelectionRange(input.value.length, input.value.length);
  }
}
function selRetry(){
  const tx = selPending;
  if (!tx) return;
  const cb = navigator.clipboard;
  if (cb && typeof cb.writeText === "function") cb.writeText(tx).then(() => selCopied(tx), () => {});
  else if (wordsSelection() === tx){ try{ if (document.execCommand("copy")) selCopied(tx); }catch(_){} }
}

// ---- 朗读: the phone's own voices (§10.14) ---------------------------------------------------
let speaker = null;
const speakSt = new Map();
function paintSpeak(){
  if (!speaker) return;
  const b = el("speakBtn"), id = currentPage().id, st = id === null ? "idle" : (speakSt.get(id) || "idle");
  b.disabled = id === null || !(currentPage().reply || "").trim();
  b.dataset.state = st;
  b.setAttribute("aria-label", t('r.speak.' + st)); b.title = t('r.speak.' + st);
}
function speakTap(){
  const p = currentPage();
  if (p.id === null) return;
  speaker.tap(p.id, p.reply || "");
}

// ---- 分享 / 转发: with the host's `tgfwd` one tap → the owner's own Telegram (relay /forward, P118 B10); else the phone's
//      share sheet, else copy (§10.14) -----------
const fwdSt = new Map(), fwdTimers = new Map();
function setFwd(id, st, ms){
  clearTimeout(fwdTimers.get(id));
  if (st === "idle") fwdSt.delete(id); else fwdSt.set(id, st);
  if (ms) fwdTimers.set(id, setTimeout(() => { if (fwdSt.get(id) === st) setFwd(id, "idle"); }, ms));
  paintFwd();
}
function paintFwd(){
  const b = el("fwdBtn"), p = currentPage(), id = p.id, st = id === null ? "idle" : (fwdSt.get(id) || "idle");
  b.disabled = id === null || st === "sending" || !(p.reply || "").trim();
  b.dataset.state = st;
  const k = (peer.tgfwd ? 'r.tg.' : 'r.share.') + st;
  b.dataset.tg = peer.tgfwd ? "1" : "";
  b.setAttribute("aria-label", t(k)); b.title = t(k);
}
async function forwardTap(){
  const p = currentPage(), id = p.id;
  if (id === null) return;
  if (fwdSt.get(id) === "sending") return;
  const text = (p.reply || "").trim();
  if (!text) return;
  setFwd(id, "sending");
  if (peer.tgfwd){
    const r = await C.api.tgForward(id);
    if (r.ok){ setFwd(id, "sent", 2000); toast(t('r.tg.sent'), 2000); return; }
    if (r.why === "rate_limited" || r.why === "recently_sent" || r.why === "in_flight"){
      setFwd(id, "idle"); toast(t(r.why === "rate_limited" ? 'r.tg.limited' : 'r.tg.again'), 2600); return;
    }
    if (r.why !== "not_configured"){ setFwd(id, "failed", 3000); toast(t('r.tg.failed'), 2600); return; }
    peer.tgfwd = false;                 // Telegram was switched off since `ready`: the share sheet from now on
  }
  if (navigator.share){
    try{ await navigator.share({text}); setFwd(id, "sent", 2000); return; }
    catch(e){ if (e && e.name === "AbortError"){ setFwd(id, "idle"); return; } }
  }
  const ok = await copyButton(text, el(rdOpen() ? 'rdShareBtn' : 'shareBtn'));
  setFwd(id, ok ? "sent" : "failed", ok ? 2000 : 3000);
}

// ---- 转发到邮箱 (P189): the whole turn — the owner's message, this reply, its files — through P184's dialog (contacts.js,
//      host cap `contacts`): 发给我自己 by default, or people from the address book; the dialog can leave the message out.
function paintMail(){
  const b = el("mailBtn"), p = currentPage(), fw = window.AgentJMailForward;
  b.hidden = !(fw && fw.available());
  b.disabled = p.id === null || !(p.reply || "").trim() || !C.connected();
}
function mailTap(){
  const p = currentPage(), fw = window.AgentJMailForward;
  if (p.id === null || !fw) return;
  fw.open({ turn: p.id, whole: true });
}

// ---- the full-screen reader (relay ADR-049) ----------------------------------------------
var RD_SIZES = [14, 16, 18, 20, 24, 28, 32], RD_MIN = 14, RD_MAX = 32, RD_DEFAULT = 20, RD_KEY = "aj.readerFs";
// P260: file cards use the same double-tap bounds as the reply reader.
var rdFs = RD_DEFAULT, rdAt = null, rdGuard = -1e9, rdMuteUntil = 0, rdPushed = false, rdSkipPop = false, rdPinch = null, rdPinchAt = -1e9, rdPtr = "";
try{ const v = Number(localStorage.getItem(RD_KEY)); if (v >= RD_MIN && v <= RD_MAX) rdFs = Math.round(v); }catch(_){}
function rdSave(){ try{ localStorage.setItem(RD_KEY, String(rdFs)); }catch(_){} }
function rdOpen(){ return !el("rd").hidden; }
function rdSetFs(v, save){
  v = Math.min(RD_MAX, Math.max(RD_MIN, Math.round(v)));
  const sc = el("rdScroll"), max = sc.scrollHeight - sc.clientHeight, at = max > 0 ? sc.scrollTop / max : 0;
  rdFs = v;
  el("rd").style.setProperty("--rd-fs", v + "px");
  const sl = el("rdSlider"); sl.value = String(v); sl.setAttribute("aria-valuetext", t('r.rd.px', {n: v}));
  el("rdFsN").textContent = String(v);
  el("rdMinus").disabled = v <= RD_MIN; el("rdPlus").disabled = v >= RD_MAX;
  const m2 = sc.scrollHeight - sc.clientHeight;
  if (max > 0 && m2 > 0) sc.scrollTop = at * m2;
  if (save) rdSave();
}
function rdStep(dir){
  const next = dir > 0 ? RD_SIZES.find(x => x > rdFs) : RD_SIZES.slice().reverse().find(x => x < rdFs);
  if (next !== undefined) rdSetFs(next, true);
}
function openReader(){
  if (rdOpen()) return;
  const p = currentPage(), tx = (p && p.reply || "").trim();
  if (!tx) return;
  rdGuard = performance.now(); rdMuteUntil = rdGuard + 600;
  try{ const s = getSelection(); if (s && !s.isCollapsed) s.removeAllRanges(); }catch(_){}
  const max = mainEl.scrollHeight - mainEl.clientHeight;
  rdAt = {key: shownKey, scroll: mainEl.scrollTop, text: tx};
  fill(el("rdWords"), tx, p.source?.k === "friend_report", sysPage(p));
  renderReaderMedia(el("rdWords"), p);                 // P59 (ADR-A164): local pictures / files in place, not their Markdown
  rdHead();
  if (document.activeElement === input) input.blur();
  const rd = el("rd");
  rd.hidden = false; rd.dataset.modalOpen = "1"; document.body.dataset.reader = "1";
  rdSetFs(rdFs, false);
  const sc = el("rdScroll"), m2 = sc.scrollHeight - sc.clientHeight;
  sc.scrollTop = max > 0 && m2 > 0 ? mainEl.scrollTop / max * m2 : 0;
  try{ history.pushState({ajReader: 1}, ""); rdPushed = true; }catch(_){ rdPushed = false; }
}
let rdPaging = false;
const rdHasText = p => !!(p && (p.reply || "").trim() && !isSilent(p.reply));
function rdBounce(dir){
  const sc = el('rdScroll');
  if (!matchMedia('(prefers-reduced-motion: reduce)').matches)
    sc.animate([{transform:'translateX(0)'},{transform:`translateX(${dir < 0 ? 10 : -10}px)`},{transform:'translateX(0)'}],{duration:180});
  toast(t(dir < 0 ? 'r.rd.first' : 'r.rd.last'), 1400);
}
async function rdPage(dir){
  if (!rdOpen() || rdPaging) return;
  rdPaging = true;
  try {
    const before = rdAt?.key, beforeId = currentPage()?.id;
    let moved = await goPage(dir);
    while (moved && !rdHasText(currentPage())) moved = await goPage(dir);
    if (!rdOpen()) return;
    if (!moved && !rdHasText(currentPage())) {
      const i = pages().findIndex(p => p.id === beforeId);
      if (i >= 0) { pageAt=i; follow=i===pages().length-1; shownKey=null; showPage(); }
    }
    if (rdAt?.key === before) rdBounce(dir);      // showPage → rdSync painted the new reply; nothing moved = an end
  } finally { rdPaging = false; }
}
// P273: the header's corners in the reader — double tap the Agent / time = the first reply, double tap the page number =
// the newest, the unread badge = the first unread. Each lands on a page that has words (empty pages are stepped over).
async function rdJump(where){
  if (!rdOpen() || rdPaging) return;
  rdPaging = true;
  try {
    const before = rdAt?.key;
    if (where === "first") await goEdge(false);
    else if (where === "last") toNewest();
    else if (!(await goFirstUnread(false))) return;
    const dir = where === "last" ? -1 : 1;
    for (let i = 0; i < 500 && rdOpen() && !rdHasText(currentPage()); i++) if (!(await goPage(dir))) break;
    if (rdOpen() && rdAt?.key === before && where !== "unread") rdBounce(where === "first" ? -1 : 1);
  } finally { rdPaging = false; }
}
// The header: the Agent's name, the reply's time, the page number (newest = brighter; a newer reply pulses it), the
// unread badge — the same numbers as the message card's header, for the page under the reader.
function rdHead(){
  const p = currentPage(), rd = el("rd"), u = el("rdUnread"), ou = el("omUnread"), n = +ou.dataset.n || 0;
  el("rdName").textContent = C.agentName();
  el("rdTime").textContent = p && p.ts ? hhmm(p.ts) : "";
  el("rdPg").textContent = el("pg").textContent;
  rd.dataset.latest = follow ? "1" : "0";
  rd.dataset.newreply = unreadReply ? "1" : "0";
  rd.dataset.unread = n ? "1" : "0";
  el("rdUnreadN").textContent = el("omUnreadN").textContent;
  u.dataset.n = String(n);
  u.setAttribute("aria-label", ou.getAttribute("aria-label") || ""); u.title = ou.title;
  u.setAttribute("aria-disabled", n ? "false" : "true"); u.tabIndex = n ? 0 : -1;
  const rb = el("rdReadBtn");
  if (rb){ rb.disabled = false; rb.hidden = false; rb.classList.add("on"); rb.setAttribute("aria-pressed", "true");
           rb.setAttribute("aria-label", t('r.rd.exit')); rb.title = t('r.rd.exit'); }
}
function rdSync(){
  if (!rdAt) return;
  rdHead();
  if (rdAt.key === shownKey) return;
  const p = currentPage();
  if (!rdHasText(p)) return;                    // an empty page on the way: the jump that brought us here moves on
  const tx = p.reply.trim();
  rdAt = {key: shownKey, scroll: 0, text: tx};
  fill(el("rdWords"), tx, p.source?.k === "friend_report", sysPage(p)); renderReaderMedia(el("rdWords"), p);
  el("rdScroll").scrollTop = 0;
}
// The footer: the reply card's six icons, cloned once (same glyphs, same order — 阅读 · 朗读 · 转发 · 邮件 · 复制 · 回复,
// 回复 in the corner) and kept in step with the card's buttons (disabled / hidden / state / label), which already follow the
// page under the reader. 回复 leaves the reader (quotes this reply and focuses the field); 复制, 朗读, Telegram and
// e-mail stay; the book = leave.
const RD_ACTS = [["readBtn", "rdReadBtn"], ["speakBtn", "rdSpeakBtn"], ["fwdBtn", "rdFwdBtn"], ["mailBtn", "rdMailBtn"],
                 ["copyReply", "rdCopyBtn"], ["replyBtn", "rdReplyBtn"]];
const RD_MIRROR = ["disabled", "hidden", "data-state", "data-tg", "aria-label", "title"];
function rdBuildActs(){
  const box = el("rdActs"), pairs = [];
  for (const [src, id] of RD_ACTS){
    const s = el(src), b = s.cloneNode(true);
    b.id = id; b.classList.remove("folded"); b.removeAttribute("data-i18n-attr");
    box.appendChild(b);
    if (src !== "readBtn") pairs.push([s, b]);
  }
  const copy = (s, b) => {
    for (const a of RD_MIRROR){ const v = s.getAttribute(a); if (v === null) b.removeAttribute(a); else b.setAttribute(a, v); }
    b.classList.toggle("on", s.classList.contains("on"));
  };
  const mo = new MutationObserver(rs => { for (const r of rs){ const m = pairs.find(x => x[0] === r.target); if (m) copy(m[0], m[1]); } });
  for (const [s, b] of pairs){ copy(s, b); mo.observe(s, {attributes: true, attributeFilter: [...RD_MIRROR, "class"]}); }
}
function rdCopy(){
  const tx = currentPage().reply || "";
  copyOut(tx, t('r.copy.reply'), el('rdCopyBtn'));
}
function rdReply(){
  const p = currentPage();
  if (!p || p.id === null) return;
  closeReader();
  startReply(p);
  // the reader's layer gives the focus back to its opener in the MutationObserver microtask queued by closeReader;
  // this one runs after it, still inside the tap, so the caret ends in the field (and the phone keyboard opens)
  queueMicrotask(() => {
    if (!quotes.some(q => q.id === p.id) || document.activeElement === input) return;
    focusComposer(); input.setSelectionRange(input.value.length, input.value.length);
  });
}
// The type-size popover (footer, left): − · slider · + — pinch and + / - keep working without it.
function rdFsPop(open){
  const pop = el("rdFsPop"), b = el("rdFsBtn");
  if (open === undefined) open = pop.hidden;
  if (open === !pop.hidden) return;
  pop.hidden = !open; b.setAttribute("aria-expanded", open ? "true" : "false");
  if (open) el("rdSlider").focus({preventScroll: true});
}
function closeReader(fromPop){
  if (!rdOpen()) return;
  rdGuard = performance.now(); rdPinch = null;
  rdFsPop(false);
  const rd = el("rd");
  rd.hidden = true; delete rd.dataset.modalOpen; delete document.body.dataset.reader;
  el("rdWords").replaceChildren();
  clearReaderMedia();
  if (rdAt && rdAt.key === shownKey){ mainEl.scrollTop = rdAt.scroll; stick = atBottom(); paintMore(); }
  rdAt = null;
  if (!fromPop && rdPushed && history.state && history.state.ajReader){ rdSkipPop = true; history.back(); }
  rdPushed = false;
}
function rdFollow(tx, sys = false, dataOnly = false){
  if (!rdAt || rdAt.key !== shownKey || !tx || tx === rdAt.text) return;
  const sc = el("rdScroll"), top = sc.scrollTop;
  rdAt.text = tx; fill(el("rdWords"), tx, dataOnly, sys); sc.scrollTop = top;
}
function dblTap(node, ok, fn){
  let down = null, last = null;
  node.addEventListener("pointerdown", e => {
    if (e.pointerType === "mouse") return;
    if (!e.isPrimary){ down = null; last = null; return; }
    let sel = false; try{ const s = getSelection(); sel = !!s && !s.isCollapsed && node.contains(s.anchorNode); }catch(_){}
    down = !sel && ok(e.target) && performance.now() - rdPinchAt > 400 ? {x: e.clientX, y: e.clientY, t: performance.now()} : null;
    if (!down) last = null;
  });
  node.addEventListener("pointercancel", () => { down = null; last = null; });
  node.addEventListener("pointerup", e => {
    if (!down || e.pointerType === "mouse") return;
    const d = down, now = performance.now(); down = null;
    if (now - d.t > RD_TAP_MS || Math.hypot(e.clientX - d.x, e.clientY - d.y) > RD_TAP_PX){ last = null; return; }
    if (last && now - last.t <= RD_DTAP_MS && Math.hypot(e.clientX - last.x, e.clientY - last.y) <= RD_PAIR_PX){
      last = null;
      if (now - rdGuard > 400) fn();
      return;
    }
    last = {x: e.clientX, y: e.clientY, t: now};
  });
  node.addEventListener("mousedown", e => { if (e.detail >= 2 && ok(e.target)) e.preventDefault(); });
  node.addEventListener("dblclick", e => {
    if (!ok(e.target)) return;
    e.preventDefault();
    if (rdPtr === "mouse" && performance.now() - rdGuard > 400) fn();
  });
}
const rdTarget = tg => !(tg && tg.closest && tg.closest("button, a, input, [role=link], .rmbar, .mcard:not([data-kind=audio])"));
const rdDist = ts => Math.hypot(ts[0].clientX - ts[1].clientX, ts[0].clientY - ts[1].clientY);

// ---- swipe to page: within 30° of horizontal = a page turn; past 25 % or a fling commits ----------
const SWIPE_DEG = 30, SWIPE_FRAC = 0.25, SWIPE_FLING = 0.5, SWIPE_EDGE = 16, SWIPE_MIN_MOVE = 4;
// P181 追加 3: a page turn is a quick flick. The finger has to start moving within SWIPE_HOLD_MS of touching down;
// a press held longer is a long press (text selection), and a touch that starts on / grows a selection never turns pages.
const SWIPE_HOLD_MS = 300, SWIPE_SEL_PAD = 44;
let sw = null;
function nearSelection(x, y){
  const s = document.getSelection ? document.getSelection() : null;
  if (!s || !s.rangeCount || s.isCollapsed || !wordsSelection()) return false;
  for (const r of s.getRangeAt(0).getClientRects())
    if (x > r.left - SWIPE_SEL_PAD && x < r.right + SWIPE_SEL_PAD && y > r.top - SWIPE_SEL_PAD && y < r.bottom + SWIPE_SEL_PAD) return true;
  return false;
}
/** A selection appeared mid-touch: this touch is selecting text, not turning pages — put the deck back. */
function swipeVeto(){
  if (!sw) return;
  if (sw.mode === "page"){ deck.style.transition = "transform .22s var(--re)"; deck.style.transform = ""; deck.dataset.rubber = "0"; }
  sw.mode = "hold"; deck.dataset.swipe = "hold";
}
function hScrollable(tg){
  for (let n = tg; n && n !== deck; n = n.parentElement)
    if (n.scrollWidth > n.clientWidth + 1 && /(auto|scroll)/.test(getComputedStyle(n).overflowX)) return true;
  return false;
}
function swipeEnd(){
  if (!sw) return;
  const s0 = sw; sw = null;
  if (s0.mode !== "page") return;
  const w = deck.clientWidth || innerWidth;
  const go = !s0.edge && (Math.abs(s0.dx) > w * SWIPE_FRAC || (Math.abs(s0.v) > SWIPE_FLING && Math.abs(s0.dx) > 24));
  const reduce = matchMedia("(prefers-reduced-motion: reduce)").matches;
  const back = () => { deck.style.transition = reduce ? "none" : "transform .22s var(--re)"; deck.style.transform = ""; };
  if (!go){ back(); return; }
  const dir = s0.dx > 0 ? -1 : 1;              // finger right = older, left = newer
  deck.style.transition = reduce ? "none" : "transform .16s ease-in";
  deck.style.transform = `translateX(${-dir * w}px)`;
  setTimeout(async () => {
    await goPage(dir);
    deck.style.transition = "none";
    deck.style.transform = `translateX(${dir * w * 0.35}px)`;
    void deck.offsetWidth;
    back();
  }, reduce ? 0 : 160);
}

// ---- connection (relay's transport section, now the Noise session's state) ------------------------
/** P129: the session dropped but the screen stays quiet for a moment (app.js QUIET_MS): stop an in-flight send now. */
// P393 #122: a drop that heals inside the five-second quiet window never paints "off", so setConn(true) below sees no
// off→on edge; connLost marks it so an upload the drop cut (why "net") still resumes when the link is back.
let quietLost = false;
export function connLost(){ quietLost = true; outbox.lost(); }
/** on: a ready session with the host up. text: what the screen reader says while off. */
export function setConn(on, text){
  if (!C) return;
  const was = document.body.dataset.conn === "on";
  document.body.dataset.conn = on ? "on" : "off";
  if (!on){
    historyGeneration++; historySync = true;
    el("capText").textContent = text || "";
    el("capSub").textContent = "";
    const stale = el("stale");
    if (lastSync){
      stale.textContent = t('r.stale', {hm: lastSync.toTimeString().slice(0, 5)});
      stale.classList.add("has");
    }
  }
  if (on && (!was || quietLost)) uploadsResume();
  if (on) quietLost = false;
  if (on) drainOut(); else outbox.lost();          // 0.15.2: the offline queue goes out on ready, stops on a drop
  paintOut();
  wordsSrc = null; showPage(); paintChrome(); refreshSend(); paintSayCancel();
}

// P251: preserve text through the existing sealed draft store; never discard in-flight card/media work.
export function refreshBlocked(){
  return !!((document.body?.dataset?.view && document.body.dataset.view !== "chat") || !C?.connected() || ptt || asrBusy || sending || atts.length || quotes.length || askBusy || apprBusy || apprHold ||
    (askState(cur?.ask) === "open" && askSel.id === cur?.ask?.id && askSel.picks.some(p => p.length)) ||
    [...document.querySelectorAll('.gdlg:not([hidden]):not(#version-notice),.ajmodal:not([hidden]),.sheet')].some(n => n.getClientRects().length && getComputedStyle(n).visibility !== 'hidden') ||
    (document.activeElement && document.activeElement !== input && document.activeElement.matches('input,textarea,[contenteditable=true]')));
}

// ---- drafts (§10.14: sealed in IndexedDB, 24 h, deleted on unpair / re-pair) -------------------------
const DRAFT_TTL_MS = 24 * 3600 * 1000;
let draftTimer = 0, draftQ = Promise.resolve();
// P127 (追加7): one write at a time, each the field as it is at its turn. A put still sealing (async crypto) could otherwise
// land after the delete that followed a send, and the next page load would restore the words already sent.
export function saveDraft(){
  clearTimeout(draftTimer);
  return (draftQ = draftQ.then(async () => {
    const tx = input ? input.value : "";
    try{
      if (!tx.trim()) await dbDel("draft");
      else await putSealed("draft", {t: tx, at: Date.now()});
      return true;
    }catch(_){ return false; }
  }));
}
function draftSoon(){ clearTimeout(draftTimer); draftTimer = setTimeout(saveDraft, 600); }
export async function restoreDraft(){
  const d = await getSealed("draft");
  if (!d || typeof d.t !== "string" || Date.now() - (+d.at || 0) > DRAFT_TTL_MS || input.value) return;
  if (lastSent && isEcho(d.t, lastSent.text)){ saveDraft(); return; }     // P127: the words just sent are not a draft
  input.value = d.t.slice(0, MAX_TEXT());
  input.dispatchEvent(new Event("input"));
  toast(t('r.draft.restored'), 2600);
}

// ---- keyboard / visual viewport ---------------------------------------------------------
function fitViewport(){
  applyViewport();          // P142: follows visualViewport only while the keyboard is up; else full height (iOS resume)
  paintChrome();
  placeSheet();             // P238: the command card stays on the 5-hour bar
}

// ---- composer --------------------------------------------------------------------------
let sending = false, asrBusy = 0;
// F27 (0.16): words OR at least one attachment (a voice note counts) is a message — the keyboard need not open just to type
// a full stop. A failed file alone is not something to send; files still uploading are waited for by say(), never skipped.
function hasSayable(){ return !!input.value.trim() || atts.some(a => a.st !== "failed"); }
function refreshSend(){
  el("send").disabled = sending || asrBusy > 0 || !hasSayable() || !(C.connected() || outbox.host !== null);   // offline: it queues (null = not paired)
  const c = el("clr"); if (c) c.hidden = !input.value;
  paintCapNote();
}
// P318 (Leo, 2026-10-15): the cap is not in the placeholder any more (it folded to two lines on an iPhone); it shows on the
// field only near the limit — from 90 % on, 「18,000 / 20,000」 in a small tag — and capField() still stops it at the cap.
const CAP_NEAR = 0.9;
function paintCapNote(){
  const n = el("capNote"); if (!n) return;
  const len = input.value.length, max = MAX_TEXT(), on = max > 0 && len >= Math.ceil(max * CAP_NEAR);
  n.hidden = !on;
  if (on){ const f = x => x.toLocaleString(lang() === "zh" ? "zh-CN" : "en"); n.textContent = t('p318.cap', {n: f(len), max: f(max)}); }
}
// P200 追加 3 (Leo, 2026-10-13): one line never scrolls, whatever the type size, zoom or viewport — the cap is at least
// one line and a half (a fallback font's taller line box still fits; two lines do not), so only real multi-line text
// that outgrows the 30 vh cap gets the bar (a landscape phone with the keyboard up can have 30 vh under one line).
function fieldMaxH(){
  const cs = getComputedStyle(input), lh = parseFloat(cs.lineHeight) || parseFloat(cs.fontSize) * 1.45 || 0;
  return Math.max(innerHeight * 0.3, Math.ceil((parseFloat(cs.paddingTop) || 0) + (parseFloat(cs.paddingBottom) || 0) + lh * 1.5));
}
// P412 (#174, Leo 10-20: 「消息输入框的高度……永远是一行」): the box never grows. More lines scroll inside it (Shift+Enter still
// adds one) and the full-screen button above it says 「N 行」. fieldMaxH() stays the measure of how much text "fits" (long
// pastes and long takes still turn into a file past it).
function lineBox(){
  const cs = getComputedStyle(input), lh = parseFloat(cs.lineHeight) || parseFloat(cs.fontSize) * 1.45 || 24;
  return {lh, pad: (parseFloat(cs.paddingTop) || 0) + (parseFloat(cs.paddingBottom) || 0)};
}
function autosize(){
  input.style.height = ""; input.style.maxHeight = "";
  // scrollHeight is 0 while the chat view is hidden (a sub-view is open): measured again once it shows (observer below)
  let n = 1;
  if (input.value && input.scrollHeight){
    const {lh, pad} = lineBox();
    n = Math.max(1, Math.round((input.scrollHeight - pad) / lh));   // a fallback font's taller line box still counts as one
  } else input.scrollTop = 0;
  input.classList.toggle("ovf", n > 1);
  const badge = el("tFullN");
  if (badge){ badge.hidden = n < 2; if (n > 1) badge.textContent = t('p412.lines', {n}); }
  refreshSend();
}

// ---- the text cap and long pastes ----------------------------------------------------------
// One cap, the host's: 20 000 UTF-16 code units with a p33 host (4 000 with an older one). Stated in 万 in Chinese.
const MAX_TEXT = () => C.textMax();
function capWords(){
  const n = MAX_TEXT();
  return lang() === "zh" ? (n >= 10000 ? t('r.cap.wan', {n: +(n / 10000).toFixed(1)}) : t('r.cap.zi', {n})) : t('r.cap.chars', {n: n.toLocaleString("en")});
}
function capField(){
  if (input.value.length <= MAX_TEXT()) return false;
  const at = Math.min(input.selectionStart, MAX_TEXT());
  input.value = input.value.slice(0, MAX_TEXT());
  input.setSelectionRange(at, at);
  toast(t('r.cap.toast', {cap: capWords()}), 2600);
  return true;
}
let composing = false;
// P127 (追加7): the words just sent. A Mac dictation 「。」 or an IME re-commit of the same sentence can land after Send
// (while say_res is on its way, or after the field was cleared): within ECHO_MS such an echo is dropped, never re-typed.
const ECHO_MS = 3000;
let lastSent = null;      // {text, until}
// P375: programmatic composer focus is desktop-only. Touch owners focus by tapping the field.
function focusComposer(options){ focusDraft(input, options); }
function releaseComposer(){ releaseDraft(input); }
function settleField(text){
  releaseComposer();
  lastSent = {text, until: Date.now() + ECHO_MS};
  if (input.value === text || isEcho(input.value, text)) input.value = "";
  closeSug();      // P280: the `/clear` suggestion reopened when the confirm sheet gave the focus back — it must not linger
}
// Only a real keyboard / dictation input (isTrusted) or an IME's compositionend (the page never dispatches one; Chromium's
// own may arrive untrusted) is checked: the page's own writes (↑ history, a withdraw putting the words back, a transcript)
// dispatch synthetic input events and are never dropped.
function dropEcho(e, ime = false){
  if (!e || !(ime || e.isTrusted) || !lastSent || composing || !input.value || Date.now() > lastSent.until || !isEcho(input.value, lastSent.text)) return false;
  input.value = ""; autosize(); saveDraft();
  return true;
}
let twin = null;
function twinHeight(text){
  twin.style.width = input.clientWidth + "px";
  twin.value = text;
  twin.style.height = "auto";
  return twin.scrollHeight;
}
const overflowsField = text => twinHeight(text) > fieldMaxH() + 0.5;
function fieldCapacity(sample = "\u4e2d"){
  let lo = 0, hi = 64;
  while (!overflowsField(sample.repeat(hi)) && hi < MAX_TEXT()) hi *= 2;
  while (lo < hi){ const mid = (lo + hi + 1) >> 1; if (overflowsField(sample.repeat(mid))) hi = mid - 1; else lo = mid; }
  return lo;
}
// P318 (Leo, 2026-10-15): one short hint on every width (「Say something (≤ 20,000 characters)」 folded to two lines on an
// iPhone Air); the cap shows near the limit only (paintCapNote) and stays in the field's title.
function restingPlaceholder(){
  if (C && C.estopOn()) return t('chat.placeholderStopped');
  return t('p318.ph');
}
export function paintPlaceholder(){
  if (!input) return;
  if (!asrBusy) input.placeholder = restingPlaceholder();
  input.maxLength = MAX_TEXT();
  autosize();
  input.title = t('r.cap.title', {n: MAX_TEXT()});
}
function clipFiles(cd){
  let out = [...(cd.files || [])];
  if (!out.length) for (const it of cd.items || []) if (it.kind === "file"){ const f = it.getAsFile(); if (f) out.push(f); }
  return out;
}
function pastedName(f, i, n){
  if (f.name && !/^image(\.\w+)?$/i.test(f.name)) return f;
  const ty = (f.type || "").toLowerCase(), ext = ((DROP_TYPES[ty] || ".png").split(" ")[0]);
  try{ return new File([f], `${t('r.file.paste')}-${stamp()}${n > 1 ? "-" + (i + 1) : ""}${ext}`, {type: f.type, lastModified: f.lastModified}); }
  catch(_){ return f; }
}

// ---- the attachment tray: picked files upload at once, end to end (§10.3) -----------------------------
// Picking a file starts its blob upload while the words are still being typed, so Send never waits on an upload that
// could have happened earlier. Nothing reaches the Agent until Send.
function uploadError(why){
  const W = ["empty", "too_big", "type", "too_many", "quota", "disk", "unsafe_inbox", "sha_mismatch", "size_mismatch", "expired", "net", "read", "shape", "asr_off"];
  return t('r.up.' + (W.includes(why) ? why : "other"));
}
function notReady(file){ return /^image\//.test(file.type || "image/") ? t('r.up.notReadyImage') : t('r.up.notReady'); }
const atts = [];              // {file, name, kind, origin, st:"up"|"ready"|"failed"|"held", id, up, promise, node}
function kindOf(f){
  const ty = f.type || "";
  return /^image\//.test(ty) ? "image" : /^audio\//.test(ty) ? "audio" : "file";
}
function icon(kind){ return el("chipIcons").content.querySelector(`[data-kind="${kind}"]`).cloneNode(true); }
function thumb(a){
  const im = document.createElement("img");
  im.className = "thumb"; im.alt = ""; im.decoding = "async";
  im.onerror = () => { if (im.isConnected){ im.replaceWith(icon("image")); a.node && a.node.classList.remove("img"); } };
  try{ a.url = URL.createObjectURL(a.file); im.src = a.url; }catch(_){ return icon("image"); }
  return im;
}
function paintChip(a){
  if (!a.node){
    const c = document.createElement("div");
    c.className = a.kind === "image" ? "chip img" : "chip"; c.setAttribute("role", "listitem");
    const nm = document.createElement("span"); nm.className = "nm"; nm.textContent = a.label || a.name;
    const pg = document.createElement("span"); pg.className = "pg"; pg.appendChild(document.createElement("i"));
    const x = document.createElement("button"); x.type = "button"; x.className = "x";
    x.setAttribute("aria-label", t('r.tray.remove', {name: a.name})); x.appendChild(icon("x"));
    x.addEventListener("click", e => { e.stopPropagation(); removeAtt(a); });
    c.append(a.kind === "image" ? thumb(a) : icon(a.kind), nm, pg, x);
    c.title = `${t('r.kind.' + a.kind)} · ${a.name} · ${human(a.file.size)}`;
    if (a.auto){
      const go = document.createElement("button"); go.type = "button"; go.className = "auto";
      go.textContent = t('p398.autoChip'); go.title = t('p398.autoChipTitle');
      go.addEventListener("click", e => { e.stopPropagation(); if (C && C.openShotSetting) C.openShotSetting(); });
      nm.replaceWith(Object.assign(document.createElement("span"), {className: "nmcol"}));
      c.querySelector(".nmcol").append(nm, go); c.classList.add("autochip");
    }
    a.node = c; tray.appendChild(c);
  }
  a.node.dataset.st = a.st;
  a.node.querySelector(".nm").textContent = a.label || a.name;
  if (a.dur) a.node.title = t('r.tray.voiceTitle', {d: mmss(a.dur), size: human(a.file.size)});
  a.node.querySelector(".pg i").style.width = Math.round((a.pct || 0) * 100) + "%";
  if (a.st === "failed" && a.err) a.node.title = a.name + " · " + a.err;
  if (a.st === "held") a.node.title = t('r.tray.heldTitle', {kind: t('r.kind.' + a.kind), name: a.name});
}
function paintTray(){
  tray.hidden = !atts.length;
  tray.setAttribute("aria-label", t('r.tray.count', {n: atts.length, max: MAX_ATT}));
  if (C) refreshSend();                                             // F27: a file alone can make Send live
}
function stage(a){
  a.st = "up"; a.pct = 0; a.err = "";
  if (!a.up) a.up = new Upload(a.file, {purpose: "att", origin: a.origin, name: a.name, mime: a.file.type, secs: a.dur, bid: a.bid, onProgress: p => { a.pct = p; paintChip(a); }});
  a.bid = null;                                                     // P182: a stored take's upload id is reused once (resume)
  paintChip(a);
  a.promise = a.up.start().then(r => {
    if (!atts.includes(a) && !a.out) return;                        // a.out: held whole with an offline-queued message
    if (r.ok){ a.st = "ready"; a.id = a.up.bid; }
    else if (r.why === ""){ /* held or dropped by the page */ }
    else if (r.why === "net"){                                       // the connection went: resumes after the reconnect
      a.st = "up"; a.retry = true;
      if (C.connected()) setTimeout(uploadsResume, 300);                // lost while already reconnected (stale window)
    }
    else { a.st = "failed"; a.err = uploadError(r.why); toast(a.err, 3000); }
    paintChip(a);
    refreshSend();                                                   // F27: a failed file alone is nothing to send
  });
  return a.promise;
}
function uploadsResume(){ for (const a of atts) if (a.retry && a.st === "up"){ a.retry = false; stage(a); } }
// origin: file | photo | camera | paste | drop | recording (§10.3)
// dur: a recording's length in seconds — set BEFORE stage() so blob_open carries `secs` (§10.3; the host's transcript
// heading 「语音转写（N 秒，…）」 comes from it)
// bid: P182 — the upload id a recovered recording segment had before the reload (the host resumes its partial)
function addFiles(list, origin = "file", dur, bid, auto = false){
  let dropped = 0;
  const added = [];
  for (const f of list){
    if ((origin === "recording" && !LR.usable(f, dur)) || (f.type.startsWith("audio/") && f.size < LR.MIN_REC_BYTES)){
      toast(t('r.rec.tooShort'), 2600); continue;
    }
    if (!f.size){ toast(notReady(f), 3600); continue; }
    if (f.size > UPLOAD_MAX){ toast(uploadError("too_big"), 3000); continue; }
    if (atts.length >= MAX_ATT){ dropped++; continue; }
    const a = {file: f, name: f.name || "upload", kind: kindOf(f), origin};
    if (dur > 0) a.dur = dur;
    if (typeof bid === "string") a.bid = bid;
    if (auto) a.auto = true;                       // P398 (#156): 截图自动进附件 put it here — its chip says where to switch that off
    atts.push(a); added.push(a);
    stage(a);
  }
  paintTray();
  if (dropped) toast(t('r.tray.full', {max: MAX_ATT, n: dropped}), 3200);
  return added;
}
function removeAtt(a, drop = true){
  const i = atts.indexOf(a);
  if (i < 0) return;
  atts.splice(i, 1);
  if (drop && a.up) a.up.drop();
  if (a.rec){ LR.dropSeg(a.rec.take, a.rec.seg); a.rec = null; }      // P182: sent (accepted) or removed → off the phone
  if (a.node) a.node.remove();
  if (a.url){ URL.revokeObjectURL(a.url); a.url = null; }
  paintTray();
}
// A cancelled send stops an upload that is still going: the chip stays, "held", and the next Send resumes it.
function holdAtt(a){
  a.st = "held"; a.pct = 0;
  if (a.up) a.up.hold();
  paintChip(a);
}

// ---- desktop drag-and-drop: the whole page is the drop target -----------------------------
const DROP_EXT = {};
for (const [m, exts] of Object.entries(DROP_TYPES)) for (const x of exts.split(" ")) DROP_EXT[x] ??= m;
// P166: every file goes — a known type as itself, a known extension as its type, anything else as its own well-formed
// MIME or application/octet-stream (the host keeps a scrubbed extension). Only a File that cannot be re-wrapped is skipped.
function droppable(f){
  const ty = (f.type || "").split(";")[0].trim().toLowerCase();
  if (DROP_TYPES[ty]) return ty === f.type ? f : new File([f], f.name, {type: ty, lastModified: f.lastModified});
  const m = /\.[^.]+$/.exec((f.name || "").toLowerCase()), mime = (m && DROP_EXT[m[0]]) || anyMime(ty);
  if (mime === f.type) return f;
  try{ return new File([f], f.name, {type: mime, lastModified: f.lastModified}); }catch(_){ return null; }
}
function modalOpen(){
  return document.body.dataset.sheet === "1" || !menu.hidden || !!document.querySelector("[data-modal-open='1']") || document.body.dataset.view !== "chat";
}
const isFileDrag = e => !!e.dataTransfer && [...(e.dataTransfer.types || [])].includes("Files");
function dragCount(e){
  const it = e.dataTransfer && e.dataTransfer.items;
  if (it && it.length) return [...it].filter(i => i.kind === "file").length;
  return (e.dataTransfer && e.dataTransfer.files && e.dataTransfer.files.length) || 0;
}
let dragDepth = 0;
function dropUI(n){
  const z = el("dropzone");
  if (n == null){ z.classList.remove("on"); z.hidden = true; delete document.body.dataset.drag; return; }
  const full = atts.length + n > MAX_ATT;
  z.hidden = false; z.classList.toggle("full", full);
  el("dropText").textContent = full ? t('r.drop.full', {max: MAX_ATT}) : t('r.drop.text');
  el("dropSub").textContent = full ? t('r.drop.fullSub', {have: atts.length, n}) : (n ? t('r.drop.sub', {n, left: MAX_ATT - atts.length}) : "");
  document.body.dataset.drag = full ? "full" : "on";
  requestAnimationFrame(() => z.classList.add("on"));
}
// The drop's rules, shared with paste and ≡「粘贴图片」: all or none past MAX_ATT, only the allowed formats.
// P398 (#156): one take at a time (an automatic screenshot and a ⌘V of it arrive together), each compared by content with
// the tray first — a file already there is not added again, and the owner hears so. → the attachments added.
let takeChain = Promise.resolve();
function takeFiles(files, verbKey, origin, auto = false){
  if (!files.length) return Promise.resolve([]);
  const run = takeChain.then(() => takeNow(files, verbKey, origin, auto));
  takeChain = run.catch(() => {});
  return run;
}
async function takeNow(all, verbKey, origin, auto){
  const files = await attkeys.unique(all, atts.map(a => a.file));
  if (files.length < all.length) toast(t('p398.dup', {n: all.length - files.length}), 2600);
  if (!files.length) return [];
  if (atts.length + files.length > MAX_ATT){
    toast(t('r.take.tooMany', {max: MAX_ATT, have: atts.length, verb: t(verbKey), n: files.length}), 3600);
    return [];
  }
  const ok = files.map(droppable).filter(Boolean), skipped = files.length - ok.length;
  if (!ok.length){ toast(t('r.take.none'), 3000); return []; }
  const added = addFiles(ok, origin, undefined, undefined, auto);
  if (skipped) toast(t('r.take.skipped', {n: skipped}), 3200);
  return added;
}

// ---- in-page camera (desktop: preview, 拍照 / 重拍 / 使用这张; phones keep the native capture picker) ---------
const isDesktop = () => !matchMedia("(pointer:coarse)").matches && !("ontouchstart" in window);
let camStream = null;
function camView(mode, msg){
  el("camMsg").textContent = msg || "";
  el("camVideo").hidden = mode !== "live";
  el("camShot").hidden = mode !== "still";
  el("camSnap").hidden = mode !== "live";
  el("camRetake").hidden = el("camUse").hidden = mode !== "still";
  el("camFile").hidden = mode !== "error";
  el("cam").dataset.mode = mode;
}
function closeCam(){
  if (camStream){ camStream.getTracks().forEach(tr => tr.stop()); camStream = null; }
  const v = el("camVideo"); try{ v.pause(); }catch(_){} v.srcObject = null;
  el("cam").hidden = true;
  delete el("cam").dataset.modalOpen;
}
function camFallback(msg){
  toast(t('r.cam.fallback', {msg}), 3600);
  if (navigator.userActivation && navigator.userActivation.isActive){ closeCam(); el("fCamera").click(); return; }
  camView("error", msg + t('r.dot'));
}
async function openCam(){
  const box = el("cam");
  box.hidden = false; box.dataset.modalOpen = "1";
  camView("asking", t('r.cam.asking'));
  if (!navigator.mediaDevices || !navigator.mediaDevices.getUserMedia) return camFallback(t('r.cam.noApi'));
  let s;
  try{ s = await navigator.mediaDevices.getUserMedia({video: true, audio: false}); }
  catch(err){
    const n = err && err.name;
    return camFallback(n === "NotAllowedError" || n === "SecurityError" ? t('r.cam.denied')
                     : n === "NotFoundError" || n === "OverconstrainedError" ? t('r.cam.none')
                     : n === "NotReadableError" ? t('r.cam.busy') : t('r.cam.fail'));
  }
  if (box.hidden){ s.getTracks().forEach(tr => tr.stop()); return; }
  camStream = s;
  const v = el("camVideo"); v.srcObject = s;
  try{ await v.play(); }catch(_){}
  camView("live", "");
}

// ---- send ---------------------------------------------------------------------------------
const SAY_ERR = ["shape", "too_long", "stopped", "att_gone", "att_open", "too_many_att", "reply_unknown", "dup", "too_many", "no_agent", "offline", "timeout", "seat"];
// ---- the launch: a sent message flies into the logo (relay) -----------------------------------
const ROCKET_MS = 800;
function pulseLogo(){
  const o = document.querySelector(".top .orb");
  if (!o || !o.animate) return;
  o.dataset.pulse = "1";
  const a = o.animate([{transform: "scale(1)"}, {transform: "scale(1.18)", offset: .4}, {transform: "scale(1)"}], {duration: 420, easing: "ease-out"});
  a.onfinish = a.oncancel = () => { delete o.dataset.pulse; };
}
function flyRocket(land){
  const from = el("send").getBoundingClientRect(), to = document.querySelector(".top .orb").getBoundingClientRect();
  const P0 = [from.left + from.width / 2, from.top + from.height / 2], P3 = [to.left + to.width / 2, to.top + to.height / 2];
  const dx = P0[0] - P3[0], dy = P0[1] - P3[1];
  const P1 = [P0[0], P0[1] - .62 * dy], P2 = [P3[0] + .5 * dx, P3[1]];
  const at = (u, i) => (1 - u) ** 3 * P0[i] + 3 * (1 - u) ** 2 * u * P1[i] + 3 * (1 - u) * u * u * P2[i] + u ** 3 * P3[i];
  const slope = (u, i) => 3 * (1 - u) ** 2 * (P1[i] - P0[i]) + 6 * (1 - u) * u * (P2[i] - P1[i]) + 3 * u * u * (P3[i] - P2[i]);
  const ease = u => u < .5 ? 4 * u ** 3 : 1 - (-2 * u + 2) ** 3 / 2;
  const frames = [];
  for (let k = 0, N = 30; k <= N; k++){
    const u = k / N, e = ease(u);
    const ang = Math.atan2(slope(e, 1), slope(e, 0)) * 180 / Math.PI + 90;
    const op = land ? (u > .92 ? (1 - u) / .08 : 1) : (u > .7 ? Math.max(0, (.88 - u) / .18) : 1);
    frames.push({offset: u, opacity: op, transform: `translate(${at(e, 0).toFixed(1)}px,${at(e, 1).toFixed(1)}px) rotate(${ang.toFixed(1)}deg) scale(${(1 - .55 * u).toFixed(3)})`});
  }
  const r = document.createElement("i");
  r.className = "rocketfly"; r.setAttribute("aria-hidden", "true");
  r.dataset.land = land ? "1" : "0";
  r.style.transform = frames[0].transform;
  document.body.appendChild(r);
  const a = r.animate(frames, {duration: ROCKET_MS, easing: "linear", fill: "forwards"});
  a.onfinish = () => { r.remove(); if (land) pulseLogo(); };
  a.oncancel = () => r.remove();
  return {r, a};
}
function launchRocket(){
  const reduce = matchMedia("(prefers-reduced-motion: reduce)").matches;
  if (reduce || !document.querySelector(".top .orb") || !document.body.animate){ pulseLogo(); return; }
  flyRocket(true);
  playWhoosh();
}
// The launch sound: only the landing rocket makes it; never stacks, never touches the send. Volume and the player are
// js/sfx.js (P154: a Settings slider, default 15 %, routed through a gain node because iPhone ignores <audio>.volume).
const unlockWhoosh = sfx.unlock, playWhoosh = sfx.play;
// While a send is in flight: rockets keep launching (after 400 ms), × takes it back.
const LOOP_AFTER_MS = 400, LOOP_EVERY_MS = 750, LOOP_MAX_AIR = 3;
function launchLoop(){
  const reduce = matchMedia("(prefers-reduced-motion: reduce)").matches;
  const logo = document.querySelector(".top .orb");
  const L = {air: new Set(), timer: 0, breath: null, started: false};
  const fire = () => {
    for (const x of L.air) if (!x.r.isConnected) L.air.delete(x);
    if (L.air.size < LOOP_MAX_AIR) L.air.add(flyRocket(false));
  };
  L.delay = setTimeout(() => {
    L.started = true;
    document.body.dataset.sending = "1"; paintSayCancel();
    if (reduce || !logo || !document.body.animate){
      if (logo && logo.animate){
        logo.dataset.breath = "1";
        L.breath = logo.animate([{transform: "scale(1)"}, {transform: "scale(1.07)"}, {transform: "scale(1)"}], {duration: 2600, iterations: Infinity, easing: "ease-in-out"});
      }
    } else { fire(); L.timer = setInterval(fire, LOOP_EVERY_MS); }
  }, LOOP_AFTER_MS);
  L.stop = ok => {
    clearTimeout(L.delay); clearInterval(L.timer);
    delete document.body.dataset.sending;
    paintQueued();
    if (L.breath){ L.breath.cancel(); delete logo.dataset.breath; }
    if (ok) return;
    for (const x of L.air){
      if (!x.r.isConnected) continue;
      x.a.pause();
      const f = x.r.animate([{opacity: getComputedStyle(x.r).opacity}, {opacity: 0}], {duration: 220, fill: "forwards"});
      f.onfinish = () => x.a.cancel();
    }
  };
  return L;
}
// The send in flight (one at a time) and the ones accepted but still queued behind a running turn (withdrawable).
let inflight = null;      // {phase, sid, cancelled, delivered, cancelling, wake, batch}
const queued = [];        // {sid, text, atts, rt, turn, at} accepted with state "queued", not yet delivered
let activeTurn = null;    // P282: the page of this device's say the Agent took last (say_state taken / delivered)
// P127 (追加6): the host answers every say `queued` first; an idle Agent takes it within milliseconds (say_state delivered).
// Only one still waiting after QUEUE_QUIET_MS (the shared session is busy) is shown as queued — its page says
// 「已收到，助理正在收尾上一件，马上接手」 (P152: + 「前面还有 N 条」) and the composer keeps it as a pending line saying 「取消」 takes it back — so a quick hand-over
// never flickers.
const QUEUE_QUIET_MS = 700;
let queueTimer = 0;
const queueWatch = new QueueWatch();
let queueStuck = false;
const observedQueue = new Map();
function queuedWatchList(){
  const rows = hist.turns.filter(p => p.end === "open" && p.card?.queued === true);
  const ids = new Set(rows.map(p => p.id));
  for (const id of observedQueue.keys()) if (!ids.has(id)) observedQueue.delete(id);
  for (const p of rows) if (!observedQueue.has(p.id)) observedQueue.set(p.id, Date.now());
  const list = new Map(rows.map(p => [p.id, {turn: p.id, at: observedQueue.get(p.id)}]));
  for (const q of queuedShown()) list.set(q.turn, q);
  return [...list.values()].sort((a,b) => a.turn - b.turn);
}
const queuedShown = () => { const now = Date.now(); return queued.filter(q => now - (q.at || 0) >= QUEUE_QUIET_MS); };
function queuedTurn(id){ return Number.isInteger(id) && queuedWatchList().some(q => q.turn === id); }
function queuedAhead(id){ const i = queued.findIndex(q => q.turn === id); return i > 0 ? i : 0; }
// P328 (Leo, 0.22.0a1): the × is one control with three jobs — take back this phone's send in flight (send), take back a
// message still queued behind a busy session (queued), and, while the Agent works (blue) with nothing of ours to take back,
// stop the running turn (turn: §8 slash stop, the same /stop the menu sends). It used to follow only the send, so a shared
// Claude Code — which has the message in its TUI within milliseconds (say delivered) — lost it at once while the Agent kept
// working, and a shared Codex (whose say stays queued until its app-server takes it) kept it: the same blue, two answers.
// Now every harness shows it while blue; one that cannot be stopped from the phone says so in /stop's own answer.
// P398 (#155, Leo 10-20: 「显示还没发出去……我就点了取消，结果这个取消导致了新的消息的 Stop」): the pill said 「取消」 in all
// three jobs, so the instant this phone's message was handed over the same 「取消」 under the owner's thumb became /stop of
// the turn that message had just started. Now a send / queued message that is handed over reads 「已送达」 for DELIVERED_MS
// (a tap does nothing, it says so), and the turn job is worded 「Stop」 — a stop is never a message's 取消.
const DELIVERED_MS = 2200;
let deliveredAt = 0, deliveredTimer = 0;
function sawDelivered(){
  deliveredAt = Date.now();
  clearTimeout(deliveredTimer);
  deliveredTimer = setTimeout(paintSayCancel, DELIVERED_MS + 20);
}
function sayCancelMode(){
  const blue = cur && cur.status === "working" && document.body.dataset.conn === "on";
  if (inflight) return document.body.dataset.sending === "1" || blue ? "send" : null;   // idle: only after 400 ms (no flash)
  if (queued.length) return "queued";
  if (blue && Date.now() - deliveredAt < DELIVERED_MS) return "delivered";
  if (blue && !(C && C.estopOn())) return "turn";
  return null;
}
const PILL_WORD = {send: "r.say.cancel", queued: "r.say.cancel", delivered: "p398.delivered", turn: "p398.stopTurn"};
const PILL_ARIA = {send: "r.say.cancelAria", queued: "r.say.cancelAria", delivered: "p398.deliveredAria", turn: "p328.stopAria"};
let sayCancelShown = null;
function paintSayCancel(){
  const b = el("sayCancel");
  if (!b) return;
  const mode = sayCancelMode();
  b.hidden = !mode;
  if (mode) b.dataset.mode = mode;
  if (mode) document.body.dataset.saycancel = "1"; else delete document.body.dataset.saycancel;
  if (mode && mode !== sayCancelShown){
    const k = PILL_ARIA[mode], w = b.querySelector("[data-i18n]");
    b.setAttribute("data-i18n-attr", `aria-label=${k},title=${k}`);
    b.setAttribute("aria-label", t(k)); b.title = t(k);
    if (w){ w.setAttribute("data-i18n", PILL_WORD[mode]); w.textContent = t(PILL_WORD[mode]); }
  }
  sayCancelShown = mode;
  padForCancel();
}
// The typed words never run under the pill: the field's right padding reaches past its left edge (it changes width with
// the language and the type size — a ResizeObserver re-measures). The button itself is never transformed (only its <b>
// pops in), so its box is the real one.
function padForCancel(){
  const b = el("sayCancel"), f = input && input.closest(".field");
  if (!b || !f) return;
  if (b.hidden){ if (f.style.paddingRight){ f.style.paddingRight = ""; autosize(); } return; }
  const pad = Math.ceil(f.getBoundingClientRect().right - b.getBoundingClientRect().left + 4) + "px";
  if (f.style.paddingRight !== pad){ f.style.paddingRight = pad; autosize(); }

}
let stopAskedAt = 0;
const STOP_AGAIN_MS = 2500;
function stopTurn(){
  if (Date.now() - stopAskedAt < STOP_AGAIN_MS) return;
  if (!C.api.slash("stop")){ toast(t('p328.stopNet'), 2400); return; }
  stopAskedAt = Date.now();
  toNewest();
  executionToast(t('p328.stopSent'), 2200);
}
function paintQueued(){
  paintSayCancel();
  clearTimeout(queueTimer);
  const young = queued.filter(q => Date.now() - (q.at || 0) < QUEUE_QUIET_MS);
  if (young.length) queueTimer = setTimeout(paintQueued, Math.max(20, ...young.map(q => QUEUE_QUIET_MS - (Date.now() - q.at) + 20)));
  paintOut();
  if (hist.turns.length) showPage();
}
async function cancelSay(){
  const shown = el("sayCancel").dataset.mode, now = sayCancelMode();
  if (shown === "delivered" || now === "delivered"){ toast(t('r.say.tooLate'), 2600); return; }
  if (shown === "turn" && now === "turn") return stopTurn();
  if (now === "turn" || !now) return;   // P398: what the thumb aimed at (a message) is gone — never a stop in its place
  const S = inflight;
  if (S){
    if (S.cancelling || S.cancelled || S.delivered) return;
    if (S.phase === "upload"){
      S.cancelled = true;
      for (const a of S.batch) if (a.st === "up" && atts.includes(a)) holdAtt(a);
      if (S.wake) S.wake();
      return;
    }
    S.cancelling = true;
    const r = await C.api.sayCancel(S.sid);
    S.cancelling = false;
    if (inflight !== S) return;
    if (r === "cancelled"){ S.cancelled = true; if (S.wake) S.wake(); }
    else if (r === "already_delivered"){ S.delivered = true; toast(t('r.say.tooLate'), 2600); }
    else toast(r === "offline" ? t('r.say.cancelNet') : t('r.say.cancelFail'), 2400);
    return;
  }
  const q = queued[queued.length - 1];
  if (!q || q.cancelling) return;
  q.cancelling = true;
  const r = await C.api.sayCancel(q.sid);
  q.cancelling = false;
  if (r === "cancelled") withdrawn(q);
  else if (r === "already_delivered"){ dequeue(q.sid); toast(t('r.say.tooLate'), 2600); }
  else if (r === "not_found"){ dequeue(q.sid); toast(t('r.say.cancelFail'), 2400); }
  else toast(r === "offline" ? t('r.say.cancelNet') : t('r.say.cancelFail'), 2400);
}
function dequeue(sid){ const i = queued.findIndex(x => x.sid === sid); if (i >= 0) queued.splice(i, 1); paintQueued(); }
// A queued message taken back: its words return to the field (if empty) and its files to the tray (still staged on the
// host under the same ids, §10.2).
function withdrawn(q){
  if (q.gone) return;                 // say_state cancelled and say_cancel_res both say so: once
  q.gone = true;
  dequeue(q.sid);
  if (!input.value){ input.value = q.text; input.dispatchEvent(new Event("input")); }
  for (const a of q.atts) if (atts.length < MAX_ATT && !atts.includes(a)){ a.node = null; a.st = "ready"; atts.push(a); paintChip(a); }
  paintTray();
  toast(t('r.say.withdrawn'), 2600);
}
/** say_state from the host. */
const early = new Map();  // P282: sid → taken / delivered that overtook its say_res (the Agent was idle), so it never shows as queued
export function onSayState(m){
  const q = queued.find(x => x.sid === m.sid);
  if (!q){
    if (m.s === "taken" || m.s === "delivered"){ early.set(m.sid, m.s); if (early.size > 32) early.delete(early.keys().next().value); }
    return;
  }
  if (m.s === "delivered" || m.s === "taken"){ activeTurn = q.turn; sawDelivered(); dequeue(m.sid); }   // P282: taken = the Agent is on it (a shared Codex opens first)
  else if (m.s === "cancelled") withdrawn(q);
  else if (m.s === "failed"){ dequeue(m.sid); toast(t('r.say.failedLater'), 3200); }
  else if (m.s === "done") dequeue(m.sid);         // P398 say_sync: finished / unknown on the host — nothing left to withdraw
}
// P398 (#155): the host's word on this phone's queued sends after a reconnect (a say_state sent while the phone was away is
// lost): `say_sync` asks, the answers come back as say_state. A host without it (no "ss1"): the pages themselves settle it.
function syncQueued(){
  if (!queued.length || !C.api.saySync) return;
  C.api.saySync(queued.map(q => q.sid));
}
/** A queued send whose page shows it was taken (a reply under way, or the page closed) is no longer queued — the page is
    the host's own record too. True when something was dequeued (the page was repainted). */
function settleQueued(){
  let hit = false;
  for (const q of queued.slice()){
    const p = Number.isInteger(q.turn) ? hist.turns.find(x => x.id === q.turn) : null;
    if (!p) continue;
    const c = p.card || {};
    if (p.end !== "open"){ if (c.withdrawn === true) withdrawn(q); else dequeue(q.sid); hit = true; }
    else if (c.queued !== true && c.wait !== true && visibleReply(p)){ activeTurn = q.turn; sawDelivered(); dequeue(q.sid); hit = true; }
  }
  return hit;
}
histSettled = settleQueued;
// The whitelist commands (§8) run as commands when typed; /clear asks first. Anything else starting with / is text.
const CMDS = ["clear", "new", "compact", "model", "context", "cost", "usage", "status", "help", "stop", "update"];
const CMD_RE = /^\/([A-Za-z][\w:.-]{0,63})(?:[ \t]+([\s\S]*))?$/;
// P424 (#145 security): a built-in command is "now or not at all" — the offline queue only carries words. A /clear, /model,
// /compact … that waited out a rescue spell or a dropped link would land minutes later on whatever the session is by then.
// Matched on the words alone (files / quotes riding along, a full-width ／ too), so nothing command-shaped slips into the queue.
const isCmdText = (text) => { const m = /^[/\uff0f]([A-Za-z][\w:.-]{0,63})(?:\s|$)/.exec(String(text).trim()); return !!m && CMDS.includes(m[1].toLowerCase()); };
async function runCmd(cmd, arg = ""){
  if (cmd === "new") cmd = "clear";                              // P341 (#50): /new is /clear (Codex users' word) — asks first too
  if (cmd === "model") pillDrop();                               // P239: typed /model is immediate; drop a waiting tap
  if (cmd === "clear"){
    if (!await confirmSheet(t('cmd.clearTitle'), t('cmd.clearText'), t('cmd.clearYes'))) return false;
    return C.api.slash("clear", "", true);
  }
  return C.api.slash(cmd, arg);
}
// P181: one tap / hold sends a ready-made message AS THE OWNER to the main Agent, which then asks what to change and follows
// the bundled skill (theme → agentj-theme: validate before apply; cmds → agentj-commands). The field and its draft stay
// untouched; the message shows on the page like anything the owner sent. Not while stopped / offline (nothing queues).
export const PRESETS = ["theme", "cmds"];
export async function sayPreset(kind){
  if (!PRESETS.includes(kind)) return false;
  return sayWords(t('p181.prompt.' + kind), t('p181.sent.' + kind));
}
// P238: one tap on /switch-harness sends it as typed — serve hands it to the main Agent, which lists the harnesses first
const saySwitch = () => sayWords(SWITCH_CMD, t('p238.sent'));
// P368: a one-tap answer in the reply = the owner typing those words and tapping Send (same signed say)
// P398 (#154): a tap on an older page = a reply to it (the same reply_to / excerpt as 回复), 「回复 #N」 on both sides
const sayReply = (words, p = null) => sayWords(words, t('p368.sent', {say: words}), p && Number.isInteger(p.id) ? p.id : null);
async function sayWords(text, done, replyTo = null){
  releaseComposer();
  if (C.estopOn()){ toast(t('r.say.stopped'), 3200); return false; }
  if (!C.connected()){ toast(t('p181.offline'), 3000); return false; }
  sendFollow();
  let r;
  try{ r = await C.api.say({sid: C.api.newSid(), text, att: [], reply_to: replyTo, excerpt: null}); }
  catch(_){ r = {ok: false, why: "other"}; }
  if (!r.ok){ toast(r.why === "stopped" ? t('r.say.stopped') : t('r.say.err.' + (SAY_ERR.includes(r.why) ? r.why : "other")), 3000); return false; }
  if (!C.p33()) C.saidOld(text);
  launchRocket();
  toast(done, 2200);
  return true;
}
// P286: the say's quote fields — the first as reply_to / excerpt (every host knows those), the rest as quote_more; a host
// without "mq5" gets quotes 2…5 in the text instead, labelled, so nothing the owner picked is lost.
function quoteArgs(text, qs){
  if (!qs.length) return {text};
  const [a, ...more] = qs, ex = q => typeof q.excerpt === "string" && q.excerpt ? q.excerpt : null;
  const out = {text, reply_to: a.id, excerpt: ex(a)};
  if (!more.length) return out;
  if (C.api.multiQuote && C.api.multiQuote()) return {...out, more: more.map(q => ({id: q.id, excerpt: ex(q)}))};
  const lines = [t('r.quote.fold', {n: more.length})];
  more.forEach((q, i) => {
    lines.push(t('r.quote.foldItem', {i: i + 2, src: "#" + q.id + ([hhmm(q.ts), q.speaker].filter(Boolean).map(x => " · " + x).join(""))}));
    lines.push(...String(ex(q) || q.line || "").split("\n").map(l => "> " + l));
  });
  return {...out, text: (text.trim() ? text + "\n\n" : "") + lines.join("\n")};
}
async function say(voiceReady = false){
  // P406 #163 (Leo): the focus leaves the composer only once a message really went (settleField, or the send below starts);
  // an Enter with nothing to send, while sending / transcribing or stopped, keeps the cursor where the words are
  const text = input.value;
  if (sending) return;
  const emptyAudio = atts.filter(a => (a.origin === "recording" && !LR.usable(a.file, a.dur)) ||
    (a.file.type.startsWith("audio/") && a.file.size < LR.MIN_REC_BYTES));
  if (emptyAudio.length){
    for (const a of emptyAudio) removeAtt(a);
    toast(t('r.rec.tooShort'), 2600); return; // leave the words and other files for the owner to send
  }
  if (!hasSayable() || sending || (asrBusy && voiceReady !== true)) return;                 // F27: files alone (no words) go too
  // P73 (ADR-A176): /add-friend is the friends page's signed fr_add, sent from here (the approval key never leaves the page);
  // it works while stopped (a request is not an Agent action) and opens the prefilled form when offline.
  const fa = /^[/\uff0f]add[-_]friend(?:[ \t]+([\s\S]*))?$/i.exec(text.trim());
  if (fa && !atts.length && !quotes.length){
    if (await addFriendCmd((fa[1] || "").trim())){ pushHist(text); settleField(text); autosize(); saveDraft(); }
    return;
  }
  // P332 (§33): /reset = 紧急修复 ① (the signed reset ladder) — before the stop / offline checks: it is how a stuck link is fixed
  if (/^[/\uff0f]reset$/i.test(text.trim()) && !atts.length && !quotes.length){
    pushHist(text); settleField(text); autosize(); saveDraft();
    C.resetConn?.();
    return;
  }
  // P181: /customize (中文「定制命令」) sends the same preset message as a long press on the ≡ button
  if (/^[/\uff0f]customize$/i.test(text.trim()) && !atts.length && !quotes.length){
    if (await sayPreset("cmds")){ pushHist(text); settleField(text); autosize(); saveDraft(); }
    return;
  }
  // P414 (#179②): 「!」 + one line = a web terminal line (js/bang.js), never a chat message; it never queues offline either
  const bang = !atts.length && !quotes.length && C.bang ? C.bang(text) : false;
  if (bang){ if (await bang){ pushHist(text); settleField(text); autosize(); saveDraft(); } return; }
  if (/^[/\uff0f](?:stop-all|stop_all|estop)$/i.test(text.trim())){ toast(t('p435.stopWhere'), 4000); return; }
  if (C.estopOn()){ toast(t('r.say.stopped'), 3200); return; }
  // 0.15.2: not connected, or older messages still waiting → it queues behind them (/stop, /clear never wait)
  if (!C.connected() || (outbox.items.length && !isCmdText(text))) return sayLater(text);
  const c = CMD_RE.exec(text.trim());
  if (c && CMDS.includes(c[1].toLowerCase()) && !atts.length && !quotes.length){
    if (!await runCmd(c[1].toLowerCase(), (c[2] || "").trim())) return;
    pushHist(text); settleField(text); autosize(); saveDraft(); sendFollow(); launchRocket();
    return;
  }
  sending = true; refreshSend();
  releaseComposer();
  sendFollow();
  const batch = atts.slice();
  const S = inflight = {phase: "upload", batch, sid: null, cancelled: false, delivered: false, cancelling: false, wake: null};
  const loop = launchLoop();
  const rt = quotes;
  const uploaded = list => { S.phase = "upload"; return Promise.race([Promise.all(list.map(a => a.promise)), new Promise(res => { S.wake = res; })]); };
  const attempt = live => { S.phase = "say"; S.sid = S.sid || C.api.newSid(); return C.api.say({sid: S.sid, att: live.map(a => a.id), ...quoteArgs(text, rt)}); };
  let landed = false;
  try{
    for (const a of batch) if (a.st === "held" || (a.st === "up" && a.retry)){ a.retry = false; stage(a); }
    if (batch.some(a => a.st === "up")){
      toast(t('r.say.waitUploads'), 60000);
      await uploaded(batch);
      toastOff();
    }
    if (S.cancelled) return;
    let live = batch.filter(a => atts.includes(a));
    const bad = live.filter(a => a.st !== "ready");
    if (bad.length){ toast(t('r.say.badAtt', {n: bad.length}), 3200); return; }
    let r = await attempt(live);
    // A host restart or a 30-minute pause forgets staged files: the phone still holds them, upload exactly those again.
    if (!r.ok && r.why === "att_gone" && !S.cancelled){
      const gone = new Set(Array.isArray(r.att) ? r.att : live.map(a => a.id));
      const redo = live.filter(a => gone.has(a.id));
      toast(t('r.say.reupload'), 60000);
      for (const a of redo){ a.up = null; stage(a); }
      await uploaded(redo);
      toastOff();
      live = live.filter(a => atts.includes(a));
      if (live.every(a => a.st === "ready") && !S.cancelled){ S.sid = null; r = await attempt(live); }
    }
    if (S.cancelled) return;
    if (r.ok || S.delivered){
      landed = true;
      pushHist(text);
      if (rt.length && quotes === rt) cancelReply();
      settleField(text);     // P127: also when a dictation 「。」 / IME commit landed while say_res was on its way
      if (r.state === "queued" && !early.has(S.sid)) queued.push({sid: S.sid, text, atts: live.slice(), rt, turn: r.turn, at: Date.now()});
      else if (Number.isInteger(r.turn)){ activeTurn = r.turn; sawDelivered(); }
      early.delete(S.sid);
      for (const a of live) removeAtt(a, false);
      saveDraft();
      if (!C.p33()) C.saidOld(text);
    } else if (r.why === "stopped"){
      toast(t('r.say.stopped'), 3600);
    } else {
      toast(t('r.say.err.' + (SAY_ERR.includes(r.why) ? r.why : "other")), 3000);
    }
  }catch(err){ toast(t('r.say.err.other'), 3000); }
  finally{
    loop.stop(landed);
    if (landed) launchRocket();
    if (landed && sentStay && !onNewestPage()) toast(t('p340b.sentStay'), 2200);   // P340: it went; he stays where he reads
    if (S.cancelled && !S.forgotten) toast(t('r.say.cancelled'), 2400);
    inflight = null;
    sending = false; autosize(); paintQueued();
  }
}

// ---- the offline queue (0.15.2, PROTOCOL §14; outbox.js) ---------------------------------------------------
// Not connected (no network, connecting, the computer away): Send still takes the message. Its words, quote and a sid
// chosen now go into the sealed outbox and show as a pending line above the field with 「网络恢复后自动发送」; on `ready`
// they go out in order, one say at a time. Attachments: the files cannot be stored (only their names are), so a message
// with files is held whole in this page's memory and uploaded + sent on `ready`; after a reload its files are gone and
// its line says so — when the drain reaches it, its words come back to the field instead of going out without them.
const held = new Map();       // sid → the tray entries of a queued message (memory only; uploads continue under a.out)
const outbox = new Outbox({}, {
  say: outSay,
  live: () => C.connected(),
  hostNow: hostId,
  done: outDone,
  changed: () => { paintOut(); if (C) refreshSend(); },
});
function paintOut(){
  if (!C) return;
  // P282 (追加): a message queued behind a running turn shows in ONE place — its own page, marked 「排队中」 (showPage) —
  // never also as a pending line here; the field's 「取消」 still takes it back. This line is the offline outbox only.
  paintOutbox(el("outbox"), outbox.items, {line: t(peer.rescue ? 'p424.queueRescue' : 'offline.line'), lostNote: t('offline.attLost'),
    isLost: e => !!(e.att && e.att.length && !held.has(e.sid)), showLine: !C.connected() && (outbox.items.length > 0 || navigator.onLine === false)});
}
function drainOut(){ if (outbox.items.length) outbox.drain(); }
function sayLater(text){
  // 全部停下 / 清空 / 换模型 … are "now or not at all": a stop that lands minutes later would cut whatever runs then.
  // P424 (#145 security): no built-in command queues — the words stay in the field, a line says to try again later.
  if (isCmdText(text)){ toast(t(peer.rescue ? 'p424.cmdLater' : 'p424.cmdOffline'), 3200); return; }
  const bad = atts.filter(a => a.st === "failed");
  if (bad.length){ toast(t('r.say.badAtt', {n: bad.length}), 3200); return; }
  const list = atts.slice(), rt = quotes;
  const e = {sid: C.api.newSid(), text, ts: Date.now(), rt: rt.length ? {id: rt[0].id, excerpt: rt[0].excerpt || null} : null,
    ...(rt.length > 1 ? {more: rt.slice(1).map(q => ({id: q.id, excerpt: q.excerpt || null, ts: q.ts, speaker: q.speaker}))} : {}),
    att: list.length ? list.map(a => ({name: a.name, kind: a.kind})) : null};
  if (list.length) held.set(e.sid, list);           // before add(): its line is painted with the files still here
  if (outbox.add(e) === "full"){ held.delete(e.sid); toast(t('offline.full', {n: OUT_MAX}), 3200); return; }
  if (list.length){
    for (const a of list){ a.out = true; atts.splice(atts.indexOf(a), 1); if (a.node) a.node.remove(); }
    paintTray();
  }
  pushHist(text);
  if (rt.length && quotes === rt) cancelReply();
  settleField(text);
  autosize(); saveDraft(); sendFollow();
  if (C.connected()) drainOut();
}
// One queued message → say_res-like (outbox.verdict decides). Files: upload (or resume) first, then the say with the
// sid fixed at queue time; att_gone (host restarted / 30 min) → those files once more, same sid.
async function outSay(e){
  const list = held.get(e.sid);
  if (e.att && e.att.length && !list) return {ok: false, why: "att_lost"};
  const live = list || [];
  for (let n = 0; ; n++){
    for (const a of live) if (a.st !== "ready") stage(a);
    if (live.length) await Promise.all(live.map(a => a.promise));
    if (live.some(a => a.st === "up")) return {ok: false, why: "offline"};          // the connection went again
    if (live.some(a => a.st !== "ready")) return {ok: false, why: "att_failed"};
    const r = await C.api.say({sid: e.sid, att: live.map(a => a.id), ...quoteArgs(e.text, e.rt ? [e.rt, ...(e.more || [])] : [])});
    if (r.ok || r.why !== "att_gone" || n) return r;
    const gone = new Set(Array.isArray(r.att) ? r.att : live.map(a => a.id));
    for (const a of live) if (gone.has(a.id)){ a.up = null; a.st = "up"; }
  }
}
let resent = 0, resentTimer = 0;
function outDone(e, r, kind){
  const list = held.get(e.sid) || [];
  held.delete(e.sid);
  if (kind === "sent"){
    resent++; clearTimeout(resentTimer);   // P424 (#145): what waited for the link is told once it went out
    resentTimer = setTimeout(() => { if (resent) toast(t('p424.resent', {n: resent}), 3200); resent = 0; }, 800);
    if (r.ok && r.state === "queued" && !early.has(e.sid)) queued.push({sid: e.sid, text: e.text, atts: list.slice(), rt: null, turn: r.turn, at: Date.now()});
    else if (r.ok && Number.isInteger(r.turn)){ activeTurn = r.turn; sawDelivered(); }
    early.delete(e.sid);
    for (const a of list) if (a.url){ URL.revokeObjectURL(a.url); a.url = null; }
    for (const a of list) if (a.rec){ LR.dropSeg(a.rec.take, a.rec.seg); a.rec = null; }   // P182
    if (r.ok && !C.p33()) C.saidOld(e.text);
    paintQueued();
    return;
  }
  // refused for good: the words come back to an empty field, the files to the tray (still staged, like a withdraw)
  if (!input.value){ input.value = e.text; input.dispatchEvent(new Event("input")); }
  for (const a of list){
    a.out = false;
    if (atts.length < MAX_ATT){ a.node = null; atts.push(a); paintChip(a); }
    else { if (a.up) a.up.drop(); if (a.url){ URL.revokeObjectURL(a.url); a.url = null; } }
  }
  paintTray();
  if (r.why === "att_lost") toast(t('offline.attBack'), 4200);
  else if (r.why === "att_failed") toast(t('r.say.badAtt', {n: list.filter(a => a.st !== "ready").length || 1}), 3200);
  else if (r.why === "stopped") toast(t('r.say.stopped'), 3600);
  else toast(t('r.say.err.' + (SAY_ERR.includes(r.why) ? r.why : "other")), 3000);
}

// ---- the ≡ menu and `/` completion (§10.12) --------------------------------------------------------------
// Group 1 = Agent J's one-tap commands (they run at once; 清空 asks first) and 全部停下; then the host's menu items
// (menu.json in the Agent's folder — they only INSERT text, the human still sends), then the Agent's own skills.
const MENU_LIMITS = {items: 60, cmd: 64, desc: 200, group: 32};
const MENU_CMD_RE = /^\/[A-Za-z0-9][A-Za-z0-9_:.\-]{0,62}$/;
const RUN = ["compact", "clear", "new", "model", "context", "cost", "usage", "status", "stop", "update"];
const runDescription = c => t('r.run.' + c);
let hostMenu = null;          // {items:[[cmd, desc, group]], skills:[[cmd, desc]], native:[[cmd, desc, "", "run" | "agent" | "none"]], kind}
// P412 (#175): the harness the list mirrors; a `status` naming another one (after /switch-harness) asks for the list again
const HARNESS = {claude: "Claude Code", codex: "Codex", opencode: "OpenCode"};
const harnessName = () => HARNESS[hostMenu?.kind] || hostMenu?.kind || "Agent";
// #175 定稿 (Leo 20:2x): every one can be picked and typed; the host runs it (run), has the Agent do it (agent), or says why not (none)
const tuiToast = (why) => toast(why || t('p412.tui', {h: harnessName()}), 2800);
const NATIVE_ST = new Set(["run", "agent", "none"]);
function nativeTag(st){ const i = document.createElement("i"); i.className = "ntag"; i.dataset.via = st; i.textContent = t(st === "run" ? 'p412.viaRun' : 'p412.viaAgent'); return i; }
const noneWhy = (why) => (why || "").split(" · ").slice(1).join(" · ") || why;
let menuKind = null;
function kindSeen(k){
  if (!k || k === menuKind) return;
  const had = menuKind; menuKind = k;
  if (had && hostMenu && hostMenu.kind !== k) loadMenu().catch(() => {});
}
// P238 (Leo 10-13 23:31): the version of the serve this connection talks to (every `preferences` message carries it). The
// ⬆ row trusts it over a menu answer cached from before an upgrade's restart — the host log showed the phone back on the
// new 0.20.2a1 serve at 23:29:07 and the row still saying 「0.20.1a1 → 0.20.2a1 · 点击即授权升级」 at 23:31.
let liveHostVer = null;
export function hostVersionIs(v){
  const nv = typeof v === "string" && v ? v.slice(0, 40) : null;
  if (nv === liveHostVer) return;
  liveHostVer = nv;
  if (!menu.hidden) renderMenu();
}
// P182: the meeting-notes command (its zh name is in the dictionary; en /meeting-notes): audio in the tray (or the latest recording) → transcript → minutes; addresses written
// after it get the minutes by e-mail (the host only sends to addresses the owner typed). `all`: the other language's name
// too, for the type-ahead.
function toolItems(all){
  const cmd = t('r.meet.cmd'), out = [[cmd, t('r.meet.desc'), ""]];
  if (all && cmd !== "/meeting-notes") out.push(["/meeting-notes", t('r.meet.desc'), ""]);
  if (hostMenu?.father) out.push(hostMenu.father);
  return out;
}
// P238: /switch-harness (ADR-P155) sits among Agent J's one-tap commands (after /customize), with the page's own sentence — the host's list could
// leave it out (before 0.20.3 an owner command set with `agentj menu add` replaced the whole list), so any host that knows
// it (0.18.6+) gets it here; an older one never does (there the words would only reach the Agent as text).
const SWITCH_CMD = "/switch-harness";
const SWITCH_SINCE = [0, 18, 6];
function hostAtLeast(v, want){
  const m = /^(\d+)\.(\d+)\.(\d+)/.exec(typeof v === "string" ? v : "");
  if (!m) return false;
  for (let i = 0; i < 3; i++){ const n = +m[i + 1]; if (n !== want[i]) return n > want[i]; }
  return true;
}
// PEP 440 subset the host uses (0.20.2, 0.20.2a1, 0.20.2rc1): -1 / 0 / 1; not understood → NaN (never 「up to date」)
function verCmp(a, b){
  const p = v => { const m = /^(\d+)\.(\d+)(?:\.(\d+))?(?:(a|b|rc)(\d+))?$/.exec(typeof v === "string" ? v.trim() : "");
    return m && [+m[1], +m[2], +(m[3] || 0), {a: 0, b: 1, rc: 2}[m[4]] ?? 3, +(m[5] || 0)]; };
  const x = p(a), y = p(b);
  if (!x || !y) return a && a === b ? 0 : NaN;
  for (let i = 0; i < 5; i++) if (x[i] !== y[i]) return x[i] > y[i] ? 1 : -1;
  return 0;
}
const switchOffered = () => !!hostMenu && (hostMenu.switchHarness || hostAtLeast(hostMenu.upgrade.current, SWITCH_SINCE));
const switchItems = () => switchOffered() ? [[SWITCH_CMD, t('p238.switch'), ""]] : [];
let menuItems = [];           // what the type-ahead filters: [cmd, desc, group]
function insertSlash(cmd){
  const v = input.value;
  input.value = (v && !v.endsWith(" ") ? v + " " : v) + cmd + " ";
  focusComposer();
  input.setSelectionRange(input.value.length, input.value.length);
  autosize();
}
function menuFromHost(j){
  if (!j || !Array.isArray(j.items)) return null;
  const items = [], skills = [], seen = new Set();
  // The host keeps up to 60 owner rows, then appends its two built-in Agent commands.
  const builtins = j.items.slice(MENU_LIMITS.items, MENU_LIMITS.items + 2).filter(it =>
    it && (it.cmd === SWITCH_CMD || it.cmd === "/feedback-to-agentj-father"));
  for (const it of j.items.slice(0, MENU_LIMITS.items).concat(builtins)){
    if (!it || typeof it.cmd !== "string" || typeof it.desc !== "string") continue;
    const cmd = it.cmd.slice(0, MENU_LIMITS.cmd);
    if (!MENU_CMD_RE.test(cmd) || seen.has(cmd)) continue;
    seen.add(cmd);
    items.push([cmd, it.desc.slice(0, MENU_LIMITS.desc), typeof it.group === "string" ? it.group.slice(0, MENU_LIMITS.group) : ""]);
  }
  for (const s of (Array.isArray(j.skills) ? j.skills : []).slice(0, 80)){
    if (!s || typeof s.cmd !== "string") continue;
    const cmd = s.cmd.startsWith("/") ? s.cmd : "/" + s.cmd;
    if (!MENU_CMD_RE.test(cmd) || seen.has(cmd)) continue;
    seen.add(cmd);
    skills.push([cmd, typeof s.desc === "string" ? s.desc.slice(0, MENU_LIMITS.desc) : "", t('r.menu.skills')]);
  }
  // P412 (#175): the harness's own commands (host harness_cmds.py) with their route; an older host's "tui" = none
  const native = [];
  for (const x of (Array.isArray(j.native) ? j.native : []).slice(0, 120)){
    if (!x || typeof x.cmd !== "string" || !MENU_CMD_RE.test(x.cmd) || seen.has(x.cmd)) continue;
    seen.add(x.cmd);
    native.push([x.cmd, typeof x.desc === "string" ? x.desc.slice(0, MENU_LIMITS.desc) : "", "", NATIVE_ST.has(x.state) ? x.state : "none"]);
  }
  const switchHarness = items.some(it => it[0] === SWITCH_CMD);
  const father = items.find(it => it[0] === "/feedback-to-agentj-father");
  return {father, items: items.filter(it => it[0] !== SWITCH_CMD && it !== father), skills: skills.filter(it => it[0] !== SWITCH_CMD), switchHarness, reload: j.reload === true,
          native, kind: typeof j.kind === "string" ? j.kind.slice(0, 32) : null,
          upgrade: j.upgrade && typeof j.upgrade === "object" ? j.upgrade : {}, source: j.source === "file" ? "file" : "default"};
}
const IN_APP = / (?:JarvisApp|AgentJApp)\/\d/.test(navigator.userAgent);
const CLIP_READ = !!(navigator.clipboard && navigator.clipboard.read) && !IN_APP;
function renderMenu(){
  const nodes = [];
  const group = (label) => { const h = document.createElement("div"); h.className = "menugroup"; h.setAttribute("role", "presentation"); h.textContent = label; nodes.push(h); };
  const item = (cmdText, desc, data) => {
    const b = document.createElement("button");
    b.type = "button"; b.setAttribute("role", "menuitem");
    for (const [k, v] of Object.entries(data)) b.dataset[k] = v;
    const n = document.createElement("b"); n.textContent = cmdText;
    const d = document.createElement("span"); d.textContent = desc;
    if (desc && desc.length > 40) b.title = desc;   // P238: the row shows two lines at most
    b.append(n, d);
    nodes.push(b);
    return b;
  };
  group(t('r.menu.run'));
  for (const c of RUN) {
    if (c !== 'update') { item("/" + c, runDescription(c), {run: c}); continue; }
    // P238: the row follows the version on disk — between an upgrade's install and serve's restart (`installed` ≠
    // `current`) it says so and cannot be tapped; a newer-or-equal copy is 「已是最新」 (a dev build ahead of the release too)
    const u = {...(hostMenu?.upgrade || {}), ...(liveHostVer ? {current: liveHostVer} : {})};
    const have = u.installed || u.current, latest = u.latest || '–';
    const pending = !!(u.installed && u.installed !== u.current && verCmp(u.installed, u.latest) >= 0);
    const same = !pending && !!u.latest && verCmp(have, u.latest) >= 0;
    const desc = pending ? t('p238.updRestart', {v: u.installed}) : t(same ? 'r.update.current' : 'r.update.authorize');
    const b = item(t('r.update.title'), pending ? desc : `${have || '–'} → ${latest} · ${desc}`, {run: c});
    b.disabled = same || pending;
  }
  if (hostMenu?.reload) item("/reload", t('p320.reloadDesc'), {run: "reload"}).id = "reloadEntry";   // @ix app.reload_host (P320: a host that answers /reload)
  item("/reset", t('p332.menuDesc'), {}).id = "resetEntry";   // P332 (§33): 紧急修复 ① — on every host (an old one simply never answers)
  item("/customize", t('p181.customize'), {prompt: "cmds"});   // P181: 定制命令 → the preset message (≡ hold does the same)
  for (const [cmd, why] of switchItems()) item(cmd, why, {}).id = "switchEntry";   // P238: one tap sends it
  if (C.estopOn()) { const resume = item("■", t('estop.resume'), {estop: "resume"}); menuIcon(resume, "stop"); }
  // P182: Agent J's own tools — inserted like a menu item (the human adds files / addresses and sends)
  group(t('r.menu.tools'));
  for (const [cmd, why] of toolItems()) item(cmd, why, {slash: cmd});
  const host = hostMenu || {items: RUN.map(c => ["/" + c, runDescription(c), ""]), skills: []};
  menuItems = [];
  const fromHost = hostMenu ? host.items : [];
  if (fromHost.length){
    let g = null;
    for (const [cmd, why, gg] of fromHost){
      const label = gg || t('r.menu.insert');
      if (label !== g){ group(label); g = label; }
      item(cmd, why, {slash: cmd});
    }
  }
  if (host.skills.length){ group(t('r.menu.skills')); for (const [cmd, why] of host.skills) item(cmd, why, {slash: cmd}); }
  menuItems = RUN.map(c => ["/" + c, runDescription(c), ""]).concat(hostMenu?.reload ? [["/reload", t('p320.reloadDesc'), ""]] : [], [["/reset", t('p332.menuDesc'), ""]], [["/customize", t('p181.customize'), ""]], toolItems(true), switchItems(), fromHost, host.skills);
  // P412 (#175): Agent J's own first — a harness command with the same name shows once (Agent J's)
  const native = (host.native || []).filter(it => !menuItems.some(m => m[0] === it[0]));
  if (native.length){
    group(t('p412.native', {h: harnessName()}));
    for (const [cmd, why, , st] of native){
      if (st === "none"){ const b = item(cmd, why, {tui: noneWhy(why)}); b.classList.add("tui"); b.setAttribute("aria-disabled", "true"); }
      else item(cmd, why, {slash: cmd}).querySelector("b").append(nativeTag(st));
    }
  }
  menuItems = menuItems.concat(native);
  const k = item("?", t('r.keys.title'), {});
  k.className = "keysentry"; k.id = "keysEntry";
  if (CLIP_READ){ const c = item("📋", t('r.menu.clip'), {}); c.className = "clipentry"; c.id = "clipEntry"; }
  const modelsEntry = item('◉', t('models.title'), {}); modelsEntry.id = 'modelsEntry';
  if (IN_APP){ const a = item("⚙", t('r.menu.app'), {}); a.id = "appEntry"; }
  const sl = item("🔕", t("r.silent.menu"), {}); sl.id = "silentEntry";
  // P187: the list's own entries wear the menu icon set (index.html #menuIcons) instead of a glyph
  const kinds = {keysEntry: "keys", clipEntry: "clip", modelsEntry: "models", appEntry: "app", silentEntry: "silent"};
  for (const b of nodes) if (kinds[b.id]) menuIcon(b, kinds[b.id]);
  menuList.replaceChildren(...nodes);
}
function menuIcon(b, kind){
  const svg = el("menuIcons").content.querySelector(`[data-kind="${kind}"]`);
  if (!svg) return;
  const ic = document.createElement("b"); ic.className = "mi"; ic.append(svg.cloneNode(true));
  b.firstChild.replaceWith(ic); b.classList.add("iconrow");
}
async function loadMenu(){
  const j = await C.api.menu();
  if (!j) return;
  hostMenu = menuFromHost(j);
  if (hostMenu?.kind) menuKind = menuKind || hostMenu.kind;
  renderMenu(); placeSheet();
}
function closeMenu(){ menu.hidden = true; delete el("menuSheet").dataset.drag; el("menuSheet").style.transform = ""; sheetDrag = null; }
// P238: the list is a card rising from the 5-hour bar (menu.css .cmdsheet): its bottom edge on the bar, its left / width those
// of the tool row (so it lines up with ≡ on a phone and with the composer on a wide screen), at most 60 % of what is visible.
function placeSheet(){
  if (menu.hidden) return;
  const box = menu.getBoundingClientRect(), bar = el("m5h"), row = el("tools");
  const lab = bar && bar.querySelector(".lab");   // its 「5H 64%」 label sits above the line: the card stops above both
  let edge = bar && bar.getClientRects().length ? Math.min(bar.getBoundingClientRect().top, lab && lab.getClientRects().length ? lab.getBoundingClientRect().top : Infinity) : 0;
  if (!(edge > box.top)) edge = menu.parentElement.getBoundingClientRect().top;
  const r = row && row.getClientRects().length ? row.getBoundingClientRect() : {left: 8, width: box.width - 16};
  const gap = 8, w = Math.min(r.width, 480), room = edge - gap - Math.max(box.top + Math.max(gap, parseFloat(getComputedStyle(menu).paddingTop) || 0), sheetCeil());   // below the notch and (P302 追加 28) never above the message card's top
  menu.style.setProperty("--sheet-bottom", Math.max(0, box.bottom - edge + gap) + "px");
  menu.style.setProperty("--sheet-left", Math.max(gap, r.left) + "px");
  menu.style.setProperty("--sheet-w", Math.max(0, Math.min(w, box.width - 2 * gap)) + "px");
  menu.style.setProperty("--sheet-max", Math.max(160, Math.min(box.height * 0.6, room)) + "px");
}
function openMenu(){
  if (!menu.hidden) return;
  closeSug();
  menu.hidden = false;
  el("menuBody").scrollTop = 0;
  placeSheet(); renderMenu(); loadMenu();
}
function toggleMenu(){ if (menu.hidden) openMenu(); else closeMenu(); }
// Pull-down: a drag that starts on the grab / header, or on the list while it is at its top, moves the card; past 64 px (or a
// quick flick) it slides away. Every touch inside stays inside (the reply cards underneath never page sideways).
let sheetDrag = null;
function sheetTouch(){
  const sheet = el("menuSheet"), body = el("menuBody");
  sheet.addEventListener("touchstart", e => {
    if (e.touches.length !== 1){ sheetDrag = null; return; }
    const tt = e.touches[0];
    sheetDrag = {y: tt.clientY, x: tt.clientX, t: performance.now(), dy: 0, on: false,
                 top: !body.contains(e.target) || body.scrollTop <= 0};
  }, {passive: true});
  sheet.addEventListener("touchmove", e => {
    e.stopPropagation();
    const d = sheetDrag; if (!d || e.touches.length !== 1) return;
    const tt = e.touches[0], dy = tt.clientY - d.y, dx = tt.clientX - d.x;
    if (!d.on){
      if (Math.abs(dx) > Math.abs(dy)){ if (e.cancelable) e.preventDefault(); return; }   // never sideways
      if (!(d.top && dy > 6 && (body.scrollTop <= 0 || !body.contains(e.target)))) return;
      d.on = true; d.y = tt.clientY; sheet.dataset.drag = "on";
    }
    if (e.cancelable) e.preventDefault();
    d.dy = Math.max(0, tt.clientY - d.y);
    sheet.style.transform = `translateY(${d.dy}px)`;
  }, {passive: false});
  const end = () => {
    const d = sheetDrag; sheetDrag = null;
    if (!d || !d.on) return;
    const quick = d.dy > 24 && d.dy / Math.max(1, performance.now() - d.t) > 0.6;
    sheet.style.transform = "";
    if (d.dy > 64 || quick){ sheet.dataset.drag = "off"; setTimeout(closeMenu, 180); }
    else { sheet.dataset.drag = "back"; setTimeout(() => { if (sheet.dataset.drag === "back") delete sheet.dataset.drag; }, 200); }
  };
  sheet.addEventListener("touchend", end);
  sheet.addEventListener("touchcancel", end);
  // the scrim: a tap closes, a drag does nothing at all (no page scroll, no card paging behind it)
  menu.addEventListener("touchmove", e => { if (!sheet.contains(e.target) && e.cancelable) e.preventDefault(); }, {passive: false});
  menu.addEventListener("click", e => { if (e.target === menu) closeMenu(); });
}
// Type-ahead: a "/" at the very start of the field opens the list, filtered as you type (prefix matches first).
let sugList = [], sugAt = 0, sugDismissed = null, sugLoading = false, sugLoadedAt = 0;
const SLASH_RE = /^[/\uff0f]([^\s]*)$/;
function sugMatches(q){
  q = q.toLowerCase();
  const pre = [], sub = [], seen = new Set();
  for (const it of menuItems){
    if (seen.has(it[0])) continue;
    const name = it[0].slice(1).toLowerCase();
    if (name.startsWith(q)){ pre.push(it); seen.add(it[0]); }
    else if (q && (name.includes(q) || it[1].toLowerCase().includes(q))){ sub.push(it); seen.add(it[0]); }
  }
  return pre.concat(sub);
}
function closeSug(){
  if (sug.hidden) return;
  sug.hidden = true; sugList = [];
  input.removeAttribute("aria-activedescendant");
}
function paintSug(){
  [...sug.children].forEach((b, i) => b.setAttribute("aria-selected", i === sugAt ? "true" : "false"));
  const b = sug.children[sugAt];
  if (b){ input.setAttribute("aria-activedescendant", b.id); b.scrollIntoView({block: "nearest"}); }
}
function updateSug(){
  const v = input.value, m = SLASH_RE.exec(v);
  // P302 (owner: 「输入 / 没有弹出 slash command list」): a dismissal only holds while the field still reads what was dismissed —
  // once the text changes it is forgotten (it used to stick, so 「/」 → Esc → delete → 「/」 never opened the list again)
  if (sugDismissed !== null && v !== sugDismissed) sugDismissed = null;
  if (!m || v === sugDismissed || document.activeElement !== input){ closeSug(); return; }
  sugDismissed = null;
  // the host's menu (its skills come from the harness once it started) is re-read whenever the list opens afresh
  // (at most once per 5 s, so an empty list never loops)
  if (sug.hidden && !sugLoading && Date.now() - sugLoadedAt > 5000){
    sugLoading = true;
    loadMenu().catch(() => {}).finally(() => { sugLoading = false; sugLoadedAt = Date.now(); if (SLASH_RE.test(input.value)) updateSug(); });
  }
  const list = sugMatches(m[1]);
  if (!list.length){ closeSug(); return; }
  sugList = list;
  sugAt = 0;
  const q = m[1].toLowerCase();
  sug.replaceChildren(...list.map(([cmd, why, , st], i) => {
    const b = document.createElement("button");
    b.type = "button"; b.tabIndex = -1; b.id = "sug" + i; b.dataset.sug = String(i);
    b.setAttribute("role", "option");
    if (st === "none"){ b.classList.add("tui"); b.setAttribute("aria-disabled", "true"); }
    const n = document.createElement("b");
    if (q && cmd.slice(1).toLowerCase().startsWith(q)){
      const mk = document.createElement("mark"); mk.textContent = cmd.slice(0, q.length + 1);
      n.append(mk, document.createTextNode(cmd.slice(q.length + 1)));
    } else n.textContent = cmd;
    if (st === "run" || st === "agent") n.append(nativeTag(st));
    const d = document.createElement("span"); d.textContent = why;
    b.append(n, d);
    return b;
  }));
  closeMenu();
  sug.hidden = false;
  capAbove(sug); requestAnimationFrame(() => requestAnimationFrame(() => { if (!sug.hidden) capAbove(sug); }));   // P302: never under the status bar while the keyboard is up
  paintSug();
}
function pickSug(i){
  const it = sugList[i]; if (!it) return;
  if (it[3] === "none"){ tuiToast(noneWhy(it[1])); return; }   // P412 (#175): no web page can do it — the typed text stays
  input.value = it[0] + " ";
  closeSug();
  focusComposer();
  input.setSelectionRange(input.value.length, input.value.length);
  input.dispatchEvent(new Event("input"));
}

// ---- voice: hold to talk (relay ADR-048 / 052; §10.9) ------------------------------------------------
// Pointer hold = record and release to send; tap / double tap = lock; slide clearly away = cancel (cancelZone). Locked takes (P182: up to 200 min in 20-min parts), m / m m on a keyboard. A clean
// keyboard release keeps a draft: the take is turned into 16 kHz WAV here, transcribed on the customer's own computer, and the words
// are APPENDED to the field. A take longer than the field's threshold travels as an audio attachment instead.
// P181 (owner: 「手抖动就不应该取消」): a take is cancelled only when the pointer ends up clearly outside the button —
// ≥ PTT_OUT_PX (mouse) / PTT_OUT_TOUCH_PX (finger) from its edge, or above it after sliding up at least one button height
// + PTT_UP_EXTRA_PX — and is released there; coming back within PTT_BACK_PX of that line un-cancels (no flicker).
const PTT_MIN_MS = 500, PTT_OUT_PX = 80, PTT_OUT_TOUCH_PX = 96, PTT_UP_EXTRA_PX = 24, PTT_UP_EXTRA_TOUCH_PX = 32, PTT_BACK_PX = 24;
// P272: first pointer tap locks on release; its second tap within DTAP_MS keeps that lock.
// Cancellation never locks. Keyboard m / m m retains its draft / lock behavior.
const DTAP_MS = 450, LOCK_WARN_MS = 60 * 1000, LOCK_REARM_MS = 400;
let ptt = null;
let lockEndAt = -1e9, lastTapUp = -1e9, lockCount = 0;
/** P181: is (x, y) in the cancel zone of the mic button? `was` = currently cancelled (hysteresis). Exported for the tests. */
export function cancelZone(r, x0, y0, x, y, touch, was = false){
  const back = was ? PTT_BACK_PX : 0;
  const dx = Math.max(r.left - x, 0, x - r.right), dy = Math.max(r.top - y, 0, y - r.bottom);
  const out = Math.hypot(dx, dy);
  if (out >= (touch ? PTT_OUT_TOUCH_PX : PTT_OUT_PX) - back) return true;
  const up = y0 - y;
  return y < r.top && up >= r.height + (touch ? PTT_UP_EXTRA_TOUCH_PX : PTT_UP_EXTRA_PX) - back;
}
// P182: a take is cut every SEG_MS into segments that each become one ordered audio attachment while the recording goes on;
// at most REC_SEGS of them (200 minutes), fewer when the tray already holds files. Each recorder chunk (REC_SLICE_MS) is
// sealed into IndexedDB (longrec.js) first, so a reload, a crash or a lost connection loses a few seconds at most.
// REC_BPS: speech-grade compressed audio (Opus in WebM on Chromium / Firefox, AAC in MP4 on Safari) — ≈ 7 MB per segment.
// Test hook: localStorage["aj.test.segMs"] shortens the segment (only honoured below one minute, so it cannot lengthen one).
const SEG_MS_REAL = 20 * 60 * 1000, REC_SEGS = 10, REC_SLICE_MS = 4000, REC_BPS = 48000, WAV_MAX_S = 600, MUTE_MS = 3000;
const segMs = () => { const v = +(localStorage.getItem("aj.test.segMs") || 0); return v >= 1000 && v < 60000 ? v : SEG_MS_REAL; };
const REC_TYPES = ["audio/webm;codecs=opus", "audio/mp4;codecs=mp4a.40.2", "audio/mp4", "audio/ogg;codecs=opus", "audio/webm"];
const recTotalMs = p => p.recT0 ? (p.recT1 || performance.now()) - p.recT0 : 0;
const capMs = p => p.maxSegs * p.segMs;
function pttUI(p){
  if (p) speaker?.stop();
  const b = el("mic"), box = el("ptt");
  b.classList.toggle("rec", !!p && !p.cancel);
  b.classList.toggle("cancel", !!p && p.cancel);
  b.classList.toggle("lock", !!p && p.locked);
  if (!p){ box.hidden = true; box.classList.remove("lock", "cancel"); document.body.classList.remove("recblur"); el("pttSeg").hidden = true; el("pttAct").hidden = true; el("pttNote").hidden = true; el("micLabel").textContent = asrBusy ? t('r.asr.tag') : restingMicLabel(); return; }
  box.hidden = false;
  box.classList.toggle("cancel", p.cancel);
  box.classList.toggle("lock", p.locked);
  // P205 / P346 (#54): every take (held or locked) blurs the page behind in its own state colour (app.css .ptt);
  // locked → cancel · end · send under the circle (a held take has a finger on the glass, release ends it)
  document.body.classList.add("recblur");
  el("pttAct").hidden = !p.locked;
  const note = el("pttNote");
  note.textContent = !p.locked || !p.rec ? ""
    : matchMedia("(pointer:coarse)").matches ? (p.wlFail ? t('r.ptt.awakeNoLock') : t('r.ptt.awake')) : "";
  note.hidden = !note.textContent;
  el("pttTime").dataset.lock = t('r.ptt.locked');
  const s = p.rec ? Math.floor(recTotalMs(p) / 1000) : 0;
  el("pttTime").textContent = p.rec ? Math.floor(s / 60) + ":" + String(s % 60).padStart(2, "0") : "…";
  // P182: the segment line — which part, the ceiling, and (on a phone) keep the screen on
  const seg = el("pttSeg");
  seg.hidden = !p.rec || (!p.locked && !p.seg);
  if (!seg.hidden){
    const parts = [t('r.ptt.seg', {n: p.seg + 1, max: p.maxSegs, m: Math.round(capMs(p) / 60000)})];
    if (p.seg) parts.push(t('r.ptt.segUp', {n: p.seg}));
    seg.textContent = parts.join(" · ");
  }
  if (p.rec && p.T == null) p.T = voiceThresholdS();
  const long = p.rec && recTotalMs(p) / 1000 > p.T;
  const keyboardTake = p.viaM || p.id === "key";
  const left = capMs(p) - recTotalMs(p);
  if (p.locked){
    const stop = p.viaM ? t('r.ptt.anyKey') : t('r.ptt.tapEnd');
    el("pttHint").textContent = p.cancel ? t('r.ptt.releaseCancel')
      : p.rec && left <= LOCK_WARN_MS ? t('r.ptt.left', {s: Math.max(0, Math.ceil(left / 1000)), stop})
      : !p.rec ? t('r.ptt.opening') : p.viaM ? t('r.ptt.anyKeyEsc')
      : matchMedia("(pointer:coarse)").matches ? t('r.ptt.tapEndSlide') : t('r.ptt.tapEndEsc');
    el("micLabel").textContent = p.cancel ? t('r.mic.releaseCancel') : stop;
    return;
  }
  el("pttHint").textContent = p.cancel ? t('r.ptt.releaseCancel') : p.pend ? (p.viaM ? t('r.ptt.lockM') : t('r.ptt.lockTap'))
    : p.rec ? t(keyboardTake ? (long ? 'r.ptt.keyLongHint' : 'r.ptt.keyHint') : (long ? 'r.ptt.longHint' : 'r.ptt.hint')) : t('r.ptt.opening');
  el("micLabel").textContent = p.cancel ? t('r.mic.releaseCancel') : t(keyboardTake ? (long ? 'r.mic.keyReleaseVoice' : 'r.mic.keyReleaseText') : (long ? 'r.mic.releaseVoice' : 'r.mic.releaseText'));
}
function pttTap(p){
  p.id = null; p.tUp = performance.now();
  p.pend = setTimeout(() => { p.pend = 0; pttEnd(p, "release"); }, DTAP_MS);
  pttUI(p);
}
function pttLock(p){
  clearTimeout(p.pend); p.pend = 0; p.tUp = 0; lastTapUp = -1e9; lockCount++;
  p.locked = true; p.id = null; p.cancel = false; p.lockT0 = performance.now();
  if (p.rec) wakeOn(p);
  try{ if (navigator.vibrate) navigator.vibrate([15, 60, 15]); }catch(_){}
  pttUI(p);
}
// P182: Screen Wake Lock keeps the phone from locking itself while a take runs (a lock cuts the microphone on iOS and on
// Android Chrome); taken again when the page comes back, released when the take ends. Unsupported / refused → the hint says so.
function wakeOn(p){
  if (p.wl && !p.wl.released) return;
  if (!navigator.wakeLock || !navigator.wakeLock.request){ p.wlFail = true; return; }
  navigator.wakeLock.request("screen").then(w => { if (p.over) w.release().catch(() => {}); else { p.wl = w; p.wlFail = false; } }, () => { p.wlFail = true; });
}
function wakeOff(p){ if (p.wl){ p.wl.release().catch(() => {}); p.wl = null; } }
// P205 (Leo 10-13: a locked Android phone handed the page 23 s of digital zeros): the microphone is opened WITHOUT the
// browser's noise suppression and echo cancellation (auto gain stays). Suppression gates every pause down to near-digital
// silence (-112 dBFS in Leo's takes), which can swallow quiet speech and made a real gap indistinguishable from a pause;
// nothing plays while recording (the speaker stops), so there is no echo to cancel. With it off, a stretch of true digital
// silence means the system fed the page zeros — recorded as a gap (with track mute → unmute) and told to the owner.
const MIC_AUDIO = {echoCancellation: false, noiseSuppression: false, autoGainControl: true};
const microphone = createMicrophone({
  getUserMedia: () => navigator.mediaDevices.getUserMedia({audio: MIC_AUDIO}),
  visible: () => document.visibilityState === "visible"
});
const SILENT_PEAK = 4 / 32768;
// The level meter: an analyser on the take's stream (→ a muted gain → the destination, so every engine pulls it). Fails
// soft — no meter, the ring breathes instead — and is closed with the take.
function meterCtx(){
  const AC = window.AudioContext || window.webkitAudioContext;
  if (!AC) return null;
  try{ const ac = new AC(); ac.resume?.().catch(() => {}); return ac; }catch(_){ return null; }
}
function meterOn(p, stream){
  if (!p.ac) return;
  try{
    const src = p.ac.createMediaStreamSource(stream), an = p.ac.createAnalyser(), mute = p.ac.createGain();
    an.fftSize = 1024; mute.gain.value = 0;
    src.connect(an); an.connect(mute); mute.connect(p.ac.destination);
    p.an = an; p.buf = new Float32Array(an.fftSize);
    const frame = () => { if (p.over) return; meterSample(p); p.raf = requestAnimationFrame(frame); };
    p.raf = requestAnimationFrame(frame);
  }catch(_){ p.an = null; }
}
function meterOff(p){
  cancelAnimationFrame(p.raf);
  const c = document.querySelector("#ptt .pttc"); if (c){ c.style.removeProperty("--lv"); delete c.dataset.live; }
  if (p.ac){ p.ac.close?.().catch(() => {}); p.ac = null; }
  p.an = null;
}
function meterSample(p){
  if (!p.an || p.over || !p.rec || !p.ac || p.ac.state !== "running") return;
  p.an.getFloatTimeDomainData(p.buf);
  let peak = 0, sum = 0;
  for (let i = 0; i < p.buf.length; i++){ const x = p.buf[i]; sum += x * x; if (x > peak) peak = x; else if (-x > peak) peak = -x; }
  const c = document.querySelector("#ptt .pttc");
  if (c && document.visibilityState === "visible"){ c.dataset.live = "1"; c.style.setProperty("--lv", Math.min(1, Math.sqrt(Math.sqrt(sum / p.buf.length)) * 2.2).toFixed(3)); }
  if (!p.nsOff) return;                                                // suppression on: a pause reads as silence too
  const now = performance.now();
  if (peak <= SILENT_PEAK){ if (!p.silT0) p.silT0 = now; }
  else if (p.silT0){ gapAdd(p, p.silT0, now, true); p.silT0 = 0; }
}
/** A stretch [a, b] (performance.now) with no sound captured; kept as ms from the take's start. */
function gapAdd(p, a, b, silence){
  if (!p.recT0 || !a) return;
  if (silence) a = Math.max(a, p.recT0 + GAP_SKIP_MS);
  if (b - a < GAP_MIN_MS) return;
  p.gaps.push({a: a - p.recT0, b: b - p.recT0});
}
/** Ranges overlapping by < 0.4 s merged, sorted. */
export function gapMerge(list){
  const xs = list.filter(g => g && g.b > g.a).map(g => ({a: g.a, b: g.b})).sort((x, y) => x.a - y.a), out = [];
  for (const g of xs){ const l = out[out.length - 1]; if (l && g.a <= l.b + 400) l.b = Math.max(l.b, g.b); else out.push(g); }
  return out;
}
/** The take's gaps inside [t0, t1] (performance.now), as ms from t0. */
function gapsIn(p, t0, t1){
  const base = t0 - p.recT0, end = t1 - p.recT0;
  return gapMerge(p.gaps.map(g => ({a: Math.max(g.a, base) - base, b: Math.min(g.b, end) - base})));
}
/** "0:07–0:30、1:02–1:20" (at most three ranges, then "…"). */
export function gapText(list){
  const sep = lang() === "en" ? ", " : "、";
  const xs = list.slice(0, 3).map(g => mmss(g.a / 1000) + "–" + mmss(g.b / 1000));
  return xs.join(sep) + (list.length > 3 ? sep + "…" : "");
}
// Told once per take, after the destination is known (and after an interruption notice, which it would otherwise cover).
function gapNote(p, list){
  if (!p || p.gapNoted || !list.length) return;
  p.gapNoted = true;
  const show = () => toast(t('r.ptt.gap', {r: gapText(list)}), 8000);
  if (p.noted) setTimeout(show, 7000); else show();
}
const takeGaps = (p, decoded) => gapMerge([...gapsIn(p, p.recT0, p.recT1 || performance.now()), ...(p.nsOff && decoded ? decoded : [])]);
const MIC_OPEN_MAX_MS = 15000;
function pttStart(id, y){
  if (ptt || document.visibilityState !== "visible") return;
  if (C.asr() !== "ready"){ C.asrInstall?.(); return; }
  if (!navigator.mediaDevices || !navigator.mediaDevices.getUserMedia || !window.MediaRecorder){
    toast(t('r.ptt.noRec'), 3000); return;
  }
  const p = ptt = {epoch: localEpoch, id, x0: 0, y0: y, t0: performance.now(), cancel: false, over: false, touch: false,
                   send: false, stream: null, rec: null, recT0: 0, recT1: 0, chunks: [],
                   pend: 0, tUp: 0, locked: false, lockT0: 0, cap: 0, viaM: false,
                   seg: 0, segMs: segMs(), maxSegs: Math.max(1, Math.min(REC_SEGS, MAX_ATT - atts.length)),
                   takeP: null, take: null, stampS: stamp(), lastData: 0, mutedAt: 0, wl: null, wlFail: false, interrupted: false,
                   hidAt: 0, visAt: 0, cutAt: 0, segT0: 0, segT: [], gaps: [], muteT0: 0, nsOff: false, ac: null, silT0: 0, gapSeen: 0};
  // P346: a take the watch just ended (an interruption) is not painted back — pttEnd already took the card down
  p.timer = setInterval(() => { meterSample(p); pttWatch(p); if (!p.over) pttUI(p); }, 250);
  pttUI(p);
  // Native permission/device acquisition may never settle. Do not leave a
  // locked take waiting forever, and do not retain a stream arriving after cancel.
  p.openTimer = setTimeout(() => {
    if (p.over || p.rec) return;
    window.dispatchEvent(new CustomEvent('agentj-audio-error', {detail: {stage: 'capture', error: 'TimeoutError'}}));
    pttEnd(p, "cancel", true);
    toast(t('r.ptt.noMic'), 3000);
  }, MIC_OPEN_MAX_MS);
  p.ac = meterCtx();                                               // P205: made inside the gesture, so iOS lets it run
  microphone.acquire().then(lease => {
    const stream = lease.stream;
    clearTimeout(p.openTimer);
    if (p.over){
      lease.release();
      return;
    }
    p.micLease = lease;
    p.trackOff = [];
    p.stream = stream;
    for (const tr of stream.getAudioTracks()){
      const listen = (event, fn) => { tr.addEventListener(event, fn); p.trackOff.push(() => tr.removeEventListener(event, fn)); };
      listen("ended", () => pttInterrupted(p));
      listen("mute", () => { p.mutedAt = Date.now(); p.muteT0 = performance.now(); });
      listen("unmute", () => { p.mutedAt = 0; gapAdd(p, p.muteT0, performance.now()); p.muteT0 = 0; });
      try{ p.nsOff = tr.getSettings().noiseSuppression === false; }catch(_){ p.nsOff = false; }
    }
    meterOn(p, stream);
    if (!segStart(p)){ toast(t('r.ptt.noRec'), 3000); pttEnd(p, "cancel", true); return; }
    p.recT0 = performance.now();
    p.takeP = LR.startTake(p.rec.mimeType || "").then(v => (p.take = v), () => null);
    if (p.locked) wakeOn(p);
    try{ if (navigator.vibrate) navigator.vibrate(20); }catch(_){}
    pttUI(p);
  }, () => {
    clearTimeout(p.openTimer);
    if (p.over) return;
    pttEnd(p, "cancel", true);
    toast(t('r.ptt.noMic'), 3000);
  });
}
function microphoneAway(){
  if (ptt){
    if (ptt.locked || ptt.seg){
      ptt.hidAt = performance.now();
      ptt.autoSend = false;
      pttInterrupted(ptt);
    } else pttEnd(ptt, "cancel", true);
  }
  microphone.close();
}
function recOptions(){
  const o = {audioBitsPerSecond: REC_BPS};
  const ok = MediaRecorder.isTypeSupported ? REC_TYPES.find(m => { try{ return MediaRecorder.isTypeSupported(m); }catch(_){ return false; } }) : null;
  if (ok) o.mimeType = ok;
  return o;
}
// One recorder per segment, all on the same stream: every segment is a complete file of its own. The next recorder starts
// before the previous one stops, so the cut loses nothing.
function segStart(p){
  let rec;
  try{ rec = new MediaRecorder(p.stream, recOptions()); }
  catch(_){ try{ rec = new MediaRecorder(p.stream); }catch(_){ return false; } }
  const n = p.seg, chunks = [], t0 = performance.now();
  let i = 0, last = t0;
  p.rec = rec; p.chunks = chunks; p.segT0 = t0; p.segT[n] = t0;
  rec.ondataavailable = e => {
    if (!e.data || !e.data.size) return;
    chunks.push(e.data);
    const now = performance.now(), ms = now - last, k = i++;
    last = now; p.lastData = Date.now();
    // P198: audio still arriving while hidden moves "known good" forward; a full slice after coming back clears it
    if (p.hidAt){ if (document.visibilityState !== "visible") p.hidAt = now; else if (now - p.visAt > REC_SLICE_MS + 1000) p.hidAt = 0; }
    p.takeP?.then(id => id && LR.addChunk(id, n, k, e.data, ms));
  };
  rec.onerror = () => pttInterrupted(p);
  rec.onstop = () => segStop(p, n, chunks, rec, t0);
  try{ rec.start(REC_SLICE_MS); }catch(_){ return false; }
  clearTimeout(p.cap);
  p.cap = setTimeout(() => segRoll(p), p.segMs);
  p.lastData = Date.now();
  return true;
}
function segRoll(p){
  if (ptt !== p || p.over) return;
  if (p.seg + 1 >= p.maxSegs){                                     // the ceiling: the take ends, nothing is lost
    toast(t('r.ptt.capped', {m: Math.round(capMs(p) / 60000)}), 4200);
    pttEnd(p, "release", true);
    return;
  }
  const old = p.rec;
  old.closing = true;
  p.seg++;
  if (!segStart(p)){ p.seg--; p.rec = old; pttEnd(p, "release", true); return; }
  try{ old.stop(); }catch(_){}
  toast(t('r.ptt.segDone', {n: p.seg}), 2600);
  pttUI(p);
}
function segStop(p, n, chunks, rec, t0){
  const end = rec.closing || !p.over ? performance.now() : (p.recT1 || performance.now());
  const secs = Math.max(0, (end - t0) / 1000);
  const type = (rec.mimeType || chunks.find(b => b.type)?.type || "audio/webm").split(";")[0];
  if (rec !== p.rec || rec.closing && !rec.final){               // an earlier segment, cut while the take goes on
    if (chunks.length) segAttach(p, n, new Blob(chunks, {type}), secs);
    return;
  }
  pttFinish(p, n, chunks.length ? new Blob(chunks, {type}) : null, secs);
}
// The watch, every 250 ms while a take runs (and when the page comes back): a microphone the system took away (screen
// locked, another app, a call) ends the take as an interruption — what was recorded stays and goes to the tray.
function pttWatch(p){
  if (p.over || !p.rec || document.visibilityState !== "visible") return;
  const tr = p.stream && p.stream.getAudioTracks()[0];
  const now = Date.now();                                           // wall clock: the stall watch ignores a shifted timeline
  if (tr && tr.readyState === "ended") return pttInterrupted(p);
  if (p.mutedAt && now - p.mutedAt > MUTE_MS) return pttInterrupted(p);
  if (p.rec.state === "inactive" && !p.rec.closing) return pttInterrupted(p);
  if (p.lastData && now - p.lastData > REC_SLICE_MS * 3 + 2000) return pttInterrupted(p);
}
// P198: the take ends where the audio did, not when the page noticed. Found on return to the foreground (iOS takes the
// microphone at the screen lock and freezes the page): the moment the page went hidden, or the last audio that came in
// while it was hidden; a muted track: the moment of the mute. So the time with the screen locked is never counted.
function pttInterrupted(p){
  if (p.over || p.interrupted) return;
  p.interrupted = true;
  const now = performance.now();
  let cut = document.visibilityState === "visible" && p.hidAt ? p.hidAt : now;
  if (p.mutedAt) cut = Math.min(cut, now - Math.max(0, Date.now() - p.mutedAt));
  p.cutAt = Math.max(p.segT0 || p.recT0, Math.min(cut, now));
  pttEnd(p, "release", true);
}
// P198: the interruption notice says where the recording really went — the tray, the message field (being transcribed),
// or nowhere (not a sound was captured); shown once the destination is known.
function intNote(p, where){
  if (!p || !p.interrupted || p.noted) return;
  p.noted = true;
  const d = mmss(recTotalMs(p) / 1000);
  toast(where === "tray" ? t('r.ptt.interrupted', {d}) : where === "text" ? t('r.ptt.intText', {d}) : t('r.ptt.intNone'), 7000);
}
function pttEnd(p, reason, quiet){
  if (!p || p.over) return;
  p.over = true;
  clearInterval(p.timer); clearTimeout(p.pend); clearTimeout(p.cap); clearTimeout(p.openTimer);
  wakeOff(p);
  for (const off of p.trackOff || []) off();
  p.trackOff = [];
  if (!p.stream) microphone.close(); // cancel/timeout invalidates an unresolved permission request
  const tEnd = p.tUp || p.cutAt || performance.now();
  if (p.muteT0){ gapAdd(p, p.muteT0, tEnd); p.muteT0 = 0; }
  if (p.silT0){ gapAdd(p, p.silT0, tEnd, true); p.silT0 = 0; }
  meterOff(p);
  if (ptt === p) ptt = null;
  if (p.locked) lockEndAt = performance.now();
  const held = (p.tUp || performance.now()) - p.t0;
  p.recT1 = p.tUp || p.cutAt || performance.now();
  p.send = reason === "release" && !p.cancel && !!p.rec && (p.locked || p.interrupted || p.seg > 0 || held >= PTT_MIN_MS);
  if (!quiet){
    if (reason === "release" && !p.cancel && !p.locked && !p.seg && held < PTT_MIN_MS) { /* empty tap: quietly discard */ }
    else if (!p.send && p.rec) toast(p.seg ? t('r.ptt.cancelledLast', {n: p.seg}) : t('r.ptt.cancelled'), p.seg ? 4200 : 1400);
  }
  if (p.rec){ p.rec.final = true; p.rec.closing = false; }
  if (p.rec && p.rec.state !== "inactive"){ try{ p.rec.stop(); }catch(_){ pttFinish(p, p.seg, null, 0); } }
  else if (p.rec) pttFinish(p, p.seg, p.chunks.length ? new Blob(p.chunks, {type: (p.rec.mimeType || p.chunks.find(b => b.type)?.type || "audio/webm").split(";")[0]}) : null, recTotalMs(p) / 1000);
  p.micLease?.release();
  if (p.interrupted) microphone.close(); // revoked/muted sources must not poison the next gesture
  pttUI(ptt);
}
// A take longer than T seconds goes as an audio attachment (transcribed at send time); T = what the field can show,
// its measured capacity divided by a speaking rate, clamped to [20 s, 90 s].
const VOICE_CPS = 4, VOICE_T_MIN = 20, VOICE_T_MAX = 90;
function voiceThresholdS(){ return Math.min(VOICE_T_MAX, Math.max(VOICE_T_MIN, fieldCapacity() / VOICE_CPS)); }
function audioExt(type){
  const ty = (type || "").split(";")[0];
  return {"audio/webm": "webm", "audio/ogg": "ogg", "audio/mp4": "m4a", "audio/aac": "aac", "audio/mpeg": "mp3", "audio/wav": "wav", "audio/x-wav": "wav"}[ty] || "webm";
}
// A segment of a long take → the tray at once (its upload starts while the recording goes on), in recording order.
function segAttach(p, n, blob, secs, quiet){
  if (!LR.usable(blob, secs)) { p.takeP?.then(id => id && LR.dropSeg(id, n)); return null; }
  const name = `${t('r.file.voice')}-${p.stampS}-${n + 1}.${audioExt(blob.type)}`;
  const f = new File([blob], name, {type: blob.type || "audio/webm"});
  if (f.size > UPLOAD_MAX){ toast(uploadError("too_big"), 3000); return null; }
  const [a] = addFiles([f], "recording", secs, p.bids && p.bids[n]);
  if (!a){ if (!quiet) toast(t('r.voice.trayFull', {max: MAX_ATT}), 3200); return null; }
  a.label = t('r.tray.part', {n: n + 1, d: mmss(secs)});
  const g = p.gaps && p.segT && p.segT[n] ? gapsIn(p, p.segT[n], p.segT[n] + secs * 1000) : [];   // P205
  if (g.length) a.label += t('r.tray.gap', {r: gapText(g)});
  paintChip(a);
  p.takeP?.then(id => { if (!id) return; a.rec = {take: id, seg: n}; LR.closeSeg(id, n, Math.round(secs * 1000)); if (a.up) LR.setBid(id, n, a.up.bid); });
  return a;
}
function pttFinish(p, n, blob, secs){
  // The lease was disabled synchronously in pttEnd; this delayed recorder callback
  // must never stop the source of the next take.
  const dropTake = () => p.takeP?.then(id => id && LR.dropSeg(id, n));
  if (!LR.usable(blob, secs)){ if (p.interrupted) intNote(p, n > 0 ? "tray" : "none"); dropTake(); return; }
  if (!p.send || !blob){ intNote(p, n > 0 ? "tray" : "none"); dropTake(); return; }   // P198: earlier parts are in the tray
  if (p.epoch !== localEpoch) return;
  if (n > 0 || secs > WAV_MAX_S){
    const final = segAttach(p, n, blob, secs);
    if (final || n > 0){ intNote(p, "tray"); gapNote(p, takeGaps(p)); }
    if (final && p.autoSend) say();
    return;
  }     // long: the compressed original, as it was recorded
  takeFinished(blob, secs, p);
}
// A finished take, from the mic or from a native shell (relayNative.take): words into the field, or a long one into the tray.
// P182: `p` (a mic take) carries its stored copy — dropped once the words are in the field, kept with the attachment.
let asrQueue = Promise.resolve();
async function takeFinished(blob, dur, p){
  const takeEpoch = p?.epoch ?? localEpoch;
  const forget = () => p?.takeP?.then(id => id && LR.dropTake(id));
  if (!LR.usable(blob, dur)){ forget(); return; }
  if (dur > voiceThresholdS()){
    if (atts.length < MAX_ATT){
      const w = blob.type === "audio/wav" ? {blob, secs: dur} : await toWav(blob);
      if (takeEpoch !== localEpoch) return;
      const b = w ? w.blob : blob;
      const f = new File([b], `${t('r.file.voice')}-${stamp()}.${audioExt(b.type)}`, {type: b.type || "audio/webm"});
      if (f.size > UPLOAD_MAX){ toast(uploadError("too_big"), 3000); return; }
      const [a] = addFiles([f], "recording", dur);
      const g = p ? takeGaps(p, w && w.gaps) : [];                    // P205
      if (a){
        a.label = t('r.tray.voice', {d: mmss(dur)}) + (g.length ? t('r.tray.gap', {r: gapText(g)}) : ""); paintChip(a);
        p?.takeP?.then(id => { if (id){ a.rec = {take: id, seg: 0}; LR.closeSeg(id, 0, Math.round(dur * 1000)); } });
      }
      if (a && p?.interrupted) intNote(p, "tray");
      else if (!g.length) toast(C.asr() === "ready" ? t('r.voice.asAtt') : t('r.voice.asAttNoAsr'), 3200);
      if (a) { gapNote(p, g); if (p?.autoSend && takeEpoch === localEpoch) say(); }
      return;
    }
    toast(t('r.voice.trayFull', {max: MAX_ATT}), 3200);
  }
  if (C.asr() !== "ready"){ C.asrInstall?.(); return; }
  intNote(p, "text");
  asrBusy++; asrUI();
  const ep = localEpoch;
  const job = transcribe(blob, dur);
  asrQueue = asrQueue.then(() => job).then(async r => {
    asrBusy--;
    if (ep !== localEpoch || takeEpoch !== localEpoch){ asrUI(); return; }           // unpaired / removed meanwhile: the words belong to no one now
    const tx = r.ok && typeof r.text === "string" ? r.text.trim() : "";
    if (tx){ appendText(tx); forget(); }
    else toast(asrError(r), 3400);
    if (p) gapNote(p, takeGaps(p, r.gaps));                          // P205: words in, but a stretch came in silent
    asrUI(); autosize();
    if (tx && p?.autoSend) await say(true);
  });
}
// P182: takes still on this phone after a reload / crash / closed tab (not yet accepted by the computer) are offered back,
// once per page load: 「放回附件栏」 puts every segment into the tray in order (same upload id: the computer resumes a partial),
// 「丢弃」 deletes them here.
let recOffered = false;
async function recRecover(){
  if (recOffered || !window.indexedDB) return;
  recOffered = true;
  let list;
  try{ list = await LR.pending([...atts.map(a => a.rec && a.rec.take), ptt && ptt.take].filter(Boolean)); }catch(_){ return; }
  if (!list.length) return;
  const segs = list.reduce((n, k) => n + k.segs.length, 0), ms = list.reduce((n, k) => n + k.segs.reduce((m, s) => m + s.ms, 0), 0);
  if (!await confirmSheet(t('r.rec.backTitle'), t('r.rec.backText', {n: segs, d: mmss(ms / 1000)}), t('r.rec.backYes'), false, t('r.rec.backNo'))){
    for (const k of list) LR.dropTake(k.id);
    toast(t('r.rec.dropped'), 2400);
    return;
  }
  let put = 0;
  for (const k of list){
    const d = new Date(k.t0), st = [d.getHours(), d.getMinutes(), d.getSeconds()].map(x => String(x).padStart(2, "0")).join("");
    const q = {stampS: st, takeP: Promise.resolve(k.id), bids: Object.fromEntries(k.segs.map(s => [s.n, s.bid]))};
    for (const s of k.segs){
      if (atts.length >= MAX_ATT){ toast(t('r.voice.trayFull', {max: MAX_ATT}), 3200); return; }
      const blob = await LR.segBlob(k.id, s.n, (k.mime || "audio/webm").split(";")[0]);
      if (!blob){ LR.dropSeg(k.id, s.n); continue; }
      if (segAttach(q, s.n, blob, s.ms / 1000, true)) put++;
    }
  }
  if (put) toast(t('r.rec.back', {n: put}), 3200);
}
async function transcribe(blob, secs){
  const w = blob.type === "audio/wav" ? {blob, secs} : await toWav(blob);
  if (!w) return {ok: false, why: "bad_audio"};
  if (w.blob.size > ASR_MAX_BYTES) return {ok: false, why: "too_long", gaps: w.gaps};
  const r = await C.api.transcribe(w.blob, w.secs);
  return r && typeof r === "object" ? Object.assign({}, r, {gaps: w.gaps}) : r;
}
function asrError(r){
  const why = r.why || "";
  if (r.ok || why === "no_speech") return t('r.asr.noSpeech');
  return t('r.asr.' + (["not_installed", "off", "broken", "busy", "timeout", "bad_audio", "net", "too_long"].includes(why) ? why : "other"));
}
function appendText(tx){
  const v = input.value;
  const sep = !v || /\s$/.test(v) || /[\u2e80-\u9fff\u3000-\u303f\uff00-\uffef]$/.test(v) || /^[\u2e80-\u9fff\u3000-\u303f\uff00-\uffef]/.test(tx) ? "" : " ";
  const room = MAX_TEXT() - v.length - sep.length;
  if (tx.length > room){
    const cut = tx.length - Math.max(0, room);
    tx = tx.slice(0, Math.max(0, room));
    toast(t('r.cap.cut', {cap: capWords(), n: cut}), 3200);
  }
  input.value = v + (tx ? sep : "") + tx;
  autosize(); draftSoon();
  try{ focusComposer({preventScroll: true}); }catch(_){}
  input.setSelectionRange(input.value.length, input.value.length);
  input.scrollTop = input.scrollHeight;
}
function restingMicLabel(){
  return t(C?.asr() === 'ready' ? 'r.mic.hold' : 'p387Voice.preparing');
}
export function paintVoiceReadiness(){
  if (!ptt && !asrBusy) {
    const preparing = C?.asr() !== 'ready';
    el('micLabel').textContent = restingMicLabel();
    el('mic').toggleAttribute('data-preparing', preparing);
    el('mic').setAttribute('aria-label', restingMicLabel());
  }
}
function asrUI(){
  const on = asrBusy > 0;
  el("asrTag").hidden = !on;
  input.placeholder = on ? t('r.asr.tag') : restingPlaceholder();
  el("mic").classList.toggle("asr", on);
  if (!ptt) el("micLabel").textContent = on ? t('r.asr.tag') : restingMicLabel();
  refreshSend();
}
const fullH = {};
function keyboardDown(){
  const w = window.innerWidth, h = window.innerHeight, vv = window.visualViewport;
  fullH[w] = Math.max(fullH[w] || 0, h);
  return fullH[w] - h <= 120 && !(vv && h - vv.height > 120);
}
// The relay Android shell's bridge (relay ADR-038 / 047 / 050), kept for a future Agent J shell: a wake-word take, a
// keyboard-panel image, a notification tap.
const NATIVE_TAKE_MAX = 4 * 1024 * 1024;
async function showTurn(id){
  if (rdOpen()) closeReader();
  for (let n = 0; n < 40; n++){
    const ps = pages(), i = ps.findIndex(p => p.id === id);
    if (i >= 0){ pageAt = i; follow = i === ps.length - 1; tailWait = false; shownKey = null; showPage(); return true; }
    if (id > hist.lastId){ syncHistory(id); await new Promise(r => setTimeout(r, 250)); continue; }
    if (!(await loadOlder())) break;
  }
  return false;
}

// ---- keyboard shortcuts (relay ADR-032 / 052) -------------------------------------------------------
const ESC2_MS = 500;
var escAt = 0, mKey = false, mCode = "";
const omCan = () => !el("om").hidden, omOpen = () => el("om").classList.contains("open");
function edgeArrow(e){
  if ((e.key !== "ArrowLeft" && e.key !== "ArrowRight") || e.ctrlKey || e.metaKey || e.altKey || e.shiftKey || e.defaultPrevented || e.repeat || dialogOpen()) return false;
  const v = input.value;
  if (!v) return true;
  if (input.selectionStart !== input.selectionEnd) return false;
  return e.key === "ArrowLeft" ? input.selectionStart === 0 : input.selectionEnd === v.length;
}
const isM = e => !e.shiftKey && (e.key === "m" || e.key === "M" || (e.code === "KeyM" && e.key.length !== 1));
function composerEmpty(){ return !input.value && !atts.length; }
const typingIn = tg => !!tg && (tg.isContentEditable || /^(INPUT|TEXTAREA|SELECT)$/.test(tg.tagName));
var silentKey = "", silentBusy = false;
function silentWhen(ts){
  if (typeof ts !== "number") return "";
  const d = new Date((ts + skew) * 1000), now = new Date();
  const day = d.toDateString() === now.toDateString() ? "" : (d.getMonth() + 1) + "-" + d.getDate() + " ";
  return day + hhmm(ts);
}
function renderSilent(focusId){
  const list = hist.turns.filter(t => isSilent(t.reply)).reverse();
  const more = el("silentMore");
  more.hidden = !olderLeft();
  more.disabled = silentBusy;
  more.textContent = silentBusy ? t('r.silent.loading') : t('r.silent.more');
  const key = hist.epoch + ":" + list.map(t => t.id).join(",") + ":" + hist.route + ":" + olderLeft();
  if (key === silentKey && focusId === undefined) return;
  silentKey = key;
  const box = el("silentList");
  const open = new Set([...box.querySelectorAll("details[open]")].map(d => +d.dataset.id));
  if (typeof focusId === "number") open.add(focusId);
  if (!list.length){
    const p = document.createElement("div"); p.className = "empty";
    p.textContent = hist.route === false ? t('r.silent.noHistory') : olderLeft() ? t('r.silent.noLoaded') : t('r.silent.empty');
    box.replaceChildren(p);
    return;
  }
  box.replaceChildren(...list.map(turn => {
    const src = turn.source && typeof turn.source === "object" ? turn.source : null;
    const text = src && typeof src.text === "string" ? src.text.trim() : "";
    const d = document.createElement("details"); d.dataset.id = String(turn.id); d.open = open.has(turn.id);
    const sm = document.createElement("summary");
    const tm = document.createElement("span"); tm.className = "t"; tm.textContent = silentWhen(turn.ts) || "—";
    const sc = document.createElement("span"); sc.className = "s"; sc.textContent = src ? sourceOf(src).label : t('r.silent.noSource');
    const ln = document.createElement("span"); ln.className = "l"; ln.textContent = text.split("\n")[0] || t('r.silent.noText');
    sm.append(tm, sc, ln);
    const full = document.createElement("div"); full.className = "full"; full.textContent = text || t('r.silent.noText');
    const rp = document.createElement("div"); rp.className = "rep"; rp.textContent = t('r.silent.reply') + turn.reply.trim();
    d.append(sm, full);
    if (typeof turn.pre === "string" && turn.pre.trim()){       // P351: the turn's process notes, folded with it
      const pr = document.createElement("div"); pr.className = "rep pre"; pr.textContent = t('r.silent.pre') + turn.pre.trim();
      d.append(pr);
    }
    d.append(rp);
    return d;
  }));
  if (typeof focusId === "number"){
    const d = box.querySelector(`details[data-id="${focusId}"]`);
    if (d) d.scrollIntoView({block: "nearest"});
  }
}
function openSilent(id){
  closeSug();                                   // P187: above the command list — back (Esc) returns to it
  el("silent").hidden = false;
  if (typeof id !== "number") el("silentList").scrollTop = 0;
  renderSilent(id);
  el("silentClose").focus({preventScroll: true});
}
function closeSilent(){ el("silent").hidden = true; if (document.activeElement) document.activeElement.blur(); }
async function moreSilent(){
  if (silentBusy) return;
  silentBusy = true; renderSilent();
  const had = hist.turns.filter(t => isSilent(t.reply)).length;
  for (;;){
    const b = await loadOlderBatch();
    if (!b || !b.all || hist.turns.filter(t => isSilent(t.reply)).length > had) break;
  }
  silentBusy = false;
  showPage(); renderSilent();
}

// P142 (A16): the sign-in QR / secret pickup / sudo-secret cards and the setup page also hold the keyboard (no shortcut leaks)
const shownById = (id) => { const n = el(id); return !!n && !n.hidden; };
const dialogOpen = () => search.isOpen() || !el("silent").hidden || !el("cam").hidden || !el("keys").hidden || !el("confirm").hidden || !el("badge-panel").hidden || !el("settings").hidden
  || shownById("lqr") || shownById("sout") || shownById("elev") || shownById("setup-view") || fsed.isOpen();
function openKeys(){ closeKeysFind(); paintKeys(); closeSug(); el("keys").hidden = false; el("keysClose").focus(); }   // P187: above the list / Settings
// P207: the 「键盘快捷键」 page is drawn from interactions.json (interactions.gen.js ROWS + the ix* dictionary texts), never
// written by hand: one card per group, a row per entry with a key — its keys, what it does and the mouse / touch gesture.
const isMac = () => /Mac|iP(hone|ad|od)/.test(navigator.platform || navigator.userAgent || "");
function paintKeys(){
  const box = el("keysCols");
  if (!box) return;
  const mac = isMac(), cards = [], terms = keysTerms();
  for (const g of ['functional', 'single', 'gestures']){
    const rows = IX_ROWS.map(([id, keys, ges]) => [id, g === 'gestures' ? keys : keys.filter(k => k[0].startsWith('Alt+Shift+') === (g === 'functional')), ges])
      .filter(r => g === 'gestures' ? r[2] : r[1].length)
      .filter(r => !terms.length || terms.every(w => keysHay(g, r).includes(w)));   // @ix keys.search
    if (!rows.length) continue;
    const sec = document.createElement("section"); sec.className = "menucard"; sec.dataset.group = g;
    const h = document.createElement("h3"); h.className = "setg"; h.textContent = t('ixw.' + g);
    const dl = document.createElement("dl"); dl.className = "keydl";
    for (const [id, keys, hasGesture] of rows){
      const dt = document.createElement("dt"), dd = document.createElement("dd");
      dt.dataset.ix = id;
      // P363 (#69): one combination per line, top to bottom — the key column is only as wide as the longest single
      // combination, the description gets the rest (Ctrl+A · Ctrl+E · … used to wrap into a wide block)
      keys.forEach(([combo, how]) => {
        const line = document.createElement("span"); line.className = "kcombo";
        if (how === "hold") line.append(t('ixw.hold') + " ");
        const one = () => { const f = document.createDocumentFragment(); kbdTokens(combo, mac).forEach((tok, j) => { if (j) f.append("+"); const k = document.createElement("kbd"); k.textContent = tok || t('ixw.space'); f.append(k); }); return f; };
        line.append(one());
        if (how === "double"){ line.append(" "); line.append(one()); }
        dt.append(line);
      });
      dd.textContent = t('ix.' + id);
      if (hasGesture){ const sm = document.createElement("small"); sm.className = "keyges"; sm.textContent = t('ixw.gesture') + t('ixges.' + id); dd.append(sm); }
      dl.append(dt, dd);
    }
    sec.append(h, dl); cards.push(sec);
  }
  // P412 #176: the cards' own keys, as they are given right now (Alt+Shift + a letter of the English title, or a digit)
  const own = cardKeys().filter(c => !terms.length || terms.every(w => [c.title, c.combo, ...kbdTokens(c.combo, mac), ...both('ixw.cards'), ...both('ix.app.open_card')].join(' \n ').toLocaleLowerCase().includes(w)));
  if (own.length){
    const sec = document.createElement("section"); sec.className = "menucard"; sec.dataset.group = "cards";
    const h = document.createElement("h3"); h.className = "setg"; h.textContent = t('ixw.cards');
    const dl = document.createElement("dl"); dl.className = "keydl";
    for (const c of own){
      const dt = document.createElement("dt"), dd = document.createElement("dd"), line = document.createElement("span");
      dt.dataset.card = c.key; line.className = "kcombo";
      kbdTokens(c.combo, mac).forEach((tok, j) => { if (j) line.append("+"); const k = document.createElement("kbd"); k.textContent = tok; line.append(k); });
      dt.append(line); dd.textContent = t('p412.openCard', {name: c.title}); dl.append(dt, dd);
    }
    sec.append(h, dl); cards.splice(cards[0] && cards[0].dataset.group === 'functional' ? 1 : 0, 0, sec);
  }
  box.replaceChildren(...cards);
  const none = el("keysNone");
  if (none){ none.hidden = !(terms.length && !cards.length); none.textContent = none.hidden ? "" : t('r.keys.findNone'); }
}
// P344: the sheet's search. A row matches when every word of the query is somewhere in its description, gesture text
// (中文 and English, whichever language the page shows), its keys as written (Alt+Shift+H, ArrowLeft, Mod+K) or as drawn
// (⌥⇧H / ⌘ / Ctrl / ←), or its card's / group's title (翻页, Files…); case-insensitive.
const KEY_WORDS = {Mod: 'mod cmd command ctrl control ⌘', Alt: 'alt option opt ⌥', Shift: 'shift ⇧', Ctrl: 'ctrl control',
  Escape: 'esc escape', Enter: 'enter return', Space: 'space', Backspace: 'backspace delete ⌫'};
function keysHay(g, [id, keys]){
  const parts = [...both('ix.' + id), ...both('ixkw.' + id), ...both('ixges.' + id), ...both('ixw.' + g), ...both('ixgrp.' + id.split('.')[0])];
  for (const [combo, how] of keys){
    const drawn = [kbdTokens(combo, true), kbdTokens(combo, false)].map(x => x.join('+'));
    parts.push(combo, ...drawn, ...drawn.map(x => x.replace(/\+/g, '')), combo.replace(/\+/g, ''));
    parts.push(combo.split('+').map(k => ({Mod: '⌘', Alt: '⌥', Shift: '⇧', Ctrl: '⌃'})[k] || k).join(''));   // ⌥⇧C, as Mac menus write it
    for (const p of combo.split('+')) if (KEY_WORDS[p]) parts.push(KEY_WORDS[p]);
    if (combo.includes('Space')) parts.push(...both('ixw.space'));
    if (how === 'hold') parts.push(...both('ixw.hold'));
  }
  return parts.join(' \n ').toLocaleLowerCase();
}
const keysTerms = () => { const f = el("keysFind"); return f && !f.hidden ? el("keysFindInput").value.trim().toLocaleLowerCase().split(/\s+/).filter(Boolean) : []; };
function openKeysFind(){
  const f = el("keysFind"), i = el("keysFindInput");
  if (f.hidden){ f.hidden = false; el("keysFindBtn").setAttribute("aria-expanded", "true"); }
  i.focus(); i.select();
}
// Esc / the magnifier again: the query goes and every row is back; the next Esc closes the sheet.
function closeKeysFind(){
  const f = el("keysFind");
  if (!f) return;
  const was = !f.hidden || !!el("keysFindInput").value;
  const inField = document.activeElement === el("keysFindInput");
  f.hidden = true; el("keysFindInput").value = ""; el("keysFindBtn").setAttribute("aria-expanded", "false");
  if (inField) el("keysFindBtn").focus();
  if (was) paintKeys();
}
const keysFindOpen = () => !!el("keysFind") && !el("keysFind").hidden;
const escKeys = () => keysFindOpen() ? closeKeysFind() : closeKeys();
// The sheet's own keys (/ and Ctrl / ⌘+K) only while it is the top layer, never from another field (the search field
// itself keeps typing "/"), never over a confirm / sign-in / secret / camera layer above it.
function keysFindKey(e, tg){
  if (el("keys").hidden || !el("confirm").hidden || !el("cam").hidden || shownById("lqr") || shownById("sout") || shownById("elev")) return false;
  const own = tg === el("keysFindInput");
  if (e.key === "/" && !e.ctrlKey && !e.metaKey && !e.altKey) return !own && !typingIn(tg);
  return (e.metaKey || e.ctrlKey) && !e.altKey && !e.shiftKey && (e.key === "k" || e.key === "K" || e.code === "KeyK") && (own || !typingIn(tg));
}
function closeKeys(){ closeKeysFind(); el("keys").hidden = true; if (document.activeElement) document.activeElement.blur(); }
export async function pasteClipImages(){
  let items;
  try{ items = await navigator.clipboard.read(); }
  catch(err){
    toast(err && err.name === "NotAllowedError" ? t('r.clip.denied') : t('r.clip.fail'), 3200);
    return;
  }
  const files = [];
  let hasText = false;
  for (const it of items || []){
    const ty = it.types.find(x => /^image\//.test(x));
    if (!ty){ hasText = hasText || it.types.includes("text/plain"); continue; }
    try{ const b = await it.getType(ty); files.push(new File([b], "image", {type: b.type || ty})); }catch(_){}
  }
  if (!files.length){ toast(hasText ? t('r.clip.text') : t('r.clip.none'), 3000); return; }
  files.forEach(f => shotclip.seen(f));        // P154: what the owner pasted is not a new screenshot
  takeFiles(files.map((f, i) => pastedName(f, i, files.length)), 'r.verb2.paste', "paste");
}
// P181: hold the file button → the clipboard's text at the cursor in the field, or a .txt attachment by pasteRoute
// (P406 #168: characters only, never the field's height). Hold the photo button → the clipboard's image(s) as attachments,
// a light hint when there is none. Both run on the release (the browser's user gesture): iOS Safari shows its own 「粘贴」 bubble first.
export {CLIP_TEXT_MAX};
// P406 #168: the words already in the field plus this paste pass one message's limit → this paste alone as a .txt
// attachment, the field untouched, and a short note saying why
function pasteOver(text){
  const full = atts.length >= MAX_ATT;
  addFiles([new File([text], `${t('r.file.paste')}-${stamp()}.txt`, {type: "text/plain"})], "paste");
  if (!full) toast(t('p406.paste.over', {cap: capWords()}), 3600);
}
export async function pasteClipText(){
  if (!navigator.clipboard || !navigator.clipboard.readText){ toast(t('p181.clip.unsupported'), 3200); return; }
  let text;
  try{ text = await navigator.clipboard.readText(); }
  catch(err){ toast(err && err.name === "NotAllowedError" ? t('r.clip.denied') : t('r.clip.fail'), 3200); return; }
  if (!text){ toast(t('p181.clip.noText'), 2400); return; }
  const route = pasteRoute({value: input.value, start: input.selectionStart, end: input.selectionEnd, text, max: MAX_TEXT()});
  if (route === "over"){
    if (atts.length >= MAX_ATT){ toast(t('r.take.tooMany', {max: MAX_ATT, have: atts.length, verb: t('r.verb2.paste'), n: 1}), 3600); return; }
    pasteOver(text); return;
  }
  if (route === "file"){
    if (atts.length >= MAX_ATT){ toast(t('r.take.tooMany', {max: MAX_ATT, have: atts.length, verb: t('r.verb2.paste'), n: 1}), 3600); return; }
    addFiles([new File([text], `${t('r.file.paste')}-${stamp()}.txt`, {type: "text/plain"})], "paste");
    toast(t('p181.clip.asFile', {n: CLIP_TEXT_MAX}), 2600);
    return;
  }
  const v = input.value, a = input.selectionStart ?? v.length, b = input.selectionEnd ?? v.length;
  input.value = v.slice(0, a) + text + v.slice(b);
  focusComposer();
  input.setSelectionRange(a + text.length, a + text.length);
  input.dispatchEvent(new Event("input"));
  toast(t('p181.clip.inserted'), 1600);
}
export async function pasteClipImage(){
  if (!navigator.clipboard || !navigator.clipboard.read){ toast(t('p181.clip.unsupported'), 3200); return; }
  return pasteClipImages();
}
// P154 截图自动进附件 (js/shotclip.js): a new clipboard image after a screenshot round trip → the tray, like paste
// (MAX_ATT and its message included); never sent by itself.
// P340 #27: quiet = the caller says it in its own notice (screenshot + clipboard); → {added, n, max}
// P398 (#156): auto = 截图自动进附件 took it (not the gear / a paste) — its chip says 「自动放入 · 设置里可关」; → a Promise now
export async function attachShot(file, quiet = false, auto = false){
  const added = (await takeFiles([pastedName(file, 0, 1)], 'r.verb2.shot', "paste", auto)).length > 0;
  if (added && !quiet) toast(t('r.shot.added', {n: atts.length, max: MAX_ATT}), 2600);
  return {added, n: atts.length, max: MAX_ATT};
}
// P110 external shares enter the existing tray / encrypted upload path, never auto-send.
/** P190: text from the scanner goes into the field at the caret (never sent by itself). */
// P264: the skill command starts the draft; existing words are kept as the owner's idea.
export function prepareSetupDraft(text) {
  input.value = text + (input.value.trim() ? '\n\n' + input.value : '') + '\n';
  input.dispatchEvent(new Event('input'));
  focusComposer({preventScroll: true}); input.setSelectionRange(input.value.length, input.value.length);
}
export function insertText(text){
  const at = document.activeElement === input ? input.selectionStart : input.value.length;
  const end = document.activeElement === input ? input.selectionEnd : at;
  const pre = at > 0 && !/\s$/.test(input.value.slice(0, at)) ? " " : "";
  input.setRangeText(pre + text, at, end, "end");
  input.dispatchEvent(new Event("input"));
  try { focusComposer({preventScroll: true}); } catch { /* */ }
}
export function importShare({images, text}){
  if (images.length) takeFiles(images, 'r.verb2.paste', 'paste');
  if (text) { input.value += (input.value ? "\n" : "") + text; input.dispatchEvent(new Event('input')); }
}
function clearComposer(){
  if (sending){ toast(t('r.clear.sending'), 1800); return; }
  input.value = "";
  input.dispatchEvent(new Event("input"));
  for (const a of atts.slice()) removeAtt(a);
  toast(t('r.clear.done'), 1400);
}
// readline editing in the composer: js/termkeys.js (P412: shared with the full-screen editor)
// ↑ / ↓ history of sent messages: the last 50, sealed in IndexedDB (§10.14), loaded once into memory.
const HIST_MAX = 50;
var histAt = -1, histMem = [];
function histLoad(){ return histMem; }
function pushHist(text){
  histAt = -1;
  if (!text.trim()) return;
  histMem = histMem.filter(x => x !== text);
  histMem.push(text);
  histMem = histMem.slice(-HIST_MAX);
  putSealed("ihist", histMem).catch(() => {});
}
function histShow(v){
  sugDismissed = v;
  input.value = v;
  input.dispatchEvent(new Event("input"));
  input.setSelectionRange(v.length, v.length);
}
function histKey(up){
  const h = histLoad();
  const browsing = histAt >= 0 && histAt < h.length && input.value === h[histAt];
  if (!browsing){ histAt = -1; if (input.value || !up) return false; }
  if (up){
    const next = histAt < 0 ? h.length - 1 : histAt - 1;
    if (next < 0) return !!h.length;
    histAt = next; histShow(h[next]); return true;
  }
  if (histAt >= h.length - 1){ histAt = -1; histShow(""); return true; }
  histAt++; histShow(h[histAt]); return true;
}
function openFull(){ closeSug(); closeMenu(); fsed.open(); }
// A finger on the message box that moves up ≥ 56 px (more up than sideways) opens full screen. While the box's own lines can
// still scroll that way the finger scrolls them as before; the 56 px count from where they reached their end.
function swipeUpToFull(field){
  if (!field) return;
  let sw = null;
  const atEnd = () => input.scrollTop + input.clientHeight >= input.scrollHeight - 1;
  field.addEventListener("touchstart", e => {
    sw = e.touches.length === 1 ? {x: e.touches[0].clientX, y: e.touches[0].clientY} : null;
  }, {passive: true});
  field.addEventListener("touchmove", e => {
    if (!sw || e.touches.length !== 1) return;
    const tt = e.touches[0];
    if (!atEnd()){ sw.y = tt.clientY; sw.x = tt.clientX; return; }
    const dx = tt.clientX - sw.x, dy = sw.y - tt.clientY;
    if (dy >= 56 && dy > Math.abs(dx) * 1.5){ sw = null; openFull(); }
  }, {passive: true});
  field.addEventListener("touchend", () => { sw = null; }, {passive: true});
}
// P412 (#174): Ctrl+S puts the draft aside / brings it back (Claude Code's stash; this tab's memory only), Ctrl+R finds an
// earlier message that contains what the box says (again = further back; the box's words are the search).
let stash = null, hfind = null;
function stashKey(){
  const v = input.value;
  if (!v && stash === null) return;
  const back = stash; stash = v || null;
  input.value = back || "";
  input.dispatchEvent(new Event("input"));
  input.setSelectionRange(input.value.length, input.value.length);
  toast(t(back === null ? 'p412.stashed' : 'p412.unstashed'), 1800);
}
function histFind(){
  const h = histLoad();
  if (!hfind || input.value !== hfind.shown) hfind = {q: input.value.trim().toLowerCase(), at: h.length, shown: null};
  for (let i = hfind.at - 1; i >= 0; i--){
    if (hfind.q && !h[i].toLowerCase().includes(hfind.q)) continue;
    hfind.at = i; hfind.shown = h[i];
    histShow(h[i]);
    toast(t('p412.histHit', {n: h.length - i}), 1400);
    return;
  }
  toast(t('p412.histNone'), 1800);
}
/** Unpair / re-pair / revoke (§10.14, P33-X02 / X13): nothing typed, attached, queued or remembered for one computer
 *  survives in memory — the ↑↓ history, the field, the quote, the tray (object URLs revoked), the queued sends and the one in
 *  flight (cancelled; it never resumes). A transcription still on its way is dropped when it lands (localEpoch). */
let localEpoch = 0;
export function forgetLocal(){
  localEpoch++;
  histMem = []; histAt = -1;
  forgetKill(); stash = null; hfind = null; fsed.forget();   // P412: the kill ring, Ctrl+S and the full-screen copy too
  clearTimeout(draftTimer); lastSent = null;
  if (inflight){ inflight.cancelled = true; inflight.forgotten = true; if (inflight.wake) inflight.wake(); }
  queued.length = 0; queueWatch.reset(); observedQueue.clear(); queueStuck = false;
  outbox.wipe();
  for (const l of held.values()) for (const a of l) if (a.url){ URL.revokeObjectURL(a.url); a.url = null; }
  held.clear();
  forgetMedia();                                     // F21: fetched files and their object URLs (§13)
  search.clearAll(false);                            // P188: the search pages (their sealed copy goes with wipeLocal)
  forgetRendered();                                  // P57: rendered copies of the Agent's words (code / math / diagrams)
  for (const a of atts.slice()) removeAtt(a, false);
  if (ptt) pttEnd(ptt, "cancel", true);
  microphone.close();
  LR.wipe();                                         // P182: stored recordings go with the pairing
  cancelReply(); closeQuote();
  if (input){ input.value = ""; autosize(); }
  paintQueued();
}
const MOD_KEYS = new Set(["Shift", "Control", "Alt", "Meta", "AltGraph", "CapsLock", "Fn", "FnLock", "NumLock", "ScrollLock", "OS", "Super", "Hyper", "Symbol", "SymbolLock"]);
const swallowed = new Set();
const keyId = e => e.code || e.key;
const swallow = e => { e.preventDefault(); e.stopImmediatePropagation(); };

// ---- language switch: everything this module wrote --------------------------------------------------
function relang(){
  RelayMD.LABELS.copy = t('r.md.copy'); RelayMD.LABELS.copyAria = t('r.md.copyAria'); RelayMD.LABELS.copied = t('r.md.copied'); RelayMD.LABELS.image = t('r.md.image'); RelayMD.LABELS.tapCopy = t('r.md.tapCopy'); RelayMD.LABELS.details = t('p339.details');
  wordsSrc = null; qsKey = null;
  renderMenu();
  pttUI(ptt); paintTray(); for (const a of atts) paintChip(a);
  for (const c of document.querySelectorAll("#qbar .qc")) c.remove();   // P286: chips rebuilt in the new language
  paintPlaceholder(); paintReply(); setOm(el("om").classList.contains("open")); paintOut();
  paintKeys();   // P207
  if (cur) render(cur); else showPage();
}

// ---------------------------------------------------------------- wiring
const CAM_HOLD_MS = 500;
export function init(ctx){
  C = ctx;
  el("sheet").hidden = true;
  input = el("input"); mainEl = el("main"); deck = el("deck"); tray = el("tray"); menu = el("menu"); menuList = el("menuList"); sug = el("sug"); mic = el("mic");
  for(const [id,close,fallback,rank,trap] of [
    ['rd',closeReader,'readBtn',45,true],['cam',closeCam,'tCamera',50,true],
    ['keys',escKeys,'keysBtn',40,true],   // @ix keys.search_close (P344: Esc clears + closes the sheet's search first)
    ['silent',closeSilent,'input',40,true],   // P356: P344 had put this entry inside the comment above — the silent panel lost its layer
    ['menu',closeMenu,'slashBtn',30,true],['sug',()=>{sugDismissed=input.value;closeSug();},'input',25,false],
    ['qbar',cancelReply,'input',5,false]
  ])registerLayer({element:()=>el(id),open:()=>!el(id).hidden,close,fallback:()=>{const node=el(fallback);return node&&!node.closest('[hidden]')?node:(id==='keys'?el('setBtn'):input);},rank,trap});
  registerLayer({element:()=>el('om'),open:()=>el('om').classList.contains('open'),close:()=>setOm(false),fallback:()=>el('omMore'),rank:4,trap:false});
  // P188: 搜索聊天记录 — above the command list and the shortcuts; Esc / × close it, the 🔍 gets the focus back
  registerLayer({element:()=>el('srch'),open:search.isOpen,close:search.close,fallback:()=>el('srchBtn'),rank:42,trap:true});
  search.init({
    pages: () => pages(), olderLeft, epoch: () => hist.epoch, hostId, sourceOf, agentName: () => C.agentName(),
    canFind: () => !!(C.api.canFind && C.api.canFind()), find: (q, sort) => C.api.findHistory(q, sort),
    beforeOpen: () => { closeMenu(); closeSug(); if (rdOpen()) closeReader(); },
    show: async (id) => {                            // a page-back already on its way (the background catch-up) first lands
      for (let n = 0; n < 60; n++){ if (await showTurn(id)) return true; if (!hist.older) return false; await new Promise(r => setTimeout(r, 250)); }
      return false;
    },
    flash: (id, ts, where) => {
      if (where.includes("in") && !where.includes("reply") && omCan() && !omOpen()) setOm(true);
      search.flashCards(ts, where);
    },
  });
  registerLayer({element:()=>el('sheet'),open:()=>document.body.dataset.sheet==='1',close:()=>el('sheetClose').click(),fallback:()=>el('pendTag'),rank:20});
  RelayMD.LABELS.copy = t('r.md.copy'); RelayMD.LABELS.copyAria = t('r.md.copyAria'); RelayMD.LABELS.copied = t('r.md.copied'); RelayMD.LABELS.image = t('r.md.image'); RelayMD.LABELS.tapCopy = t('r.md.tapCopy'); RelayMD.LABELS.details = t('p339.details');
  speaker = new Speaker((id, st, why) => {
    if (st === "idle") speakSt.delete(id); else speakSt.set(id, st);
    if (st === "failed"){ toast(t('r.speak.err.' + (["novoice", "empty", "unsupported", "engine", "missing_key", "provider_rejected", "provider_busy", "provider_unavailable", "network", "busy", "not_ready", "tap_play"].includes(why) ? why : "engine")), 2600); setTimeout(() => { if (speakSt.get(id) === "failed"){ speakSt.delete(id); paintSpeak(); } }, 3000); }
    paintSpeak();
  }, why=>{if(why==='voice_fallback')toast(t('r.speak.voice_fallback'),2600);});
  document.addEventListener('pointerdown',()=>speaker?.unlock(),{passive:true});
  mainEl.addEventListener("scroll", () => { stick = atBottom(); paintMore(); }, {passive: true});   // @ix page.scroll
  try{ const ro = new ResizeObserver(() => paintMore()); ro.observe(mainEl); ro.observe(el("read")); }
  catch(_){ addEventListener("resize", paintMore); }
  el("words").addEventListener("click", copyCodeClick);
  el("words").addEventListener("click", () => { if (wordsSrc === NO_ECHO_SRC) refreshNewest(); });   // P135: 「点此刷新」
  el("rdWords").addEventListener("click", copyCodeClick);
  for (const id of ["words", "rdWords"]) el(id).addEventListener("cc:copied", copiedNote);   // P292: the inline code's 「已拷贝」

  // sheet
  el("apprDeny").addEventListener("click", () => { const p = apprLive(); if (p) apprPost(p, "deny"); });
  el("cmdMore").addEventListener("click", toggleCmd);
  el("cmdCopy").addEventListener("click", () => copyOut(cmdFull, t('r.appr.cmdCopied')));
  holdToApprove(el("apprAllow"), "allow");   // @ix appr.approve (+ apprHotkey: ⌘ / Ctrl+Enter)
  document.addEventListener("keydown", apprHotkey, true);     // capture: before the text box's Enter-to-send
  holdToApprove(el("apprBatch"), "batch");
  holdToApprove(el("apprGoalAdd"), "goaladd");   // P339: approve + add the offered line to the goal's grant (a tap alone never approves)
  el("cmdToggle").addEventListener("click", toggleGoalCmd);
  el("qs").addEventListener("click", e => {
    const b = e.target.closest && e.target.closest(".opt");
    const a = cur && cur.ask;
    if (!b || b.disabled || !a || askState(a) !== "open" || askBusy) return;
    const qi = +b.dataset.q, n = +b.dataset.n, multi = !!(a.questions[qi] || {}).multi;
    if (askSel.id !== a.id) askSel = {id: a.id, picks: a.questions.map(() => [])};
    const c0 = Array.isArray(askSel.picks[qi]) ? askSel.picks[qi] : [];
    const field = el("qs").querySelectorAll(".qfree textarea")[qi]; if (field) field.value = "";
    askSel.picks[qi] = multi ? (c0.includes(n) ? c0.filter(x => x !== n) : c0.concat([n]).sort((x, y) => x - y)) : (c0[0] === n ? [] : [n]);
    renderCard(cur);
  });
  el("askSend").addEventListener("click", () => {
    const a = cur && cur.ask;
    if (!a || askState(a) !== "open" || askBusy || !askComplete(a)) return;
    askPost(a, false);
  });
  el("askCancel").addEventListener("click", () => {
    const a = cur && cur.ask;
    if (!a || askState(a) !== "open" || askBusy) return;
    if (Date.now() >= askArmUntil){
      askArmUntil = Date.now() + 4000;
      renderCard(cur);
      setTimeout(() => { if (cur) renderCard(cur); }, 4050);
      return;
    }
    askArmUntil = 0;
    askPost(a, true);
  });
  el("sheetClose").addEventListener("click", () => {   // @ix appr.fold (Esc: the sheet's layer)
    foldedCard = cur && cur.card ? cardKey(cur.card) : null;
    if (cur && cur.ask && ASK_FINAL.includes(cur.ask.state)) askDismissed = cur.ask.id;
    if (cur && cur.approval && APPR_FINAL.includes(cur.approval.state)) apprDismissed = cur.approval.id;
    if (cur) renderCard(cur);
  });
  el("pendTag").addEventListener("click", () => { foldedCard = null; if (cur) renderCard(cur); });   // @ix app.pending

  // chrome
  new MutationObserver(() => paintChrome()).observe(document.body,
    {attributes: true, subtree: true, attributeFilter: ["hidden", "data-status", "data-conn", "data-kind", "data-sheet", "data-kb", "data-reader", "data-view", "data-agent", "data-fs"]});
  // Water level can change without a page-state repaint. Observe only its own style,
  // not every descendant style; chrome writes only html tokens, so this cannot loop.
  new MutationObserver(() => paintChrome()).observe(el("water"),
    {attributes: true, attributeFilter: ["data-known", "style"]});
  new MutationObserver(rs => {
    if (document.documentElement.dataset.ios === "1" || rs.some(r => r.attributeName !== "style")) paintChrome(true);
  }).observe(document.documentElement, {attributes: true, attributeFilter: ["data-theme", "style"]});
  document.addEventListener("visibilitychange", () => { if (document.visibilityState === "visible") { paintChrome(true); refreshNewest(); } });
  window.addEventListener("pageshow", () => paintChrome(true));
  window.addEventListener("focus", () => paintChrome(true));
  try{ matchMedia("(prefers-color-scheme: dark)").addEventListener("change", () => paintChrome(true)); }catch(_){}
  paintChrome();

  // @ix app.quota (the two quota lines below)
  for (const id of ["mWeek", "m5h"]) el(id).addEventListener("click", meterToast);
  initUsage();   // P207 §1
  setInterval(renderSub, 30000);
  setInterval(() => {
    if (document.body.dataset.conn === "on" && queuedWatchList().length) showPage();
    else { queueWatch.reset(); queueStuck = false; }
  }, 5000);
  pressable(el("meta"), e => pillStep(pillHalf(e)), pillDefault);   // @ix app.model_step app.effort_step app.model_default
  // P239: a pick still waiting its 5 s is sent when the page is left (background, app switch, close), not lost.
  document.addEventListener("visibilitychange", () => { if (document.visibilityState === "hidden") pillFlush(); });
  addEventListener("pagehide", pillFlush);
  // P207 (追加 2): the logo — tap = the page's type size (unchanged), hold = the first unread reply (was: back to 中, which Leo
  // found did nothing; 中 is one more tap away and Settings keeps the reset)
  pressable(el("orb"), () => C.menuPages(), () => C.goChat());   // @ix app.pages_menu page.go_chat

  el("omMore").addEventListener("click", () => setOm(!el("om").classList.contains("open")));   // @ix page.expand
  el("omCopy").addEventListener("click", (e) => { e.stopPropagation(); copySource(); });   // @ix card.copy_source (P363 #73)
  // P181: a double tap / double click on the source card's words = 「…」 / O (open ⇄ close here, never the full-screen reader);
  // a long press still selects text, and a double click no longer selects a word first.
  dblTap(el("omText"), rdTarget, () => setOm(!el("om").classList.contains("open")));
  el("pgPrev").addEventListener("click", () => goPage(-1));   // @ix page.prev
  const latestCorner = () => { if (pageAt !== pages().length - 1 || unreadReply) toNewest(); };
  el("pgLatest").addEventListener("click", () => { if (unreadReply) latestCorner(); });
  historyDoubleTap(el("pgLatest"), latestCorner);   // @ix page.newest
  historyDoubleTap(el("omFirst"), () => goEdge(false));   // @ix page.earliest
  // P207: the middle third — one tap = the first unread; nothing to do (no badge) = no reaction
  el("omUnread").addEventListener("click", () => { if (+el("omUnread").dataset.n) goFirstUnread(false); });   // @ix page.first_unread
  cornerLongPress(el("omFirst"), () => { const p = currentPage(); if (p && p.source && p.source.k === "phone") openRename(p.source); });   // P179 @ix app.rename_remote
  el("pgNext").addEventListener("click", () => goPage(1));   // @ix page.next
  el("copyReply").addEventListener("click", copyReply);   // @ix card.copy
  el("replyBtn").addEventListener("click", () => startReply(currentPage()));   // @ix card.reply
  el("webReportBtn").addEventListener("click", () => hotTap(currentPage()));   // P216 / P285 @ix card.web_report
  longPress(el("webReportBtn"), openHot);   // P285 @ix card.hot_pick
  try{ const barSize = new ResizeObserver(() => fitBar());
    // P340: the group's width is fitBar's own output now — observing it would loop (WebKit reports it); the bar and the
    // left button (hidden / shown) are the inputs
    barSize.observe(el("rmbar"), {box: "border-box"}); barSize.observe(el("webReportBtn"));   // border box: fitBar moves the padding
    new MutationObserver(fitBar).observe(el("rmIcons"), {childList:true,subtree:true,attributes:true,attributeFilter:["hidden"]});
  }catch(_){}
  for (const id of ["qbX", "qbJump"]) el(id).addEventListener("pointerdown", e => e.preventDefault());
  el("qbX").addEventListener("click", cancelReply);   // @ix compose.cancel_reply
  el("qbJump").addEventListener("click", () => { if (quotes.length) jumpToQuote(quotes[0]); });
  el("quoteDlg").addEventListener("click", e => { if (e.target === el("quoteDlg")) closeQuote(); });
  el("quoteDlgJump").addEventListener("click", () => { const q = qdlgAt; closeQuote(); if (q) jumpToQuote(q); });
  el("quoteDlgDel").addEventListener("click", () => { const q = qdlgAt; closeQuote(); if (q) removeQuote(q); });
  registerLayer({element: () => el("quoteDlg"), open: () => !el("quoteDlg").hidden, close: closeQuote, fallback: () => input, rank: 48});
  el("omQuote").addEventListener("click", () => jumpToTurn(+el("omQuote").dataset.id));   // @ix page.quoted
  el("omQuoteFold").addEventListener("click", e => { e.stopPropagation(); toggleQFold(); });   // P340: fold / unfold the quotes
  el("omQuote").addEventListener("keydown", e => {
    if (e.key !== "Enter" && e.key !== " ") return;
    e.preventDefault(); e.stopPropagation(); jumpToTurn(+el("omQuote").dataset.id);
  });

  // select to quote
  document.addEventListener("selectionchange", () => {
    clearTimeout(selTimer); selTimer = 0;
    if (!wordsSelection()){ selLast = null; return; }
    if (!selPtr) selTimer = setTimeout(() => selCommit(null), SEL_IDLE_MS);
  });
  document.addEventListener("pointerdown", () => { selPtr = true; clearTimeout(selTimer); selTimer = 0; }, true);
  document.addEventListener("pointercancel", e => {
    selPtr = false;
    clearTimeout(selTimer); selTimer = 0;
    if (wordsSelection()){ const type = e.pointerType || null; selTimer = setTimeout(() => selCommit(type), SEL_IDLE_MS); }
  }, true);
  // @ix card.quote_selection (select words in the reply → quote them)
  const selUp = e => { selRetry(); selPtr = false; const type = e.pointerType || "mouse"; setTimeout(() => selCommit(type), 0); };
  if (window.PointerEvent) document.addEventListener("pointerup", selUp, true);
  else document.addEventListener("mouseup", selUp, true);
  document.addEventListener("touchend", selRetry, true);
  document.addEventListener("keydown", selRetry, true);
  document.addEventListener("copy", () => { if (selPending && wordsSelection() === selPending) selCopied(selPending); });

  el("speakBtn").addEventListener("click", speakTap);   // @ix card.speak
  el("fwdBtn").addEventListener("click", forwardTap);   // @ix card.forward
  el("mailBtn").addEventListener("click", mailTap);   // @ix card.mail

  // reader
  bindReaderSwipe(el('rdScroll'), {open: rdOpen, step: rdPage}); // @ix read.prev read.next
  addEventListener("popstate", () => {
    if (rdSkipPop){ rdSkipPop = false; return; }
    if (rdOpen()){ rdPushed = false; closeReader(true); }
  });
  document.addEventListener("pointerdown", e => { rdPtr = e.pointerType || ""; }, true);
  dblTap(el("rm"), rdTarget, openReader);   // @ix card.reader
  dblTap(el("rdScroll"), rdTarget, () => closeReader());   // @ix read.close
  document.addEventListener("click", e => { if (performance.now() - rdGuard < 350){ e.preventDefault(); e.stopPropagation(); } }, true);
  el("readBtn").addEventListener("click", openReader);
  el("rdClose").addEventListener("click", () => closeReader());
  // P273: header corners + footer
  rdBuildActs();
  historyDoubleTap(el("rdWho"), () => rdJump("first"));   // @ix read.first
  historyDoubleTap(el("rdPgBtn"), () => rdJump("last"));   // @ix read.last
  el("rdPgBtn").addEventListener("click", () => { if (unreadReply) rdJump("last"); });
  el("rdUnread").addEventListener("click", () => { if (+el("rdUnread").dataset.n) rdJump("unread"); });   // @ix read.first_unread
  el("rdReadBtn").addEventListener("click", () => closeReader());
  el("rdSpeakBtn").addEventListener("click", speakTap);   // @ix read.speak
  el("rdFwdBtn").addEventListener("click", forwardTap);   // @ix read.forward
  el("rdMailBtn").addEventListener("click", mailTap);   // @ix read.mail
  el("rdCopyBtn").addEventListener("click", rdCopy);   // @ix read.copy
  el("rdReplyBtn").addEventListener("click", rdReply);   // @ix read.reply
  el("rdFsBtn").addEventListener("click", () => rdFsPop());
  registerLayer({element: () => el("rdFsPop"), open: () => !el("rdFsPop").hidden, close: () => rdFsPop(false), fallback: () => el("rdFsBtn"), rank: 46, trap: false});
  document.addEventListener("pointerdown", e => {
    if (el("rdFsPop").hidden || el("rdFsPop").contains(e.target) || el("rdFsBtn").contains(e.target)) return;
    rdFsPop(false);
  }, true);
  el("rdMinus").addEventListener("click", () => rdStep(-1));
  el("rdPlus").addEventListener("click", () => rdStep(1));   // @ix read.font (+ the pinch below)
  el("rdSlider").addEventListener("input", () => rdSetFs(Number(el("rdSlider").value), false));
  el("rdSlider").addEventListener("change", () => rdSetFs(Number(el("rdSlider").value), true));
  el("rdScroll").addEventListener("touchstart", e => { if (e.touches.length === 2) rdPinch = {d: Math.max(1, rdDist(e.touches)), fs: rdFs}; }, {passive: true});
  el("rdScroll").addEventListener("touchmove", e => {
    if (!rdPinch || e.touches.length !== 2) return;
    if (e.cancelable) e.preventDefault();
    rdSetFs(rdPinch.fs * rdDist(e.touches) / rdPinch.d, false);
  }, {passive: false});
  const rdPinchEnd = e => { if (rdPinch && e.touches.length < 2){ rdPinch = null; rdPinchAt = performance.now(); rdSave(); } };
  el("rdScroll").addEventListener("touchend", rdPinchEnd);
  el("rdScroll").addEventListener("touchcancel", rdPinchEnd);
  for (const g of ["gesturestart", "gesturechange"]) document.addEventListener(g, e => { if (rdOpen()) e.preventDefault(); });

  // swipe   @ix page.prev page.next (cards right / left)
  deck.addEventListener("touchstart", e => {
    if (e.touches.length !== 1 || document.body.dataset.sheet === "1"){ sw = null; return; }
    const tt = e.touches[0];
    if (tt.clientX < SWIPE_EDGE || tt.clientX > innerWidth - SWIPE_EDGE || hScrollable(e.target)){ sw = null; return; }
    sw = {x: tt.clientX, y: tt.clientY, t: performance.now(), mode: null, dx: 0, lx: tt.clientX, lt: performance.now(), v: 0};
    if (nearSelection(tt.clientX, tt.clientY)){ sw.mode = "hold"; deck.dataset.swipe = "hold"; }   // dragging a selection handle
  }, {passive: true});
  document.addEventListener("selectionchange", () => { if (sw && wordsSelection()) swipeVeto(); });
  deck.addEventListener("touchmove", e => {
    if (!sw || e.touches.length !== 1) return;
    const tt = e.touches[0], dx = tt.clientX - sw.x, dy = tt.clientY - sw.y;
    if (sw.mode === null){
      if (Math.abs(dx) + Math.abs(dy) < SWIPE_MIN_MOVE) return;
      sw.mode = performance.now() - sw.t > SWIPE_HOLD_MS ? "hold"
        : Math.abs(dy) <= Math.abs(dx) * Math.tan(SWIPE_DEG * Math.PI / 180) ? "page" : "scroll";
      deck.dataset.swipe = sw.mode;
    }
    if (sw.mode !== "page") return;
    if (e.cancelable) e.preventDefault();
    const now = performance.now();
    sw.v = (tt.clientX - sw.lx) / Math.max(1, now - sw.lt); sw.lx = tt.clientX; sw.lt = now;
    const ps = pages(), at = follow ? ps.length - 1 : pageAt;
    const edge = (dx > 0 && at === 0 && !(hist.turns.length && hist.turns[0].id > hist.firstId)) || (dx < 0 && at >= ps.length - 1);
    sw.dx = edge ? dx * 0.3 : dx;
    sw.edge = edge;
    deck.dataset.rubber = edge ? "1" : "0";
    deck.style.transition = "none";
    deck.style.transform = `translateX(${sw.dx}px)`;
  }, {passive: false});
  deck.addEventListener("touchend", swipeEnd);
  deck.addEventListener("touchcancel", () => { if (sw && sw.mode === "page"){ sw.dx = 0; sw.edge = true; } swipeEnd(); });

  // viewport
  watchViewport(fitViewport);   // P142: + pageshow / visibility / focus / resize, now + next frame + 300 ms
  input.addEventListener("focus", () => setTimeout(fitViewport, 300));
  input.addEventListener("blur", () => setTimeout(fitViewport, 300));

  // composer
  input.addEventListener("compositionstart", () => { composing = true; });
  // P302: an IME (Chinese keyboards on Android, pinyin on a Mac) commits 「/」 through a composition — its input events arrive
  // while composing, so the list is opened here, once the text is final
  input.addEventListener("compositionend", e => { composing = false; if (dropEcho(e, true)) return; capField(); autosize(); draftSoon(); updateSug(); });
  input.addEventListener("input", e => { if (!composing && dropEcho(e)) return; if (!composing) capField(); autosize(); draftSoon(); });
  if (window.IntersectionObserver) new IntersectionObserver(es => { if (es.some(e => e.isIntersecting)) autosize(); }).observe(input);
  twin = document.createElement("textarea");
  twin.setAttribute("aria-hidden", "true"); twin.tabIndex = -1; twin.rows = 1;
  twin.style.cssText = "height:auto;max-height:none;overflow:hidden";
  const twinBox = document.createElement("div");
  twinBox.setAttribute("aria-hidden", "true");
  twinBox.style.cssText = "position:absolute;left:-10000px;top:0;visibility:hidden;pointer-events:none;width:0;height:0;overflow:visible";
  twinBox.appendChild(twin); document.body.appendChild(twinBox);
  addEventListener("resize", paintPlaceholder);
  addEventListener("pageshow", paintPlaceholder);
  document.addEventListener("visibilitychange", () => { if (!document.hidden) paintPlaceholder(); else saveDraft(); });
  try{ let phW = -1; new ResizeObserver(() => { if (input.clientWidth !== phW){ phW = input.clientWidth; paintPlaceholder(); } }).observe(input); }catch(_){}
  try{ document.fonts.addEventListener("loadingdone", paintPlaceholder); }catch(_){}
  // P406 #168: `at` = where the words will land (the ⌘V-anywhere path appends at the end); default: the field's selection
  const takePaste = (e, at = null) => {
    const cd = e.clipboardData;
    if (!cd) return;
    const text = cd.getData("text/plain");
    const files = clipFiles(cd);
    if (files.length){
      if (!text) e.preventDefault();
      files.forEach(f => shotclip.seen(f));
      takeFiles(files.map((f, i) => pastedName(f, i, files.length)), 'r.verb2.paste', "paste");
    }
    if (!text) return;
    const route = pasteRoute({value: input.value, start: at ?? input.selectionStart, end: at ?? input.selectionEnd, text, max: MAX_TEXT()});
    if (route === "field" || route === "none") return;
    e.preventDefault();
    if (route === "over"){ pasteOver(text); return; }
    const full = atts.length >= MAX_ATT;
    addFiles([new File([text], `${t('r.file.paste')}-${stamp()}.txt`, {type: "text/plain"})], "paste");
    if (!full) toast(t('r.paste.long'), 2600);
  };
  input.addEventListener("paste", takePaste);
  // P393 #143 (Leo): ⌘V / Ctrl+V with the focus on no field (the page itself, a button) pastes into the composer — the
  // same path as a paste in it. Never while a menu / page / sheet / dialog is open; a field (key card, search) keeps its own.
  const editable = n => !!n && (n.isContentEditable || /^(INPUT|TEXTAREA|SELECT)$/.test(n.tagName));
  document.addEventListener("paste", e => {
    const cd = e.clipboardData, l = activeLayer();
    if (e.defaultPrevented || !cd || editable(e.target) || editable(document.activeElement) || modalOpen() || (l && l.rank >= 0)
        || document.querySelector("dialog[open]") || input.disabled || !input.getClientRects().length) return;
    const text = cd.getData("text/plain");
    if (!text && !clipFiles(cd).length) return;
    takePaste(e, input.value.length);
    const keep = e.defaultPrevented;
    e.preventDefault(); focusComposer();
    if (!text || keep) return;
    const n = input.value.length;
    input.setRangeText(text, n, n, "end");
    input.dispatchEvent(new Event("input", {bubbles: true}));
  });

  // drag and drop
  window.addEventListener("dragenter", e => {
    e.preventDefault();
    if (!isFileDrag(e) || modalOpen()) return;
    dragDepth++;
    dropUI(dragCount(e));
  });
  window.addEventListener("dragover", e => {
    e.preventDefault();
    const take = isFileDrag(e) && !modalOpen();
    if (e.dataTransfer) e.dataTransfer.dropEffect = take && atts.length + dragCount(e) <= MAX_ATT ? "copy" : "none";
    if (!take){ if (dragDepth){ dragDepth = 0; dropUI(null); } return; }
    if (!dragDepth) dragDepth = 1;
    dropUI(dragCount(e));
  });
  window.addEventListener("dragleave", e => {
    e.preventDefault();
    if (!dragDepth) return;
    dragDepth = Math.max(0, dragDepth - 1);
    if (!dragDepth) dropUI(null);
  });
  window.addEventListener("dragend", () => { dragDepth = 0; dropUI(null); });
  window.addEventListener("drop", e => {   // @ix compose.paste
    e.preventDefault();
    dragDepth = 0; dropUI(null);
    if (!isFileDrag(e) || modalOpen()) return;
    takeFiles([...((e.dataTransfer && e.dataTransfer.files) || [])], 'r.verb2.drop', "drop");
  });

  // pickers + camera
  const PICKERS = {tDoc: "fDoc", tPhoto: "fPhoto", tCamera: "fCamera"};   // @ix compose.file compose.photo compose.camera
  for (const [btn, inp] of Object.entries(PICKERS)){
    el(btn).addEventListener("click", e => {
      if (btn === "tCamera" && camHeld) { camHeld = false; e.preventDefault(); return; }   // P190: that was a hold (= scan)
      if (btn === "tCamera" && isDesktop()) return openCam();
      el(inp).click();
    });
  }
  // P190: the camera button held = scan a QR code (tap = photo, as before). Runs on release (a user gesture); a move off the
  // button cancels. No fifth button in the tool row.
  let camHold = 0, camHeld = false;
  const camBtn = el("tCamera");
  camBtn.addEventListener("pointerdown", e => {   // @ix app.scan (camera held)
    if (e.button !== 0) return;
    camHeld = false; clearTimeout(camHold);
    camHold = setTimeout(() => { camHold = 0; camHeld = true; camBtn.dataset.held = "1"; try { navigator.vibrate?.(12); } catch { /* */ } }, CAM_HOLD_MS);
  });
  const camEnd = (fire) => () => {
    clearTimeout(camHold); camHold = 0;
    const was = camBtn.dataset.held === "1";
    delete camBtn.dataset.held;
    if (was && fire && C?.scan) C.scan();
    if (!fire) camHeld = false;
  };
  camBtn.addEventListener("pointerup", camEnd(true));
  camBtn.addEventListener("pointercancel", camEnd(false));
  camBtn.addEventListener("pointerleave", camEnd(false));
  camBtn.addEventListener("contextmenu", e => e.preventDefault());
  el("camSnap").addEventListener("click", () => {
    const v = el("camVideo"), c = el("camShot");
    c.width = v.videoWidth || 1280; c.height = v.videoHeight || 720;
    c.getContext("2d").drawImage(v, 0, 0, c.width, c.height);
    camView("still", "");
  });
  el("camRetake").addEventListener("click", () => camView("live", ""));
  el("camUse").addEventListener("click", () => {
    el("camShot").toBlob(b => {
      if (b) addFiles([new File([b], `${t('r.file.camera')}-${stamp()}.jpg`, {type: "image/jpeg"})], "camera");
      else toast(t('r.cam.bad'), 2600);
      closeCam();
    }, "image/jpeg", 0.9);
  });
  el("camFile").addEventListener("click", () => { closeCam(); el("fCamera").click(); });
  el("camCancel").addEventListener("click", closeCam);
  addEventListener("keydown", e => { if (e.key === "Escape" && !el("cam").hidden) closeCam(); });
  document.addEventListener("visibilitychange", () => { if (document.visibilityState === "hidden" && !el("cam").hidden) closeCam(); });
  addEventListener("pagehide", () => { if (!el("cam").hidden) closeCam(); });
  const ORIGIN = {fPhoto: "photo", fCamera: "camera", fDoc: "file"};
  for (const id of ["fPhoto", "fCamera", "fDoc"]){
    el(id).addEventListener("change", () => {
      const i = el(id), files = [...(i.files || [])];
      i.value = "";
      if (files.length) addFiles(files.map(f => droppable(f) || f), ORIGIN[id]);
    });
  }

  // menu + type-ahead
  renderMenu();
  el("slashBtn").addEventListener("click", e => {   // @ix compose.slash
    e.stopPropagation(); toggleMenu();
  });
  sheetTouch();
  // P285: the hot-key sheet lists what the 「/」 list offers, in its order (/update and 全部停下 are not names to bind)
  initHot({run: RUN.filter(c => c !== "update"), say: ["customize", SWITCH_CMD.slice(1)], changed: paintActs,
    refresh: () => loadMenu().catch(() => {}),
    groups: () => {
      const host = hostMenu || {items: [], skills: []}, out = [];
      out.push({label: t('r.menu.run'), rows: RUN.filter(c => c !== "update").map(c => ["/" + c, runDescription(c)])
        .concat([["/customize", t('p181.customize')]], switchItems())});
      out.push({label: t('r.menu.tools'), rows: [["/meeting-notes", t('r.meet.desc')]]});
      for (const [cmd, why, gg] of host.items){
        const label = gg || t('r.menu.insert'), g = out.find(x => x.label === label);
        if (g) g.rows.push([cmd, why]); else out.push({label, rows: [[cmd, why]]});
      }
      if (host.skills.length) out.push({label: t('r.menu.skills'), rows: host.skills.map(([c, d]) => [c, d])});
      return out;
    }});
  // the 5-hour bar's label appears / changes with the first reading, the composer grows with the text: keep the card on it
  try{ const ro = new ResizeObserver(() => placeSheet()); ro.observe(el("m5h")); const lab = el("m5h").querySelector(".lab"); if (lab) ro.observe(lab); ro.observe(menu.parentElement); }catch(_){}
  document.addEventListener("click", e => { if (!menu.hidden && !(e.target.closest && e.target.closest("#menu, #keys, #silent"))) closeMenu(); });
  // P181 long presses: ≡ → 「定制命令」, file → clipboard text, photo → clipboard image (camera: nothing for now)
  longPress(el("slashBtn"), () => { closeMenu(); sayPreset("cmds"); });   // @ix compose.customize
  longPress(el("tDoc"), pasteClipText, {at: "release"});   // @ix compose.paste_text
  longPress(el("tPhoto"), pasteClipImage, {at: "release"});   // @ix compose.paste_image
  menu.addEventListener("click", async e => {
    const b = e.target.closest && e.target.closest("button");
    if (!b) return;
    e.stopPropagation();
    if (b.id === "menuClose"){ closeMenu(); return; }
    if (b.id === "modelsEntry"){ closeMenu(); C.models?.(); return; }
    if (b.id === "silentEntry"){ openSilent(); return; }
    if (b.id === "keysEntry"){ openKeys(); return; }
    if (b.id === "appEntry"){ closeMenu(); location.href = / AgentJApp\//.test(navigator.userAgent) ? "agentj-app://status" : "jarvis-app://status"; return; }
    if (b.id === "clipEntry"){ closeMenu(); pasteClipImages(); return; }
    if (b.id === "switchEntry"){ closeMenu(); saySwitch(); return; }
    if (b.id === "resetEntry"){ closeMenu(); C.resetConn?.(); return; }   // P332: Settings → 紧急修复, ① starts
    if (b.dataset.run){ closeMenu(); if (await runCmd(b.dataset.run)) { toNewest(); executionToast(t('r.run.sent', {cmd: "/" + b.dataset.run}), 1600); } return; }
    if (b.dataset.prompt){ closeMenu(); sayPreset(b.dataset.prompt); return; }
    if (b.dataset.estop){ closeMenu(); C.estop(b.dataset.estop === "stop"); return; }
    if (b.dataset.tui !== undefined){ tuiToast(b.dataset.tui); return; }   // P412 #175: listed with the reason it cannot be done here
    if (b.dataset.slash){ closeMenu(); insertSlash(b.dataset.slash); }
  });
  input.addEventListener("input", () => { if (!composing) updateSug(); });
  input.addEventListener("compositionend", updateSug);
  input.addEventListener("focus", updateSug);
  input.addEventListener("blur", () => setTimeout(() => { if (document.activeElement !== input) closeSug(); }, 150));
  sug.addEventListener("mousedown", e => e.preventDefault());
  for (const ev of ["resize", "scroll"]) window.visualViewport?.addEventListener(ev, () => { if (!sug.hidden) capAbove(sug); });
  wireCeil();   // P302 追加 28: --ui-sheet-ceil / --ui-sheet-stage-top follow the WEEKLY rule
  sug.addEventListener("click", e => {
    e.stopPropagation();
    const b = e.target.closest("[data-sug]");
    if (b) pickSug(Number(b.dataset.sug));
  });
  input.addEventListener("keydown", e => {
    if (sug.hidden || e.isComposing) return;
    const n = sugList.length;
    if (e.key === "ArrowDown" || e.key === "ArrowUp"){
      e.preventDefault(); sugAt = (sugAt + (e.key === "ArrowDown" ? 1 : n - 1)) % n; paintSug();
    } else if (e.key === "Tab" && !e.shiftKey){
      e.preventDefault(); e.stopImmediatePropagation(); pickSug(sugAt);
    } else if (e.key === "Escape"){
      e.preventDefault(); e.stopImmediatePropagation(); sugDismissed = input.value; closeSug();
    } else if (e.key === "Enter" && !e.shiftKey){
      const typed = input.value.replace(/^\uff0f/, "/");
      if (sugList[sugAt] && typed !== sugList[sugAt][0]){ e.preventDefault(); e.stopImmediatePropagation(); pickSug(sugAt); }
      else closeSug();
    }
  });

  // mic
  addEventListener("resize", keyboardDown);
  keyboardDown();
  mic.addEventListener("pointerdown", e => {   // @ix compose.talk compose.talk_lock
    if (e.button !== 0 || !e.isPrimary) return;
    e.preventDefault();
    if (e.pointerType !== "mouse" && document.activeElement === input && keyboardDown()) input.blur();
    if (ptt && ptt.pend){ pttLock(ptt); return; }
    if (ptt && ptt.locked && ptt.id === null){
      if (performance.now() - ptt.lockT0 <= DTAP_MS) return; // second tap keeps the new lock
      try{ mic.setPointerCapture(e.pointerId); }catch(_){}
      ptt.id = e.pointerId; ptt.x0 = e.clientX; ptt.y0 = e.clientY; ptt.touch = e.pointerType !== "mouse"; return;
    }
    if (ptt || performance.now() - lockEndAt < LOCK_REARM_MS) return;
    try{ mic.setPointerCapture(e.pointerId); }catch(_){}
    const second = performance.now() - lastTapUp <= DTAP_MS;   // the first tap's take already ended (blur, a failed mic): lock anyway
    pttStart(e.pointerId, e.clientY);
    if (ptt){ ptt.x0 = e.clientX; ptt.touch = e.pointerType !== "mouse"; if (second) pttLock(ptt); }
  });
  mic.addEventListener("pointermove", e => {
    if (!ptt || e.pointerId !== ptt.id) return;
    const c = cancelZone(mic.getBoundingClientRect(), ptt.x0, ptt.y0, e.clientX, e.clientY, ptt.touch, ptt.cancel);
    if (c !== ptt.cancel){ ptt.cancel = c; pttUI(ptt); try{ if (navigator.vibrate) navigator.vibrate(12); }catch(_){} }
  });
  // The end of a press, from whichever event WebKit / Chromium delivers first; the rest find ptt.id already cleared.
  const shortTap = p => !p.locked && !p.cancel && typeof p.id === "number" && performance.now() - p.t0 < PTT_MIN_MS;
  const pressEnd = (how, e) => {
    if (!ptt) return;
    if (how === "touchend" ? typeof ptt.id !== "number" || e.touches.length : e.pointerId !== ptt.id) return;
    if (shortTap(ptt) && (how === "pointerup" || how === "touchend")){ pttLock(ptt); lastTapUp = performance.now(); return; }
    if (ptt.locked){
      if (how === "pointerup" || how === "touchend") pttEnd(ptt, "release");
      else { ptt.id = null; ptt.cancel = false; pttUI(ptt); }
      return;
    }
    const released = how === "pointerup" || how === "touchend";
    ptt.autoSend = released && !ptt.cancel && !ptt.interrupted;
    pttEnd(ptt, released ? "release" : "cancel");
  };
  mic.addEventListener("pointerup", e => pressEnd("pointerup", e));
  mic.addEventListener("pointercancel", e => pressEnd("pointercancel", e));
  // a capture lost before its pointerup (WebKit may order them so): give the pointerup 80 ms to arrive before ending the take
  mic.addEventListener("lostpointercapture", e => {
    const p = ptt, id = e.pointerId;
    if (!p || id !== p.id) return;
    setTimeout(() => { if (ptt === p && p.id === id) pressEnd("lostpointercapture", e); }, 80);
  });
  mic.addEventListener("touchend", e => pressEnd("touchend", e));
  mic.addEventListener("contextmenu", e => e.preventDefault());
  mic.addEventListener("selectstart", e => e.preventDefault());
  mic.addEventListener("dblclick", e => e.preventDefault());
  // P205 / P346 (#54): the recording card's three icons (a locked take): cancel · end · end and send
  el("pttEnd").addEventListener("click", () => { if (ptt && ptt.locked) pttEnd(ptt, "release"); });   // P205 @ix compose.rec_end
  el("pttCancel").addEventListener("click", () => { if (ptt && ptt.locked) pttEnd(ptt, "cancel"); });   // P205 @ix compose.rec_cancel
  // P342 (#46): end + send in one tap — the same auto-send a released hold-to-talk take gets (short → words sent once
  // transcribed, long → the voice attachment sent), so no 「结束」 then 「发送」
  el("pttSend").addEventListener("click", () => { if (ptt && ptt.locked){ ptt.autoSend = true; pttEnd(ptt, "release"); } });   // @ix compose.rec_send
  mic.addEventListener("keydown", e => {
    if ((e.key === " " || e.key === "Enter") && !e.repeat){
      e.preventDefault();
      if (ptt && ptt.locked) pttEnd(ptt, "release"); else pttStart("key", 0);
    }
  });
  mic.addEventListener("keyup", e => {
    if ((e.key === " " || e.key === "Enter") && ptt && ptt.id === "key"){ e.preventDefault(); pttEnd(ptt, "release"); }
  });
  mic.addEventListener("blur", () => { if (ptt && ptt.id === "key") pttEnd(ptt, "cancel"); });
  // P496: privacy boundary — a hidden page never retains an enabled audio source.
  // Preserve a locked/segmented take as an interruption; foreground return is passive.
  document.addEventListener("visibilitychange", () => {
    if (document.visibilityState !== "visible") microphoneAway();
  });
  window.addEventListener("blur", () => { if (ptt && !ptt.locked && !ptt.pend && !ptt.seg) pttEnd(ptt, "cancel"); });   // P181: not between the two taps; P182: not mid-take
  window.addEventListener("pagehide", microphoneAway);

  // native shell hooks (relay's window.relayNative, same four methods)
  window.relayNative = Object.freeze({
    take(b64, secs){
      if (typeof b64 !== "string" || !b64 || b64.length > NATIVE_TAKE_MAX * 4 / 3 + 4) return false;
      let bytes;
      try{ bytes = Uint8Array.from(atob(b64), c => c.charCodeAt(0)); }catch(_){ return false; }
      const dur = Number(secs) > 0 ? Number(secs) : bytes.length / 32000;
      takeFinished(new Blob([bytes], {type: "audio/wav"}), dur);
      return true;
    },
    busy(){ return !!ptt; },
    image(b64, mime){
      if (!/^image\/(png|jpeg|webp|gif)$/.test(mime) || typeof b64 !== "string" || !b64 || b64.length > UPLOAD_MAX * 4 / 3 + 4) return false;
      let bytes;
      try{ bytes = Uint8Array.from(atob(b64), c => c.charCodeAt(0)); }catch(_){ return false; }
      takeFiles([pastedName(new File([bytes], "image", {type: mime}), 0, 1)], 'r.verb2.paste', "paste");
      return true;
    },
    show(id){
      id = Number(id);
      if (!Number.isInteger(id) || id <= 0) return false;
      showTurn(id);
      return true;
    },
  });

  // send, clear, keys
  el("send").addEventListener("click", () => { unlockWhoosh(); say(); });   // @ix compose.send (hold = sendvol.js: compose.volume)
  sendvol.attach(el("send"), input);
  el("sayCancel").addEventListener("click", e => { e.stopPropagation(); cancelSay(); });
  if (window.ResizeObserver) new ResizeObserver(padForCancel).observe(el("sayCancel"));   // P328: language / type size
  // P412 (#174): the full-screen editor — the tool row's fifth button (@ix compose.fullscreen), Ctrl+G, a swipe up on the box
  fsed.init({input, maxText: MAX_TEXT, send: () => { unlockWhoosh(); say(); }, items: () => menuItems, tui: tuiToast, t, toastAction,
             touch: touchComposer});
  el("tFull").addEventListener("mousedown", e => e.preventDefault());   // the box keeps its caret
  el("tFull").addEventListener("click", openFull);
  swipeUpToFull(input.closest(".field"));
  const clr = el("clr");
  for (const ev of ["pointerdown", "mousedown"]) clr.addEventListener(ev, e => {
    clr._keep = clr._keep || document.activeElement === input;
    e.preventDefault();
  });
  clr.addEventListener("click", e => {   // @ix compose.clear
    e.preventDefault(); e.stopPropagation();
    if (clr._keep || document.activeElement === clr) focusComposer();
    clr._keep = false;
    const text = input.value;
    if (!text) return;
    input.value = "";
    input.dispatchEvent(new Event("input"));
    toastAction(t('r.clr.done'), t('r.clr.undo'), () => {
      input.value = (text + input.value).slice(0, MAX_TEXT());
      input.dispatchEvent(new Event("input"));
      focusComposer();
      input.setSelectionRange(input.value.length, input.value.length);
    }, 5000);
  });
  input.addEventListener("keydown", e => {
    if (e.key === "Enter" && !e.shiftKey && !e.isComposing && e.keyCode !== 229){   // @ix compose.send compose.newline
      e.preventDefault();
      if (!e.ctrlKey && !e.metaKey && !e.altKey && backslashEnter(input)) return;   // P412: `\` + Enter = a new line
      unlockWhoosh(); say();
    }
  });
  el("silentClose").addEventListener("click", closeSilent);
  el("silentMore").addEventListener("click", moreSilent);
  el("silent").addEventListener("click", e => { if (e.target === el("silent")) closeSilent(); });
  el("keysClose").addEventListener("click", closeKeys);
  el("keysBtn").addEventListener("click", openKeys);   // @ix app.keys
  el("keys").addEventListener("click", e => { if (e.target === el("keys")) closeKeys(); });
  el("keysFindBtn").addEventListener("click", () => { if (keysFindOpen()) closeKeysFind(); else openKeysFind(); });   // @ix keys.search
  el("keysFindInput").addEventListener("input", () => paintKeys());

  // P207: what each table key of interactions.json does (tools/interactions check compares these ids with the JSON).
  const scrollStep = () => Math.max(40, Math.round(mainEl.clientHeight * 0.25));
  const scrollScreen = () => Math.max(40, Math.round(mainEl.clientHeight * 0.9));
  const pick = id => { closeMenu(); el(id).click(); };
  const shown = id => { const b = el(id); return !!b && !b.hidden && !b.disabled && (b.getClientRects().length > 0); };
  // 「这一页的文件」: the file card that has the focus, else the first one of this page; its own icon does the rest.
  const fileAct = cls => {
    const fc = document.activeElement?.closest?.(".mcard") || el("words").querySelector(".mcard") || el("mstrip")?.querySelector(".mcard");
    const b = fc && fc.querySelector(".mact." + cls);
    if (!b || b.hidden) { toast(t(fc ? 'r.ix.fileNo' : 'r.ix.noFile'), 1600); return; }
    b.click();
  };
  // Function navigation must reveal its destination above ordinary reading/help layers.
  // Use each layer's own close action, so reader history and focus restoration remain intact.
  const keyNav = (fn, keepSearch = false) => {
    if (mediaViewerOpen()) closeMediaViewer();
    if (rdOpen()) closeReader();
    if (!el('keys').hidden) closeKeys();
    if (!el('silent').hidden) closeSilent();
    if (!keepSearch && search.isOpen()) search.close();
    closeMenu(); closeSug();
    return fn();
  };
  const ACT = {
    // @ix-actions-begin
    "page.prev": () => goPage(-1),
    "page.next": () => goPage(1),
    "page.newest": () => goEdge(true),
    "page.earliest": () => goEdge(false),
    "page.first_unread": () => goFirstUnread(true),
    "page.quoted": () => { const q = el("omQuote"); if (!q.hidden && !el("om").hidden) jumpToTurn(+q.dataset.id); else toast(t('r.ix.noQuote'), 1600); },
    // P150: ↑ expands the message card (= o), ↓ folds it; with nothing to toggle they scroll as before.
    "page.expand": e => {
      if (e.key === "o"){ if (!el("om").hidden) setOm(!el("om").classList.contains("open")); return; }
      if (e.key === "ArrowUp"){ if (omCan() && !omOpen()) setOm(true); else mainEl.scrollBy(0, -scrollStep()); return; }
      if (omCan() && omOpen()) setOm(false); else mainEl.scrollBy(0, scrollStep());
    },
    "page.scroll": e => mainEl.scrollBy(0, e.shiftKey ? -scrollScreen() : scrollScreen()),
    "card.reader": () => openReader(),
    "card.speak": () => speakTap(),
    "card.forward": () => forwardTap(),
    "card.mail": () => { if (shown("mailBtn")) mailTap(); else toast(t('r.ix.noMail'), 2000); },
    "card.copy": () => copyReply(),
    "card.copy_source": () => copySource(),
    "card.reply": () => startReply(currentPage()),
    "card.web_report": () => { if (shown("webReportBtn") && !el("webReportBtn").disabled) hotTap(currentPage()); },   // P216 / P285
    "file.preview": () => fileAct("pv"),
    "file.download": () => fileAct("dl"),
    "file.share": () => fileAct("sh"),
    "file.mail": () => fileAct("mail"),
    "file.forward": () => fileAct("tg"),
    "app.pages_menu": () => C.menuPages(),
    "app.font_step": () => setFz((fzAt + 1) % FZ_STEPS.length, true),
    "app.rename_agent": () => { if (isReady()) openAgentName(); else toast(t('p181.name.offline'), 2600); },
    "app.rename_remote": () => { const p = currentPage(); if (p && p.source && p.source.k === "phone") openRename(p.source); else toast(t('r.ix.noRemote'), 2000); },
    "app.busy_list": () => { if (shown("busyDots")) el("busyDots").click(); else toast(t('r.ix.noBusy'), 1600); },
    "app.robots_theme": () => { closeMenu(); sayPreset("theme"); },
    "app.load": () => { if (shown("loadHit")) el("loadHit").click(); },
    "app.quota": () => el("mWeek").click(),
    "app.model_step": () => { if (!el("meta").hidden) pillStep("model"); },
    "app.effort_step": () => { if (!el("meta").hidden) pillStep("effort"); },
    "app.model_default": () => { if (!el("meta").hidden) pillDefault(); },
    "app.scan": () => { closeMenu(); if (C.scan) C.scan(); },
    "app.settings": e => keyNav(() => e.altKey && e.shiftKey && C.openSettings ? C.openSettings() : C.settings && C.settings()),
    "app.keys": () => { if (mediaViewerOpen()) closeMediaViewer(); openKeys(); },
    "app.upgrade_host": () => keyNav(() => { C.goChat && C.goChat(); upgradeFromKey(); }),   // P265: from any layer (reader too) back to chat, then the upgrade card
    "app.reload_host": () => { reloadHost(); },   // P320: the same as ≡ /reload (an older host without it: the row is absent, the key says so)
    "app.refresh_web": () => { refreshApp(); },   // P265: the same action as Settings → 更新并刷新, not a click on a hidden button
    "app.search": () => keyNav(() => search.toggle(), true),
    "app.pair_add": () => keyNav(() => C.pairAdd && C.pairAdd()),
    "app.screenshot": async () => {
      if (C.screenshot) await C.screenshot(); // capture the visible layer first; only then reveal its attachment
      if (mediaViewerOpen()) closeMediaViewer();
      if (rdOpen()) closeReader();
      if (!el('keys').hidden) closeKeys();
    },
    "app.friends": () => keyNav(() => C.friends && C.friends()),
    "app.bots": () => keyNav(() => C.bots && C.bots()),
    "app.setup": () => keyNav(() => C.setup && C.setup()),
    "app.pending": () => { if (shown("pendTag")) el("pendTag").click(); },
    "compose.focus": () => { closeMenu(); focusComposer(); input.setSelectionRange(input.value.length, input.value.length); },
    // P154 追加 8: "/" outside the field = into the field with the "/" typed at the end of the draft — the same input event
    // as typing it there, so an empty draft opens the slash-command menu. Not while a dialog / Settings is open, not from
    // another field, never on a held key.
    "compose.slash": () => {
      closeMenu(); focusComposer();
      const n = input.value.length;
      input.setRangeText("/", n, n, "end");
      input.dispatchEvent(new InputEvent("input", {inputType: "insertText", data: "/", bubbles: true}));
    },
    "compose.mention": () => {
      if (modalOpen() || activeLayer() || document.querySelector('dialog[open]') || input.disabled) return;
      input.focus({preventScroll:true}); const n = input.value.length;   // the @ key: a keyboard is there, focus even on touch
      input.setRangeText((n && !/\s$/.test(input.value) ? ' ' : '') + '@', n, n, 'end');
      input.dispatchEvent(new InputEvent('input', {inputType:'insertText',data:'@',bubbles:true}));
    },
    "compose.customize": () => { closeMenu(); sayPreset("cmds"); },
    "compose.file": () => pick("tDoc"),
    "compose.paste_text": () => pasteClipText(),
    "compose.photo": () => pick("tPhoto"),
    "compose.paste_image": () => pasteClipImage(),
    "compose.camera": () => pick("tCamera"),
    "compose.volume": () => sendvol.open(),
    // @ix-actions-end
  };
  // P265: global functional keys precede field editing and use physical codes (macOS Option symbols).
  window.addEventListener('keydown', e => {
    if (!functionalLookup(e) || !C.paired?.()) return;
    // Preserve the locked take's existing any-key release, then perform the deliberate function action.
    if (ptt && ptt.locked && !e.repeat) pttEnd(ptt, 'release');
    // Secret/approval layers keep ownership; never navigate away from an unanswered sensitive card.
    const blocked = !el('confirm').hidden || shownById('lqr') || shownById('sout') || shownById('elev') || document.body.dataset.sheet === '1' || fsed.isOpen();
    dispatchFunctional(e, ACT, C.paired?.(), blocked);
  }, true);
  document.addEventListener("keydown", e => {
    if (e.isComposing || (e.keyCode === 229 && !isM(e))) return;
    if (document.body.dataset.view !== "chat") return;
    if (fsed.isOpen()) return;   // P412: the full-screen editor has its own keys (js/fsedit.js); Esc = layers.js
    const tg = e.target;
    if (e.key === "Escape"){
      if (!el("silent").hidden){ e.preventDefault(); closeSilent(); return; }
      if (rdOpen()){ e.preventDefault(); closeReader(); return; }
      if (!el("keys").hidden){ e.preventDefault(); escKeys(); return; }
      if (!el("cam").hidden || !el("confirm").hidden || !el("badge-panel").hidden || !el("settings").hidden) return;
      if (!menu.hidden){ e.preventDefault(); closeMenu(); return; }
      if (quotes.length){ e.preventDefault(); escAt = 0; cancelReply(); return; }   // @ix compose.cancel_reply
      if (tg === input){ e.preventDefault(); input.blur(); escAt = 0; return; }   // @ix compose.focus compose.clear (Esc Esc below) // Leave the field without counting dismissal toward double-Escape clear.
      // P150: Esc never interrupts the Agent (owner, 2026-10-12) — the stop button / /stop / e-stop are the only ways.
      const now = performance.now();
      if (escAt && now - escAt <= ESC2_MS){
        escAt = 0; e.preventDefault();
        if (!composerEmpty()) clearComposer();
        return;
      }
      escAt = now;
      return;
    }
    if (keysFindKey(e, tg)){ e.preventDefault(); openKeysFind(); return; }   // @ix keys.search (P344: before ⌘K / Ctrl+K = chat search)
    // @ix app.search
    // P188: ⌘K anywhere / Ctrl+K outside the field (in the field Ctrl+K stays readline's 删到行尾) opens 搜索聊天记录; again = close.
    if ((e.metaKey || (e.ctrlKey && tg !== input)) && !e.altKey && !e.shiftKey && (e.key === "k" || e.key === "K" || e.code === "KeyK")){
      if (dialogOpen() && !search.isOpen()) return;
      e.preventDefault(); search.toggle(); return;
    }
    // @ix app.settings (⌘, / Ctrl+,)
    // F13: ⌘, (mac) / Ctrl+, opens Settings from anywhere on the chat screen, the field and the reader included.
    if ((e.ctrlKey || e.metaKey) && !e.altKey && !e.shiftKey && (e.key === "," || e.code === "Comma")){
      if (dialogOpen() && el("settings").hidden) return;
      e.preventDefault(); if (rdOpen()) closeReader(); closeMenu(); C.settings && C.settings(); return;
    }
    if (rdOpen()){   // @ix read.close read.font read.scroll
      if (e.ctrlKey || e.metaKey || e.altKey || (tg && tg.id === "rdSlider" && e.key.startsWith("Arrow"))) return;
      const horizontal = e.key === "ArrowLeft" || e.key === "ArrowRight", selected = getSelection();
      if (horizontal && (e.shiftKey || (selected && !selected.isCollapsed))) return; // selection keeps native arrows
      const sc = el("rdScroll"), rstep = Math.max(40, Math.round(sc.clientHeight * 0.25)), rscreen = Math.max(40, Math.round(sc.clientHeight * 0.9));
      const f = {f: () => closeReader(), "+": () => rdStep(1), "=": () => rdStep(1), "-": () => rdStep(-1), "_": () => rdStep(-1),
                 g: () => rdJump("last"), G: () => rdJump("first"), u: () => rdJump("unread"), y: rdCopy, r: rdReply,   // @ix read.first read.last read.first_unread read.copy read.reply
                 l: () => el("rdSpeakBtn").click(), t: () => el("rdFwdBtn").click(), e: () => { if (!el("rdMailBtn").hidden) el("rdMailBtn").click(); },   // @ix read.speak read.forward read.mail
                 ArrowLeft: () => rdPage(-1), ArrowRight: () => rdPage(1),
                 ArrowUp: () => sc.scrollBy(0, -rstep), ArrowDown: () => sc.scrollBy(0, rstep),
                 " ": () => sc.scrollBy(0, e.shiftKey ? -rscreen : rscreen)}[e.key];
      if (f){ e.preventDefault(); f(); }
      return;
    }
    // @ix compose.fullscreen — P412 (#174): Ctrl+G (Claude Code's 「在编辑器里写」) opens the full-screen editor
    if (e.ctrlKey && !e.metaKey && !e.altKey && !e.shiftKey && (e.key === "g" || e.key === "G" || e.code === "KeyG")
        && (tg === input || !(typingIn(tg) || dialogOpen()))){ e.preventDefault(); openFull(); return; }
    if (tg === input){
      const k = e.key.length === 1 ? e.key.toLowerCase() : "";
      const ctrl = e.ctrlKey && !e.metaKey && !e.altKey && !e.shiftKey;
      // @ix compose.readline compose.history compose.edge_arrows compose.stash compose.history_search
      if (termKey(e, input, MAX_TEXT())) return;
      if (ctrl && k === "s"){ e.preventDefault(); stashKey(); return; }
      if (ctrl && k === "r"){ e.preventDefault(); histFind(); return; }
      if ((e.key === "ArrowUp" || e.key === "ArrowDown") && !e.ctrlKey && !e.metaKey && !e.altKey && !e.shiftKey
          && !e.defaultPrevented && sug.hidden && histKey(e.key === "ArrowUp")){ e.preventDefault(); return; }
      // P150: ← / → turn the card when the caret has nowhere to go (empty field, or already at that end); never on a held key with text.
      if (edgeArrow(e)){ e.preventDefault(); goPage(e.key === "ArrowLeft" ? -1 : 1); return; }
    }
    if (e.ctrlKey || e.metaKey || typingIn(tg) || typingIn(document.activeElement) || dialogOpen()) return;
    // P207: every other key of this screen comes from interactions.json (interactions.gen.js KEYMAP → ACT below); Alt+key
    // only when it is one of ours (Alt+M is never the talk key).
    const ix = ixLookup(e);
    if (e.altKey && !ix) return;
    if (!e.altKey && isM(e)){   // @ix compose.talk compose.talk_lock (hold m / m m: keyup ends it, so not in the table)
      e.preventDefault();
      if (mKey || e.repeat) return;
      mKey = true; mCode = e.code;
      if (ptt && ptt.pend){ ptt.viaM = true; pttLock(ptt); return; }
      if (ptt || performance.now() - lockEndAt < LOCK_REARM_MS) return;
      pttStart("key", 0);
      if (ptt) ptt.viaM = true;
      return;
    }
    if (!ix || (e.repeat && !ix.repeat)) return;
    const act = ACT[ix.id];
    if (!act) return;
    e.preventDefault();
    act(e);
  });
  document.addEventListener("keyup", e => {
    if (!mKey || !(mCode ? e.code === mCode : isM(e))) return;
    mKey = false;
    if (!ptt || ptt.id !== "key") return;
    e.preventDefault();
    if (!ptt.locked && !ptt.cancel && performance.now() - ptt.t0 < PTT_MIN_MS) pttTap(ptt);
    else pttEnd(ptt, "release");
  });
  window.addEventListener("keydown", e => {
    if (swallowed.has(keyId(e))){ swallow(e); return; }
    if ((e.key === "Enter" || e.key === " ") && e.target && e.target.closest && e.target.closest(".pttbtn")) return;   // P205: the icon decides
    if (!ptt || !ptt.locked || MOD_KEYS.has(e.key) || e.isComposing
        || (e.keyCode === 229 && (typingIn(e.target) || typingIn(document.activeElement)))) return;
    swallow(e);
    if (e.repeat || (mKey && (mCode ? e.code === mCode : isM(e)))) return;
    swallowed.add(keyId(e));
    pttEnd(ptt, e.key === "Escape" ? "cancel" : "release");
  }, true);
  window.addEventListener("keyup", e => { if (swallowed.delete(keyId(e))) swallow(e); }, true);
  window.addEventListener("blur", () => { mKey = false; swallowed.clear(); });

  onLang(relang);
  paintPlaceholder();
  try{ document.fonts.ready.then(paintPlaceholder); }catch(_){}
  getSealed("ihist").then(h => { if (Array.isArray(h)) histMem = h.filter(x => typeof x === "string").slice(-HIST_MAX); });
  restoreDraft();
  paintKeys();   // P207: the sheet is in the DOM from the start (drawn from interactions.json), as the hand-written one was
  outbox.open(); addEventListener("online", paintOut); addEventListener("offline", paintOut);
  showPage();
  pttUI(null);
  paintTray();
}
/** Called when the session becomes ready again (a new host turn source, menu). */
export function onReady(){ syncQueued(); paintVoiceReadiness(); refreshNewest(); paintPlaceholder(); refreshSend(); loadMenu(); paintActs(); renderMenu(); setTimeout(() => recRecover().catch(() => {}), 800); }
export function onEstop(){ paintPlaceholder(); renderMenu(); refreshSend(); }
export function rerender(){ paintVoiceReadiness(); if (cur) render(cur); else showPage(); }
/** For the screens test: internal state worth asserting. */
export function debug(){ return {search: search.debug(), pageAt, follow, pages: pages().length, total: totalPages(), atts: atts.map(a => ({st: a.st, name: a.name, id: a.id || null, origin: a.origin})), queued: queued.length, asrBusy, hist: hist.turns.map(x => x.id), menuItems: menuItems.map(x => x[0]), ptt: ptt ? {locked: ptt.locked, cancel: ptt.cancel} : null, lockCount, histMem: histMem.slice(), killBuf: killed()}; }


let userPreferences={};
export function applyPreferences(value){
  userPreferences=value||{};configureSpeech(userPreferences.voice);
  ownerAddr=cleanAddress(userPreferences.owner?.address);   // P205: the working line's address (rerender follows in app.js)
  speaker?.stop();
  const app=userPreferences.appearance||{};
  if(app.language)window.AJLang?.set(app.language);
  if(app.theme==='system')document.documentElement.removeAttribute('data-theme');
  else if(['light','dark'].includes(app.theme))document.documentElement.setAttribute('data-theme',app.theme);
  setHot(userPreferences.ui?.card_hot_action);   // P285: absent (an older host) = the default
  loadMenu();
}
export function announceTurn(turn,old){
  if(!visibleReply({reply:typeof turn.reply === 'string' ? turn.reply : turn.reply?.text}) || !old || turn.end!=='done' || new URLSearchParams(location.search).has('watcher'))return;
  if(userPreferences.voice?.speak_replies)speaker?.tap(turn.id,typeof turn.reply==='string'?turn.reply:(turn.reply?.text||''));
  else if(userPreferences.voice?.speak_notifications && userPreferences.voice?.tts?.mode==='phone')speaker?.tap('notification-'+turn.id,t('preferences.notification'));
}
document.addEventListener('keydown',e=>{
  const combo=[e.ctrlKey?'ctrl':null,e.altKey?'alt':null,e.shiftKey?'shift':null,e.metaKey?'meta':null,e.key.toLowerCase()].filter(Boolean).join('+');
  const binding=(userPreferences.keyboard?.bindings||[]).find(x=>x.combo===combo);
  if(!binding)return;
  const fn={'open-menu':toggleMenu,'read-reply':speakTap,'stop-speech':()=>speaker?.stop(),'focus-input':()=>focusComposer(),'interrupt':()=>C.api.slash('stop')}[binding.action];
  if(fn){e.preventDefault();e.stopImmediatePropagation();fn();}
},true);

export function configProblem(error){toast(t('preferences.error')+String(error).slice(0,300),8000);}

// Native shell reads this local endpoint snapshot; ciphertext is decrypted only inside this WebView.
window.agentjNative=Object.freeze({snapshot:()=>({
  ready:isReady(),
  wake_threshold:userPreferences.voice?.wake_threshold||0.25,
  wake_tokens:userPreferences.voice?.wake_tokens||'',wake_enabled:userPreferences.voice?.wake_enabled===true,
  latest:hist.turns.filter(x=>x.end==='done').at(-1)?.id||0,
  speak_notifications:userPreferences.voice?.speak_notifications===true,
  theme:userPreferences.appearance?.theme||'system',
  tts_mode:userPreferences.voice?.tts?.mode||'phone',tts_voice:userPreferences.voice?.tts?.voice||'',tts_rate:userPreferences.voice?.tts?.rate||1,language:userPreferences.appearance?.language||'zh'
})});

// A dismissal must never become the first half of the inherited double-Escape action.
export function resetEscape(){ escAt=0; }

// P387: reuse the normal message navigation and reader lifecycle for TV keys.
export function remoteMessage(dir){return rdOpen()?rdPage(dir):goPage(dir);}
export function remoteReader(open){if(open)openReader();else if(rdOpen())closeReader();}
export function remoteReading(){return rdOpen();}
export function remoteScroll(dir){const node=rdOpen()?el('rdScroll'):mainEl;if(node)node.scrollBy({top:dir*node.clientHeight,behavior:'instant'});}
