// #510: an agent's signed note is evidence, not translated UI copy. Keep it intact
// in the expandable summary; a Chinese short line explains the host's category.
export function approvalNote(reason, card, language, tr, verb) {
  if (!reason || language !== 'zh' || /[\u3400-\u9fff]/.test(reason)) return reason;
  const labels = (card?.cats || []).map(c => tr('ask.cat.' + (c === 'credentials' && card.cred === 'read' ? 'credentials_read' : c)))
    .filter(s => !s.startsWith('ask.cat.'));
  const category = labels.join('、') || (/[\u3400-\u9fff]/.test(card?.why || '') ? card.why : verb);
  return tr('p510.categoryNote', {category});
}
