// P492: only host-supplied official authorization metadata; never a chat/draft value.
import { t } from './t.js';
import { copyNow } from './copycode.js';
export function authOf(a) {
  if (!a || !['claude','codex','kimi'].includes(a.kind) || typeof a.url !== 'string' || a.url.length > 8192 || /[\s\x00-\x1f]/.test(a.url)) return null;
  try {
    const u = new URL(a.url);
    const hosts = { claude: ['claude.com','claude.ai','console.anthropic.com','platform.claude.com'], codex: ['auth.openai.com'] };
    if (u.protocol !== 'https:' || u.username || u.password || u.port || u.hash) return null;
    if (a.kind === 'kimi' ? !/^(?:[a-z0-9-]+\.)*kimi\.(com|ai)$/.test(u.hostname) : !hosts[a.kind].includes(u.hostname)) return null;
    if (a.kind === 'claude' && !u.pathname.endsWith('/oauth/authorize')) return null;
  } catch { return null; }
  if (typeof a.code !== 'string' || a.code.length > 32 || !/^[A-Za-z0-9-]*$/.test(a.code)
      || !Number.isSafeInteger(a.expires_at) || a.expires_at <= 0 || !Number.isInteger(a.round) || !Number.isInteger(a.rounds)
      || a.round < 1 || a.rounds > 3 || a.round > a.rounds) return null;
  return Object.freeze({kind:a.kind,url:a.url,code:a.code,expires_at:a.expires_at,round:a.round,rounds:a.rounds});
}
export function paintAuth(box, a) {
  box.replaceChildren(); box.hidden = !a;
  if (!a) return;
  const p = document.createElement('p'); p.textContent = t('p332.auth.wait') + ' · ' + t('ar.urlStep1');
  const actions = document.createElement('p'); actions.className = 'mailbtns';
  const link = document.createElement('a'); link.href = a.url; link.target = '_blank'; link.rel = 'noopener noreferrer';
  link.className = 'aj-btn aj-btn--primary'; link.textContent = t('ar.openLink');
  const copy = document.createElement('button'); copy.type = 'button'; copy.className = 'aj-btn'; copy.textContent = t('ar.copy');
  const result = document.createElement('span'); result.setAttribute('role','status');
  copy.addEventListener('click', async () => { result.textContent = t(await copyNow(a.url) ? 'ar.copied' : 'r.copy.fail'); });
  actions.append(link, copy, result); box.append(p, actions);
  if (a.code) { const c = document.createElement('p'); c.className = 'arcode'; c.textContent = a.code; const b = copy.cloneNode(); b.textContent = t('ar.copy'); b.addEventListener('click', async()=>{ result.textContent=t(await copyNow(a.code)?'ar.copied':'r.copy.fail'); }); box.append(c,b); }
  const round = document.createElement('p'); round.textContent = t('ar.round', { n:a.round, max:a.rounds }); box.append(round);
  const note = document.createElement('p'); note.textContent = t('ar.cardStep'); box.append(note);
}
