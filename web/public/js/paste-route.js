// P406 #168 (Leo: 「只要输入框里边有文字……就一直让它往里贴，只要总长度不超就行。输入框里没文字……超过多少，就给它进附件」):
// where pasted text goes. Decided by characters only — never by the field's height (a tall paste just scrolls).
//   the composer already holds words (what stays after the selection is replaced): into the field, unless together they pass
//     the single-message limit `max` → 'over' (this paste alone becomes an attachment, the words already there are kept)
//   an empty composer: longer than `clipMax` characters → 'file', otherwise into the field
export const CLIP_TEXT_MAX = 2000;
export function pasteRoute({value = '', start, end, text = '', max, clipMax = CLIP_TEXT_MAX}){
  if (!text) return 'none';
  const a = Number.isInteger(start) ? start : value.length, b = Number.isInteger(end) ? end : a;
  const kept = value.length - Math.max(0, b - a);
  if (kept > 0) return kept + text.length > max ? 'over' : 'field';
  return [...text].length > clipMax || text.length > max ? 'file' : 'field';
}
