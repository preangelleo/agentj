// P498: host-authenticated report metadata only; friend text never creates an action.
import { peer, isReady } from './session.js';
import { signedWrite, resumeButton } from './controls.js';
import { t } from './t.js';
import { mk, toast } from './ui.js';

const painted = new WeakMap();

export function paintReportActions(box, page) {
  const card = page.card;
  const wf = page.source?.k === 'task' && peer.wfresume1 && /^[0-9a-f]{32}$/.test(card?.wf_resume?.run_id || '');
  const fr = page.source?.k === 'friend_report' && peer.friendreport1 && Array.isArray(card?.friend_report?.ids)
    && card.friend_report.ids.some(id => typeof id === 'string' && /^[0-9a-f]{64}$/.test(id));
  if (!wf && !fr) { painted.delete(box); return false; }
  const key = JSON.stringify([page.id, page.source.k, wf ? card.wf_resume : card.friend_report,
    t('p498.reply'), t('p498.resume'), wf ? isReady() : null]);
  const previous = painted.get(box);
  if (previous?.key === key && previous.anchor === box.firstChild) return true;
  box.replaceChildren();
  const remember = () => { painted.set(box, {key, anchor:box.firstChild}); return true; };
  if (wf) {
    box.hidden = false;
    box.append(resumeButton(card.wf_resume.run_id));
    return remember();
  }
  const report = card?.friend_report;
  if (page.source?.k !== 'friend_report' || !peer.friendreport1 || !report || !Array.isArray(report.ids)) return false;
  const valid = report.ids.filter(id => typeof id === 'string' && /^[0-9a-f]{64}$/.test(id)).slice(0, 8);
  const ids = valid.filter(id => !['replied','reply_uncertain'].includes(report.states?.[id]));
  if (valid.length && !ids.length) {
    box.hidden = false;
    box.append(mk('span', '', t(valid.some(id => report.states?.[id] === 'reply_uncertain') ? 'p498.replyUnconfirmed' : 'p498.sent')));
    return remember();
  }
  if (!ids.length) return false;
  box.hidden = false;
  const unknown = ids.some(id => report.states?.[id] === 'reply_unknown');
  if (unknown) box.append(mk('span', '', t('p498.replyUnknown')));
  const toggle = mk('button', 'cmdundo p505-report-action', t(unknown ? 'p498.resend' : 'p498.reply'));
  toggle.type = 'button';
  const form = mk('form', 'p498-reply'); form.hidden = true;
  const input = mk('textarea', 'aj-input'); input.maxLength = 2000; input.rows = 3;
  input.setAttribute('aria-label', t('p498.replyText')); input.placeholder = t('p498.replyText');
  const select = mk('select', 'aj-input'); select.setAttribute('aria-label', t('p498.chooseReport'));
  for (const id of ids) { const option = mk('option', '', String(valid.indexOf(id) + 1)); option.value = id; select.append(option); }
  select.hidden = valid.length === 1;
  const send = mk('button', 'aj-btn aj-btn--sm p505-report-action', t('p498.send')); send.type = 'submit';
  toggle.addEventListener('click', () => { form.hidden = !form.hidden; if (!form.hidden) input.focus(); });
  form.addEventListener('submit', async event => {
    event.preventDefault();
    if (send.disabled || !input.value.trim() || !isReady()) return;
    send.disabled = true;
    const target = {id:select.value, text:input.value, ...(report.states?.[select.value] === 'reply_unknown' ? {resend:true} : {})};
    const res = await signedWrite('fr_report_reply', target, {t:'fr_report_reply', ...target});
    toast(t(res.ok ? 'p498.sent' : res.state === 'reported' ? 'p498.retryReply' : 'p498.replyUnconfirmed'), 4000);
    if (res.ok) { input.value = ''; form.hidden = true; toggle.disabled = true; }
    send.disabled = false;
  });
  form.append(select, input, send); box.append(toggle, form);
  return remember();
}
