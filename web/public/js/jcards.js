// P360 Agent J Cards (PROTOCOL §34, spec agentjarvis/docs/specs/AGENTJ-CARDS-SPEC.md): cards 6, 7, 8 … in the logo menu.
// The computer does the work; this page only draws one of the built-in templates from what the computer read and turns a
// button into a chat message — shown in full on the official confirm card, sent only on the owner's tap (or put in the
// message box as a draft). No card code runs here, no card can style the confirm card, nothing is stored on the phone.
import {ask1, peer, isReady} from './session.js';
import {RelayMD} from './md.js';
import {t, lang} from './t.js';
import {confirmSheet} from './ui.js';
import {sysWhen, cronText} from './controls.js';
import {cardColor, setColors, resetColors} from './jcard-color.js';

let list = null, busy = null, epoch = 0, view = 0;
const subs = [];
export const available = () => !!peer.jcards1;
export const cards = () => list || [];
/** The cards the deck shows after the five system cards (switched on, in the computer's order). */
export const deck = () => cards().filter((c) => c.on);
export const nameOf = (c) => pick(c && c.name);
export function onChange(fn) { subs.push(fn); }
export function clear() { list = null; busy = null; epoch++; view++; resetColors(); for(const d of document.querySelectorAll('.jc-dialog'))d.dispatchEvent(new Event('cancel',{cancelable:true})); }
// P360 (Leo 10-17: 「每个卡片都有一个 unique internal URL」): a card's address is its id — card/<id>[#<anchor>] (the name may
// repeat). ID / ANCHOR mirror host jcards.ID_RE / ANCHOR_RE.
export const ID = /^[a-z][a-z0-9-]{1,31}$/, ANCHOR = /^[A-Za-z0-9][A-Za-z0-9_-]{0,63}$/;
export const ADDR = /^card\/([a-z][a-z0-9-]{1,31})(?:#([A-Za-z0-9][A-Za-z0-9_-]{0,63}))?/;
/** One internal address, whole (host jcards.JLINK_RE): skill/ · workflow/ · doc/[<folder>/]<file>.md · card/<id>[#<anchor>] · page/. */
export const JLINK = /^(skill\/[a-z0-9][a-z0-9-]{1,63}|workflow\/[A-Za-z0-9][A-Za-z0-9._-]{0,79}|doc\/(?:[A-Za-z0-9][A-Za-z0-9._-]{0,79}\/)?[A-Za-z0-9][A-Za-z0-9._-]{0,79}\.md|card\/[a-z][a-z0-9-]{1,31}(?:#[A-Za-z0-9][A-Za-z0-9_-]{0,63})?|page\/(?:chat|workflows|skills|memory|agentj|cards))$/;
/** `@<name>（card/<id>）` for every switched-on card — what a mention puts in the message (the Agent reads the id), with the
 *  chip's label. Two cards with one name (or a card named like a system card, `taken`) carry 「官方」/「我的」, or the id. */
export function mentions(taken = []) {
  const on = deck(), out = [];
  for (const c of on) {
    const n = nameOf(c), same = on.filter((x) => nameOf(x) === n);
    let tag = '';
    if (same.length > 1) tag = same.filter((x) => !!x.official === !!c.official).length > 1 ? c.id : t(c.official ? 'p360.tagOfficial' : 'p360.tagMine');
    else if (taken.includes(n)) tag = t('p360.tagMine');
    out.push({ c, text: `@${n}\uFF08card/${c.id}\uFF09`, label: '@' + n + (tag ? ' · ' + tag : ''), tag });
  }
  return out;
}
const pick = (v) => (v && typeof v === 'object' ? v[lang()] || v.en || v.zh || '' : String(v || ''));
const changed = () => { for (const f of subs) f(); };
function node(tag, text, cls) { const n = document.createElement(tag); if (text !== undefined) n.textContent = text; if (cls) n.className = cls; return n; }
function button(text, fn, cls = 'fp-action') { const n = node('button', text, cls); n.type = 'button'; n.onclick = fn; return n; }

/** jcard_list → the list (one request at a time; an older computer without the capability: an empty list). */
export function refresh() {
  if (!available() || !isReady()) return Promise.resolve(cards());
  if (busy) return busy;
  const e = epoch;
  busy = ask1({ t: 'jcard_list', lang: lang() }, 'jcard_items', 15000).then((m) => {
    busy = null;
    if (e !== epoch || !Array.isArray(m.cards)) return cards();
    const before = JSON.stringify(list);
    setColors(m.colors || {});
    list = m.cards.filter((c) => c && typeof c.id === 'string');
    if (JSON.stringify(list) !== before) changed();
    return list;
  });
  return busy;
}

/** jcard_set: one card's switch (Settings → Cards). → true when the computer saved it. */
export async function setOn(id, on) {
  const m = await ask1({ t: 'jcard_set', id, on }, 'jcard_res', 15000);
  if (!m.ok) return false;
  const c = cards().find((x) => x.id === id);
  if (c) { c.on = on; changed(); }
  return true;
}

// ---------------------------------------------------------------- line icons (the manifest names one; no card images)
const PATHS = {
  note: 'M6 3h9l4 4v14H6zM14 3v5h5M9 12h7M9 16h7',
  checklist: 'M10 6h10M10 12h10M10 18h10M3.5 6l1.5 1.5L8 4.5M3.5 12l1.5 1.5L8 10.5M3.5 18l1.5 1.5L8 16.5',
  calendar: 'M4 6h16v15H4zM4 10h16M8 3v5M16 3v5',
  folder: 'M3 6h6l2 2h10v12H3z',
  board: 'M4 4h16v16H4zM4 9h16M9 9v11',
  book: 'M5 4h11a3 3 0 0 1 3 3v13H8a3 3 0 0 1-3-3zM5 17a3 3 0 0 1 3-3h11',
  star: 'm12 3 2.7 5.6 6.1.9-4.4 4.3 1 6.1L12 17l-5.4 2.9 1-6.1-4.4-4.3 6.1-.9z',
  bolt: 'M13 2 4 14h7l-1 8 9-12h-7z',
  check: 'M5 12.5l4 4L19 7',
  box: 'M5 5h14v14H5z',
  copy: 'M9 8h11v13H9zM5 16H3V3h11v2',
  edit: 'm4 16-1 5 5-1L20 8l-4-4zM13 7l4 4',
  delete: 'M3 6h18M9 6V3h6v3M6 6l1 15h10l1-15M10 10v7M14 10v7',
  out: 'M14 4h6v6M20 4l-9 9M18 14v6H4V6h6',
};
export function icon(key, cls = 'fp-line-icon') {
  const ns = 'http://www.w3.org/2000/svg', s = document.createElementNS(ns, 'svg');
  for (const [k, v] of [['viewBox', '0 0 24 24'], ['class', cls], ['aria-hidden', 'true'], ['fill', 'none'], ['stroke', 'currentColor'], ['stroke-width', '1.7'], ['stroke-linecap', 'round'], ['stroke-linejoin', 'round']]) s.setAttribute(k, v);
  const p = document.createElementNS(ns, 'path'); p.setAttribute('d', PATHS[key] || PATHS.note); s.append(p); return s;
}

/** One deck card (the menu builds the five system cards itself). `i` = its place in the deck (5, 6, …). */
export function deckCard(c, i, open) {
  const b = button('', open, 'fp-menu-card jc-card jc-accent-' + (/^[a-z-]{2,32}$/.test(c.accent) ? c.accent : 'noir'));
  b.dataset.card = c.id; b.style.setProperty('--fp-i', String(i));
  const head = node('div', undefined, 'fp-menu-head jc-head');
  head.append(icon(c.icon, 'fp-line-icon jc-icon'));
  b.append(head, node('h2', nameOf(c), 'jc-name'), node('p', pick(c.desc), 'jc-desc'));   // one strip look with the five
  cardColor(b, c.id);
  return b;
}

// ---------------------------------------------------------------- one card's page
const PROBLEM = { empty: 'p360.empty', refused: 'p360.refused', bad_data: 'p360.badData', unreadable: 'p360.unreadable', off: 'p360.off', not_found: 'p360.gone' };

/** The element of place `at` on a drawn card page (null: not there — the page stays at its top). */
function place(box, at, m) {
  if (at === 'latest') return box.querySelector('.jc-entry') || [...box.querySelectorAll('.jc-item')].at(-1) || box;
  if (!at || !ANCHOR.test(at)) return null;
  const block = (b) => b === 'top' ? box : b === 'content' ? box.querySelector('.jc-notes,.jc-list') : box.querySelector(b === 'actions' ? '.jc-actions' : '.jc-links');
  if (at === 'top' || at === 'actions' || at === 'links') return block(at);
  const a = (Array.isArray(m.anchors) ? m.anchors : []).find((x) => x && x.id === at);
  if (a && a.block) return block(a.block);
  if (a && a.heading) return [...box.querySelectorAll('.jc-notes :is(h1,h2,h3,h4,h5,h6)')].find((h) => h.textContent.trim() === String(a.heading).trim()) || headingEntry(box, a.heading, m);
  return [...box.querySelectorAll('.jc-item,.jc-entry')].find((li) => li.dataset.item === at) || null;   // a checklist row
}
// Legacy heading anchors retain their source-order semantics after paragraphs become cards.
// Mirror the host's note span boundaries; entry text equality would confuse duplicate notes.
function headingEntry(box, heading, m) {
  if (!Array.isArray(m.entries)) return null;
  let count=0, active=false, found=false;
  for (const line of String(m.text||'').split(/\r?\n/)) {
    const head=/^#{1,6}\s/.test(line), boundary=!line.trim()||head||/^\s*([-*_])(?:\s*\1){2,}\s*$/.test(line),bullet=/^(?:[-+*]|\d+[.)])\s+/.test(line);
    if (boundary||bullet) active=false;
    if (head) {
      if(found)return box; // An empty section stays at the card top.
      const parsed=node('div');parsed.append(RelayMD.toDOM(RelayMD.parse(line),document));
      if(parsed.textContent.trim()===String(heading).trim())found=true;
    }
    if(!boundary&&!active){if(found)return box.querySelectorAll('.jc-entry')[count]||box;count++;active=true;}
  }
  return found?box:null;
}
const WEB = /^https?:\/\/[^\s@/?#]+([/?#]\S*)?$/;
/** A card's web link: the address is shown whole first; the new tab gets no opener and no referrer (this page's address
 *  carries the pairing). Anything not plain http(s) — or with user:password@ — is not opened. */
async function web(href) {
  let u = null;
  try { u = new URL(href); } catch (_) { /* not an address */ }
  if (!u || !WEB.test(href) || !/^https?:$/.test(u.protocol) || u.username || u.password) return;
  const ask = confirmSheet(t('p360.webTitle'), href, t('p360.webOpen'));
  const shown = document.getElementById('confirm-text');
  if (shown) shown.textContent = href;
  if (await ask) window.open(u.href, '_blank', 'noopener,noreferrer');
}

/** Draws card `id` into `body` (the five-pages page). hooks: { title(text), say(text), draft(text), nav(jlink), at? }. */
export async function render(body, id, hooks) {
  const e = ++view, c = cards().find((x) => x.id === id);
  body.replaceChildren();
  hooks.title(c ? nameOf(c) : '');
  const wait = node('p', t('p244.fivepages31'), 'fp-meta'); body.append(wait);
  const m = await ask1({ t: 'jcard_get', id, lang: lang() }, 'jcard_get', 15000);
  if (e !== view || !body.isConnected) return;
  body.replaceChildren();
  const box = node('section', undefined, 'jc-body' + (c ? ' jc-accent-' + c.accent : ''));
  box.dataset.card = id; box.dataset.template = m.template || ''; box.dataset.anchor = 'top';
  body.append(box);
  if (!m.ok) { box.append(node('p', t(PROBLEM[m.why] || (m.t === 'offline' || m.t === 'timeout' ? 'p360.offline' : 'p360.gone')), 'jc-note')); return; }
  if (m.problem) box.append(node('p', t(PROBLEM[m.problem] || 'p360.unreadable'), 'jc-note'));
  if (m.template === 'notes' && Array.isArray(m.entries)) {
    const stack = node('div', undefined, 'jc-notes jc-stack');
    for (const it of m.entries) {
      const entry = node('article', undefined, 'jc-entry'); entry.dataset.item = it.id;
      const acts = node('div', undefined, 'jc-entry-actions');
      for (const [op, key] of [['copy','copy'],['edit','edit'],['delete','delete']]) {
        const b = button('', async () => {
          if (op === 'copy') { try { await navigator.clipboard.writeText(it.text); b.setAttribute('aria-label',t('p386.copied')); } catch { b.setAttribute('aria-label',t('p386.failed')); } return; }
          const text = op === 'edit' ? await editNote(it.text) : undefined;
          if (op === 'edit' && text === null) return;
          if (op === 'delete' && !await confirmSheet(t('p386.delete'), it.text, t('p386.delete'))) return;
          if (e !== view || !body.isConnected) return;
          b.disabled = true;
          const result = await ask1({t:'jcard_note',id,entry:it.id,revision:m.revision,op,...(op==='edit'?{text}:{})},'jcard_note',15000);
          if (e !== view || !body.isConnected) return;
          if (result.ok) render(body,id,hooks); else if (result.why === 'conflict' || result.why === 'changed') { const expected=view+1; await render(body,id,hooks); if(view===expected&&body.isConnected)body.prepend(node('p',t('p386.changed'),'jc-note')); } else { b.disabled=false; const error=node('p',t(result.why==='changed'?'p386.changed':'p386.failed'),'jc-note'); entry.append(error); }
        }, 'jc-entry-icon');
        if (op === 'edit' && it.editable === false) b.disabled = true;
        b.setAttribute('aria-label', t('p386.'+op)); b.title=t('p386.'+op); b.dataset.noteOp=op; b.append(icon(key)); acts.append(b);
      }
      const prose=node('div',undefined,'fp-prose'); prose.append(RelayMD.toDOM(RelayMD.parse(String(it.text)),document)); entry.append(acts,prose); stack.append(entry);
    }
    box.append(stack);
  } else if (m.template === 'automation' && Array.isArray(m.items)) {
    const stack=node('div',undefined,'jc-stack');
    for(const it of m.items) {
      const row=node('article',undefined,'jc-entry jc-automation'); row.dataset.item=it.id;
      row.append(node('h2',it.name));
      const at=button('@',()=>hooks.draft(String(it.prompt||'/agentj-automation @'+it.name)+' '),'jc-auto-at'); at.setAttribute('aria-label',t('p386.ask',{name:it.name})); row.append(at);
      const fields=node('dl'); for(const key of ['schedule','repeat','next','harness','model','description','source']) { let value=it[key]; if(Array.isArray(value))value=sysWhen(value); else if(key==='schedule' && value) { let when=it.when; if(!when && typeof value==='string' && value.startsWith('[')){try{when=JSON.parse(value);}catch{}} value=Array.isArray(when)?sysWhen(when):cronText(value); } if(key==='next' && typeof value==='number') { const date=new Date(value>1e14?value/1000:value>1e11?value:value*1000); value=Number.isFinite(date.getTime())?date.toLocaleString(lang()==='zh'?'zh-CN':'en-GB'):null; } fields.append(node('dt',t('p386.'+key)),node('dd',value||t('p386.unknown'))); } row.append(fields);stack.append(row);
    } box.append(stack);
  } else if (m.template === 'notes' && m.text) {
    const prose = node('div', undefined, 'fp-prose jc-notes');
    prose.append(RelayMD.toDOM(RelayMD.parse(String(m.text)), document));
    box.append(prose);
  } else if (m.template === 'checklist' && Array.isArray(m.items) && m.items.length) {
    const ul = node('ul', undefined, 'jc-list');
    for (const it of m.items) {
      const li = node('li', undefined, 'jc-item'), acts = Array.isArray(it.acts) ? it.acts : [];
      li.dataset.item = it.id; li.dataset.done = it.done ? '1' : '0';
      const main = button('', acts[0] ? () => run(acts[0], hooks) : null, 'jc-row');
      main.append(icon(it.done ? 'check' : 'box', 'jc-check'), node('span', String(it.text || ''), 'jc-text'));
      if (acts[0]) main.setAttribute('aria-label', `${acts[0].label}：${it.text}`); else main.disabled = true;
      li.append(main);
      for (const a of acts.slice(1)) li.append(button(a.label, () => run(a, hooks), 'jc-chip'));
      ul.append(li);
    }
    box.append(ul);
  }
  if (m.cut) box.append(node('p', t('p360.cut'), 'jc-note'));
  if (Array.isArray(m.actions) && m.actions.length) {
    const row = node('div', undefined, 'jc-actions');
    for (const a of m.actions) { const b = button(a.label, () => run(a, hooks), 'fp-action jc-action'); b.dataset.act = a.id; if (a.draft) b.dataset.draft = '1'; row.append(b); }
    box.append(row);
  }
  // links (Leo 10-17: 「Hyperlink……新浏览器 Tab；internal link……直接导航过去」): href → the web page, jlink → inside Agent J
  if (Array.isArray(m.links) && m.links.length) {
    const row = node('div', undefined, 'jc-links');
    for (const l of m.links) {
      const out = typeof l.href === 'string', b = button(l.label, () => (out ? web(l.href) : hooks.nav(String(l.jlink || ''))), 'jc-link ' + (out ? 'jc-link-web' : 'jc-link-in'));
      b.dataset.link = l.id;
      if (out) { b.title = l.href; b.append(icon('out', 'jc-link-icon')); }
      row.append(b);
    }
    box.append(row);
  }
  const at = place(box, hooks.at, m);
  if (at) {
    at.dataset.at = '1'; at.scrollIntoView({ block: 'start' }); at.classList.add('jc-flash');
    setTimeout(() => { at.classList.remove('jc-flash'); delete at.dataset.at; }, 1600);
  }
  // Storage paths belong to the managing skill, not the owner's card surface.
}

/** A button: a draft goes into the message box; anything else is shown whole on the official confirm card first. */
async function run(a, hooks) {
  const text = String(a.prompt || '');
  if (!text) return;
  if (a.draft) { hooks.draft(text); return; }
  const ask = confirmSheet(t('p360.confirmTitle'), text, t('p360.send'));
  const shown = document.getElementById('confirm-text');
  if (shown) shown.textContent = text;   // exactly what will be sent (the sheet's fillText would turn `…` into code)
  if (await ask) hooks.say(text);
}

// ---------------------------------------------------------------- Settings → Cards
/** Fills #set-cards-list: official cards (default off) and the owner's own, each with its switch. hooks: { note(text) }. */
export function renderSettings(hooks) {
  const box = document.getElementById('set-cards-list');
  if (!box) return;
  box.replaceChildren();
  const groups = [['p360.official', cards().filter((c) => c.official)], ['p360.mine', cards().filter((c) => !c.official)]];
  for (const [title, rows] of groups) {
    box.append(node('h3', t(title), 'setg'));
    if (!rows.length) { box.append(node('p', t(title === 'p360.mine' ? 'p360.mineEmpty' : 'p360.loading'), 'setnote')); continue; }
    for (const c of rows) {
      const row = node('div', undefined, 'setrow jc-setrow'); row.dataset.card = c.id;
      const ic = node('span', undefined, 'jc-dot jc-accent-' + c.accent); ic.append(icon(c.icon, 'jc-dot-icon'));
      const lab = node('span', undefined, 'setlab jc-setlab'); lab.append(node('b', nameOf(c)), node('small', pick(c.desc)));
      const sw = button('', null, 'tgl'); sw.setAttribute('role', 'switch'); sw.setAttribute('aria-checked', String(!!c.on)); sw.setAttribute('aria-label', nameOf(c)); sw.append(node('i'));
      sw.onclick = async () => {
        const on = sw.getAttribute('aria-checked') !== 'true';
        sw.setAttribute('aria-checked', String(on)); sw.disabled = true;
        const ok = await setOn(c.id, on);
        sw.disabled = false;
        if (!ok) { sw.setAttribute('aria-checked', String(!on)); hooks.note(t('p360.setFailed')); }
      };
      row.append(ic, lab, sw); box.append(row);
    }
  }
}

// An entry editor stays on the card; Escape or Cancel discards the draft.
function editNote(value) {
  return new Promise(resolve=>{
    const dialog=node('dialog',undefined,'jc-dialog'), title=node('h2',t('p386.edit')), input=node('textarea'); input.value=value; input.setAttribute('aria-label',t('p386.edit')); input.maxLength=16000;
    const done=v=>{dialog.close();dialog.remove();resolve(v);};
    dialog.append(title,input,button(t('p386.cancel'),()=>done(null)),button(t('p386.save'),()=>done(input.value)));
    dialog.addEventListener('cancel',e=>{e.preventDefault();done(null);});document.body.append(dialog);dialog.showModal();
  });
}
