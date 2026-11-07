// #375: only settled replies to a host-verified owner source can offer terminal drafts.
// A code fence is data: never execute it by itself. Host term_prefill checks the empty shell editor again.
// #584 (董事长 11-07 17:15 「在这个拷贝按钮的左边再放一个执行按钮……是不是需要扫码取决于这个 Command 本身安全性」): the same
// gate now offers 「执行」 left of the code block's copy button. One tap = the owner asks; nothing new decides anything:
//   named  the text IS a whitelisted `agentj …` command (cmdrun.js) → the signed cmd_run by NAME (phone too), result here;
//          the common repairs get a plain-words button (「修复连接」…) with the command folded beside it
//   term   this remote may use the web terminal (a phone too: #468/#375) → the 「!」 line path with `run` (terminal.js): the host: read-only →
//          runs, or the signed confirm / Face ID card; the result comes back to this block, never a chat turn
//   open   sudo / a password / a full-screen program → 「在终端里打开」: the terminal opens with the line filled in, never sent
//   agent  the terminal is off / not allowed on this remote (phone and computer alike, 17:58) → an owner message asking the
//          Agent to run it: its own approval cards apply
// Never on a friend's / customer's / channel / quoted / streaming page, a blockquote or <details> inside the reply (ownReply).
// The output box is drawn under the block from the host's masked text (textContent only), kept in memory per page.
import {t} from './t.js';
import {enter, termInfo, usable as termUsable, canEnable as termCanEnable, run as termRun, stopRun} from './termhook.js';
import {nameOf, supports, exec as cmdExec, CMDS} from './cmdrun.js';
import {isReady} from './session.js';
import {newSid} from './api.js';
import {copyable} from './copycode.js';
import {glyph} from './icons.js';
import {toast} from './ui.js';

export function ownReply(p) {
  const s = p?.source;
  return Number.isInteger(p?.id) && p.end !== 'open' && !!s && ['phone','host'].includes(s.k)
    && !s.quote && !s.via && !s.forward && !s.external && !s.friend && !p.assist && !p.external
    && (!p.role || p.role === 'assistant');
}
export function command(text, lang) {
  if (!/^(?:sh|bash|zsh|shell|console|terminal|powershell|pwsh)$/i.test(lang || '')) return null;
  const s = String(text || '').replace(/\n$/, '');
  return s.trim() && s.length <= 4096 && !/[\x00-\x1f\x7f-\x9f\u2028\u2029]/.test(s) ? s : null;
}
export function dangerous(s) {
  // Visual warning only. The host remains authoritative when Enter is pressed.
  return /(?:^|[\s;&|(\x60])["']?(?:\/[^\s;&|"'()]*\/)?(?:sudo|doas|rm|rmdir|shred|mkfs\w*|dd|chmod|chown)["']?(?:\s|$)|\b(?:curl|wget)\b[^\n]*\|\s*(?:\/[^\s|]*\/)?(?:ba|z|da)?sh\b/.test(s);
}
/** #584: needs the owner at the keyboard — a password (sudo / su / passwd / ssh / a login) or a full-screen program. UX
 *  routing only: the host still classifies whatever is finally sent. */
export function needsOwner(s) {
  return /(?:^|[\s;&|(\x60])["']?(?:\/[^\s;&|"'()]*\/)?(?:sudo|doas|su|passwd|ssh|vi|vim|nvim|nano|emacs|less|more|top|htop|btop|man|tmux|screen|watch)["']?(?:\s|$)|(?:^|\s)login(?:\s|$)/.test(s);
}
/** #584: the plain-words buttons for whitelisted commands (cmdrun.js names). */
export const HUMAN = { reconnect: 'p584.h.reconnect', doctor: 'p584.h.doctor', status: 'p584.h.status', service_status: 'p584.h.service',
  rescue_status: 'p584.h.rescue', update_check: 'p584.h.update', version: 'p584.h.version' };
export const OUT_LINES = 12;
/** Which way a tap goes. Pure apart from the four probes passed in. */
export function routeOf(cmd, {named = () => null, usable = () => false, canOpen = () => false} = {}) {
  if (named(cmd)) return 'named';
  if (needsOwner(cmd)) return canOpen() ? 'open' : 'agent';
  return usable() ? 'term' : 'agent';
}

let H = { say: async () => false, agent: () => 'Agent' };
export function configure(h) { H = { ...H, ...h }; }
const runs = new Map();          // `${page.id}:${index}` → {st: run|done|fail, route, out, code, ms, hidden, cut, why, sid, open}
let shown = null;                // {root, page}: the page on screen (a late result repaints only that one)
const KEEP = 64;
const named = (cmd) => { const n = nameOf(cmd); return n && supports(n) ? n : null; };
const probes = { named: (c) => named(c), usable: () => termUsable(), canOpen: () => termUsable() || termCanEnable() };

function mkBtn(cls, text, icon) {
  const b = document.createElement('button'); b.type = 'button'; b.className = cls; b.setAttribute('data-aj-owner', 'aj-terminal');
  if (icon) { b.append(glyph(icon)); b.setAttribute('aria-label',text); b.title=text; }
  if (text && !icon) { const s = document.createElement('span'); s.textContent = text; b.append(s); }
  return b;
}
function remember(key, r) { runs.delete(key); runs.set(key, r); while (runs.size > KEEP) runs.delete(runs.keys().next().value); }
const secs = (ms) => (Number.isInteger(ms) ? (ms < 10000 ? (ms / 1000).toFixed(1) : String(Math.round(ms / 1000))) : null);

export function paint(root, page) {
  for (const n of root.querySelectorAll('.term-command,.runbox,.fixbtn')) n.remove();
  for (const d of root.querySelectorAll('details.fixcmd')) d.replaceWith(...[...d.childNodes].filter((n) => n.tagName !== 'SUMMARY'));
  shown = { root, page };
  if (hostPage(page)) return fixBar(root, page);
  if (!ownReply(page)) return;
  const blocks = [...root.querySelectorAll('.codeblock pre[data-lang]')];
  blocks.forEach((pre, i) => {
    // The reply root is itself a blockquote. Only descendants count as quoted material.
    const quote = pre.closest('blockquote,details');
    if (quote && quote !== root) return;
    const cmd = command(pre.querySelector('code')?.textContent, pre.dataset.lang);
    if (!cmd) return;
    const wrap = pre.closest('.codeblock'), bar = wrap.querySelector('.bar'), key = page.id + ':' + i;
    const route = routeOf(cmd, probes), r = runs.get(key), online = isReady(), busy = r && r.st === 'run';
    const name = route === 'named' ? named(cmd) : null;
    const label = !online ? t('p584.offlineShort') : busy ? t('p584.running')
      : route === 'open' ? t('p584.inTerm') : route === 'agent' ? t('p584.askAgent', { agent: H.agent() }) : t('p584.run');
    const aria = route === 'open' ? t('p584.inTerm') : route === 'agent' ? t('p584.askAgentAria', { agent: H.agent() }) : t('p584.runOn');
    const tap = () => go(key, cmd, route, name, page);
    if (name && HUMAN[name]) {
      // a repair the owner should not need to read: the big button, the command folded beside it (still copyable)
      const big = mkBtn('fixbtn', busy ? t('p584.running') : t(HUMAN[name]), 'run');
      big.setAttribute('aria-label', t(HUMAN[name]) + ' · ' + aria);
      big.title = t(HUMAN[name]) + ' · ' + (online ? 'agentj ' + cmd.replace(/^agentj\s+/, '') : t('p584.offline'));
      state(big, online, busy);
      big.addEventListener('click', (e) => { e.preventDefault(); e.stopPropagation(); if (!online) return toast(t('p584.offline'), 4000); tap(); });
      const d = document.createElement('details'); d.className = 'fixcmd';
      const s = document.createElement('summary'); s.setAttribute('data-aj-owner', 'aj-terminal'); s.textContent = t('p584.showCmd');
      wrap.before(big, d); d.append(s, wrap);
    } else {
      const b = mkBtn('term-command runbtn', label, 'run');
      b.classList.toggle('term-command--danger', dangerous(cmd));
      b.setAttribute('aria-label', label === t('p584.run') ? aria : label + ' · ' + aria);
      b.title = !online ? t('p584.offline') : route === 'open' ? t('p584.inTermHint') : route === 'agent' ? t('p584.askAgentHint') : aria;
      state(b, online, busy);
      b.addEventListener('click', (e) => { e.preventDefault(); e.stopPropagation(); if (!online) return toast(t('p584.offline'), 4000); tap(); });
      bar.insertBefore(b, bar.querySelector('.copy'));
    }
    if (r) (wrap.closest('details.fixcmd') || wrap).after(box(key, r, cmd, route, name, page));
  });
}
/** #584 ⑧: a host-written page (/update's result, a disconnect or upgrade notice: k sys / cmd) that tells the owner to run a
 *  whitelisted repair gets its plain-words button at the end — by NAME only, so no text on the page can choose what runs. */
export const hostPage = (p) => !!p && Number.isInteger(p.id) && p.end !== 'open' && ['sys', 'cmd'].includes(p.source?.k);
function fixBar(root, page) {
  const seen = new Set();
  for (const c of root.querySelectorAll('code')) {
    const n = named(c.textContent || '');
    if (n && HUMAN[n]) seen.add(n);
  }
  const online = isReady();
  for (const n of seen) {
    const key = page.id + ':fix:' + n, r = runs.get(key), busy = r && r.st === 'run', cmd = 'agentj ' + CMDS[n];
    const big = mkBtn('fixbtn', busy ? t('p584.running') : t(HUMAN[n]), 'run');
    big.setAttribute('aria-label', t(HUMAN[n]) + ' · ' + t('p584.runOn')); big.title = online ? cmd : t('p584.offline');
    state(big, online, busy);
    big.addEventListener('click', (e) => { e.preventDefault(); e.stopPropagation(); if (!online) return toast(t('p584.offline'), 4000); go(key, cmd, 'named', n, page); });
    root.append(big);
    if (r) root.append(box(key, r, cmd, 'named', n, page));
  }
}
function state(b, online, busy) {
  b.disabled = busy; b.setAttribute('aria-disabled', String(!online || busy)); b.classList.toggle('off', !online);
}
function repaint(page) { if (shown && shown.page && shown.page.id === page.id && shown.root.isConnected) paint(shown.root, shown.page); }

/** One tap = one run: a block already running ignores taps (the button is disabled too); 「再跑一次」 is a new tap. */
async function go(key, cmd, route, name, page) {
  if (runs.get(key)?.st === 'run') return;
  if (route === 'open') { enter(cmd); return; }
  if (route === 'agent') {
    const text = t('p584.agentMsg', { agent: H.agent() }) + '\n```sh\n' + cmd + '\n```';
    await H.say(text, page.id);
    return;
  }
  const r = { st: 'run', route, sid: null, open: false };
  remember(key, r); repaint(page);
  if (route === 'named') {
    const m = await cmdExec(name);
    if (m.why === 'cancel') runs.delete(key);
    else Object.assign(r, m.ok || Number.isInteger(m.code) ? { st: m.code === 0 ? 'done' : 'fail', out: m.out, code: m.code, ms: m.ms, cut: m.cut }
      : { st: 'fail', why: m.why || 'failed' });
    return repaint(page);
  }
  r.sid = newSid();
  const how = await termRun(cmd, r.sid, { from: page.id, cb: (m) => {
    if (m && m.run === 1) Object.assign(r, { st: m.state === 'done' ? 'done' : 'fail', out: typeof m.out === 'string' ? m.out.slice(0, 32768) : '',
      ms: m.ms, hidden: Number.isInteger(m.hidden) ? m.hidden : 0, cut: m.cut === true, why: m.state === 'done' ? '' : m.state });
    else if (m && ['cancel', 'refused', 'expired', 'open'].includes(m.why)) Object.assign(r, { st: 'fail', why: m.why === 'open' ? 'openFail' : m.why });
    else Object.assign(r, { st: 'fail', why: (m && m.why) || 'failed' });
    repaint(page);
  } });
  if (how === 'sent' || how === 'open') return;
  runs.delete(key); repaint(page);
  if (how === 'pty') { enter(cmd); toast(t('p584.pty'), 5000); }
  else if (how === 'busy') toast(t('p584.busy'), 4000);
  else if (how !== 'program') toast(t('p584.failed'), 4000, 'error');
}

/** The black box under the block. */
function box(key, r, cmd, route, name, page) {
  const sec = document.createElement('section'); sec.className = 'runbox bangcard'; sec.setAttribute('data-aj-owner', 'aj-terminal');
  sec.setAttribute('aria-label', t('p584.boxAria')); sec.setAttribute('aria-live', 'polite');
  const head = document.createElement('p'); head.className = 'runhead'; head.textContent = headText(r, route); sec.append(head);
  if (r.st !== 'run' && r.out) {
    const lines = r.out.replace(/\n$/, '').split('\n'), long = lines.length > OUT_LINES;
    const pre = document.createElement('pre'); pre.className = 'bangout'; pre.setAttribute('data-aj-owner', 'aj-terminal'); pre.textContent = r.out;
    sec.classList.toggle('folded', long && !r.open);
    sec.append(copyable(pre, { variant: 'block', click: false }));
    if (long) {
      const m = mkBtn('bangmore', t(r.open ? 'p414.card.less' : 'p414.card.more', { n: lines.length }));
      m.setAttribute('aria-expanded', String(!!r.open));
      m.addEventListener('click', () => { r.open = !r.open; repaint(page); });
      sec.append(m);
    }
  }
  const notes = [];
  if (r.hidden) notes.push(t('p414.card.hidden', { n: r.hidden }));
  if (r.cut) notes.push(t('p414.card.cut'));
  if (notes.length) { const n = document.createElement('p'); n.className = 'bangnote'; n.textContent = notes.join(' · '); sec.append(n); }
  const acts = document.createElement('div'); acts.className = 'runacts';
  if (r.st === 'run' && route === 'term' && r.sid) {
    const s = mkBtn('runact', t('p584.stop'));
    s.addEventListener('click', () => { if (!stopRun(r.sid)) toast(t('p584.stopNo'), 3000); });
    acts.append(s);
  }
  if (r.st !== 'run') {
    const again = mkBtn('runact', t('p584.again'), 'run');
    again.disabled = !isReady();
    again.addEventListener('click', () => go(key, cmd, route, name, page));
    acts.append(again);
    if (r.out || Number.isInteger(r.code)) {
      const ask = mkBtn('runact', t('p584.ask', { agent: H.agent() }));
      ask.addEventListener('click', () => H.say(askText(cmd, r), page.id));
      acts.append(ask);
    }
  }
  if (acts.childNodes.length) sec.append(acts);
  return sec;
}
export function headText(r, route) {
  if (r.st === 'run') return t('p584.running');
  const s = secs(r.ms), took = s ? t('p584.took', { s }) : '';
  if (r.why && !r.out && !Number.isInteger(r.code)) return t(['refused', 'expired', 'cancel', 'openFail', 'closed', 'lost', 'offline', 'busy', 'running'].includes(r.why) ? 'p584.why.' + r.why : 'p584.why.failed');
  if (r.why === 'running') return t('p584.why.running');
  if (r.why === 'closed') return t('p584.why.closed') + (took ? ' · ' + took : '');
  if (Number.isInteger(r.code)) return t(r.code === 0 ? 'p584.ok' : 'p584.exit', { code: r.code }) + (took ? ' · ' + took : '');
  return t('p584.done') + (took ? ' · ' + took : '') + (route === 'term' ? ' · ' + t('p584.noCode') : '');
}
const ASK_MAX = 3000;
export function askText(cmd, r) {
  const out = (r.out || '').length > ASK_MAX ? '…\n' + r.out.slice(-ASK_MAX) : (r.out || '');
  const state = Number.isInteger(r.code) ? t('p584.askCode', { code: r.code }) : t('p584.askNoCode');
  return t('p584.askMsg') + '\n```sh\n' + cmd + '\n```\n' + state + '\n```text\n' + (out.replace(/```/g, "'''") || t('p414.card.empty')) + '\n```';
}
// tests
export const _debug = () => ({ runs: [...runs.entries()].map(([k, v]) => [k, { ...v }]) });
export function _reset() { runs.clear(); shown = null; }
