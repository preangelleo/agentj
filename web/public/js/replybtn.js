// P368 (backlog #78, Leo 10-17: 「在你的 response 里边可以放 button……点击就直接发给你」) · one-tap answers and #tags in a reply.
// The Agent writes `[派](reply:派)` (Markdown link, scheme reply:) — md.js makes it an inert <span class="rlink" data-say>
// showing the label (Telegram / mail / the reader show the same plain word). Here, on the reply card only, the spans of the
// Agent's own words become buttons: one tap sends the words AS THE OWNER through the same signed say as typing them
// (relay.js sayWords), the whole group greys out and says 「已回复：派」 — once per group. Never a button: a link inside a
// quote (blockquote), code, a host system / task / command page, a page still being written, or once the owner has
// answered it (typed, or another remote). Words ≤ md.REPLY_MAX, never a /command (md.replyRef).
// P398 (#154, Leo 10-20: 「如果我当时没有点，先发了另外一条消息给你，然后再回过消息来点的时候，就已经不能点了」): a later
// message about something else no longer closes the group. Only an answer closes it — the owner's first message after it
// being one of its words (the old one-tap / typed answer), or any later message quoting this page (「回复 #N」) with one of
// its words. A tap on an older page goes as a reply to it (reply_to = its id), so the Agent knows which question it answers.
// #标签 in a reply → the chat search (P188) with that tag, every page that carries it.
import { t } from './t.js';

export const GROUP_MAX = 8;                         // more than this in one reply: the rest stay words
const OWNER = ['phone', 'telegram', 'email'];       // a page the owner wrote (the answer to the reply before it)
const NOT_AGENT = ['sys', 'task', 'cmd', 'friend_report'];           // host-written pages: not the Agent's own words
const SKIP = 'blockquote,pre,code,a,button,.mslot,.math,.mermaid-src,details';
const picked = new Map();                           // page id → the words this phone sent (until the next page shows it)
const ctxOf = new WeakMap();                        // root → the latest ctx (a click reads it)

/** The words that answered `p` (null: not answered yet), from the page list: the owner's first page after it when its
    words are one of `says` and it quotes nothing else, or any later owner page quoting `p` with one of `says`. */
function answerOf(p, pages, says) {
  const i = pages.indexOf(p);
  if (i < 0) return null;
  let first = true;
  for (const q of pages.slice(i + 1)) {
    const s = q && q.source;
    if (!s || !OWNER.includes(s.k)) continue;
    const text = typeof s.text === 'string' ? s.text.trim() : '', qid = s.quote ? s.quote.id : null;
    if (says.includes(text) && (qid === p.id || (first && qid === null))) return text;
    first = false;
  }
  return null;
}
/** This page's group state: {on, said} — on = the buttons can be tapped; said = the words chosen (shown 「已回复：…」). */
export function stateOf(p, pages, says, group = 0) {
  if (!p || !Number.isInteger(p.id)) return { on: false, said: null };
  const mine = picked.get(p.id + ':' + group);
  const ans = answerOf(p, pages || [], mine && !says.includes(mine) ? [...says, mine] : says);
  if (ans !== null) return { on: false, said: ans };
  if (mine) return { on: false, said: mine };
  return { on: p.end !== 'open', said: null };
}
/** A tap on a page the owner has written after (a later page exists): it goes as a reply to that page. */
export const older = (p, pages) => { const i = (pages || []).indexOf(p); return i >= 0 && i < pages.length - 1; };
export const agentWords = (p) => !!p && !NOT_AGENT.includes((p.source || {}).k);

/** Inside a quote / code / link … of the reply itself. The reply's own box is a <blockquote> (#words): only what lies
    between `n` and `root` counts. */
function skipped(n, root) {
  const m = n.parentElement && n.parentElement.closest(SKIP);
  return !!m && m !== root && root.contains(m);
}
function upgrade(root, ownerRoot = root) {
  const spans = [...root.querySelectorAll('span.rlink[data-say]')].filter((s) => !skipped(s, root));
  const out = [], groups = new Map();
  for (const s of spans.slice(0, GROUP_MAX)) {
    const say = s.dataset.say, b = document.createElement('button');
    const item = s.closest('li'), block = item || s.closest('p') || root;
    const line = item ? 0 : [...block.querySelectorAll('br')].filter(br => br.compareDocumentPosition(s) & Node.DOCUMENT_POSITION_FOLLOWING).length;
    let lines = groups.get(block); if (!lines) { lines = new Map(); groups.set(block, lines); }
    if (!lines.has(line)) lines.set(line, [...groups.values()].reduce((n, x) => n + x.size, 0));
    b.dataset.group = String(lines.get(line));
    b.type = 'button'; b.className = 'rbtn'; b.dataset.say = say;
    b.textContent = s.textContent;                   // the label as plain words (no link / chip nested in a button)
    const shown = b.textContent.replace(/\s+/g, ' ').trim();
    if (shown !== say) { const x = document.createElement('small'); x.className = 'rbtn-say'; x.textContent = say; b.append(x); }   // the words that go, when the label differs
    b.addEventListener('click', (e) => { e.stopPropagation(); tap(ownerRoot, b); });
    s.replaceWith(b); out.push(b);
  }
  return out;
}
async function tap(root, b) {
  const c = ctxOf.get(root);
  if (!c || b.disabled || b.getAttribute('aria-disabled') === 'true') return;
  const st = stateOf(c.page, c.pages(), says(root, b.dataset.group), b.dataset.group);
  if (!st.on) return;
  const p = c.page, say = b.dataset.say;
  const key = p.id + ':' + b.dataset.group;
  picked.set(key, say); paintGroup(root, c);
  let ok = false;
  try { ok = await c.say(say, older(p, c.pages()) ? p : null); } catch (_) { ok = false; }
  if (!ok && picked.get(key) === say) { picked.delete(key); paintGroup(root, c); }
}
const says = (root, group) => [...root.querySelectorAll('button.rbtn')].filter(b => b.dataset.group === group).map(b => b.dataset.say);
function paintGroup(root, c) {
  const bs = [...root.querySelectorAll('button.rbtn')];
  if (!bs.length) return;
  for (const group of new Set(bs.map(b => b.dataset.group))) {
  const buttons = bs.filter(b => b.dataset.group === group);
  const st = stateOf(c.page, c.pages(), buttons.map((b) => b.dataset.say), group);
  for (const b of buttons) {
    b.setAttribute('aria-disabled', st.on ? 'false' : 'true');
    b.classList.toggle('rb-off', !st.on);
    b.classList.toggle('rb-picked', !!st.said && b.dataset.say === st.said);
    b.title = st.on ? t('p368.tapSends', { say: b.dataset.say }) : st.said ? t('p368.said', { say: st.said }) : t(c.page.end === 'open' ? 'p398.writing' : 'p368.closed');
  }
  let note = [...root.querySelectorAll('.rb-note')].find(n => n.dataset.group === group);
  if (st.said) {
    if (!note) { note = document.createElement('span'); note.className = 'rb-note'; note.setAttribute('role', 'status'); note.dataset.group = group; buttons.at(-1).after(note); }
    note.textContent = t('p368.said', { say: st.said });
  } else if (note) note.remove();
  }
}

// ---- #tags
const TAG = /(^|[\s　，。！？；：、（(【\[「『"'\p{Script=Han}])#([\p{L}\p{N}_][\p{L}\p{N}_-]{0,31})(#?)/gu;
const HEX = (s) => /^(?:[0-9a-f]{3}|[0-9a-f]{6}|[0-9a-f]{8})$/i.test(s) && (/[a-f]/i.test(s) || s.length >= 6);   // #fff / #629887 are colours; #78 is a tag
function tags(root, find) {
  const w = document.createTreeWalker(root, NodeFilter.SHOW_TEXT), nodes = [];
  while (w.nextNode()) { const n = w.currentNode; if (n.data.includes('#') && !skipped(n, root) && !n.parentElement.closest('.rtag,.fp-link,.rbtn')) nodes.push(n); }
  for (const n of nodes) {
    const s = n.data, f = document.createDocumentFragment();
    let at = 0, hit = false;
    for (const m of s.matchAll(TAG)) {
      const tag = m[2];
      if (HEX(tag)) continue;
      const start = m.index + m[1].length;
      f.append(s.slice(at, start));
      const b = document.createElement('button');
      // P417: P415 pasted the reply-group lines here (s is a string, `groups` is upgrade()-local) → TypeError on every #tag.
      // Tag buttons only run find(); they carry no reply-group state, so no data-group.
      b.type = 'button'; b.className = 'rtag'; b.textContent = '#' + tag + m[3]; b.dataset.tag = tag;
      b.title = t('p368.tagFind', { tag: '#' + tag });
      b.addEventListener('click', (e) => { e.stopPropagation(); find('#' + tag); });
      f.append(b); at = start + 1 + tag.length + m[3].length; hit = true;
    }
    if (!hit) continue;
    f.append(s.slice(at));
    n.replaceWith(f);
  }
}

/** Upgrades and paints the reply `root` of page `page`. ctx: { pages() → the page list, say(words) → Promise<bool>,
    find(query) }. Safe to call on every showPage: a root already upgraded is only repainted. */
export function paint(root, page, ctx) {
  if (!root) return;
  const c = { ...ctx, page };
  ctxOf.set(root, c);
  if (root.firstChild !== root._rb) {                 // fill() rebuilt the words: upgrade them once
    root._rb = root.firstChild;
    if (agentWords(page)) upgrade(root);
    else if (page.source?.k === 'sys' && page.end !== 'open') {
      if (['wsl', 'loop'].includes(page.source.guide)) {
        const block = document.createElement('p'), span = document.createElement('span');
        span.className = 'rlink'; span.dataset.say = t('p570c.' + page.source.guide + 'Guide'); span.textContent = span.dataset.say;
        block.append(span); root.append(block); upgrade(block, root);
      }
    }
    tags(root, ctx.find);
  }
  paintGroup(root, c);
}
/** Tests. */
export function debug() { return { picked: [...picked] }; }
