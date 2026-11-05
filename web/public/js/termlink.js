// 0.26.9 security L-2 (selfheal-0269 security-r1): links inside the web terminal. xterm's default for an OSC 8 hyperlink is
// confirm() + window.open of whatever the program printed (http: or https:, and any scheme once allowNonHttpProtocols is
// on). Programs in a terminal are not the owner: a link they print opens only when it is https:, never with credentials
// in it, in a new tab with no opener and no referrer — and with no browser confirm() dialog in between (the page's own
// layers are the only dialogs). Everything else is refused silently (the text stays readable in the terminal).
/** The URL to open, or null. Only https:, no user:password@, ≤ 2048 characters. */
export function httpsOnly(uri) {
  if (typeof uri !== 'string' || !uri || uri.length > 2048 || /[\u0000-\u001f\u007f\s]/.test(uri)) return null;
  let u;
  try { u = new URL(uri); } catch { return null; }
  if (u.protocol !== 'https:' || !u.hostname || u.username || u.password) return null;
  return u.href;
}
/** xterm's `linkHandler` option. open(url) = how the page opens a vetted link (window.open with noopener,noreferrer). */
export function linkHandler(open = (url) => globalThis.open?.(url, '_blank', 'noopener,noreferrer')) {
  return {
    allowNonHttpProtocols: false,
    activate(event, uri) {
      const url = httpsOnly(uri);
      if (!url) return false;
      try { event?.preventDefault?.(); } catch { /* synthetic */ }
      open(url);
      return true;
    },
  };
}
