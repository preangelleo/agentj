// ADR-A177 (F29): the in-app QR scanner's frame plan, pure (no DOM) so test/p73_scan.test.mjs runs the exact same code
// against jsQR. iOS Safari has no BarcodeDetector, so jsQR reads canvas pixels: ask the camera for 1080p (iOS otherwise gives
// ~640×480), hand jsQR the centre square at full resolution (never the whole frame squeezed to 800 px wide, which left a
// 69-module pairing code ~3 px per module), and spread the other tries over successive frames instead of doing all of them
// every tick (STEPS below).

export const SCAN_CONSTRAINTS = Object.freeze({
  video: { facingMode: { ideal: 'environment' }, width: { ideal: 1920 }, height: { ideal: 1080 } },
  audio: false,
});
// a camera that refuses the constraints above still gets the old request
export const SCAN_FALLBACK = Object.freeze({ video: { facingMode: { ideal: 'environment' } }, audio: false });
export const SCAN_MAX_SIDE = 1080;        // the centre square at full resolution up to this (a 4K stream is shrunk): CPU bounded
export const SCAN_SMOOTH_SIDE = 720;      // … and every other try a little smaller: shrinking averages away sensor noise and
                                          // screen moiré that break jsQR's finder-pattern runs at 1:1 (still ≥ 2× the old
                                          // pixels per module: 640×480 iPhone default → 1080p, ×0.67)
export const SCAN_MAX_WHOLE = 960;        // the whole-frame try: long side ≤ this
export const SCAN_SLOW_MS = 10_000;       // scanning this long without a result → say what else works

// one try per video frame, in turn
const STEPS = [
  { crop: true, max: SCAN_MAX_SIDE, invert: false },
  { crop: true, max: SCAN_SMOOTH_SIDE, invert: false },
  { crop: true, max: SCAN_SMOOTH_SIDE, invert: true },     // light-on-dark code (ANSI mode on some terminals)
  { crop: false, max: SCAN_MAX_WHOLE, invert: false },     // held so close it overflows the centre square
];

/** Attempt k (0, 1, 2, …) on a vw×vh frame: source rectangle (sx, sy, sw, sh), canvas size (dw × dh) and whether to invert
 *  the pixels first. (We invert ourselves and always call jsQR with "dontInvert": the pinned jsQR crashes on "onlyInvert" —
 *  it never computes the inverted image — and "attemptBoth" would double every attempt.) */
export function scanPlan(vw, vh, k) {
  const st = STEPS[k % STEPS.length];
  if (!st.crop) {
    const s = Math.min(1, st.max / Math.max(vw, vh));
    return { sx: 0, sy: 0, sw: vw, sh: vh, dw: Math.round(vw * s), dh: Math.round(vh * s), invert: st.invert };
  }
  const side = Math.min(vw, vh), d = Math.min(side, st.max);
  return { sx: Math.floor((vw - side) / 2), sy: Math.floor((vh - side) / 2), sw: side, sh: side, dw: d, dh: d, invert: st.invert };
}

/** One jsQR attempt on a canvas 2D context holding nothing in particular: draws the plan's part of `source` (a <video>,
 *  or anything drawImage takes) and returns the decoded text or undefined. */
export function scanFrame(ctx, source, vw, vh, k, jsQR) {
  const p = scanPlan(vw, vh, k);
  const c = ctx.canvas;
  if (c.width !== p.dw || c.height !== p.dh) { c.width = p.dw; c.height = p.dh; }
  ctx.drawImage(source, p.sx, p.sy, p.sw, p.sh, 0, 0, p.dw, p.dh);
  const f = ctx.getImageData(0, 0, p.dw, p.dh);
  return scanPixels(f.data, f.width, f.height, p.invert, jsQR);
}

/** jsQR on RGBA pixels (inverted in place first when asked). → decoded text or undefined */
export function scanPixels(data, w, h, invert, jsQR) {
  if (invert) for (let i = 0; i < data.length; i += 4) { data[i] = 255 - data[i]; data[i + 1] = 255 - data[i + 1]; data[i + 2] = 255 - data[i + 2]; }
  return jsQR(data, w, h, { inversionAttempts: 'dontInvert' })?.data;
}
