// render.js — P57: code highlighting, math and diagrams for the Agent's words, loaded only when a page needs them.
//
// md.js stays the only thing that turns Agent text into DOM, and it still builds every node with createElement +
// textContent. It MARKS three things (see md.js "P57"): <pre data-lang> (a fenced block with a language),
// .math[data-tex] (the source as written is its text) and .codeblock.mermaid-src. After a page has settled, this module
// upgrades the marks that come into view (IntersectionObserver), loading the vendored library on first need through a
// plain <script src="vendor/…"> (script-src 'self'; nothing loads on first paint, nothing from another origin):
//   code     highlight.js 11 common bundle → hljs.highlight(…).value is escaped HTML; hlNodes() reads it with a strict
//            tokenizer (text, the five entities hljs emits, <span class="hljs-…"> and </span> — anything else rejects the
//            whole block) and rebuilds it with createElement / createTextNode; the rebuilt text must equal the source.
//            No HTML parser ever sees Agent text.
//   math     KaTeX: katex.render() builds DOM itself; trust:false (no \href / \url / \includegraphics / \html*),
//            throwOnError:true so bad TeX keeps the source as written, maxSize / maxExpand bound the work.
//   diagram  mermaid (securityLevel strict, no HTML labels, directives cannot change theme/CSS/security) renders an SVG
//            string, shown ONLY as <img src="blob:…"> — an SVG image runs no script, loads nothing, takes no clicks.
//            Failure: the code block stays, with one line 「图没画出来，下面是原文」.
// Idempotent: a mark is upgraded once (data-rx q → w → ok | x); re-renders build new nodes, served from a small cache.
// While the shown page is still open (streaming), nothing is upgraded; it happens once the page is done.
// Limits (also enforced by md.js, which does not mark bigger blocks): code 20 000, TeX 4 000, mermaid 10 000 chars.
import { t } from './t.js';

export const LIMIT = { code: 20000, tex: 4000, mermaid: 10000 };
const LIBS = {
  hljs: { js: 'vendor/hljs/highlight.min.js', g: 'hljs' },
  katex: { js: 'vendor/katex/katex.min.js', css: 'vendor/katex/katex.min.css', g: 'katex' },
  mermaid: { js: 'vendor/mermaid/mermaid.min.js', g: 'mermaid' },
};
const SETTLE_MS = 300, OPEN_RETRY_MS = 900, CACHE_MAX = 80;
const MARKS = 'pre[data-lang]:not([data-rx]), .math[data-tex]:not([data-rx]), .codeblock.mermaid-src:not([data-rx])';

// ---------------------------------------------------------------- loading
const loading = {};
export const loaded = () => Object.keys(loading).filter((k) => loading[k]);
function load(name) {
  if (loading[name]) return loading[name];
  const L = LIBS[name];
  loading[name] = new Promise((res, rej) => {
    if (L.css) { const l = document.createElement('link'); l.rel = 'stylesheet'; l.href = L.css; document.head.appendChild(l); }
    const s = document.createElement('script');
    s.src = L.js; s.async = true;
    s.onload = () => (window[L.g] ? res(window[L.g]) : rej(new Error(name)));
    s.onerror = () => { loading[name] = null; s.remove(); rej(new Error(name)); };   // next page tries again
    document.head.appendChild(s);
  });
  return loading[name];
}

// ---------------------------------------------------------------- cache (rendered results, in memory only)
const caches = { hl: new Map(), tex: new Map(), mmd: new Map() };
function cget(c, k) { if (!c.has(k)) return undefined; const v = c.get(k); c.delete(k); c.set(k, v); return v; }
function cput(c, k, v) {
  c.set(k, v);
  while (c.size > CACHE_MAX) { const [k0, v0] = c.entries().next().value; c.delete(k0); if (v0 && v0.url) URL.revokeObjectURL(v0.url); }
}
/** Unpair / revoke: drop every rendered copy of the Agent's words. */
export function forget() {
  for (const c of Object.values(caches)) { for (const v of c.values()) if (v && v.url) URL.revokeObjectURL(v.url); c.clear(); }
}

// ---------------------------------------------------------------- highlight.js output → DOM (no HTML parser)
const ENT = { amp: '&', lt: '<', gt: '>', quot: '"', '#x27': "'", '#39': "'" };
const HL_CLASS = /^(?:hljs-[a-z][a-z0-9_-]{0,40}|language-[a-z][a-z0-9-]{0,40}|[a-z][a-z0-9]{0,20}_{1,3})$/;   // language-x: an embedded language (JS in HTML)
const HL_TOKEN = /<span class="([^"<>&]*)">|<\/span>|&(amp|lt|gt|quot|#x27|#39);|[<>&]/g;
/** hljs's escaped HTML → a DocumentFragment of text nodes and <span class="hljs-…"> only, or null when the string holds
 *  anything else (another tag, an attribute, an unknown entity, unbalanced spans, > 64 deep) or its text ≠ src. */
export function hlNodes(html, src, doc) {
  const frag = doc.createDocumentFragment(), stack = [frag];
  let text = '', buf = '', last = 0, m;
  const flush = () => { if (buf) { stack[stack.length - 1].appendChild(doc.createTextNode(buf)); text += buf; buf = ''; } };
  HL_TOKEN.lastIndex = 0;
  while ((m = HL_TOKEN.exec(html))) {
    buf += html.slice(last, m.index); last = HL_TOKEN.lastIndex;
    if (m[2]) { buf += ENT[m[2]]; continue; }
    if (m[1] !== undefined) {
      const cls = m[1].split(' ');
      if (!/^(?:hljs|language)-/.test(cls[0]) || !cls.every((c) => HL_CLASS.test(c)) || stack.length > 64) return null;
      flush();
      const s = doc.createElement('span'); s.className = cls.join(' ');
      stack[stack.length - 1].appendChild(s); stack.push(s); continue;
    }
    if (m[0] === '</span>') { if (stack.length < 2) return null; flush(); stack.pop(); continue; }
    return null;                                     // a raw < > & : not hljs output
  }
  buf += html.slice(last);
  flush();
  return stack.length === 1 && text === src ? frag : null;
}

// ---------------------------------------------------------------- the three upgrades
const done = (el, v) => el.setAttribute('data-rx', v);

async function code(pre) {
  const c = pre.querySelector('code'), lang = pre.getAttribute('data-lang') || '';
  const src = c ? c.textContent : '';
  if (!c || src.length > LIMIT.code) return done(pre, 'x');
  const key = lang + '\u0000' + src;
  let frag = cget(caches.hl, key);
  if (frag === undefined) {
    const hljs = await load('hljs');
    frag = null;
    if (hljs.getLanguage(lang)) {
      try { frag = hlNodes(hljs.highlight(src, { language: lang, ignoreIllegals: true }).value, src, document); } catch { frag = null; }
    }
    cput(caches.hl, key, frag);
  }
  if (!pre.isConnected || c.textContent !== src) return;   // re-rendered meanwhile: the new node has its own mark
  if (!frag) return done(pre, 'x');
  c.replaceChildren(frag.cloneNode(true));
  c.classList.add('hljs');
  done(pre, 'ok');
}

const KATEX = { throwOnError: true, trust: false, strict: 'ignore', maxSize: 20, maxExpand: 200, output: 'htmlAndMathml' };
async function math(el) {
  const tex = el.getAttribute('data-tex') || '', display = el.classList.contains('display');
  if (!tex.trim() || tex.length > LIMIT.tex) return done(el, 'x');
  const key = (display ? 'D' : 'I') + tex;
  let box = cget(caches.tex, key);
  if (box === undefined) {
    const katex = await load('katex');
    box = document.createElement('span');
    try { katex.render(tex, box, { ...KATEX, displayMode: display }); } catch { box = null; }   // bad TeX: the source stays
    cput(caches.tex, key, box);
  }
  if (!el.isConnected || el.getAttribute('data-tex') !== tex) return;
  if (!box) return done(el, 'x');
  el.replaceChildren(...[...box.childNodes].map((n) => n.cloneNode(true)));
  done(el, 'ok');
}

// mermaid renders one diagram at a time (it is not re-entrant); the font is a system stack, because an SVG shown as
// an image cannot load the page's web fonts and the text must measure the same as it will draw.
const MMD_FONT = 'ui-sans-serif, system-ui, -apple-system, "Segoe UI", Roboto, "PingFang SC", "Microsoft YaHei", "Noto Sans CJK SC", sans-serif';
const MMD_SECURE = ['secure', 'securityLevel', 'startOnLoad', 'maxTextSize', 'maxEdges', 'suppressErrorRendering', 'theme',
  'themeVariables', 'themeCSS', 'darkMode', 'fontFamily', 'altFontFamily', 'fontSize', 'htmlLabels', 'flowchart', 'dompurifyConfig',
  'look', 'layout', 'elk', 'legacyMathML', 'forceLegacyMathML', 'deterministicIds', 'deterministicIDSeed', 'arrowMarkerAbsolute'];
let mq = Promise.resolve(), mTheme = null, mSeq = 0;
export const dark = () => {
  const th = document.documentElement.getAttribute('data-theme');
  return th === 'dark' || (th !== 'light' && !!window.matchMedia && matchMedia('(prefers-color-scheme: dark)').matches);
};
async function drawMermaid(m, src, theme) {
  if (mTheme !== theme) {
    m.initialize({ startOnLoad: false, securityLevel: 'strict', htmlLabels: false, flowchart: { htmlLabels: false },
      theme, darkMode: theme === 'dark', fontFamily: MMD_FONT, themeVariables: { fontFamily: MMD_FONT, darkMode: theme === 'dark' },
      suppressErrorRendering: true, maxTextSize: LIMIT.mermaid, maxEdges: 400, secure: MMD_SECURE });
    mTheme = theme;
  }
  const id = 'ajmmd' + (++mSeq);
  try {
    const { svg } = await m.render(id, src);
    const vb = /viewBox="\s*([-\d.e]+)[\s,]+([-\d.e]+)[\s,]+([\d.e]+)[\s,]+([\d.e]+)\s*"/.exec(svg.slice(0, 4000));
    const w = vb ? Math.ceil(+vb[3]) : 0, h = vb ? Math.ceil(+vb[4]) : 0;
    if (!(w > 0 && h > 0)) return null;
    // the root's width="100%" gives an image no size of its own: pin the viewBox size instead
    const s = svg.replace(/^<svg\b([^>]*?)\swidth="[^"]*"/, `<svg$1 width="${w}" height="${h}"`);
    return { url: URL.createObjectURL(new Blob([s], { type: 'image/svg+xml' })), w, h };
  } catch { return null; } finally {
    for (const x of [document.getElementById('d' + id), document.getElementById(id)]) if (x) x.remove();
  }
}
async function diagram(wrap) {
  const c = wrap.querySelector('pre > code'), src = c ? c.textContent : '';
  if (!c || !src.trim() || src.length > LIMIT.mermaid) return done(wrap, 'x');
  const theme = dark() ? 'dark' : 'neutral', key = theme + '\u0000' + src;
  let r = cget(caches.mmd, key);
  if (r === undefined) {
    const m = await load('mermaid');
    r = await (mq = mq.then(() => drawMermaid(m, src, theme), () => drawMermaid(m, src, theme)));
    cput(caches.mmd, key, r);
  }
  if (!wrap.isConnected || c.textContent !== src) return;
  const pre = wrap.querySelector('pre');
  if (!r) {
    const note = document.createElement('div');
    note.className = 'rxnote';
    note.textContent = t('render.fail');
    wrap.insertBefore(note, pre);
    return done(wrap, 'x');
  }
  const fig = document.createElement('div');
  fig.className = 'diagram' + (theme === 'dark' ? ' dk' : '');
  const img = document.createElement('img');
  img.alt = t('render.diagram'); img.decoding = 'async';
  img.width = r.w; img.height = r.h; img.src = r.url;
  img.style.setProperty('--w', String(r.w));          // CSSOM (CSP-safe): full size = max(1.5 × column, natural)
  fig.appendChild(img);
  // tap = full size (wide diagrams then scroll sideways); tap again = fit the column. No button: the picture is the target.
  fig.addEventListener('click', () => fig.classList.toggle('full'));
  wrap.insertBefore(fig, pre);
  wrap.classList.add('drawn');
  done(wrap, 'ok');
}

// ---------------------------------------------------------------- scheduling
let io = null;
const queued = new Set();
async function work(el) {
  queued.delete(el);
  if (!el.isConnected || el.getAttribute('data-rx') !== 'q') return;
  done(el, 'w');
  try {
    if (el.tagName === 'PRE') await code(el);
    else if (el.classList.contains('math')) await math(el);
    else await diagram(el);
  } catch { done(el, 'x'); }                          // library failed to load: the plain text stays
}
function watch(el) {
  done(el, 'q');
  if (typeof IntersectionObserver !== 'function') { work(el); return; }
  if (!io) io = new IntersectionObserver((es) => { for (const e of es) if (e.isIntersecting) { io.unobserve(e.target); work(e.target); } },
    { rootMargin: '600px 0px' });
  queued.add(el);
  io.observe(el);
}
function scan(root) {
  root._rx = 0;
  if (!root.isConnected) return;
  for (const el of queued) if (!el.isConnected) { queued.delete(el); if (io) io.unobserve(el); }   // re-rendered away
  const marks = root.querySelectorAll(MARKS);
  if (!marks.length) return;
  // the shown page is still being written: its blocks are not final — wait for the page to finish
  if (document.body && document.body.dataset.pageOpen === '1') { root._rx = setTimeout(() => scan(root), OPEN_RETRY_MS); return; }
  for (const el of marks) watch(el);
}
/** Called after md.js has (re)filled `root` with the Agent's words. Cheap when there is nothing to upgrade. */
export function upgrade(root) {
  if (!root || !root.querySelector) return;
  clearTimeout(root._rx);
  root._rx = setTimeout(() => scan(root), SETTLE_MS);
}
