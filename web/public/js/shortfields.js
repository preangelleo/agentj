// #545: short values in conversation prose use the same bare copy component as inline code.
// Walk text nodes only; never parse HTML, reinterpret links, or include hidden card metadata.
import { copyable } from './copycode.js';
import { RelayMD } from './md.js';

const FIELD = /https?:\/\/[^\s<>`"'，。；、）)]+|`([^`\n]{1,200})`|[A-Za-z0-9._%+-]{1,64}@[A-Za-z0-9-]+(?:\.[A-Za-z0-9-]+)*\.[A-Za-z]{2,63}|\bAJ-[A-Z0-9]{4}(?:-[A-Z0-9]{4}){3}\b|@[A-Za-z0-9_][A-Za-z0-9_.-]{1,63}|\b[A-Za-z0-9](?:[A-Za-z0-9-]{0,62}[A-Za-z0-9])?(?:\.[A-Za-z0-9-]+)*\.(?:app|com|net|org|io|ai|dev|co|me)(?:\/[A-Za-z0-9._~%+/?=&:#-]*)?/g;
const SKIP = 'a,button,code,pre,textarea,input,select,math,svg,[data-cc],.math,.mslot,.rlink,[contenteditable="true"]';
function field(doc, text) {
  const n = doc.createElement('span');
  n.textContent = text;
  n.dataset.ccField = '1'; n.tabIndex = 0; n.setAttribute('role', 'button');
  return copyable(n, {variant: 'bare'});
}
export function shortFieldMatches(src, {inlineCode = false} = {}) {
  const ids = [...src.matchAll(/(?:账号\s*(?:ID|Id|id)?|账户\s*(?:ID|Id|id)?|account\s*(?:ID|Id|id)|\bID)\s*[:：]?\s*([A-Za-z0-9][A-Za-z0-9_-]{2,63})/gi)]
    .map(m => ({index: m.index + m[0].lastIndexOf(m[1]), 0: m[1]}));
  FIELD.lastIndex = 0;
  let at = 0;
  const out = [];
  for (const m of [...src.matchAll(FIELD), ...ids].sort((a,b) => a.index - b.index)) {
    if (m.index < at || m[1] !== undefined && !inlineCode) continue;
    if (m[1] === undefined && (/[A-Za-z0-9_@.-]/.test(src[m.index - 1] || '') || /[A-Za-z0-9_@-]/.test(src[m.index + m[0].length] || ''))) continue;
    let value = m[1] ?? m[0];
    const link = m[1] === undefined && /^https?:\/\//i.test(value);
    if (m[1] === undefined) value = value.replace(/[.,;:!?]+$/, '');
    const href = link ? RelayMD.safeHref(value) : null;
    if (link && !href) continue;
    const end = m.index + (m[1] === undefined ? value.length : m[0].length);
    out.push({start: m.index, end, value, href}); at = end;
  }
  return out;
}

export function shortFields(root, {inlineCode = false} = {}) {
  if (!root) return;
  const doc = root.ownerDocument;
  // A mailto is an address to copy; a labelled email still displays its actual destination.
  for (const a of root.querySelectorAll('a[href]')) {
    if (!/^mailto:/i.test(a.getAttribute('href'))) continue;
    const href = RelayMD.mailHref(a.getAttribute('href'));
    if (!href) continue;
    const n = field(doc, href.slice(7)), target = a.nextElementSibling;
    if (target?.classList.contains('lhost')) target.remove();
    a.replaceWith(n);
  }
  const walk = doc.createTreeWalker(root, 4), nodes = [];
  while (walk.nextNode()) if (!walk.currentNode.parentElement.closest(SKIP)) nodes.push(walk.currentNode);
  for (const node of nodes) {
    const src = node.textContent, out = doc.createDocumentFragment();
    let at = 0;
    for (const {start, end, value, href} of shortFieldMatches(src, {inlineCode})) {
      let n;
      if (href) {
        n = doc.createElement('a'); n.href = href; n.target = '_blank'; n.rel = 'noopener noreferrer'; n.textContent = value;
      } else n = field(doc, value);
      out.append(doc.createTextNode(src.slice(at, start)), n);
      at = end;
    }
    if (at) { out.appendChild(doc.createTextNode(src.slice(at))); node.replaceWith(out); }
  }
}
