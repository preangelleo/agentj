// Voice takes → 16 kHz mono PCM16 little-endian WAV, in the page (PROTOCOL §10.9), so the customer's computer needs no
// ffmpeg: decodeAudioData (whatever MediaRecorder produced — webm/opus, mp4/aac) → OfflineAudioContext(1, ⌈s·16000⌉, 16000)
// (resample + mono) → a 44-byte RIFF header + samples. Nothing leaves the page here; the WAV then travels as a §10.3 blob.
export const RATE = 16000;
// Metadata only: no recorded samples, messages or exception text is retained.
export let lastWavFailure = null;
function failed(phase, error, blob) {
  lastWavFailure = {phase, error: error?.name || 'UnsupportedAudio', bytes: blob?.size || 0,
    mime: blob?.type || ''};
  window.dispatchEvent(new CustomEvent('agentj-audio-error', {detail: {...lastWavFailure}}));
  return null;
}

/** Float32 mono samples → WAV bytes (Uint8Array). */
export function wavBytes(samples, rate = RATE) {
  const n = samples.length;
  const out = new Uint8Array(44 + n * 2);
  const v = new DataView(out.buffer);
  const str = (o, s) => { for (let i = 0; i < s.length; i++) out[o + i] = s.charCodeAt(i); };
  str(0, 'RIFF'); v.setUint32(4, 36 + n * 2, true); str(8, 'WAVE');
  str(12, 'fmt '); v.setUint32(16, 16, true); v.setUint16(20, 1, true); v.setUint16(22, 1, true);
  v.setUint32(24, rate, true); v.setUint32(28, rate * 2, true); v.setUint16(32, 2, true); v.setUint16(34, 16, true);
  str(36, 'data'); v.setUint32(40, n * 2, true);
  for (let i = 0; i < n; i++) {
    const x = Math.max(-1, Math.min(1, samples[i]));
    v.setInt16(44 + i * 2, x < 0 ? Math.round(x * 0x8000) : Math.round(x * 0x7fff), true);
  }
  return out;
}

/** A recorded Blob → {blob: audio/wav Blob, secs} or null when this browser cannot decode it (the caller then sends the
 *  original container as an attachment, PROTOCOL §10.9). */
export async function toWav(blob) {
  const OAC = window.OfflineAudioContext || window.webkitOfflineAudioContext;
  lastWavFailure = null;
  if (!OAC) return failed('context-unavailable', null, blob);
  let ctx, phase = 'context-create';
  try {
    // Decoding is offline work. A real-time context opens an output device even
    // though nothing is played, and can stall capture when that device is absent.
    ctx = new OAC(1, 1, RATE);
    phase = 'decode';
    const buf = await new Promise((ok, no) => {
      blob.arrayBuffer().then((ab) => { const p = ctx.decodeAudioData(ab, ok, no); if (p && p.catch) p.catch(no); }, no);
    });
    phase = 'resample';
    const secs = buf.duration;
    const len = Math.max(1, Math.ceil(secs * RATE));
    const off = new OAC(1, len, RATE);
    const src = off.createBufferSource();
    src.buffer = buf;
    src.connect(off.destination);
    src.start(0);
    const rendered = await off.startRendering();
    return { blob: new Blob([wavBytes(rendered.getChannelData(0))], { type: 'audio/wav' }), secs };
  } catch (error) { return failed(phase, error, blob); }
}
