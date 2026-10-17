// P368 (backlog #78, Leo 10-17: 「在你的 response 里边可以放 button……点击就直接发给你」) · one-tap answers and #tags in a reply.
// The Agent writes `[派](reply:派)` (Markdown link, scheme reply:) — md.js makes it an inert <span class="rlink" data-say>
// showing the label (Telegram / mail / the reader show the same plain word). Here, on the reply card only, the spans of the
// Agent's own words become buttons: one tap sends the words AS THE OWNER through the same signed say as typing them
// (relay.js sayWords), the whole group greys out and says 「已回复：派」 — once per group. Never a button: a link inside a
// quote (blockquote), code, a host system / task / command page, a page still being written, or once the owner has
// already answered after it (typed, or another remote). Words ≤ md.REPLY_MAX, never a /command (md.replyRef).
// #标签 in a reply → the chat search (P188) with that tag, every page that carries it.
import { t } from './t.js';

export const GROUP_MAX = 8;                         // more than this in one reply: the rest stay words
const OWNER = ['phone', 'telegram', 'email'];       // a page the owner wrote (the answer to the reply before it)
const NOT_AGENT = ['sys', 'task', 'cmd'];           // host-written pages: not the Agent's own words
const SKIP = 'blockquote,pre,code,a,button,.mslot,.math,.mermaid-src,details';
const picked = new Map();                           // page id → the words this phone sent (until the next page shows it)
const ctxOf = new WeakMap();                        // root → the latest ctx (a click reads it)

/** The words of the owner's first page after `p` (null: none yet), from the page list. */
function nextOwner(p, pages) {
  const i = pages.indexOf(p);
  if (i < 0) return null;
  for (const q of pages.slice(i + 1)) {
    const s = q && q.source;
    if (s && OWNER.includes(s.k)) return typeof s.text === 'string' ? s.text.trim() : '';
  }
  return null;
}
/** This page's group state: {on, said} — on = the buttons can be tapped; said = the words chosen (shown 「已回复：…」). */
export function stateOf(p, pages, says) {
  if (!p || !Number.isInteger(p.id)) return { on: false, said: null };
  const mine = picked.get(p.id);
  const next = nextOwner(p, pages || []);
  if (next !== null) return { on: false, said: says.includes(next) ? next : (mine && next === mine ? mine : null) };
  if (mine) return { on: false, said: mine };
  return { on: p.end !== 'open', said: null };
}
export const agentWords = (p) => !!p && !NOT_AGENT.includes((p.source || {}).k);

/** Inside a quote / code / link … of the reply itself. The reply's own box is a <blockquote> (#words): only what lies
    between `n` and `root` counts. */
function skipped(n, root) {
  const m = n.parentElement && n.parentElement.closest(SKIP);
  return !!m && m !== root && root.contains(m);
}
function upgrade(root) {
  const spans = [...root.querySelectorAll('span.rlink[data-say]')].filter((s) => !skipped(s, root));
  const out = [];
  for (const s of spans.slice(0, GROUP_MAX)) {
    const say = s.dataset.say, b = document.createElement('button');
    b.type = 'button'; b.className = 'rbtn'; b.dataset.say = say;
    b.textContent = s.textContent;                   // the label as plain words (no link / chip nested in a button)
    const shown = b.textContent.replace(/\s+/g, ' ').trim();
    if (shown !== say) { const x = document.createElement('small'); x.className = 'rbtn-say'; x.textContent = say; b.append(x); }   // the words that go, when the label differs
    b.addEventListener('click', (e) => { e.stopPropagation(); tap(root, b); });
    s.replaceWith(b); out.push(b);
  }
  return out;
}
async function tap(root, b) {
  const c = ctxOf.get(root);
  if (!c || b.disabled || b.getAttribute('aria-disabled') === 'true') return;
  const st = stateOf(c.page, c.pages(), says(root));
  if (!st.on) return;
  const p = c.page, say = b.dataset.say;
  picked.set(p.id, say); paintGroup(root, c);
  let ok = false;
  try { ok = await c.say(say); } catch (_) { ok = false; }
  if (!ok && picked.get(p.id) === say) { picked.delete(p.id); paintGroup(root, c); }
}
const says = (root) => [...root.querySelectorAll('button.rbtn')].map((b) => b.dataset.say);
function paintGroup(root, c) {
  const bs = [...root.querySelectorAll('button.rbtn')];
  if (!bs.length) return;
  const st = stateOf(c.page, c.pages(), bs.map((b) => b.dataset.say));
  for (const b of bs) {
    b.setAttribute('aria-disabled', st.on ? 'false' : 'true');
    b.classList.toggle('rb-off', !st.on);
    b.classList.toggle('rb-picked', !!st.said && b.dataset.say === st.said);
    b.title = st.on ? t('p368.tapSends', { say: b.dataset.say }) : st.said ? t('p368.said', { say: st.said }) : t('p368.closed');
  }
  let note = root.querySelector('.rb-note');
  if (st.said) {
    if (!note) { note = document.createElement('span'); note.className = 'rb-note'; note.setAttribute('role', 'status'); bs.at(-1).after(note); }
    note.textContent = t('p368.said', { say: st.said });
  } else if (note) note.remove();
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
    tags(root, ctx.find);
  }
  paintGroup(root, c);
}
/** Tests. */
export function debug() { return { picked: [...picked] }; }
