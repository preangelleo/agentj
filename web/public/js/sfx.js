// P154 · the Send sound (the rocket's whoosh) and its volume, set in Settings (「提示音音量」, 0 = mute).
// Why a gain node: iPhone Safari ignores HTMLMediaElement.volume (it always plays at 1.0, i.e. the phone's media volume,
// which is not the ringer the owner turned down), so 0.35 on the <audio> was full volume there. The same-origin <audio>
// (CSP media-src 'self'; no fetch) is routed through an AudioContext gain when the browser has one; otherwise .volume.
// Phone-local: localStorage only, never sent anywhere. Muted is stored as the negative of the level it was at ("-40"), so
// un-muting after a reload goes back to 40 % and not to the default.
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
  return vol;
}

let audio = null, ctx = null, gain = null;
/** Called inside the Send gesture (and the Settings preview): builds the player once; iOS needs a gesture for both. */
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
          ctx.createMediaElementSource(audio).connect(gain).connect(ctx.destination);
        } catch { ctx = null; gain = null; }
      }
      if (!gain) audio.volume = gainOf(vol);
    }
    if (ctx && ctx.state === 'suspended') ctx.resume().catch(() => {});
  } catch { audio = null; }
}

/** One whoosh, from the start; nothing at 0 % or when the page is hidden. Never stacks. */
export function play() {
  try {
    if (!audio || vol <= 0 || document.visibilityState !== 'visible') return false;
    if (ctx && ctx.state !== 'running') ctx.resume().catch(() => {});   // iOS: 'interrupted' after a call / the background
    audio.currentTime = 0;
    const p = audio.play(); if (p && p.catch) p.catch(() => {});
    document.body.dataset.whoosh = String((+document.body.dataset.whoosh || 0) + 1);
    return true;
  } catch { return false; }
}

/** Settings' 「试听」: the same sound at the chosen volume. */
export function preview() { unlock(); return play(); }

export const _debug = () => ({ vol, gain: gain ? gain.gain.value : null, element: audio ? audio.volume : null, webAudio: !!gain });
