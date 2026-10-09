// Capability hints only: never infer private browsing from a denied persist grant.
export function browserEnvironment(ua = '', touch = 0) {
 const ios = /iPhone|iPad|iPod/i.test(ua) || (/Macintosh/i.test(ua) && touch > 1);
 return {platform: ios ? 'ios' : /Android/i.test(ua) ? 'android' : 'desktop',
  inApp: /MicroMessenger|\bQQ\/|QQBrowser|Weibo|Telegram|Line\/|FBAN|FBAV|Instagram|; wv\)/i.test(ua)};
}
export async function probeEnvironment({ua, touch, storage, supported, probe}) {
 const env = browserEnvironment(ua, touch);
 let writable = false, platformAuth = false, persistent = null;
 try { writable = !!(await probe()); } catch {}
 try { platformAuth = !!(await supported()); } catch {}
 try { if (storage?.persisted) persistent = !!(await storage.persisted()); } catch { persistent = false; }
 return {...env, writable, platformAuth, persistent, persistAvailable: typeof storage?.persist === 'function',
  warn: env.inApp || !writable || !platformAuth || typeof storage?.persist !== 'function' || persistent === false};
}
// The pairing fragment is one-use secret material; only copy the public page URL.
export function publicPhoneLink(href) { const u = new URL(href); u.hash = ''; u.search = ''; return u.href; }
