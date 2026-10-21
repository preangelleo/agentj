import {phoneConfirmation} from './tvremote.js';
// P361 (PROTOCOL §34) · Settings → 紧急修复 → 「Agent J 主机上的命令」: terminal commands on the Agent J host,
// each with ⧉ and 「执行」. Only the names the host lists in `host.run_cmds` (its whitelist, host/agentj/cmdrun.py) are shown;
// the phone sends a NAME, never a command line. Every run: signed `cmd_run` → the host answers `passkey_required` + the
// challenge → Face ID (started inside the tap of the sheet's button, iOS gesture rule) → the same signed object with `fa` →
// `ctl_pending` → `ctl_res {ok, cmd, code, out, cut?}`. The output is already masked on the computer; it is shown as text
// (textContent), never stored. A phone without a passkey is told so and offered the F20 「设置 Face ID」 — no fallback.
import { signedWriteFa, whyText } from './controls.js';
import { approve as faApprove, canAssert } from './faceid.js';
import { unb64u } from '../proto/wire.js';
import { isReady } from './session.js';
import { copyable } from './copycode.js';
import { t, onLang } from './t.js';
import { el, mk, confirmSheet, toastAction } from './ui.js';
import { usable as termUsable } from './termhook.js';   // P408: 「在终端里打开」 only while the terminal is on for this phone

export const CMDS = { doctor: 'doctor', reconnect: 'doctor --reconnect', status: 'status', service_status: 'service status',
  rescue_status: 'rescue status', update_check: 'update check' };
const line = (name) => 'agentj ' + CMDS[name];
let H = { setupFaceId() {}, openTerminal() {} }, names = [], R = null, term = false;   // R: the last run {name, st: run|done|fail, code, out, cut, why}

export function configure(h) { H = { ...H, ...h }; onLang(paint); }
/** settings.js render(): the host block decides which rows exist (an older host: the card stays hidden). */
export function render(info) {
  const list = info && Array.isArray(info.run_cmds) ? info.run_cmds.filter((n) => typeof n === 'string' && Object.hasOwn(CMDS, n)) : [];
  if (list.join() !== names.join() || term !== termUsable()) { names = list; term = termUsable(); paint(); }
}
function row(name) {
  const li = mk('li', 'rscmd'); li.dataset.cmd = name;
  const main = mk('div', 'rscmdmain'), code = mk('code', '', line(name));
  main.append(copyable(code, { variant: 'inline' }), mk('small', '', t('p361.cmd.' + name)));
  if (name === 'reconnect') main.append(mk('small', 'rsnote', t('p438.recommended')));
  const b = mk('button', 'aj-btn rscmdgo', t('p361.run')); b.type = 'button'; b.id = 'rs-cmd-' + name;
  b.setAttribute('aria-label', t('p361.runAria', { cmd: line(name) }));
  b.disabled = !!(R && R.st === 'run');
  b.addEventListener('click', () => run(name));
  li.append(main, b);
  if (term) {                         // P408: prefilled in the terminal's input line; it runs only when the owner presses Enter
    const o = mk('button', 'aj-btn rscmdterm', t('p408.inTerm')); o.type = 'button'; o.id = 'rs-cmd-term-' + name;
    o.setAttribute('aria-label', t('p408.inTermAria', { cmd: line(name) }));
    o.addEventListener('click', () => H.openTerminal(line(name)));
    li.append(o);
  }
  return li;
}
function result() {
  if (!R) return [];
  const head = mk('p', 'rscmdhead', line(R.name)); head.id = 'rs-cmd-head';
  if (R.st === 'ask') return [head];
  if (R.st === 'run') return [head, mk('p', 'rsnow', t('p361.running'))];
  const out = [head];
  const bar = mk('div', 'rscmdstatus');
  const close = mk('button', 'menuicon rscmdclose', '×'); close.type = 'button'; close.id = 'rs-cmd-close';
  close.setAttribute('aria-label', t('p380.closeResult')); close.title = t('p380.closeResult');
  close.addEventListener('click', () => { const name = R.name; R = null; paint(); el('rs-cmd-' + name)?.focus(); });
  if (R.why === 'restored') bar.append(mk('p', 'rsok', t('p392.restored')));   // P393: the 「连不上」 of a tap made offline turns green
  else if (R.fold) bar.append(mk('p', R.code === 0 ? 'rsok' : 'rsmsg rsfail', t(R.code === 0 ? 'p393.sumOk' : 'p393.sumFail', { time: R.hm || '' })));
  else if (Number.isInteger(R.code) && R.code >= 0) bar.append(mk('p', R.code === 0 ? 'rsok' : 'rsmsg rsfail', t(R.code === 0 ? 'p361.ok' : 'p361.fail', { code: R.code })));
  else bar.append(mk('p', 'rsmsg rsfail', ['busy', 'timeout', 'no_passkey', 'dropped'].includes(R.why) ? t('p361.why.' + R.why) : whyText(R.why) || t('p361.failed')));
  bar.append(close); out.push(bar);
  if (R.fold) {                        // P393: Settings opened again — one line; the output stays under 「详情」
    if (!R.out) return out;
    const d = mk('details', 'rsmore'); d.id = 'rs-cmd-log'; d.append(mk('summary', '', t('p332.details')));
    const pre = mk('pre', 'rsout', R.out); pre.id = 'rs-cmd-out-text'; pre.tabIndex = 0; d.append(copyable(pre, { variant: 'block', click: false }));
    out.push(d); return out;
  }
  if (R.out) { const pre = mk('pre', 'rsout', R.out); pre.id = 'rs-cmd-out-text'; pre.tabIndex = 0; out.push(copyable(pre, { variant: 'block', click: false })); }
  if (R.cut) out.push(mk('p', 'rsnote', t('p361.cut')));
  if (R.out) out.push(mk('p', 'rsnote', t('p361.masked')));
  return out;
}
export function paint() {
  term = termUsable();
  const box = el('rs-cmds'), ul = el('rs-cmd-list'), res = el('rs-cmd-out');
  if (!box || !ul || !res) return;
  box.hidden = !names.length;
  ul.replaceChildren(...names.map(row));
  res.replaceChildren(...result());
  res.hidden = !R;
}
/** The Face ID step: one sheet; its button starts get() synchronously inside the tap. null = cancelled. */
async function faceId(res) {
  if (typeof res.fa !== 'string' || typeof res.fa_ch !== 'string' || !canAssert()) return Promise.reject(new Error('passkey_failed'));
  const yes = el('confirm-yes');
  let p = null;
  const grab = () => { p = faApprove(unb64u(res.fa_ch), res.fa); p.catch(() => {}); };
  yes.addEventListener('click', grab);
  const ok = await confirmSheet(t('p361.faTitle'), t('p361.faText', { cmd: line(R.name) }), t('p361.faYes'));
  yes.removeEventListener('click', grab);
  if (!ok || !p) return null;
  R.st = 'run'; paint();
  return p;
}
export async function run(name) {
  if(phoneConfirmation())return;
  if (!Object.hasOwn(CMDS, name) || !names.includes(name) || (R && R.st === 'run')) return;
  R = { name, st: 'ask' };
  if (!isReady()) { R = { name, st: 'fail', why: 'offline', hm: new Date().toTimeString().slice(0, 5) }; return paint(); }
  paint();
  const m = await signedWriteFa('cmd_run', { cmd: name }, { t: 'cmd_run', cmd: name }, faceId);
  if (m.why === 'cancel') { R = null; return paint(); }
  const str = (v) => (typeof v === 'string' ? v.slice(0, 20000) : '');
  // the link dropped while the computer ran it (doctor --reconnect may restart Agent J there): say so, not a bare failure
  if (m.why === 'offline' && R && R.st === 'run') m.why = 'dropped';
  R = { name, st: m.ok ? 'done' : 'fail', code: Number.isInteger(m.code) ? m.code : -1, out: str(m.out), cut: m.cut === true,
    why: m.ok ? '' : String(m.why || ''), hm: new Date().toTimeString().slice(0, 5) };
  if (m.why === 'no_passkey' && phoneConfirmation(true)) { paint(); return; }
  if (m.why === 'no_passkey') toastAction(t('p361.why.no_passkey'), t('sout.setup'), () => H.setupFaceId(), 8000);
  paint();
  el('rs-cmd-out')?.scrollIntoView({ block: 'nearest' });
}
/** P393 · settings.js open(): an ended result opens folded to one line (× still closes it). */
export function foldEnded() { if (R && (R.st === 'done' || R.st === 'fail') && !R.fold) { R.fold = true; paint(); } }
/** P393 · app.js onReady: a tap refused because the link was down now says the link is back. */
export function onReady() { if (R && R.st === 'fail' && R.why === 'offline') { R.why = 'restored'; paint(); } }
// tests
export const _debug = () => ({ names: [...names], R: R && { ...R } });
export function _reset() { names = []; R = null; paint(); }
