// Local-only note import. The Host still applies its authoritative privacy check on save.
export const NOTE_FILE_MAX = 16384;
export async function readNoteTextFile(file) {
  if (!file || !/\.txt$/i.test(file.name || '') || !['', 'text/plain'].includes(file.type)) throw new Error('type');
  if (!file.size || file.size > NOTE_FILE_MAX) throw new Error('size');
  let text;
  try { text = new TextDecoder('utf-8', {fatal:true}).decode(await file.arrayBuffer()); }
  catch { throw new Error('utf8'); }
  if (!text.trim() || /[\x00-\x08\x0b\x0c\x0e-\x1f\x7f]/.test(text)) throw new Error('utf8');
  if (/-----BEGIN [A-Z ]*PRIVATE KEY-----|\b(?:sk-[A-Za-z0-9_-]{16,}|gh[pousr]_[A-Za-z0-9]{16,}|AKIA[A-Z0-9]{16})\b|\bBearer\s+\S+|\beyJ[A-Za-z0-9_-]+\.[A-Za-z0-9_-]+\.[A-Za-z0-9_-]+|(?:api[_-]?key|access[_-]?token|secret|password|passwd)\s*[=:]\s*\S+/i.test(text)) throw new Error('secret');
  return text;
}
