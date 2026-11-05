// 0.26.9 selfheal (CONTRACT §C, #461 #464) · what the chat page draws under a reply (#cmdx) for recovery:
//  · a reply whose AI coding tool is signed out (host `login`) → 「重新登录」, opening the login sheet (loginsheet.js) here
//  · the queued notice with the host's `waited_s` / `stuck` → 「已等 N 分钟」; once the turn is stuck or has run ≥ 2 minutes
//    「强制结束这一轮」 (signed rescue_do end_turn, rescue.js) — its progress / result take the button's place. Only while the
//    notice describes the present (an open page, or the newest one): an old notice further back is history.
// relay.js calls paint() before its own command-result box (true = this drew the box) and tail() after it (the waited line
// on top of a queued page of ours, whose own 「停下上一件」 stays while 「强制结束这一轮」 is not offered yet).
// H (relay.js): {pages(), queuedTurn(id), repaint(), live()} — live = connected and not 全部停下.
// 0.26.9 #471 (CONTRACT §D, merged notice): every queued notice — the host's (waited_s / stuck) and this phone's own message
// waiting behind the running turn (H.queuedTurn, H.queuedSince) — reads 「上一件还在做，已等 N 分钟」 with 「停下上一件」 under
// it: the signed rescue_do end_turn (confirm sheet first) on a host that runs the turn watchdog (H.signedEnd()), P481's /stop
// (H.stop()) on an older one. The host notice offers it once stuck or after END_AFTER_S; the owner's own queued page at once
// (as P481 did).
import { t } from './t.js';
import { loginOf, queueOf, waitedNow, prevText, canEnd, keepTicking } from './selfheal.js';
import { open as openLogin } from './loginsheet.js';
import { endTurn, flowState, clearEndTurn, onChange, openBlock, whyLine } from './rescue.js';

let endPage = null;                 // the page whose 「强制结束这一轮」 was tapped: its result belongs to it alone
let repaint = null;
onChange(() => { if (endPage !== null && repaint) repaint(); });

const button = (cls, text, fn) => { const b = document.createElement('button'); b.type = 'button'; b.className = 'cmdundo ' + cls; b.textContent = text; b.addEventListener('click', fn); return b; };

function liveQueue(p, H) {
  const own = !!H.queuedTurn?.(p.id);
  let q = queueOf(p);
  if (!q && own) {
    const at = H.queuedSince?.(p.id);
    q = { waited: Number.isFinite(at) ? Math.max(0, Math.floor((Date.now() - at) / 1000)) : 0, stuck: false };
  }
  if (!q) return null;
  const ps = H.pages(), live = own || p.end === 'open' || p.id === ps[ps.length - 1]?.id;
  return live ? { q, sec: own && !queueOf(p) ? q.waited : waitedNow(p.id, q, true), own } : null;
}
function waitedLine(sec) {
  const [key, vars] = prevText(sec);
  const line = document.createElement('p'); line.className = 'sh-waited'; line.dataset.waited = String(sec); line.setAttribute('role', 'status');
  line.textContent = t(key, vars);
  return line;
}

/** true = the box is ours this time (login button, or the waited line with its end-turn button / result). */
export function paint(box, p, H) {
  repaint = H.repaint;
  const login = loginOf(p);
  if (login) {
    box.replaceChildren(); box.hidden = false;
    const b = button('sh-login', t('p0269.login.btn'), () => openLogin(login.agent, login.why)); b.dataset.loginBtn = login.agent;
    box.append(b);
    return true;
  }
  const lq = liveQueue(p, H);
  if (!lq) return false;
  let F = flowState('end_turn');
  if (F.st !== 'idle' && F.st !== 'run' && endPage !== p.id) { clearEndTurn(); F = flowState('end_turn'); }   // another notice's result
  const mine = F.st !== 'idle' && (endPage === p.id || F.st === 'run');
  const offer = (lq.own || canEnd(lq.q, lq.sec)) && H.live();
  if (!mine && !offer) return false;          // relay.js draws its own box; tail() puts the waited line on top
  box.replaceChildren(); box.hidden = false;
  box.append(waitedLine(lq.sec));
  keepTicking(H.repaint);
  if (mine) {
    const msg = document.createElement('p'); msg.id = 'sh-end-msg'; msg.setAttribute('aria-live', 'polite');
    msg.className = 'sh-endmsg' + (F.st === 'fail' ? ' rsfail' : F.st === 'ok' ? ' rsok' : '');
    msg.textContent = F.st === 'run' ? (F.text || t('p0269.queue.ending')) : F.st === 'ok' ? (F.text || t('p0269.queue.ended')) : (whyLine(F.why) || t('p0269.queue.endFail'));
    box.append(msg);
    if (F.st === 'fail') { const go = button('', t('p0269.queue.toRescue'), () => { clearEndTurn(); endPage = null; openBlock(); }); go.dataset.endRescue = '1'; box.append(go); }
    return true;
  }
  const signed = !H.signedEnd || H.signedEnd();
  const b = button('sh-end', t('p481.stopPrevious'), () => { if (signed) { endPage = p.id; endTurn(); } else H.stop?.(); });
  b.dataset.endTurn = String(p.id);
  if (lq.own) b.dataset.queueStop = String(p.id);   // P481's hook: the owner's own queued page
  box.append(b);
  return true;
}
/** After relay.js drew its box: the waited line on top of it (nothing else changes). */
export function tail(box, p, H) {
  const lq = liveQueue(p, H);
  if (!lq) return;
  box.prepend(waitedLine(lq.sec)); box.hidden = false;
  keepTicking(H.repaint);
}
