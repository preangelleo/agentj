// #514b: transport readiness does not imply an attached coding session.
// 0.30.6 (chairman 11-10 12:14): a centred dialog (.gdlg, no focus taken), not a row under the header. 稍后 / Esc / a tap
// outside / 修复会话连接 put it away until the session status leaves down / none and comes back.
import { t, onLang } from './t.js';
import { registerLayer } from './layers.js';
let H = { repair() {}, supported: () => false }, last = null, dismissed = false;
const box = () => document.getElementById('session-help');
function later() { dismissed = true; render(last); }
export function configure(h) {
  H = h;
  document.getElementById('session-repair').addEventListener('click', () => {
    if (last?.connected && ['down','none'].includes(last.status) && H.supported()) { later(); H.repair(); }
  });
  document.getElementById('session-later').addEventListener('click', later);
  box().addEventListener('click', (e) => { if (e.target === e.currentTarget) later(); });
  registerLayer({ element: box, open: () => !box().hidden, close: later, fallback: () => document.getElementById('setBtn'), trap: false, rank: -8, preserveOutsideFocus: true });
  onLang(() => render(last));
}
export function render(s) {
  last = s;
  const b = box();
  if (!b) return;
  const agent = {claude:'Claude',codex:'Codex',opencode:'OpenCode'}[s?.agent];
  const wanted = !!(s?.connected && agent && ['down','none'].includes(s.status));
  if (!wanted) dismissed = false;
  b.hidden = !wanted || dismissed;
  if (b.hidden) return;
  document.getElementById('session-help-text').textContent = t('sessionHelp.text', {agent});
  document.getElementById('session-repair').hidden = !H.supported();
}
