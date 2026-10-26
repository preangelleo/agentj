// Every user-visible string comes from i18n.js (built from web/i18n/web.{zh,en}.json); runtime = window.AJLang
// (brand/lang.js: 中文 default, ?lang=en or the switch, saved as localStorage "aj.lang"). Dictionary text is ours, so
// `code` spans in it become <code> elements (createElement + textContent; never HTML). Host / Agent text is plain text.
import DICT from '../i18n.js';
import { copyable } from './copycode.js';   // P292: every `command` in our text can be copied (icon + one click)

import { osOf, bioLabel } from './phone-env.js';

const fmtVars = (str, vars) => (vars ? String(str).replace(/\{(\w+)\}/g, (m, k) => (Object.prototype.hasOwnProperty.call(vars, k) ? String(vars[k]) : m)) : str);
// P539 (#300): the dictionary says 「Face ID」 (written for iPhone). On Android / a Mac / Windows the same words name that
// system's own unlock (指纹或人脸 · Touch ID · Windows Hello). Linux and unknown browsers keep the dictionary text (their
// passkey sheet differs per browser; the per-platform keys such as pk.notSaved.<os> carry their own words).
const SUB_OS = ['android', 'mac', 'windows'];
let osOverride;                     // tests: setTextOs('android') · setTextOs(undefined) = detect again
let osSeen;
export function setTextOs(os) { osOverride = os; osSeen = undefined; }
export function textOs() {
  if (osOverride !== undefined) return osOverride;
  if (osSeen === undefined) {
    try { const ua = String(globalThis.navigator?.userAgent || ''); osSeen = /Mozilla\//.test(ua) ? osOf(ua, globalThis.navigator?.maxTouchPoints || 0) : 'ios'; } catch { osSeen = 'ios'; }
  }
  return osSeen;
}
const CJK = /[\u3000-\u9fff\uff00-\uffef]/;
/** "Face ID" (and "Face ID / Touch ID") → this system's unlock. A Chinese label takes the spaces around it with it. */
export function bioWords(str, os, lng) {
  if (!SUB_OS.includes(os) || typeof str !== 'string' || !str.includes('Face ID')) return str;
  const label = bioLabel(os, lng);
  const cjk = CJK.test(label);
  let s = str.replace(/Face ID ?(?:\/|\u6216|or) ?Touch ID/g, 'Face ID');
  s = s.replace(/( ?)Face ID( ?)/g, (m, a, b, at, whole) => {
    const before = whole[at - 1] || '', after = whole[at + m.length] || '';
    let word = label;
    if (lng === 'en' && !cjk && (at === 0 || /[.!?\n]\s*$/.test(whole.slice(0, at + a.length)))) word = word[0].toUpperCase() + word.slice(1);
    const keepA = a && !(cjk && CJK.test(before)), keepB = b && !(cjk && (CJK.test(after) || !after));
    return (keepA ? a : '') + word + (keepB ? b : '');
  });
  return s;
}
const raw = (key, vars) => (window.AJLang ? window.AJLang.t(DICT, key, vars) : fmtVars(DICT.zh[key] ?? key, vars));
export function t(key, vars) {
  const os = textOs();
  if (!SUB_OS.includes(os)) return raw(key, vars);
  return fmtVars(bioWords(raw(key), os, lang()), vars);   // only our words change, never host text inside {vars}
}
/** P344: one key's text in both languages (the shortcut sheet's search matches 中文 and English alike). */
export const both = (key) => [DICT.zh[key], DICT.en[key]].filter(Boolean);
export const lang = () => (window.AJLang ? window.AJLang.get() : 'zh');
export const locale = () => (lang() === 'en' ? 'en-GB' : 'zh-CN');
export const plain = (str) => String(str).replace(/`/g, '');
/** `x` → <code>x</code> wired by copycode.js (⧉ beside it, one click copies); everything else text nodes. */
export function fillText(node, str) {
  const parts = String(str).split('`');
  node.replaceChildren(...parts.map((p, i) => { if (i % 2 === 0) {
    const part = document.createDocumentFragment(); let at = 0;
    for (const m of p.matchAll(/https:\/\/(?:m\.)?agentj\.app\/[A-Za-z0-9_/?#=&.%:-]*/g)) {
      part.append(document.createTextNode(p.slice(at, m.index)));
      const a = document.createElement('a'); a.className = 'hint__link'; a.href = m[0]; a.textContent = m[0];
      a.target = '_blank'; a.rel = 'noopener noreferrer'; part.append(a); at = m.index + m[0].length;
    }
    part.append(document.createTextNode(p.slice(at))); return part;
  } const c = document.createElement('code'); c.textContent = p; return copyable(c, { label: 'set.copy' }); }));
}
/** Static markup: [data-i18n] text and [data-i18n-attr] attributes. */
export function applyStatic(root = document) {
  for (const n of root.querySelectorAll('[data-i18n]')) fillText(n, t(n.dataset.i18n));
  for (const n of root.querySelectorAll('[data-i18n-attr]')) {
    for (const pair of n.dataset.i18nAttr.split(',')) { const [a, k] = pair.split('='); n.setAttribute(a.trim(), plain(t(k.trim()))); }
  }
}
const subs = [];
export function onLang(fn) { subs.push(fn); }
if (window.AJLang) window.AJLang.onChange(() => { for (const f of subs) { try { f(); } catch { /* one bad listener must not stop the rest */ } } });
