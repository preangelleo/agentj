import { quotaWindows } from './dashboard.js';
// The chat screen = relay's phone page (pwa/index.html <script>, ADR-022 … ADR-052 there), ported section by section.
// What changed is only the transport and the wording: relay's HTTP calls (requests, the event stream, raw uploads) go through the
// adapter `ctx.api` (Noise session, PROTOCOL §10), every string comes from the i18n dictionaries, Telegram forwarding is
// the phone's share sheet, the cloud voice is the phone's own speechSynthesis, drafts and input history are sealed in
// IndexedDB. The sections keep relay's order and `// ----` markers; they share ~40 bindings (cur, hist, atts, ptt, …),
// which is why they stay one module (the leaf parts live in md.js, blobs.js, wav.js, speak.js, ui.js; padding, frag and signatures come from proto/wire.js).
import { RelayMD } from './md.js';
import { upgrade as upgradeWords, forget as forgetRendered } from './render.js';   // P57: lazy code / math / diagrams
import { t, lang, onLang, fillText } from './t.js';
import { el, toast, toastOff, toastAction, human, stamp, mmss, confirmSheet } from './ui.js';
import { Upload, TYPES as DROP_TYPES, MAX_BYTES as UPLOAD_MAX, MAX_ATT, ASR_MAX_BYTES } from './blobs.js';
import { renderPage as renderMedia, forgetAll as forgetMedia, renderReader as renderReaderMedia, clearReader as clearReaderMedia } from './media.js';   // F21: files the reply shows (§13); P59: in the reader too
import { toWav } from './wav.js';
import { Speaker, configureSpeech } from './speak.js';
import { isReady, sendApp, hostId } from './session.js';
import { getSealed, putSealed, dbDel } from './store.js';
import { Outbox, paint as paintOutbox, MAX as OUT_MAX } from './outbox.js';
import { askKind as friendAsk, renderAsk as renderFriendAsk, addFriendCmd, openFromCmd } from './friends.js';   // §17.7: friend request / friend's question cards

let C = null;                        // ctx from app.js: api, myDev(), estopOn(), canSign(), panels …
const BRAND_LOGO = { idle: 'brand/img/logo-mark.png', working: 'brand/img/status/logo-working.png',
  waiting: 'brand/img/status/logo-waiting.png', question: 'brand/img/status/logo-question.png', unknown: 'brand/img/status/logo-offline.png' };
const VERBS = ['Bash', 'Write', 'Edit', 'MultiEdit', 'NotebookEdit', 'Read', 'WebFetch', 'WebSearch', 'Delete', 'ExternalDirectory', 'CodexPermissions'];
const verb = (tool) => (VERBS.includes(tool) ? t('r.verb.' + tool) : t('r.verb.other', { tool }));
let lastStatus = null;
let cur = null;          // last rendered snapshot
const skew = 0;          // host clock = phone clock for display (turn ts come from the host, ms)
let lastSync = null;     // phone-local time of the last frame we actually received
let input = null, mainEl = null, deck = null, tray = null, menu = null, sug = null, mic = null;

// ---- the Agent's words (Markdown, DOM nodes only) -------------------------------------
let wordsSrc = null;
// pending: an open page whose reply has not come yet — no words at all (the thinking dots say it), never the
// empty-history line 「已连上你的电脑…」 (relay never showed a page before its reply; Agent J shows the say's page at once)
function renderWords(text, pending = false){
  const tx = (text || "").trim();
  const key = pending && !tx ? "\u0000pending" : tx;
  if (key === wordsSrc) return;
  wordsSrc = key;
  const w = el("words");
  w.className = "words" + (!tx ? " empty" : tx.length > 600 ? " l" : tx.length > 240 ? " m" : "");
  if (pending && !tx) w.replaceChildren(); else fill(w, tx);
  afterWords();
  rdFollow(tx);
}
function fill(w, tx){
  if (tx){
    try{
      const detail = tx.match(/^([\s\S]*)\n\n(\u8be6\u60c5|Details):\n```text\n([\s\S]*)\n```$/);
      tx = detail ? detail[1] : tx;
      w.replaceChildren(RelayMD.toDOM(RelayMD.parse(tx), document)); upgradeWords(w);
      if (detail){
        const d = document.createElement("details"), label = document.createElement("summary"), raw = document.createElement("pre");
        label.textContent = detail[2]; raw.textContent = detail[3]; d.append(label, raw); w.appendChild(d);
      }
      return;
    }catch(_){}
  }
  w.replaceChildren();
  const paras = tx ? tx.split(/\n\s*\n/) : [emptyText()];
  for (const p of paras){ const n = document.createElement("p"); n.textContent = p.trim(); w.appendChild(n); }
}
const emptyText = () => (C && C.connected() ? t('r.emptyReady') : t('r.empty'));

// ---- the reading area scrolls; the page does not ---------------------------------
const STICK_PX = 24, NEW_AT = "start";
let stick = true;
function atBottom(){ return mainEl.scrollHeight - mainEl.scrollTop - mainEl.clientHeight <= STICK_PX; }
function afterWords(){
  if (stick) mainEl.scrollTop = NEW_AT === "end" ? mainEl.scrollHeight : 0;
  paintMore();
}
function paintMore(){
  el("rm").classList.toggle("more", mainEl.scrollHeight - mainEl.scrollTop - mainEl.clientHeight > 1);
}
function copyCodeClick(e){
  const b = e.target.closest && e.target.closest(".copy");
  if (!b) return;
  const code = b.closest(".codeblock").querySelector("pre > code");
  RelayMD.copyText(code ? code.textContent : "").then(ok => {
    if (!ok){ toast(t('r.copy.fail'), 2200); return; }
    b.textContent = t('r.md.copied'); b.classList.add("done");
    clearTimeout(b._t);
    b._t = setTimeout(() => { b.textContent = t('r.md.copy'); b.classList.remove("done"); }, 1600);
  });
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
const ASK_FINAL = ["answered", "cancelled", "terminal", "timeout", "ended", "stopped"];
const ASK_RESULT_S = 6;
let askSel = {id: null, picks: []};
let askSent = {id: null, state: null};
let askBusy = false, askArmUntil = 0, askToasted = null, askResultTimer = null, qsKey = null, askDismissed = null;

function askState(a){
  if (!a) return null;
  return a.state === "open" && askSent.id === a.id ? askSent.state : a.state;
}
function askLabels(a){
  return (a.choices || []).map((pick, i) => pick.map(n => {
    const o = ((a.questions[i] || {}).options || [])[n - 1]; return o ? o.label : String(n);
  }).join(t('r.list.sep'))).join(t('r.list.sep2'));
}
function askText(a){
  const st = askState(a);
  if (st === "answered") return t('r.q.st.answered', {labels: askLabels(a)});
  return ["sent", "cancel_sent", "cancelled", "terminal", "timeout", "ended", "detached", "stopped"].includes(st) ? t('r.q.st.' + st) : "";
}
function askComplete(a){
  return askSel.id === a.id && a.questions.every((_, i) => (askSel.picks[i] || []).length > 0);
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
        if (o.description){ const d = document.createElement("span"); d.className = "d"; d.textContent = o.description; tt.appendChild(d); }
        b.append(n, tt); b.disabled = !live;
        li.appendChild(b); ol.appendChild(li);
      });
      box.appendChild(ol);
    });
  }
  const picks = a && askSel.id === a.id ? askSel.picks : (a && !open ? (a.choices || []) : []);
  box.querySelectorAll(".opt").forEach(b => {
    const on = (picks[+b.dataset.q] || []).includes(+b.dataset.n);
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
function paintTags(item){
  const box = el("askTags"); box.replaceChildren();
  if (item && item.kind === "permission"){
    if (item.cats && item.cats.length) for (const c of item.cats){ const s = document.createElement("span"); s.className = "atag danger"; s.dataset.cat = c; s.textContent = t('ask.cat.' + c); box.appendChild(s); }
    else { const s = document.createElement("span"); s.className = "atag low"; s.textContent = t('ask.low'); box.appendChild(s); }
  }
  if (item && item.task){ const s = document.createElement("span"); s.className = "atag task"; s.textContent = t('ask.task', {task: item.task}); box.appendChild(s); }
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
  p.textContent = left > 0 ? t('ask.left', {n: left}) : t('ask.timeUp');
  if (left > 0) leftTimer = setTimeout(() => paintLeft(item), 1000);
}

function renderCard(s){
  const card = s.card, a = s.ask || null, p = s.approval || null;
  const pending = s.status === "waiting" && !!card;
  const folded = pending && foldedCard === cardKey(card);
  if (!pending) foldedCard = null;
  const fin = !!a && ASK_FINAL.includes(a.state);
  const age = fin && a.final_at ? Date.now() / 1000 - skew - a.final_at : Infinity;
  if (fin && age < ASK_RESULT_S && askToasted !== a.id){ askToasted = a.id; toast(askText(a), 2600); }
  const result = fin && age < ASK_RESULT_S && !pending && askDismissed !== a.id;
  const pfin = !!p && APPR_FINAL.includes(p.state);
  const page = pfin && p.final_at ? Date.now() / 1000 - skew - p.final_at : Infinity;
  if (pfin && page < ASK_RESULT_S && apprToasted !== p.id){ apprToasted = p.id; toast(apprText(p), 2600); }
  const presult = pfin && page < ASK_RESULT_S && !pending && !result && apprDismissed !== p.id;
  clearTimeout(askResultTimer);
  const left = result ? ASK_RESULT_S - age : presult ? ASK_RESULT_S - page : 0;
  if (left > 0) askResultTimer = setTimeout(() => { if (cur) renderCard(cur); }, left * 1000 + 50);
  document.body.dataset.sheet = (pending && !folded) || result || presult ? "1" : "0";
  el("pendTag").hidden = !folded;
  el("apprBtns").hidden = true; el("apprBatch").hidden = true;
  el("sheet").classList.remove("danger");
  el("frAsk").hidden = true;
  if (result){
    el("askText").textContent = t('r.ask.answer');
    el("cmdBox").hidden = true; el("qs").hidden = false; el("why").hidden = true;
    renderQuestion(null, a);
    return;
  }
  if (presult){ renderPermission(null, p); return; }
  if (!pending){ el("askCancel").hidden = true; paintLeft(null); return; }
  el("pendText").textContent = card.kind === "question" ? t('r.pend.question') : t('r.pend.permission');
  const q = card.kind === "question";
  el("askText").textContent = q ? t('r.ask.answer') : t('r.ask.nod');
  el("cmdBox").hidden = q; el("qs").hidden = !q;
  if (q){
    renderQuestion(card, a && !ASK_FINAL.includes(a.state) ? a : null);
    el("why").hidden = true;
  } else renderPermission(card, p && p.state === "open" ? p : null);
}

// ---- tool approval from the phone (§8, §10.8; relay M2-approve) ---------------------
// 拒绝 is one tap; 批准 needs a HOLD of APPR_HOLD_MS — a tap alone never approves. Both are Ed25519-signed by this
// device's non-extractable key over exactly what the sheet shows (tool + summary; the batch scope for 批量).
const APPR_FINAL = ["approved", "denied", "terminal", "timeout", "ended", "stopped"];
const APPR_HOLD_MS = 1200;
let apprSent = {id: null, state: null}, apprBusy = false, apprToasted = null, apprDismissed = null;
let apprHold = null;

function apprState(p){
  if (!p) return null;
  return p.state === "open" && apprSent.id === p.id ? apprSent.state : p.state;
}
const FR_DONE = {approved: "allow", denied: "deny", timeout: "timeout", ended: "ended", stopped: "stopped", terminal: "ended"};
function apprText(p){
  const st = apprState(p);
  if (friendAsk(p) && FR_DONE[st]) return t('fr.ask.done.' + (p.tool === "friend_request" ? "fr_" : "pq_") + FR_DONE[st]);
  if (st === "open") return p.local_only ? t('r.appr.st.localOnly') : "";
  return ["allow_sent", "deny_sent", "batch_sent", "approved", "denied", "terminal", "timeout", "ended", "stopped"].includes(st) ? t('r.appr.st.' + st) : "";
}
function renderPermission(card, p){
  el("askText").textContent = t('r.ask.nod');
  if (friendAsk(p)){
    el("sheet").classList.toggle("danger", !!(p.pq && p.pq.high_risk));
    el("sheet").classList.toggle("friend-neutral", !(p.pq && p.pq.high_risk));                                    // §17.7: friends.js draws the card into #frAsk; relay keeps the sheet
    for (const id of ["cmdBox", "why", "qs", "askCancel", "askSend", "apprBtns", "apprBatch", "askState"]) el(id).hidden = true;
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
  const danger = !!(p && p.cats && p.cats.length);
  el("sheet").classList.toggle("danger", danger);
  el("sheetTitle").textContent = danger ? t('r.verb.danger', {verb: verb(tool)}) : verb(tool);
  el("tool").textContent = tool;
  const {main} = summarize(p ? p.summary : card.summary);
  el("cmd").textContent = main;
  const why = p && p.why ? t('ask.why', {why: p.why}) : "";
  el("why").textContent = why; el("why").hidden = !why;
  const st = apprState(p), open = st === "open";
  const sign = C.canSign();
  el("apprBtns").hidden = !open || !sign;
  el("apprDeny").disabled = apprBusy;
  el("apprAllow").hidden = !p || p.local_only;
  el("apprAllow").disabled = apprBusy;
  el("apprAllowLbl").textContent = danger ? t('r.appr.holdOne') : t('r.appr.hold');
  const batch = open && sign && p && p.scope && !danger;
  el("apprBatch").hidden = !batch;
  if (batch){
    el("apprBatchLbl").textContent = t('r.appr.batchHold');
    el("apprBatchScope").textContent = t('ask.batchScope', {scope: p.scope, max: p.batch_max, mins: Math.round(p.batch_secs / 60)});
  }
  if (!open || apprBusy) apprCancelHold();
  const text = p ? (open && !sign ? t('ask.noSign') : apprText(p)) : "";
  el("askState").hidden = !text;
  el("askState").textContent = text;
  el("elsewhereText").textContent = !p ? t('r.appr.elsewhere') : p.local_only ? t('r.appr.st.localOnlyShort') : open ? t('r.appr.elsewhereLive') : t('r.appr.elsewhere');
  paintTags(p || null);
}
async function apprPost(p, action){
  apprBusy = true; if (cur) renderCard(cur);
  const r = await C.api.approve(p, action);
  apprBusy = false;
  if (r.ok){ apprSent = {id: p.id, state: action === "batch" ? "batch_sent" : action === "allow" ? "allow_sent" : "deny_sent"}; }
  else toast(r.why === "offline" ? t('r.appr.net') : t('r.appr.fail'), 3000);
  if (cur) renderCard(cur);
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
// theme-color is rewritten as a NEW <meta> on every path that can change the page colour; the launch colour of an
// iPhone home-screen app is the last colour actually painted (js/boot.js reads it before first paint).
const STATUSBAR_MODE = "match";
let LAUNCH_CHROME = (window.__ajLaunchChrome || "");
function toHex(c){
  const m = /^rgba?\(([^)]+)\)$/.exec(c) || /^color\(srgb ([^)]+)\)$/.exec(c);
  if (!m) return "";
  const v = m[1].split(/[\s,/]+/).filter(Boolean).slice(0, 3).map(Number);
  const k = c.startsWith("color(") ? 255 : 1;
  return "#" + v.map(x => Math.round(Math.min(255, Math.max(0, x * k))).toString(16).padStart(2, "0")).join("");
}
function chromeTarget(){
  if (LAUNCH_CHROME){ if (document.body.dataset.conn !== "on") return LAUNCH_CHROME; LAUNCH_CHROME = ""; }
  try{ return toHex(getComputedStyle(el("chromeProbe")).backgroundColor); }catch(_){ return ""; }
}
function setThemeColor(hex){
  const old = [...document.querySelectorAll('meta[name="theme-color"]')];
  if (old.length === 1 && old[0].getAttribute("content") === hex && !old[0].media) return;
  const m = document.createElement("meta");
  m.name = "theme-color"; m.content = hex;
  for (const o of old) o.remove();
  document.head.prepend(m);
}
let savedChrome = "";
function saveChrome(hex){
  if (STATUSBAR_MODE !== "match" || hex === savedChrome) return;
  const b = document.body.dataset;
  if (b.conn !== "on" || b.reader === "1" || b.view !== "chat") return;
  savedChrome = hex;
  try{ localStorage.setItem("aj.chrome", hex); }catch(_){}
}
export function paintChrome(force){
  const hex = chromeTarget();
  if (!hex) return;
  document.documentElement.style.backgroundColor = hex;
  saveChrome(hex);
  if (force !== true){ setThemeColor(hex); return; }
  setThemeColor(hex.slice(0, 5) + (parseInt(hex.slice(5, 7), 16) ^ 1).toString(16).padStart(2, "0"));
  let done = false;
  const settle = () => { if (done) return; done = true; const h = chromeTarget(); if (h) setThemeColor(h); };
  requestAnimationFrame(settle); setTimeout(settle, 50);
}

// ---- dashboard numbers: "—" / an empty bar unless the host has an exact source --------
function paintUsage(u){
  u = u || {};
  paintPill(u);
  el("meta").title = u.source ? t('r.usage.age', {s: Math.round(u.age_s || 0)}) : t('r.usage.none');
  const windows = quotaWindows(u);
  for (const [idx, id] of ['mWeek', 'm5h'].entries()) {
    const bar = el(id), x = windows[idx];
    bar.hidden = !x; bar.dataset.known = x ? '1' : '0';
    bar.firstElementChild.style.width = x ? x.pct + '%' : '0';
    bar.querySelector('.lab').textContent = x ? `${x.window} ${Math.round(x.pct)}%` : '';
    const label = x ? t('r.meter.' + ({weekly:'week',daily:'daily',monthly:'monthly','5h':'h5'}[x.window])) : '';
    bar.setAttribute('aria-label', x ? t('r.meter.used', {label, pct:x.pct}) : '');
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
function meterToast(){
  const u = (cur && cur.usage) || {};
  if (!u.source){ toast(t('r.usage.none'), 3200); return; }
  const bits = [];
  for (const x of quotaWindows(u)) bits.push(t('r.meter.used', {label:t('r.meter.' + ({weekly:'week',daily:'daily',monthly:'monthly','5h':'h5'}[x.window])),pct:x.pct}));
  if (u.context_pct != null) bits.push(t('r.usage.ctx', {p: u.context_pct}));
  else if (Number.isFinite(u.context_used)) bits.push(`${u.context_used} tokens`);
  else if (postCompact) bits.push(t('r.usage.ctxPost'));
  if (u.age_s != null) bits.push(t('r.usage.ago', {s: Math.round(u.age_s)}));
  toast(bits.join(" · ") || "—", 3600);
}

/** The single entry: a snapshot from snap.js (relay's render(s)). */
export function render(s){
  if (!C) return;
  cur = s;
  lastSync = new Date();
  const st = s.status || "unknown";
  document.body.dataset.status = st;
  document.body.dataset.agent = s.hostStatus || "none";
  if (st === "waiting" && s.kind) document.body.dataset.kind = s.kind;
  else delete document.body.dataset.kind;
  paintChrome();
  el("capText").textContent = document.body.dataset.conn !== "on" ? el("capText").textContent
    : st === "waiting" ? t('r.capt.' + (s.kind || 'permission')) : t('r.capt.' + (["working", "idle"].includes(st) ? st : "unknown"));
  renderSub();
  paintLogo(st, s.kind);
  noteHistory(s);
  renderCard(s);
  paintUsage(s.usage);
  paintCompacting(s.compacting === true);
  if (s.usage && typeof s.usage.context_pct === "number" && !s.compacting) postCompact = false;
  if (lastStatus !== null && s.status !== lastStatus && navigator.vibrate && document.body.dataset.conn === "on"){
    try{ navigator.vibrate(s.status === "waiting" ? [90,60,90] : 35); }catch(_){}
  }
  lastStatus = s.status;
}

// ---- the header pill switches the model / effort (relay ADR-035; §10.11) ---------------
// Tap the model half = the next model, tap the effort half = the next effort, hold ≈ 0.6 s = the default. Taps are
// gathered: only the target standing 1 s after the last tap is sent, once. Dashed until the next meter reports it.
const PILL_GATHER_MS = 1000, HOLD_MS = 600, PILL_SETTLE_MS = 45000, HOLD_SLOP_PX = 10;
const pill = {want: null, timer: 0, sentAt: 0};
function swCat(){ return cur && cur.switch && Array.isArray(cur.switch.models) && cur.switch.models.length ? cur.switch : null; }
function realPick(){
  const u = (cur && cur.usage) || {}, c = swCat();
  const m = c && (c.models.find(x => x.id === u.model_id) || c.models.find(x => x.name === u.model));
  return {model: m ? m.id : null, effort: u.effort || null};
}
const effortsOf = (c, id) => { const m = c && c.models.find(x => x.id === id); return m && Array.isArray(m.efforts) ? m.efforts : []; };
const samePick = (a, b) => !!a && !!b && a.model === b.model && (a.effort || null) === (b.effort || null);
function paintPill(u){
  u = u || (cur && cur.usage) || {};
  const shared = el('shared-status');
  if (shared) {
    const state = C && C.connected() && u.shared_status;
    shared.hidden = !state;
    shared.textContent = state === 'desktop_writer' && u.shared_writer?.[lang()] ? u.shared_writer[lang()] : state ? t('r.shared.' + state) : '';
  }
  const c = swCat(), real = realPick();
  if (pill.want && samePick(pill.want, real)){ pill.want = null; pill.sentAt = 0; }
  if (pill.want && pill.sentAt && Date.now() - pill.sentAt > PILL_SETTLE_MS){ pill.want = null; pill.sentAt = 0; }
  const show = pill.want || null;
  const name = show ? ((c && c.models.find(x => x.id === show.model)) || {}).name || "—" : (u.model || "—");
  const effort = show ? show.effort : (u.model ? u.effort : null);
  el("metaModel").textContent = name;
  el("metaSep").textContent = effort ? " · " : "";
  el("metaEffort").textContent = effort || "";
  el("metaStale").textContent = "";
  const m = el("meta");
  if (show) m.dataset.sw = pill.sentAt && cur && cur.status !== "idle" ? "queued" : "pending";
  else delete m.dataset.sw;
}
function pillStep(half){
  const c = swCat();
  if (!c){ toast(t('r.pill.unsupported'), 2200); return; }
  const b = pill.want || realPick();
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
  paintPill();
  clearTimeout(pill.timer);
  pill.timer = setTimeout(pillSend, PILL_GATHER_MS);
}
function pillDefault(){
  const c = swCat();
  if (!c){ toast(t('r.pill.unsupported'), 2200); return; }
  clearTimeout(pill.timer);
  pill.want = {...c.default, isDefault: true}; pill.sentAt = 0;
  paintPill();
  toast(t('r.pill.default', {name: ((c.models.find(x => x.id === c.default.model) || {}).name || ""), effort: c.default.effort || "—"}), 1800);
  pillSend();
}
async function pillSend(){
  const want = pill.want; if (!want) return;
  const r = await C.api.model(want.isDefault ? {default: true} : {model: want.model, effort: want.effort});
  if (pill.want !== want) return;
  if (!r.ok){
    toast(t('r.pill.fail', {why: t('r.pill.why.' + (["unknown_model", "unknown_effort", "busy", "unsupported", "stopped", "offline", "timeout"].includes(r.why) ? r.why : "other"))}), 2600);
    pill.want = null; paintPill(); return;
  }
  pill.sentAt = Date.now();
  if (cur && cur.status !== "idle") toast(t('r.pill.queued'), 2000);
  paintPill();
}
// One press handler for both hidden controls: a tap (released before HOLD_MS, finger still) or a hold.
function pressable(node, onTap, onHold){
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
  const sep = el("metaSep").getBoundingClientRect(), m = el("meta").getBoundingClientRect();
  const cut = sep.width ? sep.left + sep.width / 2 : m.left + m.width / 2;
  return e.clientX < cut ? "model" : "effort";
}

// ---- the logo steps the page's type size: 中 → 大 → 中 → 小 → 中; hold = 中 (not stored) ----------
const FZ_STEPS = ["m", "l", "m", "s"];
let fzAt = 0;
function setFz(i, say){
  fzAt = i;
  const v = FZ_STEPS[i], root = document.documentElement;
  if (v === "m") delete root.dataset.fz; else root.dataset.fz = v;
  paintPlaceholder();
  if (say) toast(t('r.fz.' + v), 1000);
}

// ---- the logo wears the state colour ----------------------------------------------
function paintLogo(st, kind){
  if (document.body.dataset.view !== "chat") return;
  const key = st === "waiting" && kind === "question" ? "question" : (BRAND_LOGO[st] ? st : "unknown");
  const o = el("orb");
  if (o.getAttribute("src") !== BRAND_LOGO[key]) o.setAttribute("src", BRAND_LOGO[key]);
  o.dataset.v = key;
}

// ---- pages: every reply with the input it answered (relay ADR-033 / 036) -----------------
// `hist` = the loaded turns of the host's history (§10.5); pageAt = index; `follow` = on the newest page. A NEW TURN
// takes the reader to the newest page wherever they are (ADR-036); older pages loading at the front never move them.
const hist = {turns: [], count: 0, firstId: 0, lastId: 0, epoch: 0, route: true, busy: false, want: 0, older: false};
let pageAt = -1, follow = true, pagesCache = null, shownKey = null;
const noticeReads = new Set();
const isSilent = text => typeof text === "string" && text.trim() === "\u3014\u4e0d\u56de\u7fa4\u3015";
const tailSeen = {primed: false, id: 0, reply: "", virt: false};
function newTurnArrived(ps){
  const tt = ps[ps.length - 1];
  let arrived = false;
  if (tailSeen.primed && tt.id !== null) arrived = tt.id > tailSeen.id;
  if (tt.id !== null) tailSeen.id = Math.max(tailSeen.id, tt.id);
  tailSeen.reply = tt.reply; tailSeen.virt = tt.id === null; tailSeen.primed = true;
  return arrived && !isSilent(tt.reply);
}
function toNewest(){ follow = true; showPage(); }
const SRC_KINDS = ["leo", "dev", "host", "agent", "sys", "task", "cmd"];
function pages(){
  if (pagesCache) return pagesCache;
  const out = hist.turns.filter(t => !isSilent(t.reply));
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
  hist.count = 0; hist.epoch = ep; hist.turns = []; hist.lastId = 0; hist.firstId = 0; hist.want = 0;
  pagesCache = null; follow = true; pageAt = -1; tailSeen.id = 0;
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
  if (p.source && p.source.k === "phone" && p.source.dev === C.myDev() && p.end === "open") follow = true;
  showPage();
}
export function historyReset(ep){ resetHistory(ep); showPage(); }
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
  while (olderLeft()){ const b = await loadOlderBatch(); if (!b || !b.all) break; }
  showPage();
}
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
      const shown = older.filter(t => !isSilent(t.reply)).length;
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
  if (src.k === "phone") return src.dev && src.dev === C.myDev() ? {kind: "leo", label: t('r.src.you')} : {kind: "dev", label: src.name || t('r.src.otherDevice')};
  if (src.k === "telegram") return {kind:"tg",label:"Telegram"};
  if (src.k === "host") return {kind: "host", label: t('r.src.host')};
  if (src.k === "agent") return {kind: "agent", label: t('r.src.agent', {name: C.agentName()})};
  if (src.k === "task") return {kind: "task", label: src.name ? t('r.src.taskNamed', {name: src.name}) : t('r.src.task')};
  if (src.k === "cmd") return {kind: "cmd", label: t('r.src.cmd')};
  return {kind: "sys", label: src.local ? t('r.src.local') : t('r.src.sys')};
}
function showPage(){
  if (!C) return;
  const ps = pages();
  if (newTurnArrived(ps)) follow = true;
  if (follow || pageAt < 0 || pageAt > ps.length - 1) {
    pageAt = ps.length - 1;
  }
  follow = pageAt === ps.length - 1;
  const p = ps[pageAt];
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
  ot.textContent = tx || (src && src.att && src.att.length ? t('r.om.attOnly') : t('r.om.none'));
  ot.classList.toggle("none", !tx);
  if (!tx && !src?.att?.length && !src?.quote) om.hidden = true;
  const att = el("omAtt");
  att.replaceChildren();
  if (src && src.att && src.att.length){
    for (const a of src.att){ const s = document.createElement("span"); s.className = "omchip"; s.dataset.kind = a.kind; s.textContent = a.name; att.appendChild(s); }
  }
  att.hidden = !(src && src.att && src.att.length);
  const q = src && src.quote ? src.quote : null;
  el("omQuote").hidden = !q;
  el("omQuote").dataset.id = q ? String(q.id) : "";
  el("omqWho").textContent = q ? (q.who || C.agentName()) : "";
  const qx = !!q && q.excerpt === true;
  el("omqMeta").textContent = q ? [q.ts ? hhmm(q.ts / 1000) : "", qx ? t('r.quote.excerptTag') : ""].filter(Boolean).map(x => " · " + x).join("") : "";
  el("omqText").textContent = q && typeof q.text === "string" ? (qx ? t('r.quote.wrap', {text: q.text}) : q.text) : "";
  const total = totalPages(), n = total - (ps.length - 1 - pageAt);
  el("pg").textContent = blank && total <= 1 ? "0 / 0" : n + " / " + total;
  el("rmTime").textContent = p.ts ? "· " + hhmm(p.ts) : "";
  el("rmPart").textContent = p.part ? t('r.part', {k: p.part[0], n: p.part[1]}) : "";
  el("pgPrev").disabled = pageAt === 0 && !(hist.turns.length && hist.turns[0].id > hist.firstId);
  el("pgNext").disabled = pageAt >= ps.length - 1;
  el("localNote").hidden = !(src && src.local);
  paintCmdx(p);
  if (key !== shownKey){
    const moved = shownKey !== null && shownKey.split("|")[0] !== key.split("|")[0];
    shownKey = key;
    setOm(false);
    if (moved) stick = true;
  }
  renderWords(p.reply || (p.end === "open" || p.id === null ? "" : t('r.noReply')), p.end === "open" && p.id !== null);
  renderMedia(el("words"), p);
  if (rdAt && rdAt.key === shownKey) renderReaderMedia(el("rdWords"), p);   // P59: the reader's slots, same Blobs
  document.body.dataset.pageOpen = p.end === "open" ? "1" : "0";
  paintActs();
  if (!el("silent").hidden) renderSilent();
}
// A command result page (§8 cmd card → turn.card): the model buttons and 「撤销清空」 sit under the words.
function paintCmdx(p){
  const box = el("cmdx"), c = p.card;
  box.replaceChildren();
  const isCmd = !!c && p.source && p.source.k === "cmd";
  const openBtn = isCmd && typeof c.open === "string" && /^fr_(card|add)(:[A-Z0-9-]{1,40})?$/.test(c.open);   // P73
  box.hidden = !isCmd || !((Array.isArray(c.models) && c.models.length) || c.undo === true || openBtn);
  if (box.hidden) return;
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
      b.addEventListener("click", () => { for (const o of box.querySelectorAll("button")) o.disabled = true; C.api.slash("model", x.id); });
      box.appendChild(b);
    }
  }
  if (c.undo === true && p.id === newestUndoId()){
    const u = document.createElement("button"); u.type = "button"; u.className = "cmdundo"; u.textContent = t('cmd.undo');
    u.addEventListener("click", () => { u.disabled = true; C.api.slash("undo_clear"); });
    box.appendChild(u);
  }
}
const newestUndoId = () => { for (let i = hist.turns.length - 1; i >= 0; i--){ const x = hist.turns[i]; if (x.card && (x.card.cmd === "clear" || x.card.cmd === "undo_clear")) return x.card.undo ? x.id : -1; } return -1; };
function setOm(open){
  const om = el("om");
  om.classList.toggle("open", open);
  el("deck").classList.toggle("open", open);
  el("omMore").setAttribute("aria-expanded", open ? "true" : "false");
  el("omMore").setAttribute("aria-label", open ? t('r.om.collapse') : t('r.om.expand'));
  el("omMore").firstElementChild.textContent = open ? t('r.om.collapseShort') : "…";
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
  pageAt = at; follow = at === ps.length - 1;
  showPage();
  return true;
}
async function goEdge(newest){
  if (newest){ follow = true; showPage(); return; }
  follow = false; pageAt = 0;
  while (await loadOlder()) {}
  pageAt = 0; showPage();
}

// Copy: y the reply, Y the input. What is copied is the stored text, not the rendering.
function copyOut(text, doneMsg){
  if (!text){ toast(t('r.copy.nothing'), 1600); return; }
  RelayMD.copyText(text).then(ok => toast(ok ? doneMsg : t('r.copy.fail'), ok ? 1400 : 2200));
}
function copyReply(){ copyOut(currentPage().reply || "", t('r.copy.reply')); }
function copySource(){ const p = currentPage(); copyOut(p.source && p.source.text || "", t('r.copy.source')); }

// ---- reply actions: 阅读 · 朗读 · 分享 · 复制 · 回复 (relay ADR-037) ---------------------
// Each acts on a turn by its id — never a page number.
let replyTo = null;          // {id, kind, speaker, ts, line[, excerpt, copied]} while the strip is up
function paintActs(){
  const p = currentPage(), id = p.id;
  el("readBtn").disabled = !(p.reply || "").trim();
  el("replyBtn").disabled = id === null || !C.connected();
  el("replyBtn").classList.toggle("on", !!replyTo && replyTo.id === id);
  el("copyReply").disabled = !(p.reply || "").trim();
  paintSpeak(); paintFwd();
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
  replyTo = {id: p.id, kind: sourceOf(p.source).kind, speaker: C.agentName(), ts: p.ts, line: plainLine(p.reply)};
  paintReply(); paintActs();
  input.focus();
  input.setSelectionRange(input.value.length, input.value.length);
}
function paintReply(){
  const q = replyTo;
  el("qbar").hidden = !q;
  if (!q) return;
  el("qbar").style.setProperty("--qc", "var(--src-" + q.kind + ")");
  const tm = hhmm(q.ts), head = el("qbHead"), x = typeof q.excerpt === "string";
  head.replaceChildren(t(x ? 'r.quote.headEx' : 'r.quote.head', {who: q.speaker}));
  const meta = (tm ? " · " + tm : "") + (x && q.copied ? " · " + t('r.quote.copied') : "");
  if (meta){ const s = document.createElement("span"); s.textContent = meta; head.appendChild(s); }
  el("qbText").textContent = x ? excerptLine(q.excerpt) : (q.line || t('r.quote.noText'));
}
function cancelReply(){ if (!replyTo) return; replyTo = null; paintReply(); paintActs(); }
async function jumpToTurn(id){
  if (typeof id !== "number" || !(id > 0)) return;
  while (!hist.turns.some(p => p.id === id) && olderLeft() && hist.turns[0].id > id){
    const b = await loadOlderBatch(); if (!b || !b.all) break;
  }
  if (hist.turns.some(p => p.id === id && isSilent(p.reply))){ openSilent(id); return; }
  const i = pages().findIndex(p => p.id === id);
  if (i < 0){ toast(t('r.quote.gone'), 2200); return; }
  pageAt = i; follow = i === pages().length - 1; showPage();
}

// ---- select to quote (relay ADR-046; §10.6 excerpt) ---------------------------------------
const SEL_IDLE_MS = 450, EXCERPT_MAX = 2000;
let selPtr = false, selTimer = 0, selLast = null, selPending = null;
function wordsSelection(){
  const s = document.getSelection ? document.getSelection() : null;
  if (!s || !s.rangeCount || s.isCollapsed) return null;
  const w = el("words");
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
  if (replyTo && replyTo.excerpt === text && !replyTo.copied){ replyTo.copied = true; paintReply(); }
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
  if (id !== null && replyTo && replyTo.id === id && replyTo.excerpt === text) return;
  selLast = {id, text};
  if (id !== null){
    replyTo = {id, kind: sourceOf(p.source).kind, speaker: C.agentName(), ts: p.ts, line: plainLine(p.reply), excerpt: text, copied: false};
    paintReply(); paintActs();
  }
  await selCopy(text);
  if (id !== null && replyTo && replyTo.excerpt === text && selFocus(type)){
    input.focus({preventScroll: true});
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

// ---- 分享: the phone's share sheet, else copy (§10.14; relay forwarded to its owner's own bot) -----------
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
  b.setAttribute("aria-label", t('r.share.' + st)); b.title = t('r.share.' + st);
}
async function forwardTap(){
  const p = currentPage(), id = p.id;
  if (id === null) return;
  if (fwdSt.get(id) === "sending") return;
  const text = (p.reply || "").trim();
  if (!text) return;
  setFwd(id, "sending");
  if (navigator.share){
    try{ await navigator.share({text}); setFwd(id, "sent", 2000); return; }
    catch(e){ if (e && e.name === "AbortError"){ setFwd(id, "idle"); return; } }
  }
  const ok = await RelayMD.copyText(text);
  setFwd(id, ok ? "sent" : "failed", ok ? 2000 : 3000);
  toast(ok ? t('r.share.copied') : t('r.copy.fail'), 2200);
}

// ---- the full-screen reader (relay ADR-049) ----------------------------------------------
var RD_SIZES = [14, 16, 18, 20, 24, 28, 32], RD_MIN = 14, RD_MAX = 32, RD_DEFAULT = 20, RD_KEY = "aj.readerFs";
var RD_DTAP_MS = 300, RD_TAP_MS = 350, RD_TAP_PX = 10, RD_PAIR_PX = 32;
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
  fill(el("rdWords"), tx);
  renderReaderMedia(el("rdWords"), p);                 // P59 (ADR-A164): local pictures / files in place, not their Markdown
  el("rdWho").textContent = C.agentName() + (p.ts ? " · " + hhmm(p.ts) : "");
  if (document.activeElement === input) input.blur();
  const rd = el("rd");
  rd.hidden = false; rd.dataset.modalOpen = "1"; document.body.dataset.reader = "1";
  rdSetFs(rdFs, false);
  const sc = el("rdScroll"), m2 = sc.scrollHeight - sc.clientHeight;
  sc.scrollTop = max > 0 && m2 > 0 ? mainEl.scrollTop / max * m2 : 0;
  try{ history.pushState({ajReader: 1}, ""); rdPushed = true; }catch(_){ rdPushed = false; }
}
function closeReader(fromPop){
  if (!rdOpen()) return;
  rdGuard = performance.now(); rdPinch = null;
  const rd = el("rd");
  rd.hidden = true; delete rd.dataset.modalOpen; delete document.body.dataset.reader;
  el("rdWords").replaceChildren();
  clearReaderMedia();
  if (rdAt && rdAt.key === shownKey){ mainEl.scrollTop = rdAt.scroll; stick = atBottom(); paintMore(); }
  rdAt = null;
  if (!fromPop && rdPushed && history.state && history.state.ajReader){ rdSkipPop = true; history.back(); }
  rdPushed = false;
}
function rdFollow(tx){
  if (!rdAt || rdAt.key !== shownKey || !tx || tx === rdAt.text) return;
  const sc = el("rdScroll"), top = sc.scrollTop;
  rdAt.text = tx; fill(el("rdWords"), tx); sc.scrollTop = top;
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
const rdTarget = tg => !(tg && tg.closest && tg.closest("button, a, input, [role=link], .rmbar, .mcard"));
const rdDist = ts => Math.hypot(ts[0].clientX - ts[1].clientX, ts[0].clientY - ts[1].clientY);

// ---- swipe to page: within 30° of horizontal = a page turn; past 25 % or a fling commits ----------
const SWIPE_DEG = 30, SWIPE_FRAC = 0.25, SWIPE_FLING = 0.5, SWIPE_EDGE = 16, SWIPE_MIN_MOVE = 4;
let sw = null;
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
/** on: a ready session with the host up. text: what the screen reader says while off. */
export function setConn(on, text){
  if (!C) return;
  const was = document.body.dataset.conn === "on";
  document.body.dataset.conn = on ? "on" : "off";
  if (!on){
    el("capText").textContent = text || "";
    el("capSub").textContent = "";
    const stale = el("stale");
    if (lastSync){
      stale.textContent = t('r.stale', {hm: lastSync.toTimeString().slice(0, 5)});
      stale.classList.add("has");
    }
  }
  if (on && !was) uploadsResume();
  if (on) drainOut(); else outbox.lost();          // 0.15.2: the offline queue goes out on ready, stops on a drop
  paintOut();
  wordsSrc = null; showPage(); paintChrome(); refreshSend();
}

// ---- drafts (§10.14: sealed in IndexedDB, 24 h, deleted on unpair / re-pair) -------------------------
const DRAFT_TTL_MS = 24 * 3600 * 1000;
let draftTimer = 0;
export async function saveDraft(){
  clearTimeout(draftTimer);
  const tx = input ? input.value : "";
  try{
    if (!tx.trim()) await dbDel("draft");
    else await putSealed("draft", {t: tx, at: Date.now()});
  }catch(_){}
}
function draftSoon(){ clearTimeout(draftTimer); draftTimer = setTimeout(saveDraft, 600); }
async function restoreDraft(){
  const d = await getSealed("draft");
  if (!d || typeof d.t !== "string" || Date.now() - (+d.at || 0) > DRAFT_TTL_MS || input.value) return;
  input.value = d.t.slice(0, MAX_TEXT());
  input.dispatchEvent(new Event("input"));
  toast(t('r.draft.restored'), 2600);
}

// ---- keyboard / visual viewport ---------------------------------------------------------
function fitViewport(){
  const vv = window.visualViewport; if (!vv) return;
  const st = document.documentElement.style;
  st.setProperty("--vvh", vv.height + "px");
  st.setProperty("--vvtop", vv.offsetTop + "px");
  document.body.dataset.kb = (window.innerHeight - vv.height > 120) ? "1" : "0";
  paintChrome();
}

// ---- composer --------------------------------------------------------------------------
let sending = false, asrBusy = 0;
// F27 (0.16): words OR at least one attachment (a voice note counts) is a message — the keyboard need not open just to type
// a full stop. A failed file alone is not something to send; files still uploading are waited for by say(), never skipped.
function hasSayable(){ return !!input.value.trim() || atts.some(a => a.st !== "failed"); }
function refreshSend(){
  el("send").disabled = sending || asrBusy > 0 || !hasSayable() || !(C.connected() || outbox.host !== null);   // offline: it queues (null = not paired)
  const c = el("clr"); if (c) c.hidden = !input.value;
  noteEmpty();
}
function fieldMaxH(){ return innerHeight * 0.3; }
function autosize(){
  input.style.height = "auto";
  // scrollHeight is 0 while the chat view is hidden (a sub-view is open): a fixed "0px" would leave the field squashed to
  // its padding when the view comes back, so stay "auto" — the visibility observer below measures again once it shows.
  if (input.value && input.scrollHeight) input.style.height = Math.min(input.scrollHeight, fieldMaxH()) + "px";
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
function restingPlaceholder(){
  if (C && C.estopOn()) return t('chat.placeholderStopped');
  const L = t('r.ph.long', {cap: capWords()}), S = t('r.ph.short', {cap: capWords()});
  if (input.clientWidth < 80) return S;
  twin.style.fontSize = getComputedStyle(input, "::placeholder").fontSize;
  try{
    const oneLine = twinHeight("\u4e2d");
    return oneLine > 0 && twinHeight(L + "\u4e2d") <= oneLine ? L : S;
  } finally { twin.style.fontSize = ""; }
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
  noteEmpty();
  if (C) refreshSend();                                             // F27: a file alone can make Send live
}
function stage(a){
  a.st = "up"; a.pct = 0; a.err = "";
  if (!a.up) a.up = new Upload(a.file, {purpose: "att", origin: a.origin, name: a.name, mime: a.file.type, secs: a.dur, onProgress: p => { a.pct = p; paintChip(a); }});
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
function addFiles(list, origin = "file", dur){
  let dropped = 0;
  const added = [];
  for (const f of list){
    if (!f.size){ toast(notReady(f), 3600); continue; }
    if (f.size > UPLOAD_MAX){ toast(uploadError("too_big"), 3000); continue; }
    if (atts.length >= MAX_ATT){ dropped++; continue; }
    const a = {file: f, name: f.name || "upload", kind: kindOf(f), origin};
    if (dur > 0) a.dur = dur;
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
function droppable(f){
  const ty = (f.type || "").split(";")[0].trim().toLowerCase();
  if (DROP_TYPES[ty]) return ty === f.type ? f : new File([f], f.name, {type: ty, lastModified: f.lastModified});
  const m = /\.[^.]+$/.exec((f.name || "").toLowerCase()), mime = m && DROP_EXT[m[0]];
  if (!mime) return null;
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
function takeFiles(files, verbKey, origin){
  if (!files.length) return;
  if (atts.length + files.length > MAX_ATT){
    toast(t('r.take.tooMany', {max: MAX_ATT, have: atts.length, verb: t(verbKey), n: files.length}), 3600);
    return;
  }
  const ok = files.map(droppable).filter(Boolean), skipped = files.length - ok.length;
  if (!ok.length){ toast(t('r.take.none'), 3000); return; }
  addFiles(ok, origin);
  if (skipped) toast(t('r.take.skipped', {n: skipped}), 3200);
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
const SAY_ERR = ["shape", "too_long", "stopped", "att_gone", "att_open", "too_many_att", "reply_unknown", "dup", "too_many", "no_agent", "offline", "timeout"];
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
// The launch sound: only the landing rocket makes it; quiet (0.35), never stacks, never touches the send. Played from a
// same-origin <audio> (CSP media-src 'self'); relay's AudioContext path needed an HTTP fetch, which CSP forbids here.
const WHOOSH_VOLUME = 0.35;
let whooshEl = null;
function unlockWhoosh(){
  try{
    if (whooshEl) return;
    whooshEl = new Audio("assets/rocket-whoosh.mp3");
    whooshEl.preload = "auto"; whooshEl.volume = WHOOSH_VOLUME;
  }catch(_){ whooshEl = null; }
}
function playWhoosh(){
  try{
    if (!whooshEl || document.visibilityState !== "visible") return;
    whooshEl.currentTime = 0;
    const p = whooshEl.play(); if (p && p.catch) p.catch(() => {});
    document.body.dataset.whoosh = String((+document.body.dataset.whoosh || 0) + 1);
  }catch(_){}
}
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
    el("sayCancel").hidden = false; document.body.dataset.sending = "1";
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
const queued = [];        // {sid, text, atts, rt} accepted with state "queued", not yet delivered
function paintQueued(){
  const show = !!inflight && document.body.dataset.sending === "1" || (!inflight && queued.length > 0);
  el("sayCancel").hidden = !show;
  el("sayCancel").dataset.mode = inflight ? "send" : "queued";
}
async function cancelSay(){
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
  dequeue(q.sid);
  if (!input.value){ input.value = q.text; input.dispatchEvent(new Event("input")); }
  for (const a of q.atts) if (atts.length < MAX_ATT && !atts.includes(a)){ a.node = null; a.st = "ready"; atts.push(a); paintChip(a); }
  paintTray();
  toast(t('r.say.withdrawn'), 2600);
}
/** say_state from the host. */
export function onSayState(m){
  const q = queued.find(x => x.sid === m.sid);
  if (!q) return;
  if (m.s === "delivered") dequeue(m.sid);
  else if (m.s === "cancelled") withdrawn(q);
  else if (m.s === "failed"){ dequeue(m.sid); toast(t('r.say.failedLater'), 3200); }
}
// The whitelist commands (§8) run as commands when typed; /clear asks first. Anything else starting with / is text.
const CMDS = ["clear", "compact", "model", "context", "cost", "usage", "status", "help", "stop", "update"];
const CMD_RE = /^\/([A-Za-z][\w:.-]{0,63})(?:[ \t]+([\s\S]*))?$/;
async function runCmd(cmd, arg = ""){
  if (cmd === "clear"){
    if (!await confirmSheet(t('cmd.clearTitle'), t('cmd.clearText'), t('cmd.clearYes'))) return false;
    return C.api.slash("clear", "", true);
  }
  return C.api.slash(cmd, arg);
}
async function say(){
  const text = input.value;
  if (!hasSayable() || sending || asrBusy) return;                 // F27: files alone (no words) go too
  // P73 (ADR-A176): /add-friend is the friends page's signed fr_add, sent from here (the approval key never leaves the page);
  // it works while stopped (a request is not an Agent action) and opens the prefilled form when offline.
  const fa = /^[/\uff0f]add[-_]friend(?:[ \t]+([\s\S]*))?$/i.exec(text.trim());
  if (fa && !atts.length && !replyTo){
    if (await addFriendCmd((fa[1] || "").trim())){ pushHist(text); input.value = ""; autosize(); saveDraft(); }
    return;
  }
  if (C.estopOn()){ toast(t('r.say.stopped'), 3200); return; }
  // 0.15.2: not connected, or older messages still waiting → it queues behind them (/stop, /clear never wait)
  if (!C.connected() || (outbox.items.length && !/^\/(stop|clear)(\s|$)/i.test(text.trim()))) return sayLater(text);
  const c = CMD_RE.exec(text.trim());
  if (c && CMDS.includes(c[1].toLowerCase()) && !atts.length && !replyTo){
    if (!await runCmd(c[1].toLowerCase(), (c[2] || "").trim())) return;
    pushHist(text); input.value = ""; autosize(); saveDraft(); toNewest(); launchRocket();
    return;
  }
  sending = true; refreshSend();
  toNewest();
  const batch = atts.slice();
  const S = inflight = {phase: "upload", batch, sid: null, cancelled: false, delivered: false, cancelling: false, wake: null};
  const loop = launchLoop();
  const rt = replyTo;
  const uploaded = list => { S.phase = "upload"; return Promise.race([Promise.all(list.map(a => a.promise)), new Promise(res => { S.wake = res; })]); };
  const attempt = live => { S.phase = "say"; S.sid = S.sid || C.api.newSid(); return C.api.say({sid: S.sid, text, att: live.map(a => a.id), reply_to: rt ? rt.id : null, excerpt: rt ? rt.excerpt : null}); };
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
      if (rt && replyTo === rt) cancelReply();
      if (input.value === text) input.value = "";
      if (r.state === "queued") queued.push({sid: S.sid, text, atts: live.slice(), rt});
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
  paintOutbox(el("outbox"), outbox.items, {line: t('offline.line'), lostNote: t('offline.attLost'),
    isLost: e => !!(e.att && e.att.length && !held.has(e.sid)), showLine: !C.connected() && (outbox.items.length > 0 || navigator.onLine === false)});
}
function drainOut(){ if (outbox.items.length) outbox.drain(); }
function sayLater(text){
  const c = CMD_RE.exec(text.trim());
  const cmd = c && CMDS.includes(c[1].toLowerCase()) && !atts.length && !replyTo ? c[1].toLowerCase() : null;
  // 全部停下 / 清空 are "now or not at all": a stop that lands minutes later would cut whatever runs then
  if (cmd === "stop" || cmd === "clear"){ toast(t('offline.now'), 2800); return; }
  const bad = atts.filter(a => a.st === "failed");
  if (bad.length){ toast(t('r.say.badAtt', {n: bad.length}), 3200); return; }
  const list = atts.slice(), rt = replyTo;
  const e = {sid: C.api.newSid(), text, ts: Date.now(), rt: rt ? {id: rt.id, excerpt: rt.excerpt || null} : null,
    att: list.length ? list.map(a => ({name: a.name, kind: a.kind})) : null};
  if (list.length) held.set(e.sid, list);           // before add(): its line is painted with the files still here
  if (outbox.add(e) === "full"){ held.delete(e.sid); toast(t('offline.full', {n: OUT_MAX}), 3200); return; }
  if (list.length){
    for (const a of list){ a.out = true; atts.splice(atts.indexOf(a), 1); if (a.node) a.node.remove(); }
    paintTray();
  }
  pushHist(text);
  if (rt && replyTo === rt) cancelReply();
  if (input.value === text) input.value = "";
  autosize(); saveDraft(); toNewest();
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
    const r = await C.api.say({sid: e.sid, text: e.text, att: live.map(a => a.id), reply_to: e.rt ? e.rt.id : null, excerpt: e.rt ? e.rt.excerpt : null});
    if (r.ok || r.why !== "att_gone" || n) return r;
    const gone = new Set(Array.isArray(r.att) ? r.att : live.map(a => a.id));
    for (const a of live) if (gone.has(a.id)){ a.up = null; a.st = "up"; }
  }
}
function outDone(e, r, kind){
  const list = held.get(e.sid) || [];
  held.delete(e.sid);
  if (kind === "sent"){
    if (r.ok && r.state === "queued") queued.push({sid: e.sid, text: e.text, atts: list.slice(), rt: null});
    for (const a of list) if (a.url){ URL.revokeObjectURL(a.url); a.url = null; }
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
const RUN = ["compact", "clear", "model", "context", "cost", "usage", "status", "stop", "update"];
const runDescription = c => t('r.run.' + c);
let hostMenu = null;          // {items:[[cmd, desc, group]], skills:[[cmd, desc]]}
let menuItems = [];           // what the type-ahead filters: [cmd, desc, group]
function insertSlash(cmd){
  const v = input.value;
  input.value = (v && !v.endsWith(" ") ? v + " " : v) + cmd + " ";
  input.focus();
  input.setSelectionRange(input.value.length, input.value.length);
  autosize();
}
function menuFromHost(j){
  if (!j || !Array.isArray(j.items)) return null;
  const items = [], skills = [], seen = new Set();
  for (const it of j.items.slice(0, MENU_LIMITS.items)){
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
  return {items, skills, upgrade: j.upgrade || {}, source: j.source === "file" ? "file" : "default"};
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
    b.append(n, d);
    nodes.push(b);
    return b;
  };
  group(t('r.menu.run'));
  for (const c of RUN) {
    if (c !== 'update') { item("/" + c, runDescription(c), {run: c}); continue; }
    const u = hostMenu?.upgrade || {}, current = u.current || '–', latest = u.latest || '–';
    const same = u.latest && u.latest === u.current;
    const desc = t(same ? 'r.update.current' : 'r.update.authorize');
    const b = item(t('r.update.title'), `${current} → ${latest} · ${desc}`, {run: c});
    b.disabled = !!same;
  }
  const stop = item("■", C.estopOn() ? t('estop.resume') : t('estop.btn'), {estop: C.estopOn() ? "resume" : "stop"});
  stop.classList.add("estopitem");
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
  menuItems = RUN.map(c => ["/" + c, runDescription(c), ""]).concat(fromHost, host.skills);
  const k = item("?", t('r.keys.title'), {});
  k.className = "keysentry"; k.id = "keysEntry";
  if (CLIP_READ){ const c = item("📋", t('r.menu.clip'), {}); c.className = "clipentry"; c.id = "clipEntry"; }
  const modelsEntry = item('◉', t('models.title'), {}); modelsEntry.id = 'modelsEntry';
  if (IN_APP){ const a = item("⚙", t('r.menu.app'), {}); a.id = "appEntry"; }
  const sl = item("🔕", t("r.silent.menu"), {}); sl.id = "silentEntry";
  menu.replaceChildren(...nodes);
}
async function loadMenu(){
  const j = await C.api.menu();
  if (!j) return;
  hostMenu = menuFromHost(j);
  renderMenu();
}
function closeMenu(){ menu.hidden = true; }
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
  sug.replaceChildren(...list.map(([cmd, why], i) => {
    const b = document.createElement("button");
    b.type = "button"; b.tabIndex = -1; b.id = "sug" + i; b.dataset.sug = String(i);
    b.setAttribute("role", "option");
    const n = document.createElement("b");
    if (q && cmd.slice(1).toLowerCase().startsWith(q)){
      const mk = document.createElement("mark"); mk.textContent = cmd.slice(0, q.length + 1);
      n.append(mk, document.createTextNode(cmd.slice(q.length + 1)));
    } else n.textContent = cmd;
    const d = document.createElement("span"); d.textContent = why;
    b.append(n, d);
    return b;
  }));
  closeMenu();
  sug.hidden = false;
  paintSug();
}
function pickSug(i){
  const it = sugList[i]; if (!it) return;
  input.value = it[0] + " ";
  closeSug();
  input.focus();
  input.setSelectionRange(input.value.length, input.value.length);
  input.dispatchEvent(new Event("input"));
}

// ---- voice: hold to talk (relay ADR-048 / 052; §10.9) ------------------------------------------------
// Hold = record, slide up past PTT_CANCEL_PX = cancel, double tap = lock (≤ 10 min), m / m m on a keyboard. A clean
// release never sends: the take is turned into 16 kHz WAV here, transcribed on the customer's own computer, and the words
// are APPENDED to the field. A take longer than the field's threshold travels as an audio attachment instead.
const PTT_MIN_MS = 500, PTT_CANCEL_PX = 64;
const DTAP_MS = 300, LOCK_MAX_MS = 10 * 60 * 1000, LOCK_WARN_MS = 60 * 1000, LOCK_REARM_MS = 400;
let ptt = null;
let lockEndAt = -1e9;
function pttUI(p){
  if (p) speaker?.stop();
  const b = el("mic"), box = el("ptt");
  b.classList.toggle("rec", !!p && !p.cancel);
  b.classList.toggle("cancel", !!p && p.cancel);
  b.classList.toggle("lock", !!p && p.locked);
  if (!p){ box.hidden = true; box.classList.remove("lock"); el("micLabel").textContent = asrBusy ? t('r.asr.tag') : t('r.mic.hold'); return; }
  box.hidden = false;
  box.classList.toggle("cancel", p.cancel);
  box.classList.toggle("lock", p.locked);
  el("pttTime").dataset.lock = t('r.ptt.locked');
  const s = p.rec ? Math.floor((performance.now() - p.recT0) / 1000) : 0;
  el("pttTime").textContent = p.rec ? Math.floor(s / 60) + ":" + String(s % 60).padStart(2, "0") : "…";
  if (p.rec && p.T == null) p.T = voiceThresholdS();
  const long = p.rec && (performance.now() - p.recT0) / 1000 > p.T;
  if (p.locked){
    const left = LOCK_MAX_MS - (performance.now() - p.lockT0);
    const stop = p.viaM ? t('r.ptt.anyKey') : t('r.ptt.tapEnd');
    el("pttHint").textContent = p.cancel ? t('r.ptt.releaseCancel')
      : left <= LOCK_WARN_MS ? t('r.ptt.left', {s: Math.max(0, Math.ceil(left / 1000)), stop})
      : !p.rec ? t('r.ptt.opening') : p.viaM ? t('r.ptt.anyKeyEsc')
      : matchMedia("(pointer:coarse)").matches ? t('r.ptt.tapEndSlide') : t('r.ptt.tapEndEsc');
    el("micLabel").textContent = p.cancel ? t('r.mic.releaseCancel') : stop;
    return;
  }
  el("pttHint").textContent = p.cancel ? t('r.ptt.releaseCancel') : p.pend ? (p.viaM ? t('r.ptt.lockM') : t('r.ptt.lockTap'))
    : p.rec ? (long ? t('r.ptt.longHint') : t('r.ptt.hint')) : t('r.ptt.opening');
  el("micLabel").textContent = p.cancel ? t('r.mic.releaseCancel') : long ? t('r.mic.releaseVoice') : t('r.mic.releaseText');
}
function pttTap(p){
  p.id = null; p.tUp = performance.now();
  p.pend = setTimeout(() => { p.pend = 0; pttEnd(p, "release"); }, DTAP_MS);
  pttUI(p);
}
function pttLock(p){
  clearTimeout(p.pend); p.pend = 0; p.tUp = 0;
  p.locked = true; p.id = null; p.cancel = false; p.lockT0 = performance.now();
  p.cap = setTimeout(() => {
    if (ptt !== p) return;
    toast(t('r.ptt.capped', {m: LOCK_MAX_MS / 60000}), 3200);
    pttEnd(p, "release", true);
  }, LOCK_MAX_MS);
  try{ if (navigator.vibrate) navigator.vibrate([15, 60, 15]); }catch(_){}
  pttUI(p);
}
const MIC_OPEN_MAX_MS = 15000;
function pttStart(id, y){
  if (ptt) return;
  if (C.asr() !== "ready"){ C.asrInstall?.(); return; }
  if (!navigator.mediaDevices || !navigator.mediaDevices.getUserMedia || !window.MediaRecorder){
    toast(t('r.ptt.noRec'), 3000); return;
  }
  const p = ptt = {id, y0: y, t0: performance.now(), cancel: false, over: false,
                   send: false, stream: null, rec: null, recT0: 0, recT1: 0, chunks: [],
                   pend: 0, tUp: 0, locked: false, lockT0: 0, cap: 0, viaM: false};
  p.timer = setInterval(() => pttUI(p), 250);
  pttUI(p);
  // Native permission/device acquisition may never settle. Do not leave a
  // locked take waiting forever, and do not retain a stream arriving after cancel.
  p.openTimer = setTimeout(() => {
    if (p.over || p.rec) return;
    window.dispatchEvent(new CustomEvent('agentj-audio-error', {detail: {stage: 'capture', error: 'TimeoutError'}}));
    pttEnd(p, "cancel", true);
    toast(t('r.ptt.noMic'), 3000);
  }, MIC_OPEN_MAX_MS);
  navigator.mediaDevices.getUserMedia({audio: true}).then(stream => {
    clearTimeout(p.openTimer);
    if (p.over){
      stream.getTracks().forEach(tr => tr.stop());
      return;
    }
    p.stream = stream;
    let rec;
    try{ rec = new MediaRecorder(stream); }
    catch(_){ toast(t('r.ptt.noRec'), 3000); pttEnd(p, "cancel", true); return; }
    p.rec = rec;
    rec.ondataavailable = e => { if (e.data && e.data.size) p.chunks.push(e.data); };
    rec.onstop = () => pttFinish(p);
    p.recT0 = performance.now();
    rec.start();
    try{ if (navigator.vibrate) navigator.vibrate(20); }catch(_){}
    pttUI(p);
  }, () => {
    clearTimeout(p.openTimer);
    if (p.over) return;
    pttEnd(p, "cancel", true);
    toast(t('r.ptt.noMic'), 3000);
  });
}
function pttEnd(p, reason, quiet){
  if (!p || p.over) return;
  p.over = true;
  clearInterval(p.timer); clearTimeout(p.pend); clearTimeout(p.cap); clearTimeout(p.openTimer);
  if (ptt === p) ptt = null;
  if (p.locked) lockEndAt = performance.now();
  const held = (p.tUp || performance.now()) - p.t0;
  p.recT1 = p.tUp || performance.now();
  p.send = reason === "release" && !p.cancel && !!p.rec && (p.locked || held >= PTT_MIN_MS);
  if (!quiet){
    if (reason === "release" && !p.cancel && !p.locked && held < PTT_MIN_MS) toast(t('r.ptt.short'), 1600);
    else if (!p.send && p.rec) toast(t('r.ptt.cancelled'), 1400);
  }
  if (p.rec && p.rec.state !== "inactive") p.rec.stop();
  else if (p.stream) p.stream.getTracks().forEach(tr => tr.stop());
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
async function pttFinish(p){
  if (p.stream) p.stream.getTracks().forEach(tr => tr.stop());
  if (!p.send || !p.chunks.length) return;
  const blob = new Blob(p.chunks, {type: (p.rec.mimeType || "audio/webm").split(";")[0]});
  takeFinished(blob, (p.recT1 - p.recT0) / 1000);
}
// A finished take, from the mic or from a native shell (relayNative.take): words into the field, or a long one into the tray.
let asrQueue = Promise.resolve();
async function takeFinished(blob, dur){
  if (dur > voiceThresholdS()){
    if (atts.length < MAX_ATT){
      const w = blob.type === "audio/wav" ? {blob, secs: dur} : await toWav(blob);
      const b = w ? w.blob : blob;
      const f = new File([b], `${t('r.file.voice')}-${stamp()}.${audioExt(b.type)}`, {type: b.type || "audio/webm"});
      if (f.size > UPLOAD_MAX){ toast(uploadError("too_big"), 3000); return; }
      const [a] = addFiles([f], "recording", dur);
      if (a){ a.label = t('r.tray.voice', {d: mmss(dur)}); paintChip(a); }
      toast(C.asr() === "ready" ? t('r.voice.asAtt') : t('r.voice.asAttNoAsr'), 3200);
      return;
    }
    toast(t('r.voice.trayFull', {max: MAX_ATT}), 3200);
  }
  if (C.asr() !== "ready"){ C.asrInstall?.(); return; }
  asrBusy++; asrUI();
  const ep = localEpoch;
  const job = transcribe(blob, dur);
  asrQueue = asrQueue.then(() => job).then(r => {
    asrBusy--;
    if (ep !== localEpoch){ asrUI(); return; }           // unpaired / removed meanwhile: the words belong to no one now
    const tx = r.ok && typeof r.text === "string" ? r.text.trim() : "";
    if (tx) appendText(tx);
    else toast(asrError(r), 3400);
    asrUI(); autosize();
  });
}
async function transcribe(blob, secs){
  const w = blob.type === "audio/wav" ? {blob, secs} : await toWav(blob);
  if (!w) return {ok: false, why: "bad_audio"};
  if (w.blob.size > ASR_MAX_BYTES) return {ok: false, why: "too_long"};
  return C.api.transcribe(w.blob, w.secs);
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
  try{ input.focus({preventScroll: true}); }catch(_){}
  input.setSelectionRange(input.value.length, input.value.length);
  input.scrollTop = input.scrollHeight;
}
function asrUI(){
  const on = asrBusy > 0;
  el("asrTag").hidden = !on;
  input.placeholder = on ? t('r.asr.tag') : restingPlaceholder();
  el("mic").classList.toggle("asr", on);
  if (!ptt) el("micLabel").textContent = on ? t('r.asr.tag') : t('r.mic.hold');
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
    if (i >= 0){ pageAt = i; follow = i === ps.length - 1; showPage(); return true; }
    if (id > hist.lastId){ syncHistory(id); await new Promise(r => setTimeout(r, 250)); continue; }
    if (!(await loadOlder())) break;
  }
  return false;
}

// ---- keyboard shortcuts (relay ADR-032 / 052) -------------------------------------------------------
const ESC2_MS = 500, EMPTY_GUARD_MS = 1000;
var escAt = 0, escArmedInterrupt = false, emptySince = null, mKey = false, mCode = "";
const isM = e => !e.shiftKey && (e.key === "m" || e.key === "M" || (e.code === "KeyM" && e.key.length !== 1));
function composerEmpty(){ return !input.value && !atts.length; }
function noteEmpty(){
  if (!input) return;
  if (!composerEmpty()) emptySince = null;
  else if (emptySince === null || emptySince === undefined) emptySince = performance.now();
}
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
    d.append(sm, full, rp);
    return d;
  }));
  if (typeof focusId === "number"){
    const d = box.querySelector(`details[data-id="${focusId}"]`);
    if (d) d.scrollIntoView({block: "nearest"});
  }
}
function openSilent(id){
  closeMenu(); closeSug();
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

const dialogOpen = () => !el("silent").hidden || !el("cam").hidden || !el("keys").hidden || !el("confirm").hidden || !el("badge-panel").hidden || !el("settings").hidden;
function openKeys(){ closeMenu(); closeSug(); el("keys").hidden = false; el("keysClose").focus(); }
function closeKeys(){ el("keys").hidden = true; if (document.activeElement) document.activeElement.blur(); }
async function pasteClipImages(){
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
  takeFiles(files.map((f, i) => pastedName(f, i, files.length)), 'r.verb2.paste', "paste");
}
function clearComposer(){
  if (sending){ toast(t('r.clear.sending'), 1800); return; }
  input.value = "";
  input.dispatchEvent(new Event("input"));
  for (const a of atts.slice()) removeAtt(a);
  emptySince = performance.now();
  toast(t('r.clear.done'), 1400);
}
async function interruptAgent(){
  if (!C.connected()){ toast(t('r.int.offline'), 2200); return; }
  if (lastStatus === "idle"){ toast(t('r.int.idle'), 1800); return; }
  if (lastStatus === "waiting"){ toast(t('r.int.waiting'), 2400); return; }
  const ok = await C.api.slash("stop");
  toast(ok ? t('r.int.sent') : t('r.int.fail'), 2200);
}
// readline editing in the composer
var killBuf = "";
function lineBounds(v, at){
  const s0 = v.lastIndexOf("\n", at - 1) + 1;
  let e0 = v.indexOf("\n", at); if (e0 < 0) e0 = v.length;
  return [s0, e0];
}
function edit(a, b, ins){
  const v = input.value, want = v.slice(0, a) + ins + v.slice(b);
  input.setSelectionRange(a, b);
  try{ document.execCommand(ins ? "insertText" : "delete", false, ins); }catch(_){}
  if (input.value !== want){
    input.value = want;
    input.dispatchEvent(new Event("input"));
  }
  input.setSelectionRange(a + ins.length, a + ins.length);
}
function kill(a, b){
  if (b <= a) return;
  killBuf = input.value.slice(a, b);
  edit(a, b, "");
}
const READLINE = {
  a(){ const [s0] = lineBounds(input.value, input.selectionStart); input.setSelectionRange(s0, s0); },
  e(){ const [, e0] = lineBounds(input.value, input.selectionEnd); input.setSelectionRange(e0, e0); },
  u(){ const at = input.selectionStart, [s0] = lineBounds(input.value, at); kill(s0, at); },
  k(){ const v = input.value, at = input.selectionStart, [, e0] = lineBounds(v, at);
       kill(at, e0 === at && at < v.length ? at + 1 : e0); },
  w(){ const v = input.value, at = input.selectionStart; let i = at;
       while (i > 0 && /\s/.test(v[i - 1])) i--;
       while (i > 0 && !/\s/.test(v[i - 1])) i--;
       kill(i, at); },
  y(){ if (killBuf) edit(input.selectionStart, input.selectionEnd, killBuf.slice(0, MAX_TEXT() - input.value.length)); },
};
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
/** Unpair / re-pair / revoke (§10.14, P33-X02 / X13): nothing typed, attached, queued or remembered for one computer
 *  survives in memory — the ↑↓ history, the field, the quote, the tray (object URLs revoked), the queued sends and the one in
 *  flight (cancelled; it never resumes). A transcription still on its way is dropped when it lands (localEpoch). */
let localEpoch = 0;
export function forgetLocal(){
  localEpoch++;
  histMem = []; histAt = -1;
  clearTimeout(draftTimer);
  if (inflight){ inflight.cancelled = true; inflight.forgotten = true; if (inflight.wake) inflight.wake(); }
  queued.length = 0;
  outbox.wipe();
  for (const l of held.values()) for (const a of l) if (a.url){ URL.revokeObjectURL(a.url); a.url = null; }
  held.clear();
  forgetMedia();                                     // F21: fetched files and their object URLs (§13)
  forgetRendered();                                  // P57: rendered copies of the Agent's words (code / math / diagrams)
  for (const a of atts.slice()) removeAtt(a, false);
  if (replyTo) cancelReply();
  if (input){ input.value = ""; autosize(); }
  paintQueued();
}
const MOD_KEYS = new Set(["Shift", "Control", "Alt", "Meta", "AltGraph", "CapsLock", "Fn", "FnLock", "NumLock", "ScrollLock", "OS", "Super", "Hyper", "Symbol", "SymbolLock"]);
const swallowed = new Set();
const keyId = e => e.code || e.key;
const swallow = e => { e.preventDefault(); e.stopImmediatePropagation(); };

// ---- language switch: everything this module wrote --------------------------------------------------
function relang(){
  RelayMD.LABELS.copy = t('r.md.copy'); RelayMD.LABELS.copyAria = t('r.md.copyAria'); RelayMD.LABELS.copied = t('r.md.copied'); RelayMD.LABELS.image = t('r.md.image');
  wordsSrc = null; qsKey = null;
  renderMenu();
  pttUI(ptt); paintTray(); for (const a of atts) paintChip(a);
  paintPlaceholder(); paintReply(); setOm(el("om").classList.contains("open")); paintOut();
  if (cur) render(cur); else showPage();
}

// ---------------------------------------------------------------- wiring
export function init(ctx){
  C = ctx;
  input = el("input"); mainEl = el("main"); deck = el("deck"); tray = el("tray"); menu = el("menu"); sug = el("sug"); mic = el("mic");
  RelayMD.LABELS.copy = t('r.md.copy'); RelayMD.LABELS.copyAria = t('r.md.copyAria'); RelayMD.LABELS.copied = t('r.md.copied'); RelayMD.LABELS.image = t('r.md.image');
  speaker = new Speaker((id, st, why) => {
    if (st === "idle") speakSt.delete(id); else speakSt.set(id, st);
    if (st === "failed"){ toast(t('r.speak.err.' + (["novoice", "empty", "unsupported", "engine"].includes(why) ? why : "engine")), 2600); setTimeout(() => { if (speakSt.get(id) === "failed"){ speakSt.delete(id); paintSpeak(); } }, 3000); }
    paintSpeak();
  });
  mainEl.addEventListener("scroll", () => { stick = atBottom(); paintMore(); }, {passive: true});
  try{ const ro = new ResizeObserver(() => paintMore()); ro.observe(mainEl); ro.observe(el("read")); }
  catch(_){ addEventListener("resize", paintMore); }
  el("words").addEventListener("click", copyCodeClick);
  el("rdWords").addEventListener("click", copyCodeClick);

  // sheet
  el("apprDeny").addEventListener("click", () => { const p = apprLive(); if (p) apprPost(p, "deny"); });
  holdToApprove(el("apprAllow"), "allow");
  holdToApprove(el("apprBatch"), "batch");
  el("qs").addEventListener("click", e => {
    const b = e.target.closest && e.target.closest(".opt");
    const a = cur && cur.ask;
    if (!b || b.disabled || !a || askState(a) !== "open" || askBusy) return;
    const qi = +b.dataset.q, n = +b.dataset.n, multi = !!(a.questions[qi] || {}).multi;
    if (askSel.id !== a.id) askSel = {id: a.id, picks: a.questions.map(() => [])};
    const c0 = askSel.picks[qi] || [];
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
  el("sheetClose").addEventListener("click", () => {
    foldedCard = cur && cur.card ? cardKey(cur.card) : null;
    if (cur && cur.ask && ASK_FINAL.includes(cur.ask.state)) askDismissed = cur.ask.id;
    if (cur && cur.approval && APPR_FINAL.includes(cur.approval.state)) apprDismissed = cur.approval.id;
    if (cur) renderCard(cur);
  });
  el("pendTag").addEventListener("click", () => { foldedCard = null; if (cur) renderCard(cur); });

  // chrome
  new MutationObserver(() => paintChrome()).observe(document.body,
    {attributes: true, attributeFilter: ["data-status", "data-conn", "data-kind", "data-sheet", "data-kb", "data-reader", "data-view"]});
  new MutationObserver(() => paintChrome(true)).observe(document.documentElement, {attributes: true, attributeFilter: ["data-theme"]});
  document.addEventListener("visibilitychange", () => { if (document.visibilityState === "visible") { paintChrome(true); showPage(); } });
  window.addEventListener("pageshow", () => paintChrome(true));
  window.addEventListener("focus", () => paintChrome(true));
  try{ matchMedia("(prefers-color-scheme: dark)").addEventListener("change", () => paintChrome(true)); }catch(_){}
  paintChrome();

  for (const id of ["mWeek", "m5h"]) el(id).addEventListener("click", meterToast);
  setInterval(renderSub, 30000);
  pressable(el("meta"), e => pillStep(pillHalf(e)), pillDefault);
  pressable(el("orb"), () => setFz((fzAt + 1) % FZ_STEPS.length, true), () => setFz(0, true));

  el("omMore").addEventListener("click", () => setOm(!el("om").classList.contains("open")));
  el("pgPrev").addEventListener("click", () => goPage(-1));
  el("pgNext").addEventListener("click", () => goPage(1));
  el("copyReply").addEventListener("click", copyReply);
  el("replyBtn").addEventListener("click", () => startReply(currentPage()));
  for (const id of ["qbX", "qbJump"]) el(id).addEventListener("pointerdown", e => e.preventDefault());
  el("qbX").addEventListener("click", cancelReply);
  el("qbJump").addEventListener("click", () => { if (replyTo) jumpToTurn(replyTo.id); });
  el("omQuote").addEventListener("click", () => jumpToTurn(+el("omQuote").dataset.id));
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
  const selUp = e => { selRetry(); selPtr = false; const type = e.pointerType || "mouse"; setTimeout(() => selCommit(type), 0); };
  if (window.PointerEvent) document.addEventListener("pointerup", selUp, true);
  else document.addEventListener("mouseup", selUp, true);
  document.addEventListener("touchend", selRetry, true);
  document.addEventListener("keydown", selRetry, true);
  document.addEventListener("copy", () => { if (selPending && wordsSelection() === selPending) selCopied(selPending); });

  el("speakBtn").addEventListener("click", speakTap);
  el("fwdBtn").addEventListener("click", forwardTap);

  // reader
  addEventListener("popstate", () => {
    if (rdSkipPop){ rdSkipPop = false; return; }
    if (rdOpen()){ rdPushed = false; closeReader(true); }
  });
  document.addEventListener("pointerdown", e => { rdPtr = e.pointerType || ""; }, true);
  dblTap(el("rm"), rdTarget, openReader);
  dblTap(el("rdScroll"), rdTarget, () => closeReader());
  document.addEventListener("click", e => { if (performance.now() - rdGuard < 350){ e.preventDefault(); e.stopPropagation(); } }, true);
  el("readBtn").addEventListener("click", openReader);
  el("rdClose").addEventListener("click", () => closeReader());
  el("rdMinus").addEventListener("click", () => rdStep(-1));
  el("rdPlus").addEventListener("click", () => rdStep(1));
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

  // swipe
  deck.addEventListener("touchstart", e => {
    if (e.touches.length !== 1 || document.body.dataset.sheet === "1"){ sw = null; return; }
    const tt = e.touches[0];
    if (tt.clientX < SWIPE_EDGE || tt.clientX > innerWidth - SWIPE_EDGE || hScrollable(e.target)){ sw = null; return; }
    sw = {x: tt.clientX, y: tt.clientY, t: performance.now(), mode: null, dx: 0, lx: tt.clientX, lt: performance.now(), v: 0};
  }, {passive: true});
  deck.addEventListener("touchmove", e => {
    if (!sw || e.touches.length !== 1) return;
    const tt = e.touches[0], dx = tt.clientX - sw.x, dy = tt.clientY - sw.y;
    if (sw.mode === null){
      if (Math.abs(dx) + Math.abs(dy) < SWIPE_MIN_MOVE) return;
      sw.mode = Math.abs(dy) <= Math.abs(dx) * Math.tan(SWIPE_DEG * Math.PI / 180) ? "page" : "scroll";
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
  if (window.visualViewport){
    visualViewport.addEventListener("resize", fitViewport);
    visualViewport.addEventListener("scroll", fitViewport);
    fitViewport();
  }
  input.addEventListener("focus", () => setTimeout(fitViewport, 300));
  input.addEventListener("blur", () => setTimeout(fitViewport, 300));

  // composer
  input.addEventListener("compositionstart", () => { composing = true; });
  input.addEventListener("compositionend", () => { composing = false; capField(); autosize(); draftSoon(); });
  input.addEventListener("input", () => { if (!composing) capField(); autosize(); draftSoon(); });
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
  input.addEventListener("paste", e => {
    const cd = e.clipboardData;
    if (!cd) return;
    const text = cd.getData("text/plain");
    const files = clipFiles(cd);
    if (files.length){
      if (!text) e.preventDefault();
      takeFiles(files.map((f, i) => pastedName(f, i, files.length)), 'r.verb2.paste', "paste");
    }
    if (!text) return;
    const v = input.value, a = input.selectionStart, b = input.selectionEnd;
    if (!overflowsField(v.slice(0, a) + text + v.slice(b))) return;
    e.preventDefault();
    const full = atts.length >= MAX_ATT;
    addFiles([new File([text], `${t('r.file.paste')}-${stamp()}.txt`, {type: "text/plain"})], "paste");
    if (!full) toast(t('r.paste.long'), 2600);
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
  window.addEventListener("drop", e => {
    e.preventDefault();
    dragDepth = 0; dropUI(null);
    if (!isFileDrag(e) || modalOpen()) return;
    takeFiles([...((e.dataTransfer && e.dataTransfer.files) || [])], 'r.verb2.drop', "drop");
  });

  // pickers + camera
  const PICKERS = {tDoc: "fDoc", tPhoto: "fPhoto", tCamera: "fCamera"};
  for (const [btn, inp] of Object.entries(PICKERS)){
    el(btn).addEventListener("click", () => {
      if (btn === "tCamera" && isDesktop()) return openCam();
      el(inp).click();
    });
  }
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
  el("slashBtn").addEventListener("click", e => {
    e.stopPropagation(); menu.hidden = !menu.hidden;
    if (!menu.hidden){ closeSug(); renderMenu(); loadMenu(); }
  });
  document.addEventListener("click", e => { if (!menu.hidden && !(e.target.closest && e.target.closest("#menu"))) closeMenu(); });
  menu.addEventListener("click", async e => {
    const b = e.target.closest && e.target.closest("button");
    if (!b) return;
    e.stopPropagation();
    if (b.id === "modelsEntry"){ closeMenu(); C.models?.(); return; }
    if (b.id === "silentEntry"){ openSilent(); return; }
    if (b.id === "keysEntry"){ openKeys(); return; }
    if (b.id === "appEntry"){ closeMenu(); location.href = / AgentJApp\//.test(navigator.userAgent) ? "agentj-app://status" : "jarvis-app://status"; return; }
    if (b.id === "clipEntry"){ closeMenu(); pasteClipImages(); return; }
    if (b.dataset.run){ closeMenu(); if (await runCmd(b.dataset.run)) { toNewest(); toast(t('r.run.sent', {cmd: "/" + b.dataset.run}), 1600); } return; }
    if (b.dataset.estop){ closeMenu(); C.estop(b.dataset.estop === "stop"); return; }
    if (b.dataset.slash){ closeMenu(); insertSlash(b.dataset.slash); }
  });
  input.addEventListener("input", () => { if (!composing) updateSug(); });
  input.addEventListener("compositionend", updateSug);
  input.addEventListener("focus", updateSug);
  input.addEventListener("blur", () => setTimeout(() => { if (document.activeElement !== input) closeSug(); }, 150));
  sug.addEventListener("mousedown", e => e.preventDefault());
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
  mic.addEventListener("pointerdown", e => {
    if (e.button !== 0 || !e.isPrimary) return;
    e.preventDefault();
    if (e.pointerType !== "mouse" && document.activeElement === input && keyboardDown()) input.blur();
    if (ptt && ptt.pend){ pttLock(ptt); return; }
    if (ptt && ptt.locked && ptt.id === null){
      try{ mic.setPointerCapture(e.pointerId); }catch(_){}
      ptt.id = e.pointerId; ptt.y0 = e.clientY; return;
    }
    if (ptt || performance.now() - lockEndAt < LOCK_REARM_MS) return;
    try{ mic.setPointerCapture(e.pointerId); }catch(_){}
    pttStart(e.pointerId, e.clientY);
  });
  mic.addEventListener("pointermove", e => {
    if (!ptt || e.pointerId !== ptt.id) return;
    const c = ptt.y0 - e.clientY > PTT_CANCEL_PX;
    if (c !== ptt.cancel){ ptt.cancel = c; pttUI(ptt); try{ if (navigator.vibrate) navigator.vibrate(12); }catch(_){} }
  });
  mic.addEventListener("pointerup", e => {
    if (!ptt || e.pointerId !== ptt.id) return;
    if (!ptt.locked && !ptt.cancel && typeof ptt.id === "number" && performance.now() - ptt.t0 < PTT_MIN_MS) pttTap(ptt);
    else pttEnd(ptt, "release");
  });
  for (const ev of ["pointercancel", "lostpointercapture"]){
    mic.addEventListener(ev, e => {
      if (!ptt || e.pointerId !== ptt.id) return;
      if (ptt.locked){ ptt.id = null; ptt.cancel = false; pttUI(ptt); return; }
      pttEnd(ptt, "cancel");
    });
  }
  mic.addEventListener("contextmenu", e => e.preventDefault());
  mic.addEventListener("selectstart", e => e.preventDefault());
  mic.addEventListener("dblclick", e => e.preventDefault());
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
  document.addEventListener("visibilitychange", () => {
    if (document.visibilityState !== "visible" && ptt) pttEnd(ptt, ptt.locked ? "release" : "cancel");
  });
  window.addEventListener("blur", () => { if (ptt && !ptt.locked) pttEnd(ptt, "cancel"); });
  window.addEventListener("pagehide", () => { if (ptt) pttEnd(ptt, "cancel"); });

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
  el("send").addEventListener("click", () => { unlockWhoosh(); say(); });
  el("sayCancel").addEventListener("click", e => { e.stopPropagation(); cancelSay(); });
  const clr = el("clr");
  for (const ev of ["pointerdown", "mousedown"]) clr.addEventListener(ev, e => {
    clr._keep = clr._keep || document.activeElement === input;
    e.preventDefault();
  });
  clr.addEventListener("click", e => {
    e.preventDefault(); e.stopPropagation();
    if (clr._keep || document.activeElement === clr) input.focus();
    clr._keep = false;
    const text = input.value;
    if (!text) return;
    input.value = "";
    input.dispatchEvent(new Event("input"));
    toastAction(t('r.clr.done'), t('r.clr.undo'), () => {
      input.value = (text + input.value).slice(0, MAX_TEXT());
      input.dispatchEvent(new Event("input"));
      input.focus();
      input.setSelectionRange(input.value.length, input.value.length);
    }, 5000);
  });
  input.addEventListener("keydown", e => {
    if (e.key === "Enter" && !e.shiftKey && !e.isComposing && e.keyCode !== 229){ e.preventDefault(); unlockWhoosh(); say(); }
  });
  el("silentClose").addEventListener("click", closeSilent);
  el("silentMore").addEventListener("click", moreSilent);
  el("silent").addEventListener("click", e => { if (e.target === el("silent")) closeSilent(); });
  el("keysClose").addEventListener("click", closeKeys);
  el("keysBtn").addEventListener("click", openKeys);
  el("keys").addEventListener("click", e => { if (e.target === el("keys")) closeKeys(); });

  document.addEventListener("keydown", e => {
    if (e.isComposing || (e.keyCode === 229 && !isM(e))) return;
    if (document.body.dataset.view !== "chat") return;
    const tg = e.target;
    if (e.key === "Escape"){
      if (!el("silent").hidden){ e.preventDefault(); closeSilent(); return; }
      if (rdOpen()){ e.preventDefault(); closeReader(); return; }
      if (!el("keys").hidden){ e.preventDefault(); closeKeys(); return; }
      if (!el("cam").hidden || !el("confirm").hidden || !el("badge-panel").hidden || !el("settings").hidden) return;
      if (!menu.hidden){ e.preventDefault(); closeMenu(); return; }
      if (replyTo){ e.preventDefault(); escAt = 0; cancelReply(); return; }
      const now = performance.now();
      if (escAt && now - escAt <= ESC2_MS){
        escAt = 0; e.preventDefault();
        if (escArmedInterrupt) interruptAgent();
        else if (!composerEmpty()) clearComposer();
        return;
      }
      escAt = now;
      escArmedInterrupt = composerEmpty() && emptySince !== null && now - emptySince >= EMPTY_GUARD_MS;
      if (tg === input){ e.preventDefault(); input.blur(); }
      return;
    }
    // F13: ⌘, (mac) / Ctrl+, opens Settings from anywhere on the chat screen, the field and the reader included.
    if ((e.ctrlKey || e.metaKey) && !e.altKey && !e.shiftKey && (e.key === "," || e.code === "Comma")){
      if (dialogOpen() && el("settings").hidden) return;
      e.preventDefault(); if (rdOpen()) closeReader(); closeMenu(); C.settings && C.settings(); return;
    }
    if (rdOpen()){
      if (e.ctrlKey || e.metaKey || e.altKey || (tg && tg.id === "rdSlider" && e.key.startsWith("Arrow"))) return;
      const sc = el("rdScroll"), rstep = Math.max(40, Math.round(sc.clientHeight * 0.25)), rscreen = Math.max(40, Math.round(sc.clientHeight * 0.9));
      const f = {f: () => closeReader(), "+": () => rdStep(1), "=": () => rdStep(1), "-": () => rdStep(-1), "_": () => rdStep(-1),
                 ArrowUp: () => sc.scrollBy(0, -rstep), ArrowDown: () => sc.scrollBy(0, rstep),
                 " ": () => sc.scrollBy(0, e.shiftKey ? -rscreen : rscreen)}[e.key];
      if (f){ e.preventDefault(); f(); }
      return;
    }
    if (tg === input){
      const k = e.key.length === 1 ? e.key.toLowerCase() : "";
      if (e.ctrlKey && !e.metaKey && !e.altKey && !e.shiftKey && READLINE[k]){ e.preventDefault(); READLINE[k](); return; }
      if ((e.key === "ArrowUp" || e.key === "ArrowDown") && !e.ctrlKey && !e.metaKey && !e.altKey && !e.shiftKey
          && !e.defaultPrevented && sug.hidden && histKey(e.key === "ArrowUp")){ e.preventDefault(); return; }
    }
    if (e.ctrlKey || e.metaKey || e.altKey || typingIn(tg) || typingIn(document.activeElement) || dialogOpen()) return;
    if (e.key === "?"){ e.preventDefault(); openKeys(); return; }
    if (e.key === "s" && !e.shiftKey){ e.preventDefault(); closeMenu(); closeSug(); C.settings && C.settings(); return; }
    if (isM(e)){
      e.preventDefault();
      if (mKey || e.repeat) return;
      mKey = true; mCode = e.code;
      if (ptt && ptt.pend){ ptt.viaM = true; pttLock(ptt); return; }
      if (ptt || performance.now() - lockEndAt < LOCK_REARM_MS) return;
      pttStart("key", 0);
      if (ptt) ptt.viaM = true;
      return;
    }
    if (e.repeat && !["ArrowUp", "ArrowDown", " "].includes(e.key)) return;
    const btn = {a: "tDoc", p: "tPhoto", c: "tCamera"}[e.key];
    if (btn){ e.preventDefault(); closeMenu(); el(btn).click(); return; }
    const step = Math.max(40, Math.round(mainEl.clientHeight * 0.25));
    const screen = Math.max(40, Math.round(mainEl.clientHeight * 0.9));
    const nav = {j: () => goPage(-1), ArrowLeft: () => goPage(-1), k: () => goPage(1), ArrowRight: () => goPage(1),
                 g: () => goEdge(true), G: () => goEdge(false),
                 ArrowUp: () => mainEl.scrollBy(0, -step), ArrowDown: () => mainEl.scrollBy(0, step),
                 " ": () => mainEl.scrollBy(0, e.shiftKey ? -screen : screen),
                 o: () => { if (!el("om").hidden) setOm(!el("om").classList.contains("open")); },
                 y: copyReply, Y: copySource, r: () => startReply(currentPage()), f: openReader}[e.key];
    if (nav){ e.preventDefault(); nav(); return; }
    if (e.key === "i"){
      e.preventDefault(); closeMenu();
      input.focus();
      input.setSelectionRange(input.value.length, input.value.length);
    }
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
  outbox.open(); addEventListener("online", paintOut); addEventListener("offline", paintOut);
  showPage();
  pttUI(null);
  paintTray();
}
/** Called when the session becomes ready again (a new host turn source, menu). */
export function onReady(){ paintPlaceholder(); refreshSend(); loadMenu(); paintActs(); renderMenu(); }
export function onEstop(){ paintPlaceholder(); renderMenu(); refreshSend(); }
export function rerender(){ if (cur) render(cur); else showPage(); }
/** For the screens test: internal state worth asserting. */
export function debug(){ return {pageAt, follow, pages: pages().length, total: totalPages(), atts: atts.map(a => ({st: a.st, name: a.name, id: a.id || null, origin: a.origin})), queued: queued.length, asrBusy, hist: hist.turns.map(x => x.id), menuItems: menuItems.map(x => x[0]), ptt: ptt ? {locked: ptt.locked, cancel: ptt.cancel} : null, histMem: histMem.slice(), killBuf}; }


let userPreferences={};
export function applyPreferences(value){
  userPreferences=value||{};configureSpeech(userPreferences.voice);
  speaker?.stop();
  const app=userPreferences.appearance||{};
  if(app.language)window.AJLang?.set(app.language);
  if(app.theme==='system')document.documentElement.removeAttribute('data-theme');
  else if(['light','dark'].includes(app.theme))document.documentElement.setAttribute('data-theme',app.theme);
  loadMenu();
}
export function announceTurn(turn,old){
  if(isSilent(typeof turn.reply === 'string' ? turn.reply : turn.reply?.text) || !old || turn.end!=='done' || new URLSearchParams(location.search).has('watcher'))return;
  if(userPreferences.voice?.speak_replies)speaker?.tap(turn.id,typeof turn.reply==='string'?turn.reply:(turn.reply?.text||''));
  else if(userPreferences.voice?.speak_notifications && userPreferences.voice?.tts?.mode==='phone')speaker?.tap('notification-'+turn.id,t('preferences.notification'));
}
document.addEventListener('keydown',e=>{
  const combo=[e.ctrlKey?'ctrl':null,e.altKey?'alt':null,e.shiftKey?'shift':null,e.metaKey?'meta':null,e.key.toLowerCase()].filter(Boolean).join('+');
  const binding=(userPreferences.keyboard?.bindings||[]).find(x=>x.combo===combo);
  if(!binding)return;
  const fn={'open-menu':()=>{menu.hidden=!menu.hidden;},'read-reply':speakTap,'stop-speech':()=>speaker?.stop(),'focus-input':()=>input?.focus(),'interrupt':()=>C.api.slash('stop')}[binding.action];
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
