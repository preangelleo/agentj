// P412 (#174, Leo 10-20: 「全屏的文本输入编辑器……足够专业……深色 Noir 底、Lotion 白字……鼠标和快捷键都能用」; 定稿 18:44):
// the message box, full screen. Opened by the tool row's fifth button, Ctrl+G, or a swipe up on the message box (phone).
// Top left: exit full screen (Esc, Ctrl+G again, a double click / double tap on blank space — on words it still selects, as
// in the reader — or the grabber in the middle of the header pulled down); top right: clear (5-second undo); bottom left: the Peachy 「/」 — a command goes in at the caret; bottom right:
// Send (⌘ / Ctrl+Enter; Enter is a new line). Icons only, no lines between header / body / footer. The words ARE the
// message box's words (every keystroke is copied back), so leaving keeps them and a draft is saved as usual. With a soft
// keyboard up, visualViewport keeps the whole editor above it.
import { termKey, edit } from './termkeys.js';
import { registerLayer } from './layers.js';
import { DOUBLE_MS, PAIR_PX } from './file-gestures.js';

let composer = null, options = null, opener = null, saving = false, editorToken = 0;
let cfg = null, root = null, ta = null, pos = null, cmds = null, cmdQ = null, cmdList = null;
let cmdAt = 0, cmdShown = [], tap = null, mirror = null;
const $ = (id) => document.getElementById(id);
export const isOpen = () => !!root && !root.hidden;
const cmdsOpen = () => !!cmds && !cmds.hidden;

function push() {
  if (options) return; // settings keep a private draft until Save
  const box = cfg.input;
  if (box.value === ta.value) return;
  box.value = ta.value;
  box.dispatchEvent(new Event('input'));   // the box's own path: the cap, the draft, the Send button, 「N 行」
  if (box.value !== ta.value) { const at = ta.selectionStart; ta.value = box.value; ta.setSelectionRange(at, at); }
}
function paintPos() {
  if (!isOpen()) return;
  const v = ta.value, at = ta.selectionStart || 0;
  pos.textContent = cfg.t('p412.pos', { line: v.slice(0, at).split('\n').length, lines: v.split('\n').length, chars: [...v].length });
}
// iOS keyboard animation can publish its final dimensions after the last event.
// Read the latest viewport on each frame for a bounded settling interval, never a saved event value.
let fitFrame = 0, settleUntil = 0;
const ios = () => document.documentElement.dataset.ios === '1';
function fitIOS() {
  fitFrame = 0;
  if (!isOpen()) return;
  const vv = window.visualViewport;
  // Follow even the first keyboard-animation pixels; 1px only tolerates fractional viewport rounding.
  const keyboard = vv && window.innerHeight - vv.height > 1;
  if (keyboard) {
    root.style.top = vv.offsetTop + 'px'; root.style.left = vv.offsetLeft + 'px';
    root.style.width = vv.width + 'px'; root.style.height = vv.height + 'px';
    root.style.setProperty('--fs-safe-bottom', '0px');
  } else {
    for (const p of ['top', 'left', 'width', 'height', '--fs-safe-bottom']) root.style.removeProperty(p);
  }
  if (performance.now() < settleUntil) fitFrame = requestAnimationFrame(fitIOS);
}
function fit() {
  if (ios()) {
    settleUntil = performance.now() + 400;
    if (!fitFrame && isOpen()) fitIOS();
    return;
  }
  const vv = window.visualViewport;
  if (!isOpen()) return;
  if (!vv) { root.style.cssText = ''; return; }
  root.style.top = vv.offsetTop + 'px'; root.style.left = vv.offsetLeft + 'px';
  root.style.width = vv.width + 'px'; root.style.height = vv.height + 'px';
}

export function open() {
  if (isOpen()) return;
  cfg = composer; options = null;
  restoreButtons();
  show();
}
function show() {
  if (!cfg || isOpen()) return;
  const box = cfg.input, typing = document.activeElement === box;
  const a = box.selectionStart ?? box.value.length, b = box.selectionEnd ?? a;
  ta.maxLength = cfg.maxText();
  ta.value = box.value;
  root.hidden = false; drag = null; editorToken++;
  document.body.dataset.fs = '1';
  fit();
  // a touch owner's keyboard is not raised by script unless it was already up (P375); a mouse / keyboard owner types at once
  if (typing || !cfg.touch()) { ta.focus({ preventScroll: true }); ta.setSelectionRange(a, b); }
  else ta.setSelectionRange(a, b);
  paintPos();
}
export function close() {
  if (!isOpen() || saving) return;
  closeCmds(false);
  push();
  const a = ta.selectionStart, b = ta.selectionEnd;
  root.hidden = true;
  cancelAnimationFrame(fitFrame); fitFrame = 0;
  root.style.cssText = '';
  delete document.body.dataset.fs;
  try { cfg.input.setSelectionRange(a, b); } catch { /* hidden box */ }
  cfg.input.dispatchEvent(new Event('input'));
  const back = opener; opener = null; options = null; cfg = composer; restoreButtons();
  if (back?.isConnected) back.focus({preventScroll:true});
}
export function forget() { editorToken++; if (ta) ta.value = ''; if (isOpen()) { root.hidden = true; delete document.body.dataset.fs; } options=null;opener=null;cfg=composer;saving=false;restoreButtons(); }

async function send() {
  if (saving) return;
  if (!options) { push(); close(); cfg.send(); return; }
  const token=editorToken;
  saving = true; $('fsSend').disabled = true;
  try { if (await options.save(ta.value) !== false && token===editorToken) { saving = false; close(); } }
  catch { if(token===editorToken)cfg.tui(cfg.t('p537.failed')); }
  finally { if(token===editorToken){saving = false; $('fsSend').disabled = false;} }
}
function clearAll() {
  const v = ta.value;
  if (!v) return;
  ta.value = ''; push(); paintPos();
  cfg.toastAction(cfg.t('p412.cleared'), cfg.t('p412.undo'), () => {
    const box = isOpen() ? ta : cfg.input;
    box.value = (v + box.value).slice(0, cfg.maxText());
    if (isOpen()) { push(); ta.focus({ preventScroll: true }); ta.setSelectionRange(ta.value.length, ta.value.length); paintPos(); }
    else box.dispatchEvent(new Event('input'));
  }, 5000);
}

// ---- 「/」: the same list as the message box's (Agent J's one-tap commands, the menu, skills, the harness's own with its route)
function matches(q) {
  q = q.trim().replace(/^[/／]/, '').toLowerCase();
  const pre = [], sub = [], seen = new Set();
  for (const it of cfg.items()) {
    if (seen.has(it[0])) continue;
    const name = it[0].slice(1).toLowerCase();
    if (name.startsWith(q)) { pre.push(it); seen.add(it[0]); }
    else if (q && (name.includes(q) || (it[1] || '').toLowerCase().includes(q))) { sub.push(it); seen.add(it[0]); }
  }
  return pre.concat(sub);
}
function paintCmds() {
  cmdShown = matches(cmdQ.value);
  cmdAt = Math.min(cmdAt, Math.max(0, cmdShown.length - 1));
  cmdList.replaceChildren(...cmdShown.map(([cmd, why, , st], i) => {
    const b = document.createElement('button');
    b.type = 'button'; b.tabIndex = -1; b.dataset.i = String(i); b.id = 'fsCmd' + i;
    b.setAttribute('role', 'option'); b.setAttribute('aria-selected', i === cmdAt ? 'true' : 'false');
    if (st === 'none') { b.classList.add('tui'); b.setAttribute('aria-disabled', 'true'); }
    const n = document.createElement('b'); n.textContent = cmd;
    if (st === 'run' || st === 'agent') { const i = document.createElement('i'); i.className = 'ntag'; i.dataset.via = st; i.textContent = cfg.t(st === 'run' ? 'p412.viaRun' : 'p412.viaAgent'); n.append(i); }
    const d = document.createElement('span'); d.textContent = why || '';
    b.append(n, d);
    return b;
  }));
  const on = cmdList.children[cmdAt];
  if (on) { cmdQ.setAttribute('aria-activedescendant', on.id); on.scrollIntoView({ block: 'nearest' }); }
  else cmdQ.removeAttribute('aria-activedescendant');
}
function openCmds() {
  if (cmdsOpen()) { closeCmds(true); return; }
  cmdQ.value = ''; cmdAt = 0;
  cmds.hidden = false;
  paintCmds();
  if (!cfg.touch()) cmdQ.focus({ preventScroll: true });
}
function closeCmds(back = true) {
  if (!cmdsOpen()) return;
  cmds.hidden = true;
  if (back && isOpen() && !cfg.touch()) ta.focus({ preventScroll: true });
}
function pick(i) {
  const it = cmdShown[i];
  if (!it) return;
  if (it[3] === 'none') { cfg.tui((it[1] || '').split(' · ').slice(1).join(' · ') || it[1]); return; }
  const a = ta.selectionStart, b = ta.selectionEnd, v = ta.value;
  const pre = a > 0 && !/\s$/.test(v.slice(0, a)) ? ' ' : '';
  closeCmds(false);
  ta.focus({ preventScroll: true });
  edit(ta, a, b, pre + it[0] + ' ');
  push(); paintPos();
}

// ---- #174 补 2 (Leo 19:18: 「header 中间加一条拖动横杠，按住往下拉即收起」): the editor follows the finger / mouse down;
// let go past GRAB_FAR of its height (or flung down) and it leaves, short of that it springs back
export const GRAB_FAR = 0.22, GRAB_MAX_PX = 160, GRAB_FLING = 0.6;   // share of the height · px cap · px/ms
const GRAB_MS = 200;
let drag = null;
function grabDown(e) {
  if (e.button || !isOpen()) return;
  e.preventDefault();
  try { e.currentTarget.setPointerCapture(e.pointerId); } catch { /* synthetic */ }
  const now = performance.now();
  drag = { id: e.pointerId, y: e.clientY, dy: 0, ly: e.clientY, lt: now, v: 0, moved: false };
  root.style.transition = 'none';
  root.classList.add('drag');
}
function grabMove(e) {
  if (!drag || e.pointerId !== drag.id) return;
  const now = performance.now(), dy = Math.max(0, e.clientY - drag.y);
  if (now > drag.lt) drag.v = (e.clientY - drag.ly) / (now - drag.lt);
  drag.ly = e.clientY; drag.lt = now; drag.dy = dy;
  if (dy > 4) drag.moved = true;
  root.style.transform = `translateY(${dy}px)`;
}
function grabUp(e) {
  if (!drag || e.pointerId !== drag.id) return;
  const d = drag, h = root.getBoundingClientRect().height;
  drag = null;
  root.classList.remove('drag');
  const far = e.type !== 'pointercancel' && (d.dy >= Math.min(GRAB_MAX_PX, h * GRAB_FAR) || (d.v >= GRAB_FLING && d.dy > 24));
  root.style.transition = `transform ${GRAB_MS}ms ease-out`;
  root.style.transform = far ? `translateY(${h}px)` : 'translateY(0px)';
  setTimeout(() => {
    if (far) close();
    else { root.style.removeProperty('transition'); root.style.removeProperty('transform'); }
  }, GRAB_MS);
}

// ---- a double click / double tap on blank space (not on words) leaves full screen
function onText(x, y) {
  const v = ta.value;
  if (!v) return false;
  const cs = getComputedStyle(ta), r = ta.getBoundingClientRect();
  if (!mirror) { mirror = document.createElement('div'); mirror.setAttribute('aria-hidden', 'true'); document.body.append(mirror); }
  const st = mirror.style;
  st.cssText = 'position:fixed;left:0;top:0;visibility:hidden;pointer-events:none;white-space:pre-wrap;overflow:hidden;border:0;box-sizing:content-box';
  for (const p of ['fontFamily', 'fontSize', 'fontWeight', 'fontStyle', 'letterSpacing', 'wordSpacing', 'lineHeight', 'tabSize',
    'wordBreak', 'overflowWrap', 'textIndent', 'paddingTop', 'paddingRight', 'paddingBottom', 'paddingLeft']) st[p] = cs[p];
  st.width = Math.max(0, ta.clientWidth - parseFloat(cs.paddingLeft) - parseFloat(cs.paddingRight)) + 'px';
  mirror.textContent = v;
  const range = document.createRange();
  range.selectNodeContents(mirror);
  const m = mirror.getBoundingClientRect();
  const px = x - r.left - (parseFloat(cs.borderLeftWidth) || 0) + ta.scrollLeft;
  const py = y - r.top - (parseFloat(cs.borderTopWidth) || 0) + ta.scrollTop;
  let hit = false;
  for (const q of range.getClientRects()) {
    if (q.width < 1) continue;
    const qx = q.left - m.left, qy = q.top - m.top;
    if (px >= qx - 2 && px <= qx + q.width + 2 && py >= qy && py <= qy + q.height) { hit = true; break; }
  }
  mirror.textContent = '';
  return hit;
}

/** cfg: {input, maxText(), send(), items(), tui(), t(key, vars), toastAction(text, label, fn, ms), touch()} */
export function init(c) {
  cfg = composer = c;
  root = $('fsEd'); ta = $('fsText'); pos = $('fsPos'); cmds = $('fsCmds'); cmdQ = $('fsCmdQ'); cmdList = $('fsCmdList');
  if (!root || !ta) { cfg = null; return; }
  registerLayer({ element: () => root, open: isOpen, close, fallback: () => cfg.input, trigger: () => cfg.input, rank: 90 });
  registerLayer({ element: () => cmds, open: () => isOpen() && cmdsOpen(), close: () => closeCmds(true), fallback: () => ta, rank: 91, trap: false });
  $('fsExit').addEventListener('click', () => corner('topLeft', close));   // @ix edit.exit
  $('fsClear').addEventListener('click', () => corner('topRight', clearAll));   // @ix edit.clear
  $('fsSlash').addEventListener('click', () => corner('bottomLeft', openCmds));   // @ix edit.command
  $('fsSend').addEventListener('click', () => corner('bottomRight', send));   // @ix edit.send
  const grab = $('fsGrab');
  if (grab) {   // @ix edit.exit (the grabber)
    grab.addEventListener('pointerdown', grabDown);
    grab.addEventListener('pointermove', grabMove);
    for (const ev of ['pointerup', 'pointercancel']) grab.addEventListener(ev, grabUp);
    // Enter / Space on the focused grabber leaves at once (a pointer has to pull it down; a tap alone does nothing)
    grab.addEventListener('keydown', (e) => { if (e.key === 'Enter' || e.key === ' ') { e.preventDefault(); e.stopPropagation(); close(); } });
  }
  for (const id of ['fsExit', 'fsClear', 'fsSlash', 'fsSend', 'fsGrab']) $(id)?.addEventListener('mousedown', (e) => e.preventDefault());   // the caret stays
  ta.addEventListener('input', () => { push(); paintPos(); });
  for (const ev of ['keyup', 'click', 'select']) ta.addEventListener(ev, paintPos);
  ta.addEventListener('keydown', (e) => {   // @ix edit.send edit.exit edit.readline
    if (e.isComposing || e.keyCode === 229) return;
    const mods = e.altKey || e.shiftKey;
    if (e.key === 'Enter' && (e.metaKey || e.ctrlKey) && !mods) { e.preventDefault(); e.stopPropagation(); send(); return; }
    if (e.ctrlKey && !e.metaKey && !mods && (e.key === 'g' || e.key === 'G' || e.code === 'KeyG')) { e.preventDefault(); e.stopPropagation(); close(); return; }
    if (termKey(e, ta, cfg.maxText())) { e.stopPropagation(); paintPos(); }
  });
  ta.addEventListener('dblclick', (e) => { if (!onText(e.clientX, e.clientY)) { e.preventDefault(); close(); } });
  ta.addEventListener('pointerup', (e) => {
    if (e.pointerType !== 'touch') return;
    const now = performance.now(), on = onText(e.clientX, e.clientY);
    if (tap && !on && !tap.on && now - tap.t <= DOUBLE_MS && Math.hypot(e.clientX - tap.x, e.clientY - tap.y) <= PAIR_PX) { tap = null; close(); return; }
    tap = { t: now, x: e.clientX, y: e.clientY, on };
  });
  cmdQ.addEventListener('input', () => { cmdAt = 0; paintCmds(); });
  cmdQ.addEventListener('keydown', (e) => {
    if (e.isComposing) return;
    const n = cmdShown.length;
    if ((e.key === 'ArrowDown' || e.key === 'ArrowUp') && n) { e.preventDefault(); cmdAt = (cmdAt + (e.key === 'ArrowDown' ? 1 : n - 1)) % n; paintCmds(); }
    else if (e.key === 'Enter' || (e.key === 'Tab' && !e.shiftKey && n)) { e.preventDefault(); e.stopPropagation(); pick(cmdAt); }
  });
  cmdList.addEventListener('mousedown', (e) => e.preventDefault());
  cmdList.addEventListener('click', (e) => { const b = e.target.closest('[data-i]'); if (b) pick(Number(b.dataset.i)); });
  cmds.addEventListener('click', (e) => { if (e.target === cmds) closeCmds(true); });
  for (const ev of ['resize', 'scroll']) window.visualViewport?.addEventListener(ev, fit);
  addEventListener('resize', fit);
  addEventListener('pageshow', () => { if (ios()) fit(); });
  document.addEventListener('visibilitychange', () => { if (ios() && !document.hidden) fit(); });
}


// P537: the same surface and keyboard/viewport engine serves every text editor.
const buttonIds = {topLeft:'fsExit', topRight:'fsClear', bottomLeft:'fsSlash', bottomRight:'fsSend'};
let originals = null;
function restoreButtons() {
  if (!root) return;
  if (!originals) originals = Object.fromEntries(Object.values(buttonIds).map(id => [id, {nodes:[...$(id).childNodes].map(n=>n.cloneNode(true)), label:$(id).getAttribute('aria-label')} ]));
  for (const id of Object.values(buttonIds)) { $(id).replaceChildren(...originals[id].nodes.map(n=>n.cloneNode(true)));  $(id).hidden = false; $(id).disabled = false; $(id).setAttribute('aria-label', originals[id].label); $(id).title = originals[id].label; }
  root.querySelector('.fshint').textContent = composer.t('p412.hint');
  root.removeAttribute('data-text-editor');
  ta.readOnly = false; root.setAttribute('aria-label',composer.t('p412.aria'));ta.setAttribute('aria-label',composer.t('p412.aria'));
}
function corner(name, fallback) {
  if (saving) return;
  const action = options?.corners?.[name];
  if (action) return action.action({value:ta.value, setValue(v){ta.value = v;paintPos();}, close, save:send});
  return fallback();
}
/** Independent draft. save(value) returning false keeps the draft open; Esc cancels.
 * corners: {topLeft,topRight,bottomLeft,bottomRight}: {label,icon,action({value,setValue,close,save})}.
 * Icons are plain text or a caller-created DOM node, never customer HTML.
 */
export function openText(opts) {
  if (!composer || isOpen()) return false;
  opener = opts.trigger || document.activeElement;
  const box = document.createElement('textarea'); box.value = opts.value || '';
  cfg = {...composer, input:box, maxText:() => opts.maxLength || 16000, touch:composer.touch};
  options = {save:opts.save || (() => true), ...opts};
  restoreButtons();
  root.dataset.textEditor = '1'; root.setAttribute('aria-label', opts.title || composer.t('p412.aria'));
  ta.setAttribute('aria-label', opts.title || composer.t('p412.aria'));
  const buttons = {
    topLeft:{label:composer.t('p537.cancel'),icon:'×',action:({close})=>close()},
    topRight:opts.reset ? {label:composer.t('p537.reset'),icon:'↺',action:({setValue})=>setValue(opts.reset())} : null,
    bottomLeft:opts.preview ? {label:composer.t('p537.preview'),icon:'◉',action:()=>{ta.readOnly=!ta.readOnly;}} : null,
    bottomRight:{label:composer.t('p537.save'),icon:'✓',action:({save})=>save()}, ...opts.corners,
  };
  options.corners = buttons;
  for (const [name,id] of Object.entries(buttonIds)) {
    const b = $(id), action = buttons[name]; b.hidden = !action;
    if (action) { b.replaceChildren(typeof action.icon === 'string' ? document.createTextNode(action.icon) : action.icon.cloneNode(true)); b.setAttribute('aria-label',action.label); b.title=action.label; }
  }
  root.querySelector('.fshint').textContent = opts.title || composer.t('p537.hint');
  show(); return true;
}
