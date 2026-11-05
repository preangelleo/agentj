// 0.26.9 #468 (CONTRACT §B3): a turn started by an official remote assist task (source kind `official_assist`). The host may
// write it as its own source kind or as a task with via "official_assist"; both read the same. Pure (snap.js, relay.js,
// assist.js and node tests share it).
export const KIND = 'official_assist';
const ID = /^[A-Za-z0-9._:-]{1,64}$/;
/** true = this history source is an official remote assist task. */
export const isAssistSrc = (src) => !!src && typeof src === 'object' && (src.k === KIND || (src.k === 'task' && src.via === KIND));
/** What snap.js keeps of such a source beyond the usual fields: the task id when the host gave one. */
export function assistCarry(src) {
  if (!isAssistSrc(src)) return {};
  const id = [src.assist, src.assist_id, src.task_id, src.task].find((x) => typeof x === 'string' && ID.test(x));
  return { k: KIND, ...(id ? { assist_id: id } : {}) };
}
