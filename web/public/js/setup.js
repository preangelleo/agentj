// P115 (P92, ADR-A196): the first-run checklist card. The host owns the journal and every check; this card only shows the
// host's snapshot (same revision on the phone and this computer's browser) and sends the owner's own taps:
//   Use / I won't use this / Later / Start the leaving check → a signed `setup_mark` (agentjarvis-control-v1 over item, choice,
//   revision — a stale revision comes back `changed` and the card re-reads); "I'm done, check" → `setup_get` with check:true
//   (a read-only re-probe: the owner's word is a request to check, never proof). Nothing is stored here; no secrets, QR codes
//   or links ever appear on this card. Required items have no "I won't use this". Item titles / hints come from the host.
import { ask1, isReady } from './session.js';
import { signedWrite, whyText } from './controls.js';
import { t, lang, onLang } from './t.js';
import { mk, toast } from './ui.js';

const pick = (o) => (o && typeof o === 'object' ? String((lang() === 'en' ? o.en : o.zh) ?? '') : '');
const GROUPS = ['core', 'machine', 'browser', 'google', 'privacy', 'optional'];
const WHERE = ['auto', 'local', 'phone'];
const STATES = ['verified', 'pending', 'waiting_local', 'attention', 'deferred', 'unsupported', 'not_applicable', 'stale'];
const stateText = (s) => (STATES.includes(s) ? t('setup.st.' + s) : String(s));
const whereText = (w) => t('setup.where.' + (WHERE.includes(w) ? w : 'local'));
let root = null, S = null, busy = false, expanded = false;

function valid(m) {
  return m && Number.isInteger(m.revision) && m.revision > 0 && Array.isArray(m.items) && m.items.length <= 64 &&
    m.items.every((i) => i && typeof i.id === 'string' && /^[a-z]{2,20}$/.test(i.id)) && m.counts && typeof m.counts === 'object';
}

function build() {
  if (root) return root;
  root = mk('div', 'ajmodal setupcard');
  root.id = 'setupcard'; root.hidden = true;
  root.setAttribute('role', 'dialog'); root.setAttribute('aria-modal', 'true'); root.setAttribute('aria-labelledby', 'setup-title');
  root.appendChild(mk('div', 'box'));
  document.body.appendChild(root);
  onLang(() => render());
  return root;
}

/** A host snapshot (`setup_card`): show:true opens the card; show:false only refreshes an open one. */
export function card(m) {
  if (!valid(m)) return;
  if (S && m.revision < S.revision) return;           // an older snapshot never replaces a newer one
  S = m;
  build();
  if (m.show === true) root.hidden = false;
  render();
}

export function clear() { S = null; busy = false; expanded = false; if (root) root.hidden = true; }
export const isOpen = () => !!root && !root.hidden;

const btn = (cls, label, fn, id) => {
  const b = mk('button', 'aj-btn ' + cls, label); b.type = 'button'; if (id) b.id = id;
  b.disabled = busy; b.addEventListener('click', () => { if (!busy) fn(); }); return b;
};

async function mark(item, choice) {
  if (!S || busy) return;
  busy = true; render();
  const r = await signedWrite('setup_mark', { item, choice, rev: S.revision }, { t: 'setup_mark', item, choice, rev: S.revision });
  busy = false;
  if (!r.ok) {
    if (r.why === 'changed') await refresh(false);
    else toast(t('setup.notSaved', { why: whyText(r.why) }), 4000);
  }
  render();
}

function netHint() {
  const c = navigator.connection;
  const type = c && typeof c.type === 'string' ? c.type : '';
  return type === 'cellular' ? 'cellular' : type === 'wifi' ? 'wifi' : 'unknown';
}

async function micState() {
  try { const p = await navigator.permissions.query({ name: 'microphone' }); return ['granted', 'denied', 'prompt'].includes(p.state) ? p.state : null; } catch { return null; }
}

/** Ask the host for the snapshot; with check, a live read-only re-probe (optionally one item). */
export async function refresh(check, item) {
  if (!isReady()) { toast(t('setup.offline'), 3000); return; }
  busy = true; render();
  const req = { t: 'setup_get', check: !!check, net: netHint() };
  if (item) req.item = item;
  const mic = item === 'phonepermissions' || !item ? await micState() : null;
  if (mic) req.perm = { mic };
  const m = await ask1(req, 'setup_card', 60000);
  busy = false;
  if (m && m.t === 'setup_card' && valid(m)) { S = m; build(); }
  else if (m && m.t === 'setup_card' && m.result === 'needs_setup') toast(t('setup.needsSetup'), 4000);
  else if (m && m.t !== 'setup_card') toast(t('setup.unreachable'), 3000);
  render();
}

function counts(c) {
  return t('setup.counts', { v: c.verified, u: c.unused, t: c.todo, a: c.auto, d: c.undecided });
}

function overview(box) {
  const wrap = mk('div', 'setup-all');
  for (const g of GROUPS) {
    const rows = S.items.filter((i) => i.group === g);
    if (!rows.length) continue;
    const gt = (S.groups || []).find((x) => x.id === g);
    wrap.appendChild(mk('h3', 'setup-group', pick(gt?.title) || g));
    const ul = mk('ul', 'setup-list');
    for (const i of rows) {
      const li = mk('li', 'setup-row');
      const st = i.choice === 'unused' ? t('setup.st.unused')
        : i.choice === 'undecided' && i.state !== 'verified' && i.state !== 'not_applicable'
          ? t('setup.st.undecided') + (i.state === 'unsupported' ? ' · ' + stateText('unsupported') : '')
          : stateText(i.state);
      li.dataset.state = i.choice === 'unused' ? 'unused' : i.state;
      li.append(mk('span', 'setup-name', pick(i.title)), mk('span', 'setup-st', st));
      ul.appendChild(li);
    }
    wrap.appendChild(ul);
  }
  box.appendChild(wrap);
}

function actions(box, i) {
  const row = mk('div', 'ajrow setup-actions');
  const optional = i.need !== 'required';
  const unused = () => row.append(btn('', t('setup.unused'), () => mark(i.id, 'unused'), 'setup-unused'));
  if (i.id === 'exit') {
    if (S.exit_armed) {
      box.appendChild(mk('p', 'setup-note', t('setup.exitNote')));
      row.append(btn('aj-btn--primary', t('setup.exitSent'), () => refresh(true, 'exit'), 'setup-check'));
    } else {
      row.append(btn('aj-btn--primary', t('setup.exitStart'), () => mark('exit', 'start_exit'), 'setup-exit'));
    }
  } else if (i.choice === 'undecided') {
    row.append(btn('aj-btn--primary', t('setup.use'), () => mark(i.id, 'selected'), 'setup-use'));
    if (optional) unused();
  } else if (i.state === 'deferred') {
    row.append(btn('aj-btn--primary', t('setup.continue'), () => mark(i.id, 'selected'), 'setup-use'));
    if (optional) unused();
  } else {
    if (i.state !== 'unsupported') row.append(btn('aj-btn--primary', t('setup.check'), () => refresh(true, i.id), 'setup-check'));
    if (optional) unused();
  }
  if (i.id !== 'exit' && i.state !== 'deferred') row.append(btn('aj-btn--quiet', t('setup.later'), () => mark(i.id, 'later'), 'setup-later'));
  box.appendChild(row);
}

function render() {
  if (!root || !S) return;
  const box = root.querySelector('.box');
  box.replaceChildren();
  const cur = S.items.find((i) => i.id === S.current);
  const kicker = S.remote_ready ? t('setup.kickerReady') : t('setup.kicker', { g: S.group, n: GROUPS.length });
  box.append(mk('p', 'setup-kicker', kicker));
  const h = mk('h2', '', S.remote_ready ? t('setup.titleReady') : cur ? pick(cur.title) : t('setup.titleStart'));
  h.id = 'setup-title';
  box.append(h, mk('p', 'setup-counts', counts(S.counts)));
  if (!isReady() && Number.isInteger(S.updated_at)) {     // the host is the authority: offline = its last known revision
    const at = new Date(S.updated_at * 1000).toLocaleTimeString(lang() === 'en' ? 'en-GB' : 'zh-CN', { hour: '2-digit', minute: '2-digit' });
    box.appendChild(mk('p', 'setup-note', t('setup.offlineAt', { at })));
  }
  if (S.remote_ready) {
    box.appendChild(mk('p', 'setup-note', t('setup.readyNote')));
    const un = S.items.filter((i) => i.choice === 'unused').map((i) => pick(i.title));
    if (un.length) box.appendChild(mk('p', 'setup-note', t('setup.notEnabled', { list: un.join(lang() === 'en' ? ', ' : '、') })));
  } else if (cur) {
    const where = cur.state === 'waiting_local' ? 'local' : cur.where;   // "waiting for you" is never "I handle it"
    const tag = cur.state === 'unsupported' ? stateText('unsupported') : whereText(where);
    box.append(mk('p', 'setup-where', tag + ' · ' + stateText(cur.state)), mk('p', 'setup-hint', pick(cur.hint)));
    if (cur.state === 'attention') box.appendChild(mk('p', 'setup-note', t('setup.attention')));
    actions(box, cur);
  } else {
    box.appendChild(mk('p', 'setup-note', t('setup.nothing')));
  }
  const more = mk('div', 'ajrow setup-more');
  more.append(btn('aj-btn--quiet', expanded ? t('setup.hide') : t('setup.all', { n: S.items.length }),
    () => { expanded = !expanded; render(); }, 'setup-expand'),
  btn('aj-btn--quiet', t('setup.close'), () => { root.hidden = true; }, 'setup-close'));
  box.appendChild(more);
  if (expanded) overview(box);
  root.dataset.revision = String(S.revision);
}
