// P340 #27 (Leo 10-16, Mac home-screen app: 「长按设置截的屏只进了附件，没进剪贴板」): the gear's page screenshot also goes to the
// system clipboard, so it can be pasted into another chat app. Browser rules this module is built around:
//   · navigator.clipboard.write needs a user activation, and Safari (macOS / iOS) wants the write CALLED inside the gesture
//     with the image still being drawn — a ClipboardItem holding a Promise<Blob>, built synchronously in the handler.
//     So copyImage() never awaits before write(); the caller hands it the capture's promise.
//   · an older Chromium that only takes a Blob in ClipboardItem throws at construction: then the Blob is awaited and
//     written (still inside Chromium's ~5 s activation window).
//   · no async clipboard / no ClipboardItem (Firefox before 127, embedded views) → 'unsupported'; a refusal → 'failed'.
//     The page then offers 「复制图片」 (a fresh tap = a fresh activation) and, where navigator.canShare({files}) says yes,
//     「分享」 (the system share sheet).
// Nothing is read from the clipboard here, nothing is stored, nothing leaves the page.

/** Can this browser write an image to the clipboard at all? */
export function clipSupport(nav = globalThis.navigator, CI = globalThis.ClipboardItem) {
  return !!(nav && nav.clipboard && typeof nav.clipboard.write === 'function' && typeof CI === 'function');
}

/** Write a PNG (a Blob, or a promise of one) to the clipboard. Call it synchronously inside the user gesture.
 *  → Promise<'ok' | 'unsupported' | 'failed'>; never rejects. */
export function copyImage(blobOrPromise, nav = globalThis.navigator, CI = globalThis.ClipboardItem) {
  if (!clipSupport(nav, CI)) return Promise.resolve('unsupported');
  const blob = Promise.resolve(blobOrPromise);
  const write = (item) => {
    try { return Promise.resolve(nav.clipboard.write([item])).then(() => 'ok', () => 'failed'); }
    catch { return Promise.resolve('failed'); }
  };
  let item = null;
  try { item = new CI({ 'image/png': blob }); } catch { /* Blob-only ClipboardItem */ }
  if (item) return write(item);
  return blob.then((b) => { try { return write(new CI({ 'image/png': b })); } catch { return 'failed'; } }, () => 'failed');
}

/** Web Share with files (Android Chrome, iOS / macOS Safari, Windows Chromium); false where it is missing or refuses. */
export function canShareFile(file, nav = globalThis.navigator) {
  try { return !!(nav && typeof nav.share === 'function' && typeof nav.canShare === 'function' && nav.canShare({ files: [file] })); }
  catch { return false; }
}

/** → 'ok' | 'cancel' (the owner closed the sheet) | 'failed'. Call it inside a tap. */
export async function shareFile(file, nav = globalThis.navigator) {
  if (!canShareFile(file, nav)) return 'failed';
  try { await nav.share({ files: [file] }); return 'ok'; }
  catch (e) { return e && e.name === 'AbortError' ? 'cancel' : 'failed'; }
}

/** The notice after a screenshot (pure; unit-tested): text key + which fallback buttons to offer. */
export function shotNoticePlan(result, { added = true, canClip = true, canShare = false } = {}) {
  const ok = result === 'ok';
  const key = ok ? (added ? 'p340a.shotBoth' : 'p340a.shotClipOnly')
    : (added ? (result === 'unsupported' ? 'p340a.shotNoClipApi' : 'p340a.shotNoClip') : 'p340a.shotNothing');
  const actions = [];
  if (!ok && canClip) actions.push('copy');
  if (canShare) actions.push('share');
  return { key, kind: ok ? 'success' : 'warn', actions };
}
