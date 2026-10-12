// P183 ↔ P184: the one door from a file card to the 「转发到邮箱」 dialog. P184 owns the dialog (recipients, contacts, the
// owner's SMTP); this module only lets it register itself, so the file cards never import it and work without it.
//   registerMailForward(fn)   P184 calls this once at start-up. fn(req) opens the dialog; req =
//                             {kind: 'file', mid, name, mime, bytes, page: <turn id | null>, blob: () => Promise<Blob>}
//                             (blob() fetches the verified bytes through the media LRU; the dialog may ignore it and send by mid).
//   canMailForward()          false until something registered: the cards keep the mail icon hidden (P184 not merged).
//   mailForward(req)          the card's tap. → whatever fn returns, or false when nothing is registered.
// Registering (or un-registering with null) fires `aj:mailfwd` on document, so cards already on screen show / hide the icon.
let handler = null;
export function registerMailForward(fn) {
  handler = typeof fn === 'function' ? fn : null;
  document.dispatchEvent(new CustomEvent('aj:mailfwd', { detail: { on: !!handler } }));
}
export const canMailForward = () => !!handler;
export function mailForward(req) { return handler ? handler(req) : false; }
