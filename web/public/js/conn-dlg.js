// 0.30.6 (chairman 11-10 12:14 「上面不允许插入通知栏，所有的通知和按钮都是中间弹出来的那种组件」): the offline notice is
// the one global centred dialog (P150 `.gdlg`, ADR-P207-dialog), never a row under the header. A drop shorter than
// POP_AFTER_MS says nothing but the grey header dot (body[data-relink], app.css); only a link still `down` (reconnect.js,
// foreground time only) after POP_AFTER_MS opens this card. Four states (#connDlg[data-state]):
//   down          the relay answers, the Agent J host does not (waiting-host)  稍后 · 立即重试
//   reconnecting  this device cannot reach the relay                          稍后 · 立即重试
//   update        the web has a newer page than this one (version.json)        稍后 · 更新并刷新
//   up            the card was showing and the link came back: 「已重新连上」, closes by itself after UP_MS
// It never takes focus (aria-modal=false, like #version-notice); 稍后 / Esc / a tap outside hide it until the next outage.
// It waits while Settings, a sheet or another dialog is in front (Emergency repair must stay usable).
import { t, onLang } from './t.js';
import { registerLayer } from './layers.js';

export const POP_AFTER_MS = 10000;
export const UP_MS = 2000;

/** Pure: which card the painted state asks for (null = none). */
export function connMode({ link = 'up', elapsed = 0, painted = '', paired = false, covered = false, offer = null } = {}) {
  if (link !== 'down' || elapsed < POP_AFTER_MS || !paired || covered) return null;
  if (offer) return 'update';
  return painted === 'waiting-host' ? 'down' : 'reconnecting';
}

let H = { link: () => 'up', elapsed: () => 0, painted: () => '', paired: () => false, offer: () => null, retry() {}, refresh() {}, yieldTo() {} };
let mode = null, dismissed = false, tick = 0, upTimer = 0;
const $ = (id) => document.getElementById(id);
const COVER = '#settings:not([hidden]),.ajmodal:not([hidden]):not(.cp-docked),#confirm:not([hidden]),.sheet:not([hidden]),dialog[open],'
  + '.gdlg:not([hidden]):not(#connDlg):not(#version-notice):not(#session-help)';
function covered() { return [...document.querySelectorAll(COVER)].some((n) => n.getClientRects().length); }

function paint() {
  const box = $('connDlg');
  if (!box) return;
  box.hidden = !mode;
  H.yieldTo(mode === 'update');
  if (!mode) return;
  box.dataset.state = mode;
  const offer = H.offer();
  const title = { down: 'p0306.conn.downTitle', reconnecting: 'off.title.reconnecting', update: 'p0306.conn.updTitle', up: 'p0306.conn.upTitle' }[mode];
  const text = { down: 'p0306.conn.downText', reconnecting: 'p0306.conn.reText', update: 'p0306.conn.updText', up: '' }[mode];
  $('connTitle').textContent = t(title);
  $('connText').textContent = text ? t(text, { v: offer || '' }) : '';
  $('connText').hidden = !text;
  $('connRow').hidden = mode === 'up';
  $('connRetry').hidden = mode === 'update';
  $('connRefresh').hidden = mode !== 'update';
}

/** Called on every painted status / link change / view change, and once a second while the link is not up. */
export function update() {
  const link = H.link();
  if (link === 'up') {
    clearInterval(tick); tick = 0;
    if (mode && mode !== 'up' && !dismissed) {          // the card was showing: say so briefly, then go
      mode = 'up'; paint();
      clearTimeout(upTimer); upTimer = setTimeout(() => { if (mode === 'up') { mode = null; paint(); } }, UP_MS);
    } else if (mode !== 'up') { mode = null; paint(); }
    dismissed = false;
    return;
  }
  if (!tick) tick = setInterval(update, 1000);
  clearTimeout(upTimer);
  if (mode === 'up') mode = null;
  const next = dismissed ? null : connMode({ link, elapsed: H.elapsed(), painted: H.painted(), paired: H.paired(), covered: covered(), offer: H.offer() });
  if (next !== mode) { mode = next; paint(); }
}
function later() { if (mode && mode !== 'up') dismissed = true; mode = null; paint(); }

export function init(hooks) {
  H = { ...H, ...hooks };
  $('connLater').addEventListener('click', later);
  $('connRetry').addEventListener('click', () => H.retry());
  $('connRefresh').addEventListener('click', () => H.refresh());
  $('connDlg').addEventListener('click', (e) => { if (e.target === e.currentTarget) later(); });   // outside the square = later
  registerLayer({ element: () => $('connDlg'), open: () => !$('connDlg').hidden, close: later, fallback: () => $('setBtn'), trap: false, rank: -9, preserveOutsideFocus: true });
  onLang(paint);
}
export const _debug = () => ({ mode, dismissed, tick: !!tick });
