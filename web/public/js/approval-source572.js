// #572 / #541: display the host-owned source in the ordinary approval sheet.
// Text is quoted data. No source field changes admission or creates an action.
import { t, lang } from './t.js';
export function sourceLines(e) {
  const s = e?.source || {}, root = s.root || {};
  const chain = Array.isArray(s.chain) ? s.chain.map(x => typeof x === 'string' ? x : [x?.kind,x?.agent || x?.unit || x?.task || x?.id].filter(Boolean).join(' ')).filter(Boolean).join(' → ') : '';
  return {root:typeof root.text === 'string' ? root.text : '', trigger:typeof s.text === 'string' ? s.text : '', chain,
    rootRef:String(s.root_message_id || root.message_id || ''), triggerRef:String(s.message_id || ''),
    reason:String((lang() === 'en' && e?.action_en) || e?.action || '')};
}
export function paint(p) {
  const box = document.getElementById('approvalSource572');
  if (!box) return;
  box.replaceChildren(); box.hidden = !p;
  if (!p) return;
  const row = (label,value) => {
    const line=document.createElement('p');
    if (label) { const strong=document.createElement('span');strong.className='source-label572';strong.textContent=label+'\n';line.append(strong); }
    line.append(document.createTextNode(value));box.append(line);
  };
  if (p.approval_v !== 2) row('',t(p.approval_invalid ? 'p572source.invalid' : 'p572source.legacy'));
  else {
    const e=p.envelope, s=e?.source || {}, lines=sourceLines(e);
    // #0281: #541 already renders the human quote and confirmation reason.
    // Keep only the optional provenance chain here, with no missing-data filler.
    if(lines.chain) row(t('p572source.chain'),lines.chain);
    if (s.verified !== true || s.suspicious === true) row('',t('p572source.external'));
    if (!p.envelope_verified) row('',t('p572source.invalid'));
  }
  box.hidden = !box.childNodes.length;
  if(p.approval_v===2)document.getElementById('action541')?.after(box);
  if (p.approval_invalid || (p.approval_v === 2 && !p.envelope_verified)) {
    document.getElementById('apprAllow').disabled=true;
    document.getElementById('apprBatch').hidden=true;
  }
  if (p.approval_v === 2) {
    // This narrow card signs one decision, without #541's separate question/plan lifecycle.
    document.getElementById('apprBatch').hidden=true;
    document.getElementById('cmdReason').hidden=true;
  }
}
