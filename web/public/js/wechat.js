// P501: pairing material stays in this ephemeral panel, inside the paired Noise session.
import { sendApp, gen } from './session.js';
import { signedWrite } from './controls.js';
import { t, onLang, lang } from './t.js';
import { mk, toast } from './ui.js';
import { registerLayer } from './layers.js';
let root, state = {}, open = false, timer, autoOpen = true;
const $ = id => document.getElementById(id);
function render() {
  if (!root) return;
  $('wc-title').textContent = t('wc.title');
  $('wc-note').textContent = t('wc.note');
  $('wc-status').textContent = t(state.bound ? 'wc.bound' : state.error ? 'wc.error' : 'wc.unbound');
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
}
function build() {
  if (root) return;
  root = mk('div', 'ajmodal dlgcard'); root.id = 'wechat-panel'; root.hidden = true;
  root.setAttribute('role', 'dialog'); root.setAttribute('aria-modal', 'true'); root.setAttribute('aria-labelledby', 'wc-title');
  const box = mk('div', 'box');
  for (const [tag, id] of [['h2','wc-title'],['p','wc-note'],['p','wc-status'],['div','wc-qr'],['p','wc-pending']]) {
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
  $('wc-setup').addEventListener('click', () => act('setup'));
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
  const r = await signedWrite('wechat', target, {t:'wechat', ...target});
  if (!r.ok) toast(t('wc.error'), 3000);
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
export function route(m) {
  if (m.t !== 'wechat_state') return;
  state = m;
  if ($('set-wechat-row')) $('set-wechat-row').hidden = false;
  // A fresh ephemeral QR opens automatically after setup requested by the main Agent / terminal.
  if (m.qr && !open && autoOpen) { build(); open = true; root.hidden = false; }
  render();
  clearTimeout(timer);
  if (m.qr) timer = setTimeout(() => { state = {...state, qr: undefined}; render(); }, 120000);
}
export function reset() { state = {}; if (root) close(); autoOpen = true; if ($('set-wechat-row')) $('set-wechat-row').hidden = true; }
export function configure() {
  $('set-wechat')?.addEventListener('click', show);
  orderChannels(); onLang(orderChannels);
}
function orderChannels() {
  // Order is only a recommendation: both channels remain available.
  const wc = $('set-wechat-row'), tg = $('set-tgpair')?.closest('.setrow');
  if (wc && tg && wc.parentElement === tg.parentElement) {
    if (lang() === 'zh') tg.before(wc); else tg.after(wc);
  }
}
