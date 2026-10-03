// 朗读 — read a reply aloud with the phone's OWN voices (PROTOCOL §10.14). relay sent the turn to a cloud rewrite + a
// cloned voice; here nothing leaves the phone: speechSynthesis with a voice whose localService is true (a browser's
// network voices — e.g. desktop Chrome's "Google …" ones — would send the text to their vendor, so they are never used).
// Markdown is reduced to its words (code blocks skipped). Play / pause / stop on the same button, state per turn id.
import { RelayMD } from './md.js';

export const supported = () => typeof window.speechSynthesis === 'object' && typeof window.SpeechSynthesisUtterance === 'function';

/** Markdown → the sentences to read (no code blocks, no link targets, no table rules). */
export function speakText(md) {
  const out = [];
  const walk = (nodes) => {
    for (const n of nodes || []) {
      if (!n || typeof n !== 'object') continue;
      if (typeof n.text === 'string') { out.push(n.text); continue; }
      if (n.tag === 'pre') continue;
      if (n.tag === 'br') { out.push('\n'); continue; }
      walk(n.children);
      if (/^(p|h\d|li|blockquote|tr|hr)$/.test(n.tag)) out.push('\n');
      if (n.tag === 'td' || n.tag === 'th') out.push('\uff0c');
    }
  };
  try { walk(RelayMD.parse(String(md || ''))); } catch { out.push(String(md || '')); }
  return out.join('').replace(/[ \t]+/g, ' ').replace(/\n{2,}/g, '\n').trim();
}
export const langOf = (s) => ((s.match(/[\u3400-\u9fff]/g) || []).length > s.length * 0.15 ? 'zh' : 'en');

/** Split into utterances ≤ 180 characters on sentence ends (long utterances stall some engines). */
export function chunks(text) {
  const parts = text.split(/(?<=[\u3002\uff01\uff1f!?\uff1b;\n]|\.\s)/);
  const out = [];
  let cur = '';
  for (const p of parts) {
    if ((cur + p).length > 180 && cur) { out.push(cur); cur = ''; }
    cur += p;
    while (cur.length > 180) { out.push(cur.slice(0, 180)); cur = cur.slice(180); }
  }
  if (cur.trim()) out.push(cur);
  return out.map((x) => x.trim()).filter(Boolean);
}

export function localVoice(lang) {
  const vs = (window.speechSynthesis.getVoices() || []).filter((v) => v.localService !== false);
  const want = lang === 'zh' ? /^(zh|cmn)/i : /^en/i;
  return vs.find((v) => want.test(v.lang) && v.default) || vs.find((v) => want.test(v.lang)) || null;
}

/** One reader for the page. onState(id, st): st ∈ idle | gen | playing | paused | failed; why: 'novoice' | 'empty' | … */
export class Speaker {
  constructor(onState) { this.onState = onState; this.id = null; this.queue = []; this.gen = 0; }
  state(id) { return id === this.id ? this.st : 'idle'; }
  set(id, st, why) { if (id === this.id) this.st = st; this.onState(id, st, why); }
  stop() {
    const id = this.id; this.gen++; this.queue = [];
    try { window.speechSynthesis.cancel(); } catch { /* nothing playing */ }
    this.id = null; this.st = 'idle';
    if (id !== null) this.onState(id, 'idle');
  }
  /** Tap on 朗读 for turn id with its reply text. */
  tap(id, md) {
    if (!supported()) { this.onState(id, 'failed', 'unsupported'); return; }
    const ss = window.speechSynthesis;
    if (this.id === id && this.st === 'playing') { ss.pause(); this.set(id, 'paused'); return; }
    if (this.id === id && this.st === 'paused') { ss.resume(); this.set(id, 'playing'); return; }
    this.stop();
    const text = speakText(md);
    if (!text) { this.onState(id, 'failed', 'empty'); return; }
    const lang = langOf(text);
    const voice = localVoice(lang);
    if (!voice) { this.onState(id, 'failed', 'novoice'); return; }
    this.id = id; this.st = 'gen';
    this.onState(id, 'gen');
    const gen = ++this.gen;
    this.queue = chunks(text);
    const next = () => {
      if (gen !== this.gen) return;
      const part = this.queue.shift();
      if (part === undefined) { this.id = null; this.st = 'idle'; this.onState(id, 'idle'); return; }
      const u = new window.SpeechSynthesisUtterance(part);
      try{ u.voice = voice; }catch{ /* a voice object the engine does not know */ } u.lang = voice.lang;
      u.onstart = () => { if (gen === this.gen && this.st !== 'paused') this.set(id, 'playing'); };
      u.onend = () => next();
      u.onerror = (e) => { if (gen !== this.gen || (e && e.error === 'interrupted')) return; this.gen++; this.id = null; this.st = 'idle'; this.onState(id, 'failed', 'engine'); };
      ss.speak(u);
    };
    next();
  }
}
