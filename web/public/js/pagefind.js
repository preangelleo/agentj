// P363 (#70, owner 10-17: 「右上角这个搜索按钮进入到所有页面都保留……它的搜索就指向那个页面的 Index 里边的内容」): one
// magnifier at the top right for every page; what it searches is the page it is on. search.js keeps the chat's own search
// (历史消息) and asks the scope handler first; Settings keeps its own (settings.js, now per sub-page). This module is the
// shared field (look and keys of the shortcut sheet's search, P344) and the in-text find used where a page is prose or a
// list without its own index (a document, 记忆 / 活动 / 后台任务 / 模型和 API Key / 好友 / Bot 页): every hit marked with the
// CSS Highlight API (the DOM is never rewritten), Enter = next, Shift+Enter = previous, the current one scrolled to the middle.
import {t} from './t.js';

export const terms = (q) => String(q || '').trim().toLocaleLowerCase().split(/\s+/).filter(Boolean);
/** Every word of the query is somewhere in the text (case-insensitive) — the shortcut sheet's rule. */
export const matches = (hay, ts) => { const h = String(hay || '').toLocaleLowerCase(); return ts.every((w) => h.includes(w)); };

/** A search field (div.set-find.pfind > input + one status line). `ph` = dictionary key of the placeholder / label. */
export function field(id, ph) {
  const box = document.createElement('div'); box.className = 'set-find pfind'; box.id = id; box.hidden = true;
  box.setAttribute('role', 'search');
  const i = document.createElement('input'); i.type = 'search'; i.id = id + '-input'; i.enterKeyHint = 'search';
  i.autocomplete = 'off'; i.spellcheck = false; i.maxLength = 200; i.setAttribute('autocapitalize', 'off');
  const n = document.createElement('p'); n.className = 'pfind-n'; n.id = id + '-n'; n.setAttribute('role', 'status'); n.hidden = true;
  box.append(i, n);
  label(box, ph);
  return box;
}
export function label(box, ph) { const i = box.querySelector('input'); i.placeholder = t(ph); i.setAttribute('aria-label', t(ph)); box.dataset.ph = ph; }
export function say(box, text) { const n = box.querySelector('.pfind-n'); n.textContent = text || ''; n.hidden = !text; }

// ---------------------------------------------------------------- in-text find
const HL = 'aj-find', CUR = 'aj-find-cur';
let ranges = [], at = -1, root = null;
const canMark = () => typeof CSS !== 'undefined' && CSS.highlights && typeof Highlight === 'function';
export function clearMarks() {
  ranges = []; at = -1; root = null;
  if (canMark()) { CSS.highlights.delete(HL); CSS.highlights.delete(CUR); }
}
/** Mark every occurrence of every word under `box` (visible text only); → the number of hits. */
export function mark(box, q) {
  clearMarks();
  const ts = terms(q);
  if (!box || !ts.length) return 0;
  root = box;
  const skip = (n) => n.parentElement && n.parentElement.closest('[hidden],script,style,.pfind,svg');
  const walker = document.createTreeWalker(box, NodeFilter.SHOW_TEXT, { acceptNode: (n) => (skip(n) ? NodeFilter.FILTER_REJECT : NodeFilter.FILTER_ACCEPT) });
  for (let n = walker.nextNode(); n; n = walker.nextNode()) {
    const s = n.nodeValue.toLocaleLowerCase();
    for (const w of ts) for (let i = s.indexOf(w); i >= 0; i = s.indexOf(w, i + w.length)) {
      const r = document.createRange(); r.setStart(n, i); r.setEnd(n, i + w.length); ranges.push(r);
    }
  }
  ranges = ranges.filter((r) => r.getClientRects().length);
  ranges.sort((a, b) => a.compareBoundaryPoints(Range.START_TO_START, b));
  if (canMark() && ranges.length) CSS.highlights.set(HL, new Highlight(...ranges));
  return ranges.length;
}
/** Go to the next (d = 1) / previous (d = -1) hit; → its 1-based place, 0 without hits. */
export function step(d = 1) {
  if (!ranges.length) return 0;
  at = (at + d + ranges.length) % ranges.length;
  const r = ranges[at], host = r.startContainer.parentElement;
  if (host) host.scrollIntoView({ block: 'center' });
  if (canMark()) CSS.highlights.set(CUR, new Highlight(r));
  return at + 1;
}
export const hits = () => ranges.length;
export const markedIn = () => root;

// ---------------------------------------------------------------- one field over a page that has no index of its own
// Used for the 记忆 / 活动 / 后台任务 / 模型和 API Key / 好友 / Bot / 首次准备 pages and an open document: the field sits at
// the top of that page (under its header row, sticky), Esc / the magnifier again clears and closes it.
let open = null;          // {box, page}
export const isOpen = () => !!open && !open.box.hidden && open.box.isConnected && open.page.isConnected && !open.page.hidden;
export function openIn(page, ph = 'p363.findPage') {
  if (!page) return false;
  if (isOpen() && open.page === page) { close(); return true; }
  close();
  let box = page.querySelector(':scope > .pfind');
  if (!box) {
    box = field('pfind-' + (page.id || 'page'), ph); wire(box, page);
    const head = page.querySelector(':scope > .menuhead, :scope > .fp-detail-bar');   // under the page's own header row
    if (head) head.after(box); else page.prepend(box);
  }
  label(box, ph);
  box.hidden = false; open = { box, page };
  const i = box.querySelector('input'); i.value = ''; say(box, ''); i.focus();
  return true;
}
export function close() {
  if (!open) return false;
  const { box } = open; open = null;
  if (!box.isConnected) { clearMarks(); return false; }
  const was = !box.hidden;
  box.hidden = true; box.querySelector('input').value = ''; say(box, ''); clearMarks();
  return was;
}
function wire(box, page) {
  const i = box.querySelector('input');
  const run = () => { const n = mark(page, i.value); say(box, i.value.trim() ? (n ? t('p363.hits', { n }) : t('p363.none')) : ''); if (n) step(1); };
  i.addEventListener('input', run);
  i.addEventListener('keydown', (e) => {
    if (e.isComposing || e.keyCode === 229 || e.key !== 'Enter') return;
    e.preventDefault();
    if (!hits()) return;
    const k = step(e.shiftKey ? -1 : 1);
    say(box, t('p363.at', { k, n: hits() }));
  });
}

// Esc: the field goes first (cleared + closed), the page stays — the next Esc is the page's own (layers.js, P119); rank 41 sits
// over Settings (40) and every page it can be opened on, under 搜索聊天记录 (42) and a confirm (60)
export function initLayer(registerLayer, fallback) {
  registerLayer({ element: () => open && open.box, open: isOpen, close, fallback, rank: 41, trap: false });
}
