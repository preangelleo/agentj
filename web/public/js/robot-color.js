// P302 (owner 09:58: 「虚拟员工的颜色和整个背景色重叠了……从 16 个里边挑到颜色差异最大的」): a robot never wears the colour of
// the ground it stands on. The measure is CIEDE2000 (ΔE00, perceptual — WCAG contrast is a text-luminance rule and calls
// every pastel robot on every pastel card "too close"). A robot whose body is less than MIN_DE from its ground is swapped
// for the farthest robot not already standing on that ground; every other robot keeps its stable colour. Every place that
// shows the factory robots (workflow cards, people rows, the @ list, the busy header / dialog / workflow view) calls
// settleRobots after it painted. A theme with its own robots is left alone (its colours are not known here).
export const MIN_DE = 20;
// the body colour of robots 0..15 (assets/robots.json → leo-palette.json hex; test/p302-robots.test.mjs keeps them in step)
export const ROBOT_HEX = ['#71bbde','#d1a7e8','#f7827e','#dbb293','#f0aad4','#fcbe7c','#f7e17c','#b7e396','#78dedc','#6a9bcc',
  '#827dbd','#629887','#d97757','#ffe292','#acacac','#f7827e'];
const lin = (v) => { v /= 255; return v <= 0.04045 ? v / 12.92 : ((v + 0.055) / 1.055) ** 2.4; };
function lab([r8, g8, b8]) {
  const [r, g, b] = [r8, g8, b8].map(lin), f = (t) => (t > 0.008856 ? Math.cbrt(t) : 7.787 * t + 16 / 116);
  const x = f((r * 0.4124 + g * 0.3576 + b * 0.1805) / 0.95047), y = f(r * 0.2126 + g * 0.7152 + b * 0.0722),
    z = f((r * 0.0193 + g * 0.1192 + b * 0.9505) / 1.08883);
  return [116 * y - 16, 500 * (x - y), 200 * (y - z)];
}
export const rgbOf = (hex) => { const n = parseInt(String(hex).replace('#', ''), 16); return [n >> 16 & 255, n >> 8 & 255, n & 255]; };
/** CIEDE2000 between two colours ('#rrggbb' or [r, g, b]). */
export function deltaE(a, b) {
  const [L1, a1, b1] = lab(Array.isArray(a) ? a : rgbOf(a)), [L2, a2, b2] = lab(Array.isArray(b) ? b : rgbOf(b)), rad = Math.PI / 180;
  const Cm = (Math.hypot(a1, b1) + Math.hypot(a2, b2)) / 2, G = 0.5 * (1 - Math.sqrt(Cm ** 7 / (Cm ** 7 + 25 ** 7)));
  const ap1 = a1 * (1 + G), ap2 = a2 * (1 + G), C1 = Math.hypot(ap1, b1), C2 = Math.hypot(ap2, b2);
  const hue = (y, x) => { const v = Math.atan2(y, x) / rad; return v < 0 ? v + 360 : v; };
  const h1 = C1 ? hue(b1, ap1) : 0, h2 = C2 ? hue(b2, ap2) : 0;
  let dh = 0; if (C1 * C2) { dh = h2 - h1; if (dh > 180) dh -= 360; else if (dh < -180) dh += 360; }
  const dL = L2 - L1, dC = C2 - C1, dH = 2 * Math.sqrt(C1 * C2) * Math.sin(dh * rad / 2), Lm = (L1 + L2) / 2, Cpm = (C1 + C2) / 2;
  let hm = h1 + h2; if (C1 * C2) hm = Math.abs(h1 - h2) > 180 ? (h1 + h2 + (h1 + h2 < 360 ? 360 : -360)) / 2 : (h1 + h2) / 2;
  const T = 1 - 0.17 * Math.cos((hm - 30) * rad) + 0.24 * Math.cos(2 * hm * rad) + 0.32 * Math.cos((3 * hm + 6) * rad) - 0.2 * Math.cos((4 * hm - 63) * rad);
  const SL = 1 + 0.015 * (Lm - 50) ** 2 / Math.sqrt(20 + (Lm - 50) ** 2), SC = 1 + 0.045 * Cpm, SH = 1 + 0.015 * Cpm * T;
  const RT = -2 * Math.sqrt(Cpm ** 7 / (Cpm ** 7 + 25 ** 7)) * Math.sin(60 * Math.exp(-(((hm - 275) / 25) ** 2)) * rad);
  return Math.sqrt((dL / SL) ** 2 + (dC / SC) ** 2 + (dH / SH) ** 2 + RT * (dC / SC) * (dH / SH));
}
/** Robot r on ground `bg`: r itself when it stands out (ΔE00 ≥ MIN_DE), else the farthest robot from the ground that is
    not in `taken` (the robots already on that ground) — the farthest overall when all of them are taken. */
export function robotOn(r, bg, taken = new Set()) {
  const n = ROBOT_HEX.length; r = ((r | 0) % n + n) % n;
  if (!bg || deltaE(ROBOT_HEX[r], bg) >= MIN_DE) return r;
  const far = ROBOT_HEX.map((h, i) => [deltaE(h, bg), i]).sort((a, b) => b[0] - a[0] || a[1] - b[1]).map((x) => x[1]);
  return far.find((i) => !taken.has(i)) ?? far[0];
}
/** The colour a robot stands on: the first opaque background up its tree (null if none can be read). */
export function groundOf(el) {
  for (let e = el.parentElement; e; e = e.parentElement) {
    const c = getComputedStyle(e).backgroundColor, m = /rgba?\(([\d.]+)[ ,]+([\d.]+)[ ,]+([\d.]+)(?:[ ,/]+([\d.]+%?))?/.exec(c)
      || /color\(srgb ([\d.]+) ([\d.]+) ([\d.]+)(?: \/ ([\d.]+%?))?/.exec(c);
    if (!m) continue;
    const a = m[4] === undefined ? 1 : parseFloat(m[4]) / (m[4].endsWith('%') ? 100 : 1);
    if (a < 0.9) continue;
    const k = c.startsWith('color(') ? 255 : 1;
    return {el: e, rgb: [m[1], m[2], m[3]].map((v) => Math.round(parseFloat(v) * k))};
  }
  return null;
}
/** Re-dress every factory robot under `root` (data-r = its stable robot) for the ground it stands on. */
export function settleRobots(root, custom = false) {
  if (!root || custom) return;
  const on = new Map();   // ground → its robots; the ones that already stand out are placed first, so a swap never copies them
  for (const i of root.querySelectorAll('.fp-robot[data-r],.rb[data-r]')) {
    const g = groundOf(i); if (!g) continue;
    if (!on.has(g.el)) on.set(g.el, {rgb: g.rgb, all: []});
    on.get(g.el).all.push(i);
  }
  for (const {rgb, all} of on.values()) {
    const taken = new Set(all.map((i) => +i.dataset.r).filter((r) => deltaE(ROBOT_HEX[r % ROBOT_HEX.length], rgb) >= MIN_DE));
    for (const i of all) {
      const r = robotOn(+i.dataset.r, rgb, taken), h = i.classList.contains('rb18') ? 18 : 24;
      taken.add(r);
      i.dataset.shown = String(r);
      i.style.backgroundPositionY = -r * h + 'px';
    }
  }
}
