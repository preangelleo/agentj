// #514b: transport readiness does not imply an attached coding session.
import { t, onLang } from './t.js';
let H = { repair() {}, supported: () => false }, last = null;
export function configure(h) {
  H = h;
  document.getElementById('session-repair').addEventListener('click', () => {
    if (last?.connected && ['down','none'].includes(last.status) && H.supported()) H.repair();
  });
  onLang(() => render(last));
}
export function render(s) {
  last = s;
  const box = document.getElementById('session-help');
  if (!box) return;
  const agent = {claude:'Claude',codex:'Codex',opencode:'OpenCode'}[s?.agent];
  box.hidden = !(s?.connected && agent && ['down','none'].includes(s.status));
  if (box.hidden) return;
  document.getElementById('session-help-text').textContent = t('sessionHelp.text', {agent});
  document.getElementById('session-repair').hidden = !H.supported();
}
