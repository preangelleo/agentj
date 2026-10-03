// Every user-visible string comes from i18n.js (built from web/i18n/web.{zh,en}.json); runtime = window.AJLang
// (brand/lang.js: 中文 default, ?lang=en or the switch, saved as localStorage "aj.lang"). Dictionary text is ours, so
// `code` spans in it become <code> elements (createElement + textContent; never HTML). Host / Agent text is plain text.
import DICT from '../i18n.js';

const fmtVars = (str, vars) => (vars ? String(str).replace(/\{(\w+)\}/g, (m, k) => (Object.prototype.hasOwnProperty.call(vars, k) ? String(vars[k]) : m)) : str);
export function t(key, vars) { return window.AJLang ? window.AJLang.t(DICT, key, vars) : fmtVars(DICT.zh[key] ?? key, vars); }
export const lang = () => (window.AJLang ? window.AJLang.get() : 'zh');
export const locale = () => (lang() === 'en' ? 'en-GB' : 'zh-CN');
export const plain = (str) => String(str).replace(/`/g, '');
/** `x` → <code>x</code>; everything else text nodes. */
export function fillText(node, str) {
  const parts = String(str).split('`');
  node.replaceChildren(...parts.map((p, i) => { if (i % 2 === 0) return document.createTextNode(p); const c = document.createElement('code'); c.textContent = p; return c; }));
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
