// P340 (Leo 10-16 #41): the header's right-hand icons (扫码 · 搜索 · 设置) are spaced by what the eye sees — the drawn
// glyph, not the 44 px button around it (the scan frame sits 4/24 inside its box, the gear fills its box). The reference is
// the gear's drawn right edge to the screen edge (M). On a narrow phone (≤ 390 px) every drawn gap equals M; from 600 px up
// the header keeps its old spacing; in between the gap slides linearly, so a fold opening / a window resize never jumps.
// The room this frees goes to the model pill on the left (the scan button's auto margin takes it).
export const NARROW = 390, WIDE = 600;
export const IDS = ['scanBtn', 'srchBtn', 'setBtn'];

/** The drawn gap wanted between two header glyphs at viewport width w (natural = today's gap, m = the gear's margin). */
export function topGap(w, natural, m){
  if (!(natural > m)) return natural;               // already as tight as the reference: never widen
  const k = Math.min(1, Math.max(0, (w - NARROW) / (WIDE - NARROW)));
  return m + (natural - m) * k;
}

// the glyph's drawn extent in viewport px (the path's bbox + half the stroke), from the SVG itself, so a redrawn icon keeps working
export function inkOf(btn){
  const svg = [...btn.querySelectorAll('svg')].find((s) => s.getClientRects().length && getComputedStyle(s).display !== 'none');
  if (!svg) return null;
  const r = svg.getBoundingClientRect(), vb = svg.viewBox && svg.viewBox.baseVal, vw = vb && vb.width ? vb.width : 24;
  let bb; try { bb = svg.getBBox(); } catch (_) { return null; }
  if (!bb || !bb.width) return null;
  const k = r.width / vw, half = (parseFloat(svg.getAttribute('stroke-width')) || 0) / 2, x0 = vb ? vb.x : 0;
  return {left: r.left + (bb.x - x0 - half) * k, right: r.left + (bb.x - x0 + bb.width + half) * k};
}

// P340 返工 (p302): boxes never overlap — between two header buttons there are always ≥ MIN px — and every button keeps a
// 44 px target that does not reach into its neighbour's. Within that, the drawn gaps come as close to the gear's margin as
// they can: the gear stays put (it is the reference); a middle button (搜索) narrows (height stays 44; class tfit = a centred
// 44 px ::after, neighbouring centres ≥ 44 px apart, so targets only touch); the first one (扫码) keeps its full 44 px box
// and only its glyph slides inside it (--tfx), never closer than half a pixel to the box edge.
export const MIN = 8, TAP = 44, EDGE = 0.5;
/** Layout for buttons [0 … n-1] (the last, the gear, untouched) so each drawn gap i→i+1 is as near want[i] as allowed:
 *  widths w and glyph shifts dx (px, + = right). r[i] / l[i] = drawn inset from the box edge at full width; ink[i] = drawn width. */
export function topLayout(want, r, l, ink){
  const n = l.length, w = new Array(n).fill(TAP), dx = new Array(n).fill(0);
  const L = (i) => l[i] - (TAP - w[i]) / 2 + dx[i], R = (i) => r[i] - (TAP - w[i]) / 2 - dx[i];
  for (let i = n - 2; i >= 0; i--){
    if (i > 0){
      let x = TAP - 2 * (MIN + r[i] + L(i + 1) - want[i]);            // the width that makes the gap exactly want[i]
      x = Math.max(Math.min(TAP, x), ink[i] + 2 * EDGE, 2 * TAP - 2 * MIN - w[i + 1]);   // glyph fits; centres ≥ 44 apart
      w[i] = Math.min(TAP, x);
    }
    const rest = MIN + R(i) + L(i + 1) - want[i];                      // still too wide by this much: slide the glyph right
    const lo = -(l[i] - (TAP - w[i]) / 2 - EDGE), hi = r[i] - (TAP - w[i]) / 2 - EDGE;
    dx[i] = Math.max(lo, Math.min(hi, rest));
  }
  return {w, dx};
}
/** P340 first cut's name, kept for the unit test: the widths alone. */
export const topWidths = (want, r, l, ink) => topLayout(want, r, l, ink).w;

export function fitTop(doc = document){
  const all = IDS.map((id) => doc.getElementById(id)).filter(Boolean);
  for (const b of all){ b.style.removeProperty('margin-left'); b.style.removeProperty('width'); b.style.removeProperty('--tfx'); b.classList.remove('tfit'); }
  const top = doc.querySelector('header.top');
  const btns = all.filter((b) => b.parentElement === top && b.getClientRects().length);   // Settings moves them away: hands off
  if (btns.length < 2) return [];
  const inks = btns.map(inkOf);
  if (inks.some((x) => !x)) return [];
  const boxes = btns.map((b) => b.getBoundingClientRect());
  const tr = top.getBoundingClientRect();
  const margin = tr.right - inks.at(-1).right, w = doc.documentElement.clientWidth || innerWidth;
  const r = inks.map((k, i) => boxes[i].right - k.right), l = inks.map((k, i) => k.left - boxes[i].left), ink = inks.map((k) => k.right - k.left);
  const want = [], out = [];
  for (let i = 0; i < btns.length - 1; i++){
    const natural = inks[i + 1].left - inks[i].right, g0 = boxes[i + 1].left - boxes[i].right;
    // today's spacing with the boxes MIN apart is the most this ever widens to; the margin is the target
    want.push(topGap(w, natural - g0 + Math.max(MIN, g0), margin));
  }
  const {w: ws, dx} = topLayout(want, r, l, ink);
  btns.forEach((b, i) => {
    if (ws[i] < TAP - 0.05){ b.style.width = ws[i].toFixed(2) + 'px'; b.classList.add('tfit'); }
    if (Math.abs(dx[i]) > 0.05) b.style.setProperty('--tfx', dx[i].toFixed(2) + 'px');
    if (i > 0){ const g0 = boxes[i].left - boxes[i - 1].right; if (g0 < MIN - 0.05) b.style.marginLeft = (MIN - g0).toFixed(2) + 'px'; }
  });
  for (let i = 0; i < want.length; i++) out.push({want: want[i], width: ws[i], dx: dx[i]});
  return out;
}

let wired = false;
export function wireTopFit(doc = document){
  if (wired) return; wired = true;
  let raf = 0;
  const again = () => { if (raf) return; raf = requestAnimationFrame(() => { raf = 0; fitTop(doc); }); };
  addEventListener('resize', again, {passive: true});
  try {
    const ro = new ResizeObserver(again);
    for (const id of IDS){ const b = doc.getElementById(id); if (b) ro.observe(b); }
    const top = doc.querySelector('header.top'); if (top) ro.observe(top);
  } catch (_) { /* no ResizeObserver: the resize listener alone */ }
  try {   // Settings moves the three buttons into its own header (P264) and back: refit (or let go) each time
    const mo = new MutationObserver(again);
    for (const c of [doc.querySelector('header.top'), doc.getElementById('set-actions')]) if (c) mo.observe(c, {childList: true});
  } catch (_) {}
  again();
}
