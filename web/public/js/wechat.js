// P501: pairing material stays in this ephemeral panel, inside the paired Noise session.
import { peer, isReady } from './session.js';
import { sendApp, gen } from './session.js';
import { signedWrite, signedWriteFa } from './controls.js';
import { faceIdUnbind, unbindFaWhy } from './remotes.js';   // P551: unbind = Face ID, like unbinding another remote
import { t, onLang, lang, fillText } from './t.js';
import { mk, toast } from './ui.js';
import { registerLayer } from './layers.js';
import * as forwarding from './forward-choice.js';
let hooks = {};
let root, state = {}, open = false, timer, autoOpen = true;
const $ = id => document.getElementById(id);
// P540: the host's last avatar outcome — fixed words only (error · detail), never native output.
const WORD = /^[A-Za-z0-9_]{1,40}$/;
const KINDS = {claude: 'Claude Code', codex: 'Codex', opencode: 'OpenCode'};
function renderAssistant() {
  const a = state.bound && state.assistant && typeof state.assistant === 'object' ? state.assistant : null;
  const line = $('wc-assist'), hint = $('wc-assist-hint');
  const kind = KINDS[a?.kind] || 'Agent J';
  const reason = a ? [a.error, a.detail].filter(w => typeof w === 'string' && WORD.test(w)).join(' · ') : '';
  line.hidden = hint.hidden = !a;
  if (!a) { line.textContent = ''; hint.replaceChildren(); return; }
  line.textContent = a.ok ? t('wc.assistOk', {kind}) : t('wc.assistFail', {kind, reason: reason || 'unknown'});
  hint.hidden = !!a.ok;
  if (a.ok) hint.replaceChildren(); else fillText(hint, t('wc.assistHint'));
}
function render() {
  if (!root) return;
  $('wc-title').textContent = t('wc.title');
  $('wc-note').textContent = t(lang() === 'zh' ? 'p536.recommendZh' : 'p536.recommendEn', {channel: 'Telegram'});
  $('wc-status').textContent = t(state.bound && state.greeting === 'needs_message' ? 'p536.greet' : state.bound ? 'wc.bound' : state.error ? 'wc.error' : 'wc.unbound');
  $('wc-setup').textContent = t('wc.setup');
  $('wc-unbind').textContent = t('wc.unbind');
  $('wc-close').textContent = t('wc.close');
  $('wc-verify').textContent = t('wc.verify');
  $('wc-code').setAttribute('aria-label', t('wc.code'));
  $('wc-setup').hidden = !!state.bound || !!state.qr;
  $('wc-unbind').hidden = !state.bound;
  $('wc-qr').replaceChildren();
  if (state.qr && typeof state.qr.img === 'string' && state.qr.img.length <= 200000 && /^[A-Za-z0-9+/=]+$/.test(state.qr.img)) {
    const img = mk('img'); img.src = 'data:image/png;base64,' + state.qr.img; img.alt = t('wc.scan');
    $('wc-qr').append(img);
    $('wc-status').textContent = t(state.qr.status === 'need_verifycode' ? 'wc.code' : 'wc.scan');
  }
  $('wc-code-row').hidden = state.qr?.status !== 'need_verifycode';
  $('wc-pending').textContent = state.pending ? t('wc.pending', {n: state.pending}) : '';
  renderAssistant();
}
function build() {
  if (root) return;
  root = mk('div', 'ajmodal dlgcard'); root.id = 'wechat-panel'; root.hidden = true;
  root.setAttribute('role', 'dialog'); root.setAttribute('aria-modal', 'true'); root.setAttribute('aria-labelledby', 'wc-title');
  const box = mk('div', 'box');
  for (const [tag, id] of [['h2','wc-title'],['p','wc-note'],['p','wc-status'],['div','wc-qr'],['p','wc-pending'],['p','wc-assist'],['p','wc-assist-hint']]) {
    const n = mk(tag, tag === 'p' ? 'setnote' : ''); n.id = id; box.append(n);
  }
  const row = mk('div'); row.id = 'wc-code-row'; row.hidden = true;
  const code = mk('input', 'aj-input'); code.id = 'wc-code'; code.type = 'password'; code.inputMode = 'numeric'; code.maxLength = 16; code.autocomplete = 'one-time-code';
  const verify = mk('button', 'aj-btn'); verify.id = 'wc-verify'; verify.type = 'button';
  row.append(code, verify); box.append(row);
  for (const id of ['wc-setup','wc-unbind','wc-close']) {
    const b = mk('button', 'aj-btn'); b.id = id; b.type = 'button'; box.append(b);
  }
  box.querySelector('#wc-qr').setAttribute('data-screenshot-private', '');
  box.querySelector('#wc-code-row').setAttribute('data-screenshot-private', '');
  root.append(box); document.body.append(root);
  $('wc-setup').addEventListener('click', () => {
    close(); autoOpen = true;
    if (hooks.connect) hooks.connect('wx');
  });
  $('wc-unbind').addEventListener('click', () => act('unbind'));
  $('wc-verify').addEventListener('click', () => act('verify'));
  $('wc-close').addEventListener('click', close);
  registerLayer({element: () => root, open: () => open, rank: 56, close,
    fallback: () => $('set-wechat') || $('input')});
  onLang(render);
}
async function act(op) {
  const target = {op, id: op === 'verify' ? state.qr?.id || '' : '', code: op === 'verify' ? $('wc-code').value : ''};
  $('wc-code').value = '';
  const r = op === 'unbind' ? await signedWriteFa('wechat', target, {t:'wechat', ...target}, faceIdUnbind)
    : await signedWrite('wechat', target, {t:'wechat', ...target});
  const fa = r.ok ? undefined : unbindFaWhy(r.why);
  if (fa === null) return;
  if (fa) toast(fa, 8000);
  else if (!r.ok) toast(t('wc.error'), 3000);
  else if (op === 'unbind') { state = {bound:false}; render(); }
}
function close() {
  open = false; autoOpen = false; root.hidden = true;
  $('wc-code').value = ''; $('wc-qr').replaceChildren();
  state = {...state, qr: undefined};
  clearTimeout(timer);
}
export function show() {
  build(); autoOpen = true; open = true; root.hidden = false; render();
  sendApp({t:'wechat', op:'status'}, gen()).catch(() => toast(t('wc.error'), 3000));
}
export function channelStatus(m) {
  if (!m.channels) return;
  peer.tgbound = m.channels.tg_bound === true;
  peer.tgfwd = m.channels.tg_forward === true;
  window.dispatchEvent(new Event('agentj-forward-change'));
}
export function route(m) {
  if (m.t !== 'wechat_state') return;
  state = m;
  peer.wxbound = m.bound === true;
  peer.wxfwd = peer.p33 && m.can_forward === true;   // P554: no countdown — the computer says when WeChat refused
  window.dispatchEvent(new Event('agentj-forward-change'));
  renderChannels();
  if (m.bound && m.greeting === 'needs_message' && open) render();
  if ($('set-wechat-row')) $('set-wechat-row').hidden = false;
  // A fresh ephemeral QR opens automatically after setup requested by the main Agent / terminal.
  if (m.qr && !open && autoOpen) { build(); open = true; root.hidden = false; }
  render();
  clearTimeout(timer);
  if (m.qr) timer = setTimeout(() => { state = {...state, qr: undefined}; render(); }, 120000);
}
// P554: Settings asks the computer once on open, so a missed push never leaves a bound WeChat greyed out.
export function refresh() { if (isReady() && peer.p33) sendApp({t:'wechat', op:'status'}, gen()).catch(() => {}); }
export function reset() { peer.wxfwd = false; state = {}; if (root) close(); autoOpen = true; if ($('set-wechat-row')) $('set-wechat-row').hidden = true; }
export function configure(options = {}) {
  hooks = options;
  $('set-wechat')?.addEventListener('click', () => hooks.connect?.('wx'));
  $('set-tgpair')?.addEventListener('click', () => hooks.connect?.('tg'));
  $('set-fwd-wx-bind')?.addEventListener('click', () => hooks.connect?.('wx'));   // P554: the 去绑定 under the greyed option
  forwarding.configure();
  window.addEventListener('agentj-forward-change', renderChannels);
  orderChannels(); onLang(() => {orderChannels(); renderChannels();});
}
export function renderChannels() {
  if ($('set-chan')) $('set-chan').textContent = 'Telegram';
  for (const [id, channel, bound] of [['wechat','wx',peer.wxbound], ['telegram','tg',peer.tgbound]]) {
    const note = $(id === 'wechat' ? 'set-wechat-note' : 'set-chan-note');
    if (note) note.textContent = t('p536.' + channel + 'Note', {channel: 'Telegram'});
    const status = $('set-' + id + '-status');
    if (status) status.textContent = t(bound ? (channel === 'wx' && !peer.wxfwd ? 'p536.greet' : 'wc.bound') : 'wc.unbound');
  }
  forwarding.render();
}
function orderChannels() {
  // Order is only a recommendation: both channels remain available.
  const wc = $('set-wechat-row'), tg = $('set-tgpair')?.closest('.setrow');
  if (wc && tg && wc.parentElement === tg.parentElement) {
    if (lang() === 'zh') tg.before(wc); else tg.after(wc);
  }
}
