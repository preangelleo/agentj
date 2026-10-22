// P169 (PROTOCOL §23) the phone's look from the computer: `agentj theme apply|reset|restore` → {"t":"theme",…} on every
// ready and on every change; this page repaints at once (no reload). Factory themes (robots = default, dots) carry no
// files: the page already has them. A custom theme brings state / card / menu colours (CSS variables), the chat page's top-left
// logo, the Send glyph (a mask: only its shape counts) and robots (two sprite sheets) or dot colours. Everything is
// re-checked here — #rrggbb only, PNG signature + the exact pixel sizes of the host's rules — and anything else is dropped.
// Images become blob: URLs (CSP img-src blob:); the old ones are revoked. Locked whatever a theme says: pairing, safety
// code, security, offline, removed screens, the 网页版 badge, the menu's shield line, the home-screen icon, fonts.
export const FACTORY = ['robots', 'dots'];
export const FRAMES = 13;
const HEX = /^#[0-9a-fA-F]{6}$/;
const STATE_VARS = { idle: ['--c-idle'], working: ['--c-working'], waiting: ['--c-waiting'], question: ['--c-question'], offline: ['--c-offline'] };
const CARD_VARS = { owner: ['--src-leo'], agent: ['--src-agent'], computer: ['--src-host'], developer: ['--src-dev'],
  system: ['--src-sys', '--src-task', '--src-cmd'] };
// P187: the menus (Settings, the command list, shortcuts …) — five colours; muted text, dividers and fills derive from them
const MENU_VARS = { background: ['--menu-bg'], panel: ['--menu-panel'], text: ['--menu-text'], accent: ['--menu-accent'], line: ['--menu-line'] };
// P207 追加 1: every centred dialog (usage, 在办, load line, confirmations …) — ONE background token (factory Noir #2c2b26)
const DIALOG_VARS = { background: ['--dlg-bg'] };
const SQUARE = [128, 256];
const MAX_PNG = 64 * 1024;
let set = [];                    // CSS variables this module put on <html>
let urls = [];                   // blob: URLs to revoke on the next theme
let look = { id: 'robots', factory: 'robots', style: 'robots', n: 0, names: null, sheets: null, dots: 0, blink: true };
const subs = new Set();

/** busy.js reads the indicator from here: style robots|dots, custom sheets ({18, 24} URLs) + names + n, or dot count. */
export const busyLook = () => look;
let logoUrl = null;
let logoStates = null;
/** The chat page's top-left logo of a custom theme (one image for every state), or null = the official state logos. */
export const themeLogo = (state = "idle") => logoStates?.[state] || logoUrl;
/** The blob: URLs this theme holds (the previous theme's are revoked). */
export const liveUrls = () => urls.slice();
export function onTheme(fn) { subs.add(fn); return () => subs.delete(fn); }

function bytes(b64) {
  if (typeof b64 !== 'string' || b64.length > MAX_PNG * 4 / 3 + 8 || !/^[A-Za-z0-9+/]+=*$/.test(b64)) return null;
  try { const s = atob(b64), u = new Uint8Array(s.length); for (let i = 0; i < s.length; i++) u[i] = s.charCodeAt(i); return u; }
  catch (_) { return null; }
}
/** → [width, height] of a PNG, or null (signature + IHDR only; the computer checked the pixels). */
export function pngSize(u) {
  const sig = [137, 80, 78, 71, 13, 10, 26, 10];
  if (!u || u.length < 33 || sig.some((b, i) => u[i] !== b) || String.fromCharCode(...u.slice(12, 16)) !== 'IHDR') return null;
  const r = (o) => ((u[o] << 24) | (u[o + 1] << 16) | (u[o + 2] << 8) | u[o + 3]) >>> 0;
  return [r(16), r(20)];
}
function image(b64, ok) {
  const u = bytes(b64), wh = pngSize(u);
  if (!wh || !ok(wh[0], wh[1])) return null;
  const url = URL.createObjectURL(new Blob([u], { type: 'image/png' }));
  urls.push(url);
  return url;
}
const name = (x) => (x && typeof x.zh === 'string' && typeof x.en === 'string'
  ? { zh: x.zh.replace(/[\u0000-\u001f\u007f]/g, '').slice(0, 6), en: x.en.replace(/[\u0000-\u001f\u007f]/g, '').slice(0, 12) } : null);

/** The wire message → what to paint (pure; tests call it). */
export function sanitize(m) {
  const out = { id: 'robots', factory: 'robots', colors: {}, logo: null, send: null, busy: { style: 'robots' } };
  if (!m || m.t !== 'theme' || m.v !== 1) return out;
  if (FACTORY.includes(m.factory)) return { ...out, id: m.factory, factory: m.factory, busy: m.factory === 'dots' ? { style: 'dots', colors: [], blink: true } : { style: 'robots' } };
  out.factory = null;
  out.id = typeof m.id === 'string' && /^[a-z0-9][a-z0-9-]{1,31}$/.test(m.id) ? m.id : 'custom';
  const c = m.colors && typeof m.colors === 'object' ? m.colors : {};
  for (const [group, vars] of [['state', STATE_VARS], ['card', CARD_VARS], ['menu', MENU_VARS], ['dialog', DIALOG_VARS]]) {
    const g = c[group] && typeof c[group] === 'object' ? c[group] : {};
    for (const k of Object.keys(vars)) if (HEX.test(g[k] || '')) for (const v of vars[k]) out.colors[v] = g[k].toLowerCase();
  }
  if (m.logo && typeof m.logo === 'object') out.logo = { png: m.logo.png, states: m.logo.states, disc: m.logo.disc !== false };
  if (m.send && typeof m.send === 'object') out.send = { png: m.send.png };
  const b = m.busy && typeof m.busy === 'object' ? m.busy : {};
  if (b.style === 'dots') {
    const cs = Array.isArray(b.colors) ? b.colors.slice(0, 9).filter((p) => Array.isArray(p) && HEX.test(p[0]) && HEX.test(p[1])) : [];
    out.busy = { style: 'dots', colors: cs, blink: b.blink !== false };
  } else if (b.style === 'robots' && Number.isInteger(b.n) && b.n >= 1 && b.n <= 16 && Array.isArray(b.names) && b.names.length === b.n) {
    const names = b.names.map(name);
    out.busy = names.every(Boolean) ? { style: 'robots', n: b.n, names, s12: b.s12, s18: b.s18 } : { style: 'robots' };
  }
  return out;
}

function prop(k, v) { document.documentElement.style.setProperty(k, v); set.push(k); }

/** `{"t":"theme",…}` from the computer → repaint now. */
export function applyTheme(m) {
  const s = sanitize(m);
  const root = document.documentElement;
  for (const k of set) root.style.removeProperty(k);
  set = [];
  for (const u of urls) URL.revokeObjectURL(u);
  urls = [];
  for (const [k, v] of Object.entries(s.colors)) prop(k, v);
  const orb = document.getElementById('orb');
  logoUrl = s.logo ? image(s.logo.png, (w, h) => w === h && SQUARE.includes(w)) : null;
  logoStates = null;
  if (s.logo?.states && Object.keys(STATE_VARS).every(k => s.logo.states[k])) {
    const start = urls.length;
    const sizes = Object.keys(STATE_VARS).map(k => pngSize(bytes(s.logo.states[k])));
    const same = sizes.every(wh => wh && wh[0] === sizes[0][0] && wh[1] === sizes[0][1]);
    const group = same ? Object.fromEntries(Object.keys(STATE_VARS).map(k => [k, image(s.logo.states[k], (w, h) => w === h && SQUARE.includes(w))])) : {};
    if (Object.keys(STATE_VARS).every(k => group[k])) {
      logoStates = group;
      for (const url of Object.values(group)) { const preload = new Image(); preload.src = url; }
    } else { for (const u of urls.splice(start)) URL.revokeObjectURL(u); }
  }
  if (orb) orb.classList.toggle('bare', !!(logoUrl || logoStates) && s.logo.disc === false);   // the src: relay.js paintLogo (chat page only)
  const send = s.send ? image(s.send.png, (w, h) => w === h && SQUARE.includes(w)) : null;
  if (send) { prop('--send-mask', `url("${send}")`); root.dataset.thSend = '1'; } else delete root.dataset.thSend;
  const b = s.busy;
  if (b.style === 'dots') {
    b.colors.forEach(([h, l], i) => { prop(`--busy-h${i}`, h.toLowerCase()); prop(`--busy-l${i}`, l.toLowerCase()); });
    look = { id: s.id, factory: s.factory, style: 'dots', n: 0, names: null, sheets: null, dots: b.colors.length || 9, blink: b.blink };
  } else {
    const s12 = b.n ? image(b.s12, (w, h) => w === FRAMES * 12 && h === b.n * 12) : null;
    const s18 = b.n ? image(b.s18, (w, h) => w === FRAMES * 18 && h === b.n * 18) : null;
    const own = s12 && s18;
    look = { id: s.id, factory: s.factory, style: 'robots', n: own ? b.n : 0, names: own ? b.names : null,
      sheets: own ? { 18: s18, 24: s12 } : null, dots: 0, blink: true };
  }
  root.dataset.look = s.id;
  root.dataset.lookStyle = look.style;
  for (const fn of subs) { try { fn(look); } catch (_) { /* one painter failing never blocks the others */ } }
  return look;
}
