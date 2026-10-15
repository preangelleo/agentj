// P183: the full-screen preview's document views (media.js owns the viewer itself, the image zoom and the HTML frame).
// Every view is built from the SHA-verified Blob the card already holds — nothing is fetched from anywhere but our own
// vendor/ files, each loaded on first need (never on first paint), pinned in vendor/VERSIONS.md:
//   pdf       pdf.js (legacy build, module + module worker from 'self'); pages drawn into <canvas> as they scroll near,
//             isEvalSupported:false (script-src has no 'unsafe-eval'), no XFA, no scripting, no font / cmap / wasm fetches
//             (connect-src stays the relays: a PDF whose fonts are not embedded falls back to system fonts).
//   docx      mammoth → HTML shown only in the SAME empty-sandbox iframe as an Agent HTML file (mediawire.sandboxAttrs():
//             no scripts, no same-origin, no network; pictures stay data: URIs inside the document).
//   sheet     SheetJS (xlsx / xls / ods) → rows of plain strings → a <table> built with textContent; one tab per sheet.
//   table     CSV / TSV → mediawire.parseDelimited → the same table.
//   markdown  md.js (the reply's own renderer: DOM only) + render.js (code highlighting, math).
//   code      a .codeblock <pre data-lang> → render.js highlights it (hljs → strict tokenizer → DOM).
//   text      a <pre> with textContent.
// Limits: PREVIEW.TEXT_MAX of text, PREVIEW.ROWS × PREVIEW.COLS of a table, PREVIEW.PDF_PAGES pages; past them a note says
// the download has the whole file. Each view returns a cleanup function (pdf.js: destroy the document and its worker).
import { t } from './t.js';
import { RelayMD } from './md.js';
import { upgrade } from './render.js';
import { PREVIEW, parseDelimited, codeLang, sandboxAttrs } from './mediawire.js';

const mk = (tag, cls, text) => { const e = document.createElement(tag); if (cls) e.className = cls; if (text !== undefined) e.textContent = text; return e; };

// ---------------------------------------------------------------- vendor loading (lazy, 'self' only)
const scripts = {};
function script(src, global) {
  if (scripts[src]) return scripts[src];
  scripts[src] = new Promise((res, rej) => {
    if (window[global]) return res(window[global]);
    const s = document.createElement('script');
    s.src = src; s.async = true;
    s.onload = () => (window[global] ? res(window[global]) : rej(new Error(global)));
    s.onerror = () => { scripts[src] = null; s.remove(); rej(new Error(global)); };
    document.head.appendChild(s);
  });
  return scripts[src];
}
export const LIBS = { pdf: 'vendor/pdfjs/pdf.min.mjs', pdfWorker: 'vendor/pdfjs/pdf.worker.min.mjs',
  docx: 'vendor/mammoth/mammoth.browser.min.js', sheet: 'vendor/xlsx/xlsx.core.min.js' };
// pdf.js ships as an ES module that also sets globalThis.pdfjsLib: a <script type="module" src> from 'self' (our modules
// never load code dynamically — static.test), the worker likewise a module worker from vendor/.
let pdfLib = null;
function pdfjs() {
  if (!pdfLib) pdfLib = new Promise((res, rej) => {
    if (window.pdfjsLib) return res(window.pdfjsLib);
    const s = document.createElement('script');
    s.type = 'module'; s.src = LIBS.pdf;
    s.onload = () => (window.pdfjsLib ? res(window.pdfjsLib) : rej(new Error('pdfjs')));
    s.onerror = () => { s.remove(); rej(new Error('pdfjs')); };
    document.head.appendChild(s);
  }).then((m) => { m.GlobalWorkerOptions.workerSrc = new URL(LIBS.pdfWorker, document.baseURI).href; return m; },
    (e) => { pdfLib = null; throw e; });
  return pdfLib;
}

async function textOf(blob) {
  const cut = blob.size > PREVIEW.TEXT_MAX;
  const buf = await (cut ? blob.slice(0, PREVIEW.TEXT_MAX) : blob).arrayBuffer();
  return { text: new TextDecoder('utf-8', { fatal: false }).decode(buf), cut };
}
const noteEl = (key) => mk('p', 'mpvnote', t(key));

/** rows (arrays of strings) → a scrollable table; the first row is the header. */
function tableEl(rows) {
  const wrap = mk('div', 'tablewrap mpvtable');
  const tb = mk('table');
  const head = mk('thead'), body = mk('tbody');
  rows.forEach((r, i) => {
    const tr = mk('tr');
    for (const v of r) tr.appendChild(mk(i === 0 ? 'th' : 'td', null, v == null ? '' : String(v)));
    (i === 0 ? head : body).appendChild(tr);
  });
  tb.append(head, body);
  wrap.appendChild(tb);
  return wrap;
}

// ---------------------------------------------------------------- the views
async function viewPdf(blob, box) {
  const lib = await pdfjs();
  const task = lib.getDocument({ data: new Uint8Array(await blob.arrayBuffer()), isEvalSupported: false, enableXfa: false,
    enableScripting: false, useSystemFonts: true, stopAtErrors: false });
  const doc = await task.promise;
  const pages = mk('div', 'mpdf');
  box.replaceChildren(pages);
  const n = Math.min(doc.numPages, PREVIEW.PDF_PAGES);
  const first = await doc.getPage(1);
  const vp1 = first.getViewport({ scale: 1 });
  const holders = [];
  for (let i = 1; i <= n; i++) {
    const h = mk('div', 'mpage');
    h.style.aspectRatio = `${vp1.width} / ${vp1.height}`;
    h.dataset.page = String(i);
    h.setAttribute('aria-label', t('media.pv.page', { n: i, of: doc.numPages }));
    pages.appendChild(h); holders.push(h);
  }
  if (doc.numPages > n) pages.appendChild(noteEl('media.pv.more'));
  let dead = false;
  const draw = async (h) => {
    if (h.dataset.drawn) return;
    h.dataset.drawn = '1';
    try {
      const page = await doc.getPage(Number(h.dataset.page));
      if (dead) return;
      const vp0 = page.getViewport({ scale: 1 });
      h.style.aspectRatio = `${vp0.width} / ${vp0.height}`;
      const css = Math.max(200, h.clientWidth || box.clientWidth || 600);
      const scale = Math.min(4, (css / vp0.width) * Math.min(2, window.devicePixelRatio || 1));
      const vp = page.getViewport({ scale });
      const cv = mk('canvas');
      cv.width = Math.floor(vp.width); cv.height = Math.floor(vp.height);
      await page.render({ canvas: cv, canvasContext: cv.getContext('2d'), viewport: vp }).promise;
      if (!dead) { h.replaceChildren(cv); h.dataset.drawn = 'ok'; }
    } catch { if (!dead) h.dataset.drawn = 'x'; }
  };
  const io = typeof IntersectionObserver === 'function'
    ? new IntersectionObserver((es) => { for (const e of es) if (e.isIntersecting) { io.unobserve(e.target); draw(e.target); } }, { root: box, rootMargin: '600px 0px' })
    : null;
  if (io) holders.forEach((h) => io.observe(h)); else holders.slice(0, 3).forEach(draw);
  return () => { dead = true; io?.disconnect(); doc.destroy().catch(() => {}); };
}

async function viewDocx(blob, box) {
  const mammoth = await script(LIBS.docx, 'mammoth');
  const r = await mammoth.convertToHtml({ arrayBuffer: await blob.arrayBuffer() });
  // The converted document goes into the same empty-sandbox frame as an Agent HTML file; the frame's style is ours.
  const css = '<style>html{background:#faf9f5;color:#141414}body{margin:0 auto;max-width:46em;padding:24px;' +
    'font:16px/1.65 system-ui,-apple-system,"PingFang SC","Microsoft YaHei",sans-serif;overflow-wrap:anywhere}' +
    'img{max-width:100%;height:auto}table{border-collapse:collapse;margin:1em 0}td,th{border:1px solid #e3dacc;padding:4px 8px;vertical-align:top}' +
    'a{color:#2d4f83}</style>';
  const f = document.createElement('iframe');
  for (const [k, v] of Object.entries(sandboxAttrs())) f.setAttribute(k, v);
  f.className = 'mframe';
  f.title = t('media.pv.docx');
  f.srcdoc = '<!doctype html><meta charset="utf-8">' + css + String(r && r.value || '');
  box.replaceChildren(f);
}

async function viewSheet(blob, box) {
  const X = await script(LIBS.sheet, 'XLSX');
  const wb = X.read(new Uint8Array(await blob.arrayBuffer()), { type: 'array', dense: true, cellFormula: false, cellHTML: false,
    cellStyles: false, sheetRows: PREVIEW.ROWS + 1, WTF: false });
  const names = (wb.SheetNames || []).slice(0, 50);
  const tabs = mk('div', 'mpvtabs'); tabs.setAttribute('role', 'tablist');
  const area = mk('div', 'mpvsheet');
  const show = (i) => {
    const ws = wb.Sheets[names[i]];
    let rows = ws ? X.utils.sheet_to_json(ws, { header: 1, raw: false, defval: '', blankrows: false }) : [];
    let more = rows.length > PREVIEW.ROWS;
    rows = rows.slice(0, PREVIEW.ROWS).map((r) => { if (r.length > PREVIEW.COLS) more = true; return r.slice(0, PREVIEW.COLS).map((v) => String(v).slice(0, PREVIEW.CELL)); });
    area.replaceChildren(rows.length ? tableEl(rows) : noteEl('media.pv.empty'));
    if (more) area.appendChild(noteEl('media.pv.more'));
    [...tabs.children].forEach((b, j) => b.setAttribute('aria-selected', String(i === j)));
  };
  names.forEach((n, i) => {
    const b = mk('button', 'mpvtab', n); b.type = 'button'; b.setAttribute('role', 'tab');
    b.addEventListener('click', () => show(i));
    tabs.appendChild(b);
  });
  box.replaceChildren(...(names.length > 1 ? [tabs] : []), area);
  if (names.length) show(0); else area.appendChild(noteEl('media.pv.empty'));
}

async function viewTable(blob, box, m) {
  const { text, cut } = await textOf(blob);
  const tsv = /\.tsv$/i.test(m.name) || m.mime === 'text/tab-separated-values';
  const { rows, more } = parseDelimited(text, tsv ? '\t' : ',');
  box.replaceChildren(rows.length ? tableEl(rows) : noteEl('media.pv.empty'));
  if (more || cut) box.appendChild(noteEl('media.pv.more'));
}

async function viewMarkdown(blob, box) {
  const { text, cut } = await textOf(blob);
  const w = mk('div', 'words mpvmd');
  w.replaceChildren(RelayMD.toDOM(RelayMD.parse(text), document));
  w.addEventListener('click', (e) => {                 // a code block's 拷贝, as on the page (relay.js copyCodeClick)
    const b = e.target.closest && e.target.closest('.copy');
    if (!b) return;
    const code = b.closest('.codeblock')?.querySelector('pre > code');
    RelayMD.copyText(code ? code.textContent : '').then((ok) => {
      if (!ok) return;
      b.textContent = t('r.md.copied'); b.classList.add('done');
      setTimeout(() => { b.textContent = t('r.md.copy'); b.classList.remove('done'); }, 1600);
    });
  });
  box.replaceChildren(w);
  if (cut) box.appendChild(noteEl('media.pv.more'));
  upgrade(w);
}

async function viewCode(blob, box, m, lang) {
  const { text, cut } = await textOf(blob);
  const wrap = mk('div', 'codeblock mpvcode');
  const pre = mk('pre'), code = mk('code', null, text);
  if (lang && text.length <= 20000) pre.setAttribute('data-lang', lang);
  pre.appendChild(code); wrap.appendChild(pre);
  box.replaceChildren(wrap);
  if (cut) box.appendChild(noteEl('media.pv.more'));
  upgrade(box);
}

/** kind (mediawire.previewKind) → fill `box` (a scrollable .mdoc) with the document view. → a cleanup function or nothing.
 *  Throws when the document cannot be read; the caller then says so and keeps the download. */
export async function renderDoc(kind, m, blob, box) {
  box.dataset.pv = kind;
  box.replaceChildren(mk('p', 'mpvnote', t('media.loading')));
  if (kind === 'pdf') return viewPdf(blob, box);
  if (kind === 'docx') return viewDocx(blob, box);
  if (kind === 'sheet') return viewSheet(blob, box);
  if (kind === 'table') return viewTable(blob, box, m);
  if (kind === 'markdown') return viewMarkdown(blob, box);
  if (kind === 'code') return viewCode(blob, box, m, codeLang(m.name));
  const { text, cut } = await textOf(blob);
  box.replaceChildren(mk('pre', 'mpvtext', text));
  if (cut) box.appendChild(noteEl('media.pv.more'));
}
