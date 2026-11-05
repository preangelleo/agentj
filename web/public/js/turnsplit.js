// 0.26.9 #471 (CONTRACT §D, ADR-0269-w2-web): a long turn the owner talked past. Once the owner sends a message after the
// live reply card of a running turn T, every later output of T goes to a NEW card at the bottom — the same turn id, a
// continuation segment — and the old card keeps what it showed then, says 「继续见下方」 (a tap goes to the continuation)
// and gets nothing more. Purely the phone's view of the history: any harness, no wire change.
//   · note(turns): remembers, per open turn T with a visible reply, where its reply stood when an owner message came after
//     it (the first time this phone saw both). One owner message after another with no output of T in between moves the
//     cut down to the newest message (no empty card in between).
//   · layout(pages): T's page → the head (its reply up to the first cut, closed) + one page per segment that has words.
//     A segment sits right after the owner message that cut it — or right before it once that message has a reply of its
//     own (T ended and the message was answered after it: the answer stays the newest page).
// A reply the host rewrote (not an extension of what the head showed) is shown whole in the continuation: nothing is lost.
export const OWNER_KINDS = ['phone', 'telegram', 'wechat'];

const said = (p) => !!p && !!p.source && OWNER_KINDS.includes(p.source.k) && (!!(p.source.text || '').trim() || !!(p.source.att || []).length);
const words = (p) => (typeof p?.reply === 'string' ? p.reply : '');

export function createSplit() {
  let cuts = new Map();          // turn id → [{at, head, after}] (head = the reply text up to `at`; after = the owner turn id)
  /** turns: the loaded history in id order. visible(p) = it has a reply card worth calling live. */
  function note(turns, visible = (p) => !!words(p).trim()) {
    for (const T of turns) {
      if (T.end !== 'open' || !Number.isInteger(T.id)) continue;
      let list = cuts.get(T.id);
      if (!list && !visible(T)) continue;
      list = list || [];
      let last = list.length ? list[list.length - 1].after : T.id;
      const reply = words(T);
      for (const M of turns) {
        if (M.id <= last || !said(M)) continue;
        const prev = list[list.length - 1];
        if (prev && prev.at === reply.length) prev.after = M.id;         // nothing new since: the cut moves down
        else list.push({ at: reply.length, head: reply, after: M.id });
        last = M.id;
      }
      if (list.length) cuts.set(T.id, list);
    }
    if (cuts.size > 64) cuts = new Map([...cuts].slice(-64));
  }
  /** T's reply → [head, seg1, seg2…] texts. */
  function parts(T, list) {
    const r = words(T), out = [];
    let from = 0;
    for (let i = 0; i < list.length; i++) {
      const c = list[i];
      if (r.startsWith(c.head)) { out.push(r.slice(from, c.at)); from = c.at; continue; }
      out.push(c.head.slice(from));                   // rewritten: the card keeps the words it showed, the rest goes whole
      for (let j = i + 1; j < list.length; j++) out.push('');
      out.push(r);
      return out;
    }
    out.push(r.slice(from));
    return out;
  }
  /** pages (visible turns, id order) → pages with heads and continuation segments. hasOwn(p) = p has a reply of its own. */
  function layout(pages, hasOwn = (p) => !!words(p).trim()) {
    if (!cuts.size) return pages;
    const out = [], waiting = [];     // {after, page}
    const flush = (id, before) => {
      for (let i = 0; i < waiting.length;) {
        const w = waiting[i];
        if (before ? w.after === id : w.after <= id) { out.push(w.page); waiting.splice(i, 1); } else i++;
      }
    };
    for (const p of pages) {
      if (Number.isInteger(p.id) && hasOwn(p)) flush(p.id, true);       // answered after T: T's rest goes above it
      const list = Number.isInteger(p.id) ? cuts.get(p.id) : null;
      if (!list) { out.push(p); flush(p.id, false); continue; }
      const texts = parts(p, list);
      const shown = [];
      for (let k = 1; k < texts.length; k++) if (texts[k].trim()) shown.push(k);
      if (!shown.length) { out.push({ ...p, reply: texts[0] + texts.slice(1).join('') }); flush(p.id, false); continue; }
      const last = shown[shown.length - 1];
      const closed = { end: 'done', card: null, media: [], mediaSkip: 0, login: undefined, queue: undefined, part: null };
      out.push({ ...p, ...closed, reply: texts[0], cont: { id: p.id, seg: shown[0] }, split: 'head' });
      shown.forEach((k, i) => {
        const tail = k === last;
        const src = { k: p.source?.k || 'sys', dev: p.source?.dev || null, name: p.source?.name || '', text: '', att: [], quote: null, local: false, contOf: (p.source?.text || '').trim() };
        const page = { ...p, ...(tail ? {} : closed), id: p.id, seg: k, reply: texts[k], source: src, split: 'seg', ...(tail ? {} : { cont: { id: p.id, seg: shown[i + 1] } }) };
        waiting.push({ after: list[k - 1].after, page });
      });
      flush(p.id, false);
    }
    for (const w of waiting) out.push(w.page);
    return out;
  }
  return { note, layout, reset() { cuts = new Map(); }, _cuts: () => new Map(cuts) };
}

/** The page key the pager pins to: the turn id, plus the segment for a continuation. */
export const pageKey = (p) => (p.id === null ? 'live' : String(p.id)) + (p.seg ? '~' + p.seg : '');
/** A page's 「继续见下方」 target in `pages`, or -1. */
export const contIndex = (pages, p) => (p && p.cont ? pages.findIndex((x) => x.id === p.cont.id && x.seg === p.cont.seg) : -1);
/** The continuation's own top line: what it continues (≤ 60 characters of the owner's words). */
export function contOfText(src) {
  const s = typeof src?.contOf === 'string' ? src.contOf.replace(/\s+/g, ' ').trim() : '';
  return [...s].length > 60 ? [...s].slice(0, 59).join('') + '…' : s;
}
