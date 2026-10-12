// P179 (PROTOCOL §10.16a): rename THIS remote from its message card. Long-press the source label at the card's top left
// (touch hold / mouse button held ≥ 500 ms; the double tap there still jumps to the first message) → the P150 global
// centred dialog. Only this remote's own name: there is no target field on the wire; the owner renames every remote on
// the account page. Same host writer, audit and owner_name field as the account page. ESC / outside / 取消 = close.
// Names are host data: textContent only.
import { t, onLang } from './t.js';
import { registerLayer } from './layers.js';
import { ask1, isReady } from './session.js';
import { deviceInfo } from './settings.js';

const $ = (id) => document.getElementById(id);
let ctx = { myDev: () => null };
let other = null;           // the remote whose label was held, when it is not this one (shown as a note)
let openedAt = -Infinity;   // the finger lifting after the hold lands on the backdrop: that click must not close it

const isOpen = () => !$('renameDlg').hidden;
export function close() { const d = $('renameDlg'); if (d) d.hidden = true; }

function paint() {
  const me = deviceInfo(ctx.myDev());
  $('renameTitle').textContent = t('ren.title');
  $('renameSub').textContent = other ? t('ren.other', { name: other }) : t('ren.sub');
  const orig = me && me.named_by === 'owner' && me.self_name ? me.self_name : '';
  $('renameOrig').hidden = !orig;
  $('renameOrig').textContent = orig ? t('ren.orig', { name: orig }) : '';
}

/** Open for this remote. src = the page's source (a phone page); another remote's label only adds a note. */
export function open(src) {
  if (!isReady()) return;
  const mine = ctx.myDev();
  other = src && src.k === 'phone' && src.dev && src.dev !== mine ? (deviceInfo(src.dev)?.label || src.name || t('r.src.otherDevice')) : null;
  const me = deviceInfo(mine);
  $('renameInput').value = me ? (me.named_by === 'owner' ? me.name : '') : '';
  $('renameInput').placeholder = me && me.named_by !== 'owner' ? me.name : t('ren.placeholder');
  $('renameMsg').textContent = '';
  $('renameSave').disabled = false;
  paint();
  $('renameDlg').hidden = false;
  openedAt = performance.now();
  $('renameInput').focus({ preventScroll: true });
}

async function save() {
  const name = $('renameInput').value.trim();
  if (!name) { $('renameMsg').textContent = t('ren.empty'); return; }
  if ([...name].length > 64) { $('renameMsg').textContent = t('ren.long'); return; }
  $('renameSave').disabled = true;
  $('renameMsg').textContent = t('ren.saving');
  const m = await ask1({ t: 'dev_rename', name }, 'dev_rename_res', 8000);
  $('renameSave').disabled = false;
  if (m.t === 'dev_rename_res' && m.ok === true) { close(); return; }
  $('renameMsg').textContent = m.t === 'dev_rename_res' ? t(m.why === 'gone' ? 'ren.gone' : 'ren.bad') : t('ren.offline');
}

export function init({ myDev } = {}) {
  if (myDev) ctx.myDev = myDev;
  $('renameCancel').addEventListener('click', close);
  $('renameSave').addEventListener('click', save);
  $('renameInput').addEventListener('keydown', (e) => { if (e.key === 'Enter' && !e.isComposing) { e.preventDefault(); save(); } });
  $('renameDlg').addEventListener('click', (e) => { if (e.target === e.currentTarget && performance.now() - openedAt > 700) close(); });
  registerLayer({ element: () => $('renameDlg'), open: isOpen, close, fallback: () => $('omFirst'), rank: 5 });
  onLang(() => { if (isOpen()) paint(); });
}
