// P154 · the Send sound (the rocket's whoosh) and its volume, set in Settings (「提示音音量」, 0 = mute).
// Why a gain node: iPhone Safari ignores HTMLMediaElement.volume (it always plays at 1.0, i.e. the phone's media volume,
// which is not the ringer the owner turned down), so 0.35 on the <audio> was full volume there. The same-origin <audio>
// (CSP media-src 'self'; no fetch) is routed through an AudioContext gain when the browser has one; otherwise .volume.
// Phone-local: localStorage only, never sent anywhere. Muted is stored as the negative of the level it was at ("-40"), so
// un-muting after a reload goes back to 40 % and not to the default.
// P575 (#390): the state-change sounds and 「全部完成」 (js/cues.js decides which, when) play here too, through the SAME gain:
// one volume for every sound the page makes. One cue element whose src changes, so two cues can never stack; a cue that
// comes while the whoosh is still sounding waits for its end; Send stops an active cue. iOS: the element is played (muted) once inside the first
// gesture so later cues, which come with no gesture, are allowed.
import { SRC as CUE_SRC } from './cues.js';

export const VOLUME_KEY = 'aj.sfxVolume';
export const DEFAULT_VOLUME = 15;              // percent; clearly quieter than before (0.35 on desktop, 1.0 on iPhone)
export const VOLUME_STEP = 5;
const SRC = 'assets/rocket-whoosh.mp3';

let vol = DEFAULT_VOLUME, lastOn = DEFAULT_VOLUME;
try {
  const raw = localStorage.getItem(VOLUME_KEY);
  if (raw !== null && raw !== '') {
    const v = Number(raw);
    if (Number.isFinite(v)) { vol = v < 0 ? 0 : clamp(v); lastOn = clamp(Math.abs(v)) || DEFAULT_VOLUME; }
  }
} catch { /* blocked storage: the default */ }

function clamp(v) { return Math.min(100, Math.max(0, Math.round(v / VOLUME_STEP) * VOLUME_STEP)); }
/** Percent → linear gain. Squared, so the slider feels even to the ear (half way ≈ a quarter of the power). */
export const gainOf = (v) => (clamp(v) / 100) ** 2;

export function volume() { return vol; }
/** The level 静音 goes back to: the last one above 0. */
export function lastLevel() { return lastOn; }
export function setVolume(v) {
  vol = clamp(Number(v) || 0);
  if (vol > 0) lastOn = vol;
  try { localStorage.setItem(VOLUME_KEY, String(vol || -lastOn)); } catch { /* blocked storage: this visit only */ }
  if (gain) gain.gain.value = gainOf(vol);
  else if (audio) audio.volume = gainOf(vol);
  if (cue && !cueGain) cue.volume = gainOf(vol);
  return vol;
}

let audio = null, ctx = null, gain = null, cue = null, cueReady = false, cueWait = null, cueGain = false;
let cuePlayback = 0; // a real preview started during unlock owns the player after the priming promise settles
/** Called inside the Send gesture (and the Settings preview, and the page's first tap / key): builds the players once;
 *  iOS needs a gesture for both. */
export function unlock() {
  try {
    if (!audio) {
      audio = new Audio(SRC);
      audio.preload = 'auto';
      const AC = window.AudioContext || window.webkitAudioContext;
      if (AC) {
        try {
          ctx = new AC();
          gain = ctx.createGain();
          gain.gain.value = gainOf(vol);
          gain.connect(ctx.destination);
          ctx.createMediaElementSource(audio).connect(gain);
        } catch { ctx = null; gain = null; }
      }
      if (!gain) audio.volume = gainOf(vol);
    }
    if (!cue) {
      cue = new Audio(CUE_SRC['idle']);
      cue.preload = 'auto';
      let routed = false;
      if (gain) { try { ctx.createMediaElementSource(cue).connect(gain); routed = true; } catch { /* .volume below */ } }
      cueGain = routed;
      cue.volume = routed ? 1 : gainOf(vol);
      cue.muted = true;                                  // iOS: this play inside the gesture is what allows later ones
      const primingPlayback = cuePlayback;
      const p = cue.play();
      const done = () => {
        if (cuePlayback === primingPlayback) { try { cue.pause(); cue.currentTime = 0; } catch { /* not loaded yet */ } }
        cue.muted = false; cueReady = true;
      };
      if (p && p.then) p.then(done, () => { cue.muted = false; cueReady = true; }); else done();
    }
    if (ctx && ctx.state === 'suspended') ctx.resume().catch(() => {});
  } catch { audio = null; }
}

/** One whoosh, from the start; nothing at 0 % or when the page is hidden. Never stacks. */
export function play() {
  try {
    if (!audio || vol <= 0 || document.visibilityState !== 'visible') return false;
    if (ctx && ctx.state !== 'running') ctx.resume().catch(() => {});   // iOS: 'interrupted' after a call / the background
    if (cue) { cuePlayback++; cue.pause(); } // Send also invalidates an unresolved cue start receipt
    audio.currentTime = 0;
    const p = audio.play(); if (p && p.catch) p.catch(() => {});
    document.body.dataset.whoosh = String((+document.body.dataset.whoosh || 0) + 1);
    return true;
  } catch { return false; }
}

/** Settings' 「试听」: the same sound at the chosen volume. */
export function preview() { unlock(); return play(); }

const whooshing = () => !!audio && !audio.paused && !audio.ended;
/** One state / all-done sound (js/cues.js ids). Nothing at 0 %, when hidden or before the first gesture. Never stacks:
 *  onStarted runs only after Audio.play succeeds and this playback still owns the player; queueing is not a start.
 *  a new cue replaces the one still sounding; during the whoosh it waits for the whoosh's end (the newest one wins). */
export function playCue(id, force, onStarted) {
  try {
    const src = CUE_SRC[id];
    if (!src || !cue || !cueReady || vol <= 0 || (!force && document.visibilityState !== 'visible')) return false;
    if (cueWait) { audio.removeEventListener('ended', cueWait); cueWait = null; }
    if (whooshing()) {
      cueWait = () => { cueWait = null; playCue(id, false, onStarted); };
      audio.addEventListener('ended', cueWait, { once: true });
      return true;
    }
    if (ctx && ctx.state !== 'running') ctx.resume().catch(() => {});
    const playback = ++cuePlayback;
    if (!cue.src.endsWith(src)) cue.src = src;
    cue.currentTime = 0;
    const started = () => { if (cuePlayback === playback) onStarted?.(); };
    const p = cue.play();
    if (p && p.then) p.then(started, () => {}); else started();
    if (window.__AJ_TEST_CUES__ === true) {
      document.body.dataset.cue = id;
      document.body.dataset.cues = String((+document.body.dataset.cues || 0) + 1);
    }
    return true;
  } catch { return false; }
}
/** Settings' per-sound 「试听」: inside the tap, so it also unlocks. */
export function previewCue(id) { unlock(); if (cue) { cue.muted = false; cueReady = true; } return playCue(id, true); }

export const _debug = () => ({ vol, gain: gain ? gain.gain.value : null, element: audio ? audio.volume : null, webAudio: !!gain,
  cue: cue ? { src: cue.src.replace(/^.*\//, ''), volume: cue.volume, ready: cueReady } : null });
