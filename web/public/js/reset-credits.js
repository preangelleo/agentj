// #563: metadata only. Host owns credit availability; never render upstream text or IDs.
import {t, lang} from './t.js';
export function resetCreditText(value) {
  if (!value || value.status !== 'ok' || !Number.isSafeInteger(value.count) || value.count < 0)
    return t('p563.unavailable');
  if (value.count === 0) return t('p563.count', {n: 0});
  const at = value.nearest_expires_at;
  if (at == null) return t('p563.count', {n: value.count});
  const date = Number.isFinite(at) && at > 0 ? new Date(at * 1000) : null;
  if (!date || !Number.isFinite(date.getTime())) return t('p563.unavailable');
  return t('p563.summary', {n: value.count, date: date.toLocaleDateString(lang() === 'en' ? 'en-US' : 'zh-CN', {month:'long',day:'numeric'})});
}
