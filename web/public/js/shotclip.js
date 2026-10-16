// P154 · 截图自动进附件 (computer browsers that can read the clipboard: Chrome / Edge / other Chromium).
// No browser tells a web page that a screenshot was taken (iPhone, Android and desktop alike; see
// reports/qa/p154/SCREENSHOT_RESEARCH.md). What a computer CAN see: the screenshot tools that copy to the clipboard
// (Windows Win+Shift+S / PrtSc, macOS Ctrl+Cmd+Shift+4, most Linux tools) take the focus for a moment and give it back.
// So, when the owner turned this on (the switch asks the browser's clipboard permission once): the page leaves the
// foreground for at most AWAY_MAX_MS, comes back, and the clipboard now holds an image it did not hold before → that
// image goes into the attachment tray (the same path as paste: the 10-attachment rule and its message, nothing is sent
// until the owner presses Send). Phone-local switch; the image never leaves the page except through the normal
// end-to-end upload after Send. Images are compared by SHA-256 only in memory; nothing is stored.
export const SHOT_KEY = 'aj.shotClip';
export const AWAY_MAX_MS = 60_000;     // a screenshot round trip, not "something copied in another app an hour ago"

let on = false, lastHash = null, awayAt = 0, busy = false, can = null;
let H = { attach: () => {}, say: () => {} };
try { on = localStorage.getItem(SHOT_KEY) === '1'; } catch { /* blocked storage: off */ }

const desktop = () => !matchMedia('(pointer:coarse)').matches && !('ontouchstart' in window);
async function permission() {
  try { return (await navigator.permissions.query({ name: 'clipboard-read' })).state; } catch { return null; }
}
/** Chromium on a computer: clipboard.read + a queryable 'clipboard-read' permission. Safari / Firefox / phones: no. */
export async function supported() {
  if (can !== null) return can;
  can = !!(navigator.clipboard && navigator.clipboard.read && navigator.permissions && desktop()) && (await permission()) !== null;
  return can;
}
export const enabled = () => on;
/** P340 #27: the page itself just put its own screenshot on the clipboard (the gear). The first image found after that is
 *  the baseline, not a new screenshot to attach — compared by "first read after the copy", not by hash, because Chromium
 *  re-encodes images it writes to the clipboard (the bytes read back differ from the PNG we gave it). */
let ownCopy = false;
export function remember() { ownCopy = true; }

async function sha(blob) {
  const d = await crypto.subtle.digest('SHA-256', await blob.arrayBuffer());
  return [...new Uint8Array(d)].map((b) => b.toString(16).padStart(2, '0')).join('');
}
/** The first image in the clipboard, or null (text, nothing, or no permission). */
async function clipImage() {
  const items = await navigator.clipboard.read();
  for (const it of items || []) {
    const ty = it.types.find((x) => /^image\//.test(x));
    if (ty) return it.getType(ty);
  }
  return null;
}

/** The switch (a click = user activation, so the browser may ask for the permission here). true = on. */
export async function setEnabled(want) {
  if (!want) { on = false; persist(); return false; }
  if (!(await supported())) return false;
  try {
    const img = await clipImage();               // asks once; also the baseline: what is there now is not a new screenshot
    lastHash = img ? await sha(img) : null;
    on = true;
  } catch { on = false; H.say('denied'); }
  persist();
  return on;
}
function persist() { try { localStorage.setItem(SHOT_KEY, on ? '1' : '0'); } catch { /* this visit only */ } }

function away() { if (on && !awayAt) awayAt = Date.now(); }
async function back() {
  const since = awayAt; awayAt = 0;
  if (!on || busy || !since || Date.now() - since > AWAY_MAX_MS || document.visibilityState !== 'visible') return;
  if ((await permission()) !== 'granted') return;  // never prompts outside the switch
  busy = true;
  try {
    const img = await clipImage();
    if (!img) return;
    const h = await sha(img);
    if (h === lastHash) return;                    // the same image as last time: not a new screenshot
    if (ownCopy) { ownCopy = false; lastHash = h; return; }   // P340 #27: our own screenshot, pasted elsewhere meanwhile
    lastHash = h;
    H.attach(new File([img], 'image', { type: img.type }));
  } catch { /* focus moved again, or the clipboard is busy: wait for the next return */ }
  finally { busy = false; }
}
/** Something the owner pasted / dropped themselves is not a new screenshot either. */
export async function seen(blob) { try { lastHash = await sha(blob); } catch { /* ignore */ } }

export function init(h = {}) {
  H = { ...H, ...h };
  addEventListener('blur', away);
  document.addEventListener('visibilitychange', () => { if (document.visibilityState === 'hidden') away(); else back(); });
  addEventListener('focus', () => { back(); });
}
export const _debug = () => ({ on, lastHash, awayAt, can });
