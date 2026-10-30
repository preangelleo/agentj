// P408 (PROTOCOL §38, ADR-P408-terminal): the web terminal page, line mode. Loaded on the first open by js/termhook.js (a
// module script next to it — never in the first-screen bundle) together with terminal.css.
// Open = two steps: an unsigned `term_open` fetches the HOST's one-use nonce, Face ID challenge, PRF salt and one-time X25519
// key; the owner's tap on 「用 Face ID 确认」 starts the WebAuthn assertion synchronously (iOS gesture rule) with the `prf`
// extension (eval.first = the salt); the same nonce is then signed (§8 control-v1, action `term_open`, object {"mode":"line"})
// and the PRF output, when the authenticator gave one, is sealed with the §11 seal (kind `term_prf`). The first time, the
// host asks this device — and only for this pending open — for the computer login password: a `login` card shown INSIDE this
// page with a host-origin line, sealed and signed exactly like a sudo card, never a chat card.
// Line mode: whole lines (no control characters, no TAB) and two keys (Ctrl+C → int, Ctrl+\ → quit). A dangerous line comes
// back as `term_confirm` (运行 = Face ID + a signed line); sudo asks `term_sudo` (Face ID + PRF). Output is text only, kept in
// memory only (never storage, never history) and wiped when the terminal closes.
// P414 (PROTOCOL §37.12–§37.14, ADR-P414-bang): full screen like P412's editor (Noir, Lotion, no header / footer rules); a
// grabber at the top centre (drag down past the threshold = collapse, short of it = spring back; finger and mouse), the exit
// icon top right and Esc collapse it — NO double-click / double-tap gesture anywhere (it would hit running programs).
// Collapsed ≠ ended: a chip above the message box (「终端 · 剩 N 分钟」, 打开 / 结束); a sudo / confirm / login card or the
// Agent's `term_go` brings the page back. 「!」 lines from the message box (js/bang.js) run here with `bang: <sid>`.
// 「复制输出」 copies the page's own output text; 「截屏发到聊天」 draws the visible output on a canvas and puts the PNG into the
// message box's attachments (sent only when the owner sends). PRF: eval.second = the host's next salt `vn` (M3).
// P417 #185: o.enable — the same two steps with object {"mode":"enable"} (a computer browser switching the terminal on for
// itself); the host then always asks for the login password and opens the terminal right after.
import { register } from './termhook.js';
import { request, done, sendApp, gen, channel } from './session.js';
import { controlMessage, controlObject, sealValue, elevateFields, elevateDigest, elevateMessage, sha256hex, b64u, unb64u } from '../proto/wire.js';
import { signKey } from './store.js';
import { myDeviceId } from './api.js';
import { elevateChallenge, canAssert, approve } from './faceid.js';
import { registerLayer } from './layers.js';
import { phoneConfirmation } from './tvremote.js';
import { t, lang, onLang } from './t.js';
import { mk, executionToast as toast, toastAction } from './ui.js';
import { copyable, copyNow } from './copycode.js';   // P292: the shown line / argv can be copied (icon only) · P414: 「复制输出」
import { assertPrf, salt32 } from './vault.js';   // P414 (M3): PRF eval.first + eval.second, prf1 ‖ prf2
import { SVG } from './icons.js';
import { paintPk, setupStepsAside, faState, webauthnErr } from './pkassist.js';   // P539: no own passkey here · why a tap failed
import { UNSAFE } from './bang.js';               // P414 (H1c): Unicode format characters / > 8 spaces never go out

const LINE_MAX = 4096, KEEP_CHARS = 200000, HIDDEN_MS = 60000, NONCE_MS = 110000, CLOSE_SHOW_MS = 1600;
/** Control characters (C0 incl. TAB / CR / LF, DEL, C1) and the two Unicode line breaks: never in a term_line. */
export const CTRL = /[\u0000-\u001f\u007f-\u009f\u2028\u2029]/;
export const KEYS = { int: 'Ctrl+C', quit: 'Ctrl+\\' };
const INSERT = ['|', '~', '/', '-'];
const OPEN_WHY = ['disabled', 'os', 'device_not_allowed', 'web_not_allowed', 'no_passkey', 'passkey_failed', 'stale', 'replay',
  'bad_signature', 'busy', 'estop', 'locked', 'bad_password', 'cancelled', 'shape', 'timeout', 'offline', 'phone', 'device_type'];
const CLOSE_WHY = ['owner', 'timeout', 'idle', 'background', 'relay_lost', 'estop', 'host', 'disabled'];
const HEX32 = /^[0-9a-f]{32}$/, HEX16 = /^[0-9a-f]{16}$/;
const enc = new TextEncoder();

let H = { setupFaceId() {}, openSettings() {}, openRescue() {}, attach() { return null; }, insert() {}, goChat() {} };
const BANG_MS = 130000;   // P414: the host answers a 「!」 line within 120 s (next prompt, 120 s or the close)
let S = fresh();
function fresh() {
  return { phase: 'idle', minutes: 15, G: null, ch: null, r1: null, r2: null, id: null, expires: 0, fa: null, seq: 0, atPrompt: true,
    hist: [], hi: -1, draft: '', prefill: '', login: null, hiddenAt: 0,
    view: 'full', origin: 'settings', bang: null, pendBang: null, go: null, reCollapse: false,   // P414
    enable: false };   // P417 #185
}
let root = null, tick = 0, hideTimer = 0, closeTimer = 0;
const $ = (id) => root.querySelector('#' + id);

// ---------------------------------------------------------------- DOM
function css() {
  if (document.getElementById('term-css')) return;
  const l = document.createElement('link');
  l.id = 'term-css'; l.rel = 'stylesheet'; l.href = new URL('../terminal.css', import.meta.url).href;
  document.head.appendChild(l);
}
function btn(id, cls, text) { const b = mk('button', cls, text); b.type = 'button'; if (id) b.id = id; return b; }
function build() {
  if (root) return;
  css();
  root = mk('div', 'term'); root.id = 'term'; root.hidden = true;
  root.setAttribute('role', 'dialog'); root.setAttribute('aria-modal', 'true'); root.setAttribute('aria-labelledby', 'term-title');
  const top = mk('div', 'term-top');
  const title = mk('h2', 'term-title'); title.id = 'term-title';
  const rec = mk('span', 'term-rec'); rec.id = 'term-rec';
  const left = mk('span', 'term-left'); left.id = 'term-left';
  // P576b (#372): 「操作日志记录中 · 还剩 mm:ss」 is one link to the official page on what is logged, why and who can read it
  const log = mk('a', 'term-log'); log.id = 'term-log'; log.target = '_blank'; log.rel = 'noopener noreferrer';
  log.append(rec, left);
  // P414 (#179③): the grabber (top centre) and the exit icon (top right) collapse the page; 「结束」 ends the session
  const grab = btn('term-grab', 'term-grab'); grab.append(mk('span'));
  const exit = btn('term-exit', 'term-exit'); exit.append(icon('M6 6l12 12M18 6L6 18'));   // P414: 「右上角叉退出」 (Leo #179③) — collapses; 「结束」 ends
  top.append(grab, title, log, btn('term-end', 'term-end'), exit);
  const out = mk('pre', 'term-out'); out.id = 'term-out'; out.tabIndex = 0;
  out.setAttribute('role', 'log'); out.setAttribute('aria-live', 'polite');
  out.append(document.createTextNode(''), mk('span', 'term-cur'));
  const outBox = mk('div', 'term-outbox'); outBox.append(copyable(out, { variant: 'bare', click: false }));   // P414: 「复制输出」 below replaces P292's corner icon
  const bottom = mk('div', 'term-bottom');
  const status = mk('p', 'term-status'); status.id = 'term-status'; status.hidden = true; status.setAttribute('role', 'status');
  const stActs = mk('div', 'term-acts term-stacts'); stActs.id = 'term-stacts'; stActs.hidden = true;
  stActs.append(btn('term-rescue', 'term-btn'), btn('term-back', 'term-btn term-btn--yes'));
  const prog = mk('p', 'term-hint'); prog.id = 'term-prog'; prog.hidden = true;
  const bad = mk('p', 'term-hint'); bad.id = 'term-bad'; bad.hidden = true; bad.setAttribute('role', 'alert');
  const form = mk('form', 'term-form'); form.id = 'term-form'; form.autocomplete = 'off'; form.noValidate = true;
  const input = mk('input', 'term-input'); input.id = 'term-input';
  Object.assign(input, { type: 'text', autocomplete: 'off', spellcheck: false });
  for (const [k, v] of [['autocapitalize', 'off'], ['autocorrect', 'off'], ['enterkeyhint', 'send']]) input.setAttribute(k, v);
  const send = mk('button', 'term-send'); send.type = 'submit'; send.id = 'term-send';
  form.append(input, send);
  const keys = mk('div', 'term-keys'); keys.id = 'term-keys'; keys.setAttribute('role', 'group');
  keys.append(btn('term-k-int', 'term-key', KEYS.int), btn('term-k-quit', 'term-key', KEYS.quit), btn('term-k-up', 'term-key', '↑'),
    btn('term-k-down', 'term-key', '↓'), ...INSERT.map((c, i) => { const b = btn('term-k-ins' + i, 'term-key', c); b.dataset.ins = c; return b; }));
  const warn = mk('p', 'term-warn'); warn.id = 'term-nopw';
  // P414: the Agent's line waiting while the owner's own input is not empty (H1b) · 复制输出 / 截屏发到聊天 · who can read this
  const go = btn('term-gochip', 'term-gochip'); go.hidden = true;
  const goNote = mk('p', 'term-hint term-gonote'); goNote.id = 'term-gonote'; goNote.hidden = true;
  const tools = mk('div', 'term-tools'); tools.id = 'term-tools';
  tools.append(btn('term-copy', 'term-tool'), btn('term-shot', 'term-tool'));
  bottom.append(status, stActs, prog, bad, go, goNote, form, keys, tools, warn);
  // the one sheet of this page: intro, the login card, a confirm, sudo, 「结束？」
  const wrap = mk('div', 'term-sheetwrap'); wrap.id = 'term-sheetwrap'; wrap.hidden = true;
  const sheet = mk('form', 'term-sheet'); sheet.id = 'term-sheet'; sheet.autocomplete = 'off'; sheet.noValidate = true;
  sheet.setAttribute('role', 'dialog'); sheet.setAttribute('aria-modal', 'true'); sheet.setAttribute('aria-labelledby', 'term-sh-title');
  const shPw = mk('input', 'term-input term-pw'); shPw.id = 'term-sh-pw';
  Object.assign(shPw, { type: 'password', autocomplete: 'off', spellcheck: false });
  for (const [k, v] of [['autocapitalize', 'off'], ['autocorrect', 'off'], ['enterkeyhint', 'done']]) shPw.setAttribute(k, v);
  const shPwLab = mk('label', 'term-pwlab'); shPwLab.id = 'term-sh-pwlab'; shPwLab.htmlFor = 'term-sh-pw';
  const acts = mk('div', 'term-acts');
  const yes = mk('button', 'term-btn term-btn--yes'); yes.type = 'submit'; yes.id = 'term-sh-yes';
  acts.append(btn('term-sh-no', 'term-btn'), yes);
  const node = (tag, id, cls) => { const n = mk(tag, cls); n.id = id; return n; };
  const pre = node('pre', 'term-sh-pre'), pre2 = node('pre', 'term-sh-pre2');
  const sbad = node('p', 'term-sh-bad', 'term-hint'); sbad.setAttribute('role', 'alert');
  sheet.append(node('p', 'term-sh-origin', 'term-origin'), node('h2', 'term-sh-title'), node('p', 'term-sh-text'), copyable(pre, { variant: 'block', click: false }),
    node('p', 'term-sh-pre2l'), copyable(pre2, { variant: 'block', click: false }), node('p', 'term-sh-warn', 'term-warn'), shPwLab, shPw, sbad,
    node('p', 'term-sh-note'), acts, node('div', 'term-sh-pk', 'pkassist'));   // P539: under the buttons
  wrap.append(sheet);
  root.append(top, outBox, bottom, wrap);
  document.body.appendChild(root);

  setupStepsAside($('term-sh-pk'), () => sheetNo());   // P539: 在本机设置 on the intro sheet = 取消 first
  $('term-end').addEventListener('click', () => endNow());
  $('term-exit').addEventListener('click', () => collapse());
  dragInit(grab);
  $('term-copy').addEventListener('click', () => copyOut());
  $('term-shot').addEventListener('click', () => { shot().catch(() => toast(t('p414.shotFail'), 3600, 'error')); });
  go.addEventListener('click', () => takeGo());
  $('term-back').addEventListener('click', () => hide());
  $('term-rescue').addEventListener('click', () => { hide(); H.openRescue(); });
  form.addEventListener('submit', (e) => { e.preventDefault(); sendLine(); });
  input.addEventListener('keydown', (e) => {
    if (e.isComposing) return;
    if (e.key === 'ArrowUp' || e.key === 'ArrowDown') { e.preventDefault(); histStep(e.key === 'ArrowUp' ? -1 : 1); }
  });
  input.addEventListener('input', () => { $('term-bad').hidden = true; S.goNote = false; paintProg(); paintGo(); });
  shPw.addEventListener('input', () => { $('term-sh-bad').hidden = true; });
  for (const b of keys.querySelectorAll('button')) b.addEventListener('pointerdown', (e) => { if (e.pointerType !== 'mouse') e.preventDefault(); });   // keep the keyboard up
  $('term-k-int').addEventListener('click', () => sendKey('int'));
  $('term-k-quit').addEventListener('click', () => sendKey('quit'));
  $('term-k-up').addEventListener('click', () => histStep(-1));
  $('term-k-down').addEventListener('click', () => histStep(1));
  for (const b of keys.querySelectorAll('[data-ins]')) b.addEventListener('click', () => insert(b.dataset.ins));
  sheet.addEventListener('submit', (e) => { e.preventDefault(); sheetYes(); });
  $('term-sh-no').addEventListener('click', () => sheetNo());
  registerLayer({ element: () => root, open: () => !!root && !root.hidden && $('term-sheetwrap').hidden, rank: 50,
    close: () => escape(), fallback: () => document.getElementById('input') || document.getElementById('set-term-open') });
  registerLayer({ element: () => $('term-sheetwrap'), open: () => !!root && !root.hidden && !$('term-sheetwrap').hidden, rank: 52,
    close: () => sheetNo(), fallback: () => $('term-input') });
  onLang(labels);
  labels();
}
function labels() {
  if (!root) return;
  $('term-title').textContent = t('p408.title');
  $('term-rec').textContent = t('p408.rec');
  $('term-log').href = 'https://agentj.app/docs/web-terminal-log/' + (lang() === 'en' ? '?lang=en' : '');
  $('term-log').title = t('p576.logTitle');
  $('term-end').textContent = t('p408.end');
  $('term-send').textContent = t('p408.send');
  $('term-input').setAttribute('aria-label', t('p408.inputAria'));
  $('term-prog').textContent = t('p408.prog');
  $('term-keys').setAttribute('aria-label', t('p408.keys'));
  $('term-k-int').setAttribute('aria-label', t('p408.k.int'));
  $('term-k-quit').setAttribute('aria-label', t('p408.k.quit'));
  $('term-k-up').setAttribute('aria-label', t('p408.k.up'));
  $('term-k-down').setAttribute('aria-label', t('p408.k.down'));
  for (const b of root.querySelectorAll('[data-ins]')) b.setAttribute('aria-label', t('p408.k.ins', { c: b.dataset.ins }));
  $('term-rescue').textContent = t('p408.rescue');
  $('term-back').textContent = t('p408.back');
  for (const [id, k] of [['term-grab', 'p414.grab'], ['term-exit', 'p414.exit']]) { $(id).setAttribute('aria-label', t(k)); $(id).title = t(k); }
  $('term-copy').textContent = t('p414.copy');
  $('term-shot').textContent = t('p414.shot');
  $('term-nopw').textContent = t('p408.noPw') + ' ' + t('p414.reads');
  paintGo();
  paintLeft();
  paintChip();
}
function icon(d) {
  const svg = document.createElementNS(SVG, 'svg'), path = document.createElementNS(SVG, 'path');
  for (const [k, v] of [['viewBox', '0 0 24 24'], ['fill', 'none'], ['stroke', 'currentColor'], ['stroke-width', '2'], ['stroke-linecap', 'round'], ['stroke-linejoin', 'round'], ['aria-hidden', 'true']]) svg.setAttribute(k, v);
  path.setAttribute('d', d); svg.append(path);
  return svg;
}

// ---------------------------------------------------------------- the page: show / hide, its own history entry
let pushed = false, popping = false;
addEventListener('popstate', (e) => {
  if (popping) { popping = false; e.stopImmediatePropagation(); return; }
  if (!pushed || !root || root.hidden) return;
  e.stopImmediatePropagation();          // back = Esc: the sheet's 「取消」, else (P414) collapse
  if (S.phase === 'ready' && $('term-sheetwrap').hidden) { pushed = false; collapse(); return; }
  try { history.pushState({ ...(history.state || {}), ajTerm: 1 }, ''); } catch { pushed = false; }
  if (S.phase === 'closed') { hide(); return; }
  if (!$('term-sheetwrap').hidden) sheetNo(); else askEnd();
}, true);
function show() {
  build();
  S.view = 'full'; paintChip();
  root.style.transform = ''; root.classList.remove('leaving', 'drag');
  if (!root.hidden) return;
  root.hidden = false; document.body.dataset.term = '1';
  if (!pushed) { try { history.pushState({ ...(history.state || {}), ajTerm: 1 }, ''); pushed = true; } catch { pushed = false; } }
}
/** Leave the page (after a close): back to Settings (P414: or the chat, when it was opened from there), the closed line under
 *  the row. Wipes everything in memory. */
function hide() {
  clearTimeout(closeTimer); clearInterval(tick); tick = 0;
  if (!root) return;
  const line = S.phase === 'closed' ? $('term-status').textContent : '';
  const back = S.origin === 'settings' && S.view === 'full';
  closeSheet();
  root.hidden = true; delete document.body.dataset.term;
  if (pushed) { pushed = false; popping = true; try { history.back(); } catch { popping = false; } }
  wipe();
  S = fresh();
  paintChip();
  H.closedLine?.(line);
  if (back) H.openSettings();
}

// ---------------------------------------------------------------- P414: collapse / expand, the grabber, the chip
const reduced = () => { try { return matchMedia('(prefers-reduced-motion: reduce)').matches; } catch { return false; } };
/** Esc (and the browser's back): a sheet → its 「取消」 (its own layer); a running terminal → collapse; else as P408. */
function escape() {
  if (S.phase === 'ready') return collapse();
  askEnd();
}
/** The page goes down; the session runs on behind the chip above the message box. Never ends anything. */
function collapse() {
  if (!root || root.hidden || S.phase !== 'ready' || sheet) return;
  if (pushed) { pushed = false; popping = true; try { history.back(); } catch { popping = false; } }
  S.view = 'collapsed'; S.reCollapse = false;
  const fin = () => {
    if (S.view !== 'collapsed') return;
    root.hidden = true; root.classList.remove('leaving', 'drag'); root.style.transform = ''; delete document.body.dataset.term;
    paintChip();
    try { document.getElementById('input')?.focus({ preventScroll: true }); } catch { /* old browsers */ }
  };
  H.goChat();
  if (reduced()) { fin(); return; }
  root.classList.add('leaving'); root.style.transform = 'translateY(100%)';
  setTimeout(fin, 240);
}
/** The chip's 「打开」, a card that needs the owner, the Agent's term_go: full screen again. */
function expand(reCollapse = false) {
  if (S.phase !== 'ready') { show(); return; }
  const was = S.view === 'collapsed' || !root || root.hidden;
  show();
  if (was && reCollapse) S.reCollapse = true;
  setInput('');
}
/** Drag the grabber down: the page follows the finger (or the mouse); past the threshold it collapses, short of it it springs
 *  back. A tap without a drag = collapse too (the same as the exit icon). */
function dragInit(g) {
  let y0 = null, dy = 0, pid = null, moved = false;
  const min = () => Math.max(96, Math.round(root.clientHeight * 0.2));
  g.addEventListener('pointerdown', (e) => {
    if (S.phase !== 'ready' || sheet || (e.pointerType === 'mouse' && e.button !== 0)) return;
    y0 = e.clientY; dy = 0; moved = false; pid = e.pointerId;
    try { g.setPointerCapture(pid); } catch { /* synthetic events */ }
    root.classList.add('drag'); root.classList.remove('spring');
    e.preventDefault();
  });
  g.addEventListener('pointermove', (e) => {
    if (y0 === null || e.pointerId !== pid) return;
    dy = Math.max(0, e.clientY - y0);
    if (dy > 4) moved = true;
    root.style.transform = dy ? `translateY(${dy}px)` : '';
  });
  const end = (e, cancel) => {
    if (y0 === null || e.pointerId !== pid) return;
    y0 = null;
    root.classList.remove('drag');
    if (!cancel && dy > min()) { collapse(); return; }
    root.classList.add('spring'); root.style.transform = '';
    setTimeout(() => root.classList.remove('spring'), 420);
  };
  g.addEventListener('pointerup', (e) => end(e, false));
  g.addEventListener('pointercancel', (e) => end(e, true));
  g.addEventListener('click', (e) => { if (moved) { moved = false; e.preventDefault(); return; } collapse(); });
}
let chip = null;
function buildChip() {
  if (chip) return chip;
  chip = mk('div', 'termchip'); chip.id = 'term-chip'; chip.hidden = true; chip.setAttribute('role', 'group');
  const txt = mk('span', 'termchip-text'); txt.id = 'term-chip-text';
  txt.append(icon('M5 8l4 4-4 4M12 17h7'), mk('span'));
  const open_ = mk('button', 'termchip-btn termchip-open'); open_.type = 'button'; open_.id = 'term-chip-open';
  const end = mk('button', 'termchip-btn'); end.type = 'button'; end.id = 'term-chip-end';
  chip.append(txt, open_, end);
  open_.addEventListener('click', () => expand());
  end.addEventListener('click', () => endNow());
  const tray = document.getElementById('tray');
  if (tray && tray.parentNode) tray.before(chip); else document.body.appendChild(chip);
  return chip;
}
function minsLeft() { return Math.max(0, Math.ceil((S.expires - Date.now() / 1000) / 60)); }
function paintChip() {
  const on = S.phase === 'ready' && S.view === 'collapsed';
  if (!on && !chip) return;
  const c = buildChip();
  c.hidden = !on;
  if (!on) return;
  c.setAttribute('aria-label', t('p408.title'));
  const label = S.bang ? t('p414.chip.run', { line: S.bang.line.slice(0, 60) }) : t('p414.chip.left', { n: minsLeft() });
  c.querySelector('#term-chip-text span').textContent = label;
  c.querySelector('#term-chip-open').textContent = t('p414.chip.open');
  c.querySelector('#term-chip-end').textContent = t('p408.end');
  c.dataset.run = S.bang ? '1' : '0';
}
function wipe() {
  const out = $('term-out');
  out.firstChild.data = ''; out.lastChild.textContent = '';
  $('term-input').value = ''; $('term-sh-pw').value = '';
  S.hist.length = 0; S.draft = ''; S.prefill = '';
}

// ---------------------------------------------------------------- the sheet (one at a time; a later one waits)
let sheet = null;
const queue = [];
function openSheet(o) {
  if (sheet) { queue.push(o); return; }
  sheet = o;
  const w = $('term-sheetwrap');
  $('term-sh-origin').textContent = o.origin || ''; $('term-sh-origin').hidden = !o.origin;
  $('term-sh-title').textContent = o.title;
  $('term-sh-text').textContent = o.text || ''; $('term-sh-text').hidden = !o.text;
  $('term-sh-pre').textContent = o.pre || ''; $('term-sh-pre').parentElement.hidden = !o.pre;     // (the copy wrapper)
  $('term-sh-pre2l').textContent = o.pre2l || ''; $('term-sh-pre2l').hidden = !o.pre2;
  $('term-sh-pre2').textContent = o.pre2 || ''; $('term-sh-pre2').parentElement.hidden = !o.pre2;
  $('term-sh-warn').textContent = o.warn || ''; $('term-sh-warn').hidden = !o.warn;
  $('term-sh-bad').textContent = o.bad || ''; $('term-sh-bad').hidden = !o.bad;
  $('term-sh-note').textContent = o.note || ''; $('term-sh-note').hidden = !o.note;
  const pw = !!o.password;
  $('term-sh-pwlab').hidden = $('term-sh-pw').hidden = !pw; $('term-sh-pw').value = '';
  $('term-sh-pwlab').textContent = pw ? o.password : '';
  $('term-sh-yes').textContent = o.yes; $('term-sh-no').textContent = o.no;
  $('term-sh-yes').disabled = !!o.wait; $('term-sh-no').disabled = false;
  w.dataset.kind = o.kind; w.hidden = false;
  paintSheetPk(o);
  setTimeout(() => { try { (pw ? $('term-sh-pw') : $('term-sh-yes')).focus({ preventScroll: true }); } catch { /* old browsers */ } }, 40);
  if (o.ttl) o.timer = setTimeout(() => { if (sheet === o) { closeSheet(); o.onExpire?.(); } }, o.ttl);
}
function closeSheet() {
  if (!sheet) return;
  clearTimeout(sheet.timer);
  sheet = null;
  $('term-sh-pw').value = '';
  $('term-sheetwrap').hidden = true;
  const next = queue.shift();
  if (next) openSheet(next);
  else if (S.phase === 'ready' && S.reCollapse) collapse();   // P414: a card brought the collapsed page up; answered → down again
  else if (S.phase === 'ready') try { $('term-input').focus({ preventScroll: true }); } catch { /* old browsers */ }
}
/** P539: under the sheet's buttons — the quiet line when this remote has no passkey of its own (o.pk.fa = fa_s), or why the
 *  last tap failed + 再试一次 (sheetYes: the assertion starts again inside that tap). 在本机设置 only on the intro sheet. */
function paintSheetPk(o) {
  if (!root) return;
  paintPk($('term-sh-pk'), o && o.pk ? { fa: o.pk.fa, err: o.pk.err || null, retry: o.pk.err ? sheetYes : null, setup: o.kind === 'intro' } : {});
}
/** P539: a WebAuthn rejection on sheet `o` → said on the sheet (true); anything else → false (the caller toasts). */
function sheetPkFail(o, e) {
  const err = webauthnErr(e);
  if (!err || !o || sheet !== o) return false;
  o.pk = { ...(o.pk || {}), err };
  paintSheetPk(o);
  return true;
}
function sheetBusy(on) { if (!root) return; $('term-sh-yes').disabled = on; $('term-sh-no').disabled = on; }
/** The sheet's main button: runs synchronously inside the tap, so Face ID can start before any await. */
function sheetYes() { const o = sheet; if (o && !$('term-sh-yes').disabled) o.onYes(); }
function sheetNo() { const o = sheet; if (!o || $('term-sh-no').disabled) return; closeSheet(); o.onNo?.(); }

// ---------------------------------------------------------------- Face ID (+ PRF), signatures, the seal
/** WebAuthn get() over the host's challenge, started synchronously in the caller's tap. → {fa, prf: Uint8Array|null}.
 *  P414 (M3): with the host's next salt `vn` the PRF is evaluated twice (eval.second) and prf = prf1 ‖ prf2 (js/vault.js). */
function assertFa(chB64, credId, salt, vn = null) { return assertPrf(unb64u(chB64), credId, salt, salt ? vn : null); }
/** §8 control-v1 over the HOST's nonce. */
async function signCtl(action, obj, n) {
  const sk = await signKey();
  if (!sk) throw new Error('no_key');
  const ts = Date.now();
  const msg = await controlMessage(channel(), await myDeviceId(), action, n, ts, obj);
  return { n, ts, sig: b64u(new Uint8Array(await crypto.subtle.sign({ name: 'Ed25519' }, sk.priv, msg))) };
}
/** The PRF output sealed to the host's one-time key (§11 seal, kind term_prf, id = nonce = n); the bytes are zeroed. */
async function sealPrf(epk, prf, n, action, obj) {
  try {
    const digest = await sha256hex(enc.encode(controlObject(action, obj)));
    const s = await sealValue(unb64u(epk), prf, channel(), await myDeviceId(), n, 'term_prf', n, digest);
    return { pepk: b64u(s.epk), pct: b64u(s.ct) };
  } finally { prf.fill(0); }
}
const saltOf = salt32;
const epkOk = (v) => { try { return typeof v === 'string' && unb64u(v).length === 32; } catch { return false; } };
function noPasskey() {
  if (phoneConfirmation(true)) return;
  toastAction(t('p408.no.no_passkey'), t('sout.setup'), () => H.setupFaceId(), 8000);
}

// ---------------------------------------------------------------- open
/** termhook.open(): the page (and, when no terminal runs, the intro sheet). `prefill` goes into the input, never sent.
 *  P414: o.bang = {line, sid} — a 「!」 line from the message box: after the open the page collapses and the line runs;
 *  o.go = {cmd, h} — the Agent's term_go: the line goes into the input (never sent) with its note. */
function open(prefill, info, o = {}) {
  if (S.phase === 'ready') { show(); setInput(prefill); return true; }
  if (S.phase !== 'idle') { show(); if (o.go) S.go = o.go; return true; }
  if (phoneConfirmation()) return false;
  if (!canAssert()) { noPasskey(); return false; }
  S = fresh(); S.prefill = prefill || ''; S.minutes = info?.minutes || 15;
  build(); wipe(); S.prefill = prefill || '';
  S.origin = o.bang || o.go ? 'chat' : 'settings'; S.pendBang = o.bang || null; S.go = o.go || null;
  S.enable = !!o.enable && !o.bang && !o.go;
  if (S.origin === 'chat') H.goChat();
  S.phase = 'intro'; S.G = gen();
  paintChrome(); show();
  const text = o.bang ? t('p414.introBang', { line: o.bang.line.slice(0, 200), n: S.minutes }) : o.go ? t('p414.introGo', { n: S.minutes })
    : S.enable ? t('p417.intro', { n: S.minutes }) : t('p408.intro', { n: S.minutes });
  openSheet({ kind: 'intro', title: t('p408.title'), text, yes: t('p408.preparing'), no: t('sheet.cancel'), wait: true,
    onYes: introYes, onNo: () => { const b = S.pendBang; S.phase = 'idle'; abandon(); if (b) restore(b.line); } });
  fetchChallenge();
  return true;
}
function abandon() { if (S.r1) done(S.r1); if (S.r2) done(S.r2); S.phase = 'idle'; hideQuiet(); }
/** Cancelled before anything opened: leave without a 「已关闭」 line. */
function hideQuiet() { S.phase = 'idle'; hide(); }
function fetchChallenge() {
  S.ch = null;
  let timer = 0;
  const r = request(S.enable ? { t: 'term_open', mode: 'enable' } : { t: 'term_open' }, (m) => {
    if (m.t !== 'ctl_res' && m.t !== 'offline') return;
    clearTimeout(timer); done(r); if (S.r1 === r) S.r1 = null;
    if (S.phase !== 'intro') return;
    if (m.t === 'offline') return refuse('offline');
    if (m.ok === false && m.why === 'passkey_required' && HEX32.test(m.n || '') && typeof m.fa === 'string' && typeof m.fa_ch === 'string' && epkOk(m.epk)) {
      S.ch = { n: m.n, fa: m.fa, fa_s: faState(m.fa_s), fa_ch: m.fa_ch, salt: saltOf(m.salt), vn: saltOf(m.vn), epk: m.epk, at: Date.now() };
      if (sheet?.kind === 'intro') { $('term-sh-yes').textContent = t('p408.faYes'); $('term-sh-yes').disabled = false; sheet.pk = { ...(sheet.pk || {}), fa: S.ch.fa_s }; paintSheetPk(sheet); }
      return;
    }
    refuse(m.ok === false ? String(m.why || '') : 'shape', m);
  }, S.G);
  S.r1 = r;
  if (!r) { refuse('offline'); return; }
  timer = setTimeout(() => { done(r); if (S.phase === 'intro' && !S.ch) refuse('timeout'); }, 20000);
}
/** 「用 Face ID 确认」: the assertion starts here, inside the tap. */
function introYes() {
  const c = S.ch;
  if (!c) return;
  if (Date.now() - c.at > NONCE_MS) { sheetBusy(true); $('term-sh-yes').textContent = t('p408.preparing'); fetchChallenge(); return; }
  let p;
  try { p = assertFa(c.fa_ch, c.fa, c.salt, c.vn); } catch { p = Promise.reject(new Error('webauthn')); }
  sheetBusy(true);
  p.then(async ({ fa, prf }) => {
    try {   // P414 (R3-4): the PRF is zeroed even when signing throws before the seal
      if (S.phase !== 'intro') return;
      S.fa = c.fa; S.faS = c.fa_s;   // P539: the later sheets (confirm / sudo) say the same about this passkey
      const obj = { mode: S.enable ? 'enable' : 'line' };   // P417 #185: the signature names what it is for
      const signed = await signCtl('term_open', obj, c.n);
      const sealed = prf ? await sealPrf(c.epk, prf, c.n, 'term_open', obj) : {};
      closeSheet();
      S.phase = 'opening'; paintChrome(); status(t('p408.opening'));
      openStep2({ t: 'term_open', ...(S.enable ? { mode: 'enable' } : {}), ...signed, fa, ...sealed });
    } finally { prf?.fill(0); }
  }, (e) => {
    if (S.phase !== 'intro') return;
    sheetBusy(false);
    if (!sheetPkFail(sheet?.kind === 'intro' ? sheet : null, e)) toast(t('p408.faCancel'), 3600);
  }).catch(() => { if (S.phase === 'intro' || S.phase === 'opening') refuse('passkey_failed'); });
}
function openStep2(obj) {
  let timer = 0;
  const arm = (ms) => { clearTimeout(timer); timer = setTimeout(() => { done(r); if (S.phase === 'opening') refuse('timeout'); }, ms); };
  const r = request(obj, (m) => {
    if (S.r2 !== r) return;
    if (m.t === 'ctl_pending' && m.action === 'term_open') { arm(Number.isInteger(m.timeout_ms) && m.timeout_ms > 0 && m.timeout_ms <= 600000 ? m.timeout_ms : 180000); return; }
    if (m.t === 'term_ready') { clearTimeout(timer); done(r); ready(m); return; }
    if (m.t === 'ctl_res' || m.t === 'offline') { clearTimeout(timer); done(r); refuse(m.t === 'offline' ? 'offline' : String(m.why || 'shape'), m); }
  }, S.G);
  S.r2 = r;
  if (!r) { refuse('offline'); return; }
  arm(20000);
}
/** The open was refused (or the link went): say why in plain words, leave the page. no_passkey → the 「设置 Face ID」 offer. */
function refuse(why, m = {}) {
  if (S.phase !== 'intro' && S.phase !== 'opening') return;
  if (S.r1) done(S.r1); if (S.r2) done(S.r2);
  const b = S.pendBang;
  S.phase = 'idle';
  hideQuiet();
  if (b) restore(b.line);
  if (why === 'no_passkey') { noPasskey(); return; }
  const w = OPEN_WHY.includes(why) ? t('p408.no.' + why, { n: Math.max(1, Math.ceil((Number(m.secs) || 60) / 60)) }) : why;
  toast(t('p408.failed', { why: w }), 5000, 'error');
}
function ready(m) {
  if (S.phase !== 'opening' || typeof m.id !== 'string' || !HEX16.test(m.id) || !Number.isInteger(m.expires_at)) return refuse('shape');
  closeSheet();
  S.phase = 'ready'; S.id = m.id; S.expires = m.expires_at; S.seq = 0; S.atPrompt = true; S.login = null;
  status('');
  paintChrome();
  clearInterval(tick); tick = setInterval(onTick, 1000);
  if (typeof m.cwd === 'string' && m.cwd) out(t('p408.cwd', { cwd: m.cwd.slice(0, 300) }) + '\n');
  setInput(S.prefill); S.prefill = '';
  if (S.go) { const g = S.go; S.go = null; goInto(g); }
  const b = S.pendBang; S.pendBang = null;
  if (b) { collapse(); sendLine(b.line, b.sid); }   // P414: opened for a 「!」 line — it runs behind the chip
}
function setInput(v) {
  const i = $('term-input');
  if (v) i.value = String(v).replace(CTRL, ' ').slice(0, LINE_MAX);
  try { i.focus({ preventScroll: true }); i.setSelectionRange(i.value.length, i.value.length); } catch { /* old browsers */ }
}

// ---------------------------------------------------------------- the login card (this device's pending open only)
async function loginCard(m) {
  if (S.phase !== 'opening' || !S.r2 || m.term !== S.r2 || m.state === 'running') return;   // never a card for anything else
  if (typeof m.id !== 'string' || !HEX32.test(m.id) || !HEX32.test(m.n || '') || !epkOk(m.epk)) return;
  const c = { id: m.id, n: m.n, epk: unb64u(m.epk), tries: Number.isInteger(m.tries) ? m.tries : 3, bad: Number.isInteger(m.bad) ? m.bad : 0,
    ttl: Number.isInteger(m.ttl) && m.ttl > 0 && m.ttl <= 600 ? m.ttl : 120, fa: typeof m.fa === 'string' && m.fa.length <= 1400 ? m.fa : null, faCh: null,
    fa_s: faState(m.fa_s) };   // P539
  c.digest = await elevateDigest('login', elevateFields({ kind: 'login', term: m.term }));
  if (c.fa) c.faCh = await elevateChallenge(channel(), await myDeviceId(), c.id, 'login', c.n, c.digest).catch(() => null);
  if (S.phase !== 'opening') return;
  if (sheet?.kind === 'login') closeSheet();
  S.login = c;
  openSheet({ kind: 'login', origin: t('p408.loginFrom'), title: t('p408.loginTitle'), password: t('p408.loginLabel'),
    bad: c.bad ? t('p408.pwBad', { n: c.tries }) : '', note: t('p408.pwNote'), yes: t(c.fa ? 'p408.pwSend' : 'p408.pwSendPlain'), no: t('sheet.cancel'),
    ttl: c.ttl * 1000, pk: c.fa ? { fa: c.fa_s } : null, onYes: () => loginAnswer(c, true), onNo: () => loginAnswer(c, false) });
}
/** Answered exactly like a §11 sudo card: Face ID first (inside the tap), then sealed + signed `elev_answer`. */
function loginAnswer(c, ok) {
  if (S.login !== c) return;
  const pw = $('term-sh-pw');
  if (ok && !pw.value) { $('term-sh-bad').textContent = t('p408.pwEmpty'); $('term-sh-bad').hidden = false; return; }
  let p = Promise.resolve(null);
  if (ok && c.fa) {
    if (!canAssert() || !c.faCh) { toast(t('p408.faCancel'), 3600); return; }
    try { p = approve(c.faCh, c.fa); } catch { p = Promise.reject(new Error('webauthn')); }
  }
  const value = ok ? enc.encode(pw.value) : null;
  pw.value = '';
  if (ok) sheetBusy(true);
  p.then(async (fa) => {
    try {
      if (ok && c.fa && !fa) throw new Error('fa');
      const sk = await signKey();
      if (!sk) throw new Error('no_key');
      const ch = channel(), dev = await myDeviceId(), ts = Date.now();
      const msg = { t: 'elev_answer', id: c.id, ok, n: c.n, ts };
      let ctSha = null;
      if (ok) { const s = await sealValue(c.epk, value, ch, dev, c.id, 'login', c.n, c.digest); msg.epk = b64u(s.epk); msg.ct = b64u(s.ct); ctSha = s.ctSha; }
      msg.sig = b64u(new Uint8Array(await crypto.subtle.sign({ name: 'Ed25519' }, sk.priv, elevateMessage(ch, dev, c.id, 'login', ok ? 'allow' : 'deny', c.n, ts, c.digest, ctSha))));
      if (fa) msg.fa = fa;
      await sendApp(msg, S.G);
      if (sheet?.kind === 'login') closeSheet();
      if (ok) status(t('p408.checking'));
    } finally { value?.fill(0); }
  }).catch((e) => {
    if (S.login !== c) return;
    sheetBusy(false);
    if (!sheetPkFail(sheet?.kind === 'login' ? sheet : null, e)) toast(t('p408.faCancel'), 3600);
  });
}

// ---------------------------------------------------------------- lines, keys, history
/** The input line — or (P414, §37.12) a 「!」 line from the message box with its say sid (`bang`), which leaves the input alone. */
function sendLine(given, bang) {
  if (S.phase !== 'ready' || (sheet && !bang)) return false;
  const i = $('term-input'), line = bang ? given : i.value;
  if (CTRL.test(line)) return badLine('p408.bad');
  if (UNSAFE.test(line)) return badLine('p414.cf');   // P414 (H1c): bidi / zero-width / format characters, > 8 spaces
  if (enc.encode(line).length > LINE_MAX) return badLine('p408.long');
  const seq = ++S.seq;
  S.pre = { seq, was: S.atPrompt };   // P414 (R3-1): a line the host did not run gives the prompt state back (notRun)
  send({ t: 'term_line', id: S.id, seq, line, ...(bang ? { bang } : {}) });
  echo(line);
  if (line) { if (S.hist[S.hist.length - 1] !== line) S.hist.push(line); if (S.hist.length > 200) S.hist.shift(); }
  if (bang) { clearTimeout(S.bang?.timer); S.bang = { sid: bang, seq, line, timer: setTimeout(() => bangEnd(bang), BANG_MS) }; paintChip(); }
  else { S.hi = -1; S.draft = ''; i.value = ''; S.go = null; paintGo(); }
  S.atPrompt = false; paintProg();
  return true;
}
/** P414 (R3-1): line `seq` was not run (a cancelled / expired confirm, a `running` refusal) — the shell is where it was. */
function notRun(seq) { if (S.pre && S.pre.seq === seq && !S.atPrompt) { S.atPrompt = S.pre.was; paintProg(); } }
function badLine(key) {
  $('term-bad').textContent = t(key); $('term-bad').hidden = false;
  if (S.view === 'collapsed') toast(t(key), 5000, 'error');
  return false;
}
function sendKey(key) { if (S.phase === 'ready' && Object.hasOwn(KEYS, key)) send({ t: 'term_keys', id: S.id, key }); }
function send(obj) { sendApp(obj, S.G).catch(() => dropped()); }
function histStep(d) {
  if (S.phase !== 'ready' || !S.hist.length) return;
  const i = $('term-input');
  if (S.hi === -1) { if (d > 0) return; S.draft = i.value; S.hi = S.hist.length; }
  S.hi = Math.max(0, S.hi + d);
  if (S.hi >= S.hist.length) { S.hi = -1; i.value = S.draft; } else i.value = S.hist[S.hi];
  setInput('');
}
function insert(c) {
  const i = $('term-input');
  if (i.disabled) return;
  const a = i.selectionStart ?? i.value.length, b = i.selectionEnd ?? a;
  i.value = i.value.slice(0, a) + c + i.value.slice(b);
  try { i.focus({ preventScroll: true }); i.setSelectionRange(a + c.length, a + c.length); } catch { /* old browsers */ }
}
function paintProg() { if (root) $('term-prog').hidden = !(S.phase === 'ready' && !S.atPrompt && $('term-input').value.length > 0); }

// ---------------------------------------------------------------- P414: 「!」 lines (§37.12) and the Agent's term_go (§37.13)
/** termhook.bang(): a 「!」 line → true once it is sent (or the open flow for it started; the field is cleared). */
function bang(line, sid, info) {
  if (S.phase === 'ready') {
    if (S.bang) { toast(t('p414.bangBusy'), 4000); return false; }
    // P414 (R3-1): a program is reading input — the line is never sent (it would be the program's input, e.g. a password);
    // the owner answers it in the full-screen terminal
    if (!S.atPrompt) { programWaits(); return false; }
    return sendLine(line, sid);
  }
  if (S.phase !== 'idle') { toast(t('p414.opening'), 3000); return false; }
  return open('', info, { bang: { line, sid } });
}
/** P414 (R3-1): the terminal full screen + 「a program is waiting for input; answer it there」. */
function programWaits() { expand(); toast(t('p414.bangProgram'), 6000); }
function bangEnd(sid) { if (S.bang && S.bang.sid === sid) { clearTimeout(S.bang.timer); S.bang = null; paintChip(); } }
/** Put a 「!」 line that did not run back into the message box (offered, never forced over what is there). */
function restore(line) { toastAction(t('p414.notRun'), t('p414.putBack'), () => H.insert('!' + line), 8000); }
function onBang(m) {
  if (typeof m.sid !== 'string' || !S.bang || m.sid !== S.bang.sid) return;
  const b = S.bang;
  bangEnd(m.sid);
  if (m.ok !== false) return;
  // §37.12 (host build): `failed` = the line DID run (its output is on this page) but its chat page could not be written —
  // never offered back for a second run; `running` (another 「!」 still running) = it did not run
  if (m.why === 'failed') { toast(t('p414.bangFailed'), 5000, 'error'); return; }
  if (m.why === 'program') { programWaits(); return; }   // P414 (R3-1): not written, not sent to the Agent
  if (m.why === 'running') { notRun(b.seq); toast(t('p414.bangRunning'), 4000); }
  restore(b.line);
}
/** term_go {cmd?, h}: the terminal full screen; a closed one still needs Face ID to open. The line is never sent. */
function go(m, info) {
  const cmd = typeof m.cmd === 'string' ? m.cmd.slice(0, LINE_MAX) : '';
  if (cmd && (CTRL.test(cmd) || UNSAFE.test(cmd))) return;           // H1c: the host refuses these too
  const g = cmd ? { cmd, h: typeof m.h === 'string' ? m.h.slice(0, 64) : '' } : null;
  if (S.phase === 'idle') { open('', info, { go: g || { cmd: '', h: '' } }); return; }
  if (S.phase !== 'ready') { show(); if (g) S.go = g; return; }
  expand();
  if (g) goInto(g);
}
/** Empty input → the Agent's line goes in (not sent) with its note; the owner's own words stay → a chip to tap it in (H1b). */
function goInto(g) {
  if (!g || !g.cmd) return;
  const i = $('term-input');
  if (!i.value) { i.value = g.cmd; S.go = null; S.goNote = true; setInput(''); }
  else S.go = g;
  paintGo();
}
function takeGo() {
  const g = S.go;
  if (!g) return;
  const i = $('term-input');
  if (i.value && S.hist[S.hist.length - 1] !== i.value) S.hist.push(i.value);   // the owner's words stay reachable with ↑
  i.value = g.cmd; S.go = null; S.goNote = true;
  setInput(''); paintGo();
}
function paintGo() {
  if (!root) return;
  const c = $('term-gochip'), g = S.go;
  c.hidden = !g || S.phase !== 'ready';
  c.textContent = g ? t('p414.goChip', { cmd: g.cmd.slice(0, 200) }) : '';
  $('term-gonote').textContent = t('p414.goNote');
  $('term-gonote').hidden = !(S.goNote && $('term-input').value) || S.phase !== 'ready';
}

// ---------------------------------------------------------------- P414: 复制输出 · 截屏发到聊天
const outText = () => { const pre = $('term-out'); return pre.firstChild.data + pre.lastChild.textContent; };
async function copyOut() {
  const ok = await copyNow(outText()).catch(() => false);
  toast(t(ok ? 'p414.copied' : 'p414.copyFail'), 2600, ok ? 'success' : 'error');
}
/** The visible part of the output, drawn as it is shown (Noir ground, Lotion monospace), as a PNG in the message box's
 *  attachments; then the page collapses. Never sent by itself. */
async function shot() {
  const pre = $('term-out'), cs = getComputedStyle(pre);
  const W = pre.clientWidth, Hh = pre.clientHeight, dpr = Math.min(2, globalThis.devicePixelRatio || 1);
  if (!W || !Hh) return;
  const c = document.createElement('canvas');
  c.width = Math.round(W * dpr); c.height = Math.round(Hh * dpr);
  const g = c.getContext('2d');
  g.scale(dpr, dpr);
  g.fillStyle = '#2c2b26'; g.fillRect(0, 0, W, Hh);
  const fs = parseFloat(cs.fontSize) || 14, lh = parseFloat(cs.lineHeight) || fs * 1.5;
  const pl = parseFloat(cs.paddingLeft) || 16, pr = parseFloat(cs.paddingRight) || 16, pt = parseFloat(cs.paddingTop) || 12;
  g.font = `${cs.fontWeight || 400} ${fs}px ${cs.fontFamily}`; g.fillStyle = '#faf9f5'; g.textBaseline = 'middle';
  const rows = wrapRows(g, outText(), W - pl - pr);
  const first = Math.max(0, Math.floor((pre.scrollTop - pt) / lh)), n = Math.ceil(Hh / lh) + 1;
  rows.slice(first, first + n).forEach((r, k) => g.fillText(r, pl, pt + (first + k) * lh - pre.scrollTop + lh / 2));
  const blob = await new Promise((res) => c.toBlob(res, 'image/png'));
  c.width = c.height = 0;
  if (!blob) throw new Error('png');
  const d = new Date(), z = (x) => String(x).padStart(2, '0');
  const file = new File([blob], `terminal-${d.getFullYear()}${z(d.getMonth() + 1)}${z(d.getDate())}-${z(d.getHours())}${z(d.getMinutes())}${z(d.getSeconds())}.png`, { type: 'image/png' });
  const r = await H.attach(file);
  if (r && r.added === false) return;      // the message box said why (too many files)
  collapse();
  toast(t('p414.shotAdded'), 3600, 'success');
}
/** The output's rows as the page wraps them (pre-wrap, break anywhere), measured with the canvas font. */
export function wrapRows(g, text, maxW) {
  const rows = [], w = new Map();
  const cw = (ch) => { let x = w.get(ch); if (x === undefined) { x = g.measureText(ch).width; w.set(ch, x); } return x; };
  for (const line of String(text).split('\n')) {
    let row = '', x = 0;
    for (const ch of line) { const k = cw(ch); if (x + k > maxW && row) { rows.push(row); row = ''; x = 0; } row += ch; x += k; }
    rows.push(row);
  }
  return rows;
}

// ---------------------------------------------------------------- output: text only, \r / \b handled, memory only
let col = 0;
function out(data) {
  const pre = $('term-out'), done_ = pre.firstChild, cur = pre.lastChild;
  const near = pre.scrollHeight - pre.scrollTop - pre.clientHeight < 48;
  let line = cur.textContent;
  const s = String(data).replace(/\x1b\[[0-9;?]*[ -/]*[@-~]|\x1b\][^\x07\x1b]*(?:\x07|\x1b\\)|\x1b[@-_]/g, '');
  let flushed = '';
  for (const ch of s) {
    if (ch === '\n') { flushed += line + '\n'; line = ''; col = 0; }
    else if (ch === '\r') col = 0;
    else if (ch === '\b') col = Math.max(0, col - 1);
    else if (ch === '\t') { const n = 8 - (col % 8); line = line.slice(0, col) + ' '.repeat(n) + line.slice(col + n); col += n; }
    else if (CTRL.test(ch)) continue;
    else { line = line.slice(0, col) + ch + line.slice(col + ch.length); col += ch.length; }
  }
  if (flushed) done_.appendData(flushed);
  if (done_.length > KEEP_CHARS) done_.deleteData(0, done_.length - KEEP_CHARS * 0.8);
  cur.textContent = line;
  if (near) pre.scrollTop = pre.scrollHeight;
}
function echo(line) {
  const pre = $('term-out');
  col = pre.lastChild.textContent.length;
  out(line + '\n');
  pre.scrollTop = pre.scrollHeight;
}

// ---------------------------------------------------------------- host messages
function onMsg(m) {
  if (!m || typeof m.t !== 'string') return;
  if (m.t === 'elev' && m.kind === 'login') { loginCard(m).catch(() => {}); return; }
  if (m.t === 'elev_done' && S.login && m.id === S.login.id) { if (sheet?.kind === 'login') closeSheet(); if (m.result !== 'done' && m.result !== 'saved') S.login = null; return; }
  if (m.t === 'elev_refused' && S.login && m.id === S.login.id) { sheetBusy(false); toast(t('p408.faRefused'), 3600); return; }
  if (S.phase !== 'ready' || m.id !== S.id) {
    if (m.t === 'term_close' && S.phase === 'opening') refuse(CLOSE_WHY.includes(m.why) ? m.why : 'host');
    return;
  }
  if (m.t === 'term_out') { if (typeof m.data === 'string') out(m.data.slice(0, 65536)); if (m.prompt === true) { S.atPrompt = true; paintProg(); } return; }
  if (m.t === 'term_bang') return onBang(m);                               // P414 (§37.12)
  if (m.t === 'term_confirm') { expand(true); return confirmLine(m); }      // P414: a card that needs the owner brings the page up
  if (m.t === 'term_sudo') { expand(true); return sudoAsk(m); }
  if (m.t === 'term_close') return closed(CLOSE_WHY.includes(m.why) ? m.why : 'host');
}
function confirmLine(m) {
  // P573: `uv` 1 = Face ID, 0 = the button alone (the signed line still goes); no `uv` (an older host) = Face ID as before
  const uv = m.uv === 0 ? 0 : 1;
  if (typeof m.line !== 'string' || !HEX32.test(m.n || '') || !Number.isInteger(m.seq) || (uv && (typeof m.fa_ch !== 'string' || !S.fa))) return;
  const why = (lang() === 'en' && typeof m.why_en === 'string' ? m.why_en : typeof m.why === 'string' ? m.why : '').slice(0, 400);
  const line = m.line.slice(0, LINE_MAX);
  // P414 (H1): a line the Agent filled in and the owner sent unedited — always this card, whatever the classifier said
  const agent = m.agent === 1 || m.agent === true;
  const bangSid = S.bang && S.bang.seq === m.seq ? S.bang.sid : null;   // §37.12: the signed resend keeps `bang`
  openSheet({ kind: agent ? 'agent' : 'confirm', title: t(agent ? 'p414.agentTitle' : 'p408.confirmTitle'), pre: line,
    text: agent ? t('p414.agentText') : why ? t('p408.confirmWhy', { why }) : '', yes: t('p408.run'), no: t('sheet.cancel'),
    ttl: 120000, pk: uv ? { fa: S.faS || null } : null, onExpire: () => { out(t('p408.expired') + '\n'); notRun(m.seq); if (bangSid) bangEnd(bangSid); },
    onNo: () => { out(t('p408.notRun') + '\n'); notRun(m.seq); if (bangSid) { bangEnd(bangSid); restore(line); } },
    onYes: () => {
      let p;
      if (!uv) p = Promise.resolve({ fa: undefined });
      else try { p = assertFa(m.fa_ch, S.fa, null); } catch { p = Promise.reject(new Error('webauthn')); }
      sheetBusy(true);
      p.then(async ({ fa }) => {
        const signed = await signCtl('term_line', { id: S.id, line }, m.n);
        send({ t: 'term_line', id: S.id, seq: m.seq, line, ...signed, ...(fa ? { fa } : {}), ...(bangSid ? { bang: bangSid } : {}) });
        // §37.12: the host's 120 s for a 「!」 line start when it runs (now), not when it was first sent
        if (bangSid && S.bang && S.bang.sid === bangSid) { clearTimeout(S.bang.timer); S.bang.timer = setTimeout(() => bangEnd(bangSid), BANG_MS); }
        closeSheet();
      }).catch((e) => { sheetBusy(false); if (e?.name === 'Error' || !sheetPkFail(sheet?.kind === 'confirm' || sheet?.kind === 'agent' ? sheet : null, e)) toast(t('p408.faCancel'), 3600); });   // P539
    } });
}
function sudoAsk(m) {
  if (!Array.isArray(m.argv) || !m.argv.every((a) => typeof a === 'string') || typeof m.rid !== 'string' || !HEX32.test(m.n || '') || typeof m.fa_ch !== 'string' || !S.fa) return;
  const argv = m.argv.slice(0, 256).join(' ').slice(0, 4000), line = typeof m.line === 'string' ? m.line.slice(0, LINE_MAX) : '';
  const salt = saltOf(m.salt), vn = saltOf(m.vn), epk = epkOk(m.epk) ? m.epk : null, rid = m.rid.slice(0, 64);
  openSheet({ kind: 'sudo', title: t('p408.sudoTitle'), text: t('p408.sudoCmd'), pre: argv, pre2l: t('p408.sudoLine'), pre2: line,
    warn: m.differs === true ? t('p408.differs') : '', yes: t('p408.sudoYes'), no: t('p408.sudoNo'), ttl: 60000, pk: { fa: S.faS || null },
    onNo: () => send({ t: 'term_sudo_no', id: S.id, rid }),
    onYes: () => {
      let p;
      try { p = assertFa(m.fa_ch, S.fa, salt, vn); } catch { p = Promise.reject(new Error('webauthn')); }
      sheetBusy(true);
      p.then(async ({ fa, prf }) => {
        try {   // P414 (R3-4): the PRF is zeroed even when signing throws before the seal
          const obj = { id: S.id, rid };
          const signed = await signCtl('term_sudo', obj, m.n);
          const sealed = prf && epk ? await sealPrf(epk, prf, m.n, 'term_sudo', obj) : {};
          send({ t: 'term_sudo_ok', id: S.id, rid, ...signed, fa, ...sealed });
          closeSheet();
        } finally { prf?.fill(0); }
      }).catch((e) => { sheetBusy(false); if (e?.name === 'Error' || !sheetPkFail(sheet?.kind === 'sudo' ? sheet : null, e)) toast(t('p408.faCancel'), 3600); });   // P539
    } });
}

// ---------------------------------------------------------------- end, close, dropped, background
function askEnd() {
  if (S.phase === 'closed') return hide();
  if (S.phase !== 'ready') { if (sheet) sheetNo(); else abandon(); return; }
  openSheet({ kind: 'end', title: t('p408.endTitle'), text: t('p408.endText'), yes: t('p408.end'), no: t('p408.keepOn'), onYes: () => { closeSheet(); endNow(); } });
}
function endNow() {
  if (S.phase !== 'ready') { abandon(); return; }
  send({ t: 'term_close', id: S.id });
  closed('owner');
}
function closed(why) {
  if (S.phase !== 'ready') return;
  S.phase = 'closed';
  queue.length = 0; closeSheet();
  clearInterval(tick); tick = 0;
  clearTimeout(S.bang?.timer); S.bang = null;
  paintChrome();
  status(t('p408.closed', { why: t('p408.why.' + why) }));
  wipe();
  if (S.view === 'collapsed') { toast(t('p408.closed', { why: t('p408.why.' + why) }), 4000); hide(); return; }   // P414: no page to show it on
  clearTimeout(closeTimer); closeTimer = setTimeout(() => { if (S.phase === 'closed' && root && !root.hidden) hide(); }, CLOSE_SHOW_MS);
}
/** The Noise session this terminal rode is gone: never pretend it is still connected. */
function dropped() {
  if (S.phase === 'intro' || S.phase === 'opening') return refuse('offline');
  if (S.phase !== 'ready') return;
  S.phase = 'closed';
  queue.length = 0; closeSheet();
  clearInterval(tick); tick = 0;
  clearTimeout(S.bang?.timer); S.bang = null;
  if (S.view === 'collapsed') { wipe(); toast(t('p408.dropped'), 5000, 'error'); hide(); return; }   // P414
  paintChrome();
  status(t('p408.dropped'));
  $('term-stacts').hidden = false;
  wipe();
}
function onTick() {
  if (S.phase !== 'ready') return;
  if (gen() !== S.G) return dropped();
  paintLeft(); paintChip();
}
document.addEventListener('visibilitychange', () => {
  if (S.phase !== 'ready') return;
  clearTimeout(hideTimer);
  if (document.visibilityState === 'hidden') {
    S.hiddenAt = Date.now();
    hideTimer = setTimeout(() => { if (S.phase === 'ready') { send({ t: 'term_close', id: S.id, why: 'background' }); closed('background'); } }, HIDDEN_MS);
  } else if (S.hiddenAt && Date.now() - S.hiddenAt >= HIDDEN_MS) {   // a frozen timer (iOS): the minute passed anyway
    send({ t: 'term_close', id: S.id, why: 'background' }); closed('background');
  } else S.hiddenAt = 0;
});

// ---------------------------------------------------------------- chrome
function status(text) { const s = $('term-status'); s.textContent = text; s.hidden = !text; if (!text) $('term-stacts').hidden = true; }
function paintChrome() {
  const live = S.phase === 'ready';
  $('term-input').disabled = !live; $('term-send').disabled = !live;
  for (const b of $('term-keys').querySelectorAll('button')) b.disabled = !live;
  $('term-log').hidden = !live;
  $('term-end').hidden = S.phase === 'closed';
  $('term-form').hidden = $('term-keys').hidden = $('term-nopw').hidden = S.phase === 'closed';   // closed: only why + where to go
  root.dataset.phase = S.phase;
  $('term-tools').hidden = S.phase !== 'ready';
  $('term-exit').hidden = $('term-grab').hidden = S.phase !== 'ready';
  paintLeft(); paintProg(); paintGo(); paintChip();
}
function paintLeft() {
  if (!root || S.phase !== 'ready') return;
  const s = Math.max(0, Math.round(S.expires - Date.now() / 1000));
  $('term-left').textContent = t('p408.left', { t: `${Math.floor(s / 60)}:${String(s % 60).padStart(2, '0')}` });
}

register({
  configure(h) { H = { ...H, ...h }; },
  open, onMsg, bang, go,
  gone: () => dropped(),
  isOpen: () => S.phase === 'ready',
  _debug: () => ({ phase: S.phase, id: S.id, seq: S.seq, hist: [...S.hist], atPrompt: S.atPrompt, login: !!S.login, sheet: sheet && sheet.kind, queued: queue.length,
    view: S.view, bang: S.bang && S.bang.sid, go: !!S.go }),
});
