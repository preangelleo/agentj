// 朗读 — read a reply aloud with the phone's OWN voices (PROTOCOL §10.14). relay sent the turn to a cloud rewrite + a
// cloned voice; here nothing leaves the phone: speechSynthesis with a voice whose localService is true (a browser's
// network voices — e.g. desktop Chrome's "Google …" ones — would send the text to their vendor, so they are never used).
// Markdown is reduced to its words (code blocks skipped). Play / pause / stop on the same button, state per turn id.
import { RelayMD } from './md.js';
import { speechAudio } from './api.js';
let prefs={};
export function configureSpeech(value){prefs=value||{};}
export const speechPreferences=()=>prefs;
export const speechFailureWhy = (error) => {
  if(error?.name==='NotAllowedError') return 'tap_play';
  const why=error?.why || error?.message;
  if (why==='offline' || why==='missing') return 'not_ready';
  if (why==='timeout') return 'network';
  return ['novoice','empty','unsupported','engine','missing_key','provider_rejected','provider_busy','provider_unavailable','network','busy','not_ready'].includes(why)?why:'engine';
};

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
  const vs = (window.speechSynthesis.getVoices() || []).filter((v) => v.localService === true);
  const want = lang === 'zh' ? /^(zh|cmn)/i : /^en/i;
  if(prefs.tts?.phone_voice){const selected=vs.find(v=>v.name===prefs.tts.phone_voice||v.voiceURI===prefs.tts.phone_voice);if(selected)return selected;}
  return vs.find((v) => want.test(v.lang) && v.default) || vs.find((v) => want.test(v.lang)) || null;
}

// iOS can report no voices until voiceschanged. Bound the wait and remove the listener.
export function waitForVoices(timeout=1500) {
  const ss=window.speechSynthesis;
  if((ss.getVoices()||[]).length) return Promise.resolve();
  return new Promise(resolve=>{
    const done=()=>{clearTimeout(timer);ss.removeEventListener?.('voiceschanged',changed);resolve();};
    const changed=()=>{if((ss.getVoices()||[]).length)done();};
    const timer=setTimeout(done,timeout);
    ss.addEventListener?.('voiceschanged',changed);changed();
  });
}

/** One reader for the page. onState(id, st): st ∈ idle | gen | playing | paused | failed; why: 'novoice' | 'empty' | … */
export class Speaker {
  constructor(onState, onNotice=()=>{}) { this.onState = onState; this.onNotice=onNotice; this.fallbackNotices=new Set(); this.id = null; this.queue = []; this.gen = 0; }
  state(id) { return id === this.id ? this.st : 'idle'; }
  set(id, st, why) { if (id === this.id) this.st = st; this.onState(id, st, why); }
  stop() {
    const id = this.id; this.gen++; this.queue = [];
    if(this.audio){this.audio.pause();this.audio.removeAttribute('src');this.audio.onended=null;this.audio.onerror=null;}
    if(this.url){URL.revokeObjectURL(this.url);this.url=null;}
    if(this.unlockUrl){URL.revokeObjectURL(this.unlockUrl);this.unlockUrl=null;}
    try { window.speechSynthesis.cancel(); } catch { /* nothing playing */ }
    this.id = null; this.st = 'idle';
    if (id !== null) this.onState(id, 'idle');
  }
  unlock() {
    if(typeof Audio!=='function') return;
    if(!this.audio) this.audio=new Audio();
    if(this.unlocked || this.url) return;
    this.audio.muted=true;
    const silence='UklGRsQAAABXQVZFZm10IBAAAAABAAEAQB8AAIA+AAACABAAZGF0YaAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAA';
    this.unlockUrl=URL.createObjectURL(new Blob([Uint8Array.from(atob(silence),c=>c.charCodeAt(0))],{type:'audio/wav'}));
    this.audio.src=this.unlockUrl;
    try { const attempt=this.audio.play(); attempt?.then(()=>{this.unlocked=true;},()=>{}); } catch {}
  }
  async hostTap(id){
    if(this.id===id && this.audio && this.url){
      if(this.audio.paused){try{this.audio.muted=false;await this.audio.play();this.set(id,'playing');}catch(error){this.set(id,'failed',speechFailureWhy(error));}}
      else{this.audio.pause();this.set(id,'paused');}return;
    }
    this.stop();this.unlock();this.id=id;this.st='gen';this.onState(id,'gen');const g=this.gen;
    try{
      const blob=await speechAudio(id);if(g!==this.gen)return;
      this.url=URL.createObjectURL(blob);this.audio.src=this.url;this.audio.muted=false;
      if(this.unlockUrl){URL.revokeObjectURL(this.unlockUrl);this.unlockUrl=null;}
      this.audio.onended=()=>{if(g===this.gen)this.stop();};
      this.audio.onerror=()=>{if(g===this.gen){this.stop();this.onState(id,'failed','engine');}};
      await this.audio.play();if(g===this.gen)this.set(id,'playing');
    }catch(error){if(g===this.gen){const why=speechFailureWhy(error);if(why==='tap_play')this.set(id,'failed',why);else{this.stop();this.onState(id,'failed',why);}}}
  }
  /** Tap on 朗读 for turn id with its reply text. */
  async tap(id, md) {
    if (prefs.tts?.mode && prefs.tts.mode !== "phone") return this.hostTap(id);
    if (!supported()) { this.onState(id, 'failed', 'unsupported'); return; }
    const ss = window.speechSynthesis;
    if (this.id === id && this.st === 'playing') { ss.pause(); this.set(id, 'paused'); return; }
    if (this.id === id && this.st === 'paused') { ss.resume(); this.set(id, 'playing'); return; }
    this.stop();
    const text = speakText(md);
    if (!text) { this.onState(id, 'failed', 'empty'); return; }
    const lang = langOf(text);
    this.id = id; this.st = 'gen';
    this.onState(id, 'gen');
    const gen = ++this.gen;
    if(!(ss.getVoices()||[]).length) await waitForVoices();
    if(gen!==this.gen) return;
    const voice = localVoice(lang);
    if (!voice) { this.id=null;this.st='idle';this.onState(id, 'failed', 'novoice'); return; }
    const selected=prefs.tts?.phone_voice;
    const fallback=selected ? voice.name!==selected&&voice.voiceURI!==selected : !!prefs.tts?.voice;
    const notice=selected||prefs.tts?.voice;
    if(fallback&&!this.fallbackNotices.has(notice)){this.fallbackNotices.add(notice);this.onNotice('voice_fallback');}
    this.queue = chunks(text);
    const next = () => {
      if (gen !== this.gen) return;
      const part = this.queue.shift();
      if (part === undefined) { this.id = null; this.st = 'idle'; this.onState(id, 'idle'); return; }
      const u = new window.SpeechSynthesisUtterance(part);
      try{ u.voice = voice; }catch{ /* a voice object the engine does not know */ } u.lang = voice.lang; u.rate = prefs.tts?.rate || 1;
      u.onstart = () => { if (gen === this.gen && this.st !== 'paused') this.set(id, 'playing'); };
      u.onend = () => next();
      u.onerror = (e) => { if (gen !== this.gen || (e && e.error === 'interrupted')) return; this.gen++; this.id = null; this.st = 'idle'; this.onState(id, 'failed', 'engine'); };
      ss.speak(u);
    };
    next();
  }
}
