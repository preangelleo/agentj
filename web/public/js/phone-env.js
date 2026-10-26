// Capability hints only: never infer private browsing from a denied persist grant.
import DICT from '../i18n.js';
export const OSES = ['ios', 'android', 'mac', 'windows', 'linux', 'other'];
/** P539 (#300): which system this browser runs on, for the passkey words (Face ID / Touch ID / fingerprint or face /
 *  Windows Hello / screen lock). An iPad that says "Macintosh" but has a touch screen stays ios. browserEnvironment keeps
 *  its exact {platform, inApp} shape (existing callers and tests); probeEnvironment adds `os`. */
export function osOf(ua = '', touch = 0) {
 if (/iPhone|iPad|iPod/i.test(ua) || (/Macintosh/i.test(ua) && touch > 1)) return 'ios';
 if (/Android|HarmonyOS|OpenHarmony/i.test(ua)) return 'android';
 if (/Macintosh|Mac OS X/i.test(ua)) return 'mac';
 if (/Windows/i.test(ua)) return 'windows';
 if (/CrOS/i.test(ua)) return 'other';
 if (/Linux|X11/i.test(ua)) return 'linux';
 return 'other';
}
/** The name of this system's own unlock, from the dictionary (zh / en). */
export function bioLabel(os, lang = 'zh') {
 const d = DICT[lang === 'en' ? 'en' : 'zh'] || {}, k = 'p539.bio.' + (OSES.includes(os) ? os : 'other');
 return d[k] ?? DICT.zh[k] ?? 'Face ID';
}
export function browserEnvironment(ua = '', touch = 0) {
 const ios = /iPhone|iPad|iPod/i.test(ua) || (/Macintosh/i.test(ua) && touch > 1);
 return {platform: ios ? 'ios' : /Android/i.test(ua) ? 'android' : 'desktop',
  inApp: /MicroMessenger|\bQQ\/|QQBrowser|Weibo|Telegram|Line\/|FBAN|FBAV|Instagram|; wv\)/i.test(ua)};
}
export async function probeEnvironment({ua, touch, storage, supported, probe}) {
 const env = {...browserEnvironment(ua, touch), os: osOf(ua, touch)};
 let writable = false, platformAuth = false, persistent = null;
 try { writable = !!(await probe()); } catch {}
 try { platformAuth = !!(await supported()); } catch {}
 try { if (storage?.persisted) persistent = !!(await storage.persisted()); } catch { persistent = false; }
 return {...env, writable, platformAuth, persistent, persistAvailable: typeof storage?.persist === 'function',
  warn: env.inApp || !writable || !platformAuth || typeof storage?.persist !== 'function' || persistent === false};
}
// The pairing fragment is one-use secret material; only copy the public page URL.
export function publicPhoneLink(href) { const u = new URL(href); u.hash = ''; u.search = ''; return u.href; }
// P547: where this device keeps its own Face ID / fingerprint key — the wording of the peer-approval sheets (pkp.where.*):
// iPhone / iPad → iCloud Keychain, Android → Google Password Manager, Mac → Touch ID, Windows → Windows Hello.
export function keyPlatform(ua = '', touch = 0) {
 const e = browserEnvironment(ua, touch);
 if (e.platform !== 'desktop') return e.platform;
 return /Macintosh|Mac OS X/i.test(ua) ? 'mac' : /Windows/i.test(ua) ? 'windows' : 'other';
}
