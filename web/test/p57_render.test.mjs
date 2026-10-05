// P57 (render lane): md.js marks for code / math / diagrams, the $ rules, the highlight.js output sanitiser, size limits,
// the vendored files (pinned by sha256) and the lazy-loading rules of js/render.js. Run: node --test web/test/*.test.mjs
import { test } from 'node:test';
import assert from 'node:assert/strict';
import { readFileSync, readdirSync, statSync } from 'node:fs';
import { createHash } from 'node:crypto';
import { join, relative, sep } from 'node:path';
import vm from 'node:vm';
import { fileURLToPath } from 'node:url';

const PUB = fileURLToPath(new URL('../public/', import.meta.url));
const read = (r) => readFileSync(join(PUB, r), 'utf8');
globalThis.window = globalThis.window || {};          // t.js (imported by render.js) reads window.AJLang
const { RelayMD } = await import('../public/js/md.js');
const R = await import('../public/js/render.js');

// the same minimal DOM as modules.test.mjs (createElement / createTextNode / fragments / attributes / textContent)
class FNode {
  constructor(tag) { this.tag = tag; this.kids = []; this.attrs = {}; this.className = ''; this.data = null; }
  appendChild(n) { if (n.tag === '#frag') this.kids.push(...n.kids); else this.kids.push(n); return n; }
  append(...ns) { for (const n of ns) this.appendChild(n); }
  setAttribute(k, v) { this.attrs[k] = String(v); }
  getAttribute(k) { return k in this.attrs ? this.attrs[k] : null; }
  get textContent() { return this.data !== null ? this.data : this.kids.map((k) => k.textContent).join(''); }
  set textContent(v) { this.kids = []; this.data = null; if (this.tag === '#text') this.data = String(v); else this.kids.push(fdoc.createTextNode(v)); }
  all() { const out = []; const st = [...this.kids]; while (st.length) { const n = st.pop(); if (n.tag !== '#text') { out.push(n); st.push(...n.kids); } } return out; }
}
const fdoc = {
  createElement: (t) => new FNode(t),
  createTextNode: (s) => { const n = new FNode('#text'); n.data = String(s); return n; },
  createDocumentFragment: () => new FNode('#frag'),
};
const render = (src) => { const root = new FNode('div'); RelayMD.toDOM(RelayMD.parse(src), fdoc, root); return root; };
const walk = (n, out = []) => { for (const k of n.kids) { if (k.tag !== '#text') { out.push(k); walk(k, out); } } return out; };   // document order
const maths = (r) => walk(r).filter((n) => /(^| )math( |$)/.test(n.className));
const inlineMath = (s) => RelayMD.parse(s).flatMap((b) => b.children || []).filter((n) => n.tag === 'math').map((n) => n.tex);

test('P57 markers: <pre data-lang>, .mermaid-src code block, .math[data-tex] with the source as its text', () => {
  let r = render('```Python\nprint("<b>hi</b>")\n```');
  const pre = r.all().find((n) => n.tag === 'pre');
  assert.equal(pre.getAttribute('data-lang'), 'python', 'language lower-cased onto the <pre>');
  assert.equal(pre.textContent, 'print("<b>hi</b>")', 'the code is text, never markup');
  assert.equal(render('```\nplain\n```').all().find((n) => n.tag === 'pre').getAttribute('data-lang'), null, 'no language: no mark');
  assert.equal(render('```a"b\nx\n```').all().find((n) => n.tag === 'pre').getAttribute('data-lang'), null, 'odd language names are not marked');
  r = render('```mermaid\nflowchart TD\n  A --> B\n```');
  const wrap = r.all().find((n) => /codeblock/.test(n.className));
  assert.equal(wrap.className, 'codeblock mermaid-src');
  assert.equal(r.all().find((n) => n.tag === 'pre').getAttribute('data-lang'), null, 'a diagram is not highlighted');
  assert.ok(r.all().some((n) => n.className === 'copy'), 'still a code block with its copy button (the fallback)');
  r = render('看 $x^2$ 和 \\(a+b\\)。\n\n$$\n\\frac{1}{2}\n$$\n\n\\[ E = mc^2 \\]');
  const m = maths(r);
  assert.deepEqual(m.map((n) => [n.tag, n.className, n.getAttribute('data-tex')]),
    [['span', 'math', 'x^2'], ['span', 'math', 'a+b'], ['div', 'math display', '\n\\frac{1}{2}\n'], ['div', 'math display', ' E = mc^2 ']]);
  assert.deepEqual(m.map((n) => n.textContent), ['$x^2$', '\\(a+b\\)', '$$\n\\frac{1}{2}\n$$', '\\[ E = mc^2 \\]'], 'unrendered, the reader sees what the agent wrote');
  assert.equal(r.textContent.includes('看 $x^2$ 和 \\(a+b\\)。'), true);
  // inline $$…$$ / \[…\] inside a paragraph are display math in a <span>
  assert.deepEqual(maths(render('so $$a=b$$ ok')).map((n) => [n.tag, n.className]), [['span', 'math display']]);
  // math inside lists and quotes
  assert.equal(maths(render('- item $a$\n  $$\n  b\n  $$\n> $c$')).length, 3);
  // nothing in data-tex is ever markup: a "<script>" in TeX is an attribute value
  const evil = maths(render('$<img src=x onerror=alert(1)>$'));
  assert.equal(evil.length, 1);
  assert.equal(evil[0].kids.length, 1); assert.equal(evil[0].kids[0].tag, '#text');
});

test('P57 the $ rule: prices stay prices (Pandoc: no space inside the $…$, closing $ not followed by a digit)', () => {
  for (const s of ['It costs $5 and $10.', '$5/$10', 'between 5$ and 6$', 'US$5 vs US$10', 'pay $5, get $10 back', '$ x$', '$x $',
    '\\$x\\$', 'a $ b $ c', '$5-$10', 'cost: $3.50 + $1', '$$', '$$$$', '`$a$` b', 'x $a\nb$', '$a `b` c$'])
    assert.deepEqual(inlineMath(s), [], `not math: ${JSON.stringify(s)}`);
  for (const [s, want] of [['$x$', ['x']], ['($a+b$)', ['a+b']], ['$\\alpha$.', ['\\alpha']], ['$x^2$ and $y_1$', ['x^2', 'y_1']],
    ['价格 $5，公式 $E=mc^2$', ['E=mc^2']], ['\\(a\\) \\[b\\]', ['a', 'b']], ['x $$a\nb$$', ['a\nb']]])
    assert.deepEqual(inlineMath(s), want, `math: ${JSON.stringify(s)}`);
  assert.equal(render('\\$5 and \\$10').textContent, '$5 and $10', '\\$ is a literal dollar');
  assert.equal(render('It costs $5 and $10.').textContent, 'It costs $5 and $10.');
  // an unclosed display block, or one with a blank line inside, stays text
  assert.equal(maths(render('$$\na\n\nb\n$$')).length, 0);
  assert.equal(maths(render('$$\nunclosed')).length, 0);
});

test('P57 size limits: code > 20 000, TeX > 4 000, mermaid > 10 000 characters are not marked (they stay plain)', () => {
  const { MAX_CODE_HL, MAX_TEX, MAX_MERMAID } = RelayMD.LIMITS;
  assert.deepEqual([MAX_CODE_HL, MAX_TEX, MAX_MERMAID], [20000, 4000, 10000]);
  assert.deepEqual(R.LIMIT, { code: MAX_CODE_HL, tex: MAX_TEX, mermaid: MAX_MERMAID }, 'render.js enforces the same limits');
  const pre = (src) => render(src).all().find((n) => n.tag === 'pre');
  assert.equal(pre('```js\n' + 'x'.repeat(MAX_CODE_HL) + '\n```').getAttribute('data-lang'), 'js');
  assert.equal(pre('```js\n' + 'x'.repeat(MAX_CODE_HL + 1) + '\n```').getAttribute('data-lang'), null);
  const wrapCls = (src) => render(src).all().find((n) => /codeblock/.test(n.className)).className;
  assert.equal(wrapCls('```mermaid\n' + 'x'.repeat(MAX_MERMAID) + '\n```'), 'codeblock mermaid-src');
  assert.equal(wrapCls('```mermaid\n' + 'x'.repeat(MAX_MERMAID + 1) + '\n```'), 'codeblock');
  assert.equal(maths(render('$' + 'a'.repeat(MAX_TEX) + '$')).length, 1);
  assert.equal(maths(render('$' + 'a'.repeat(MAX_TEX + 1) + '$')).length, 0);
  assert.equal(maths(render('$$\n' + 'a\n'.repeat(MAX_TEX) + '$$')).length, 0, 'a long display block too');
});

test('P57 math parsing stays linear on 200 KB pathological inputs', () => {
  const N = 200000;
  for (const [name, src] of Object.entries({ dollars: '$a '.repeat(N / 3), pairs: '$$a '.repeat(N / 4), parens: '\\(a '.repeat(N / 3),
    brackets: '\\[a '.repeat(N / 3), lines: '$$a\n'.repeat(N / 4), mixed: '$x *a* '.repeat(N / 7), closers: 'a$ '.repeat(N / 3) })) {
    const t = performance.now();
    RelayMD.parse(src);
    const ms = performance.now() - t;
    assert.ok(ms < 600, `${name}: ${Math.round(ms)} ms`);
  }
});

// ---------------------------------------------------------------- highlight.js output → DOM
const spansOnly = (frag) => frag.all().every((n) => n.tag === 'span' && Object.keys(n.attrs).length === 0 && n.className.split(' ').every((c) => /^(hljs|language)-|_$/.test(c)));
const hljs = (() => { const ctx = {}; vm.runInNewContext(read('vendor/hljs/highlight.min.js') + '\n;this.hljs = hljs;', ctx); return ctx.hljs; })();

test('P57 hlNodes: real highlight.js output becomes text + <span class="hljs-…"> only, with exactly the source text', () => {
  for (const [lang, src] of [['python', 'def f(x):\n    return "<script>alert(1)</script>" if x else 0x1F  # done\n'],
    ['html', '<img src=x onerror="alert(1)"><script>alert(document.cookie)</script>'], ['javascript', 'const a = `${b}`; // \'q\' & "r"'],
    ['bash', 'echo "$HOME" && rm -rf /tmp/x'], ['json', '{"a": [1, 2, "&amp;"]}']]) {
    const html = hljs.highlight(src, { language: lang, ignoreIllegals: true }).value;
    const frag = R.hlNodes(html, src, fdoc);
    assert.ok(frag, `${lang}: accepted`);
    assert.equal(frag.textContent, src, `${lang}: same text`);
    assert.ok(frag.all().length > 0, `${lang}: some highlighting`);
    assert.ok(spansOnly(frag), `${lang}: only class-only hljs spans`);
  }
});

test('P57 hlNodes: anything that is not hljs output is rejected as a whole (the block stays plain text)', () => {
  const src = 'x';
  for (const bad of ['<img src=x onerror=alert(1)>x', '<script>alert(1)</script>', '<span class="hljs-keyword" onclick="alert(1)">x</span>',
    '<span class="hljs-keyword" style="color:red">x</span>', '<span class="evil">x</span>', '<span class="hljs-keyword evil">x</span>',
    '<span class=hljs-keyword>x</span>', '<span class="hljs-keyword">x', 'x</span>', '<b>x</b>', '&nbsp;x', '&#60;x', '<a href="javascript:x">x</a>',
    '<span class="hljs-keyword"><svg onload=alert(1)>x</svg></span>', 'x<', 'x>', 'x&', '<!-- -->x', '<span class="hljs-a">y</span>'])
    assert.equal(R.hlNodes(bad, src, fdoc), null, `rejected: ${bad}`);
  const deep = '<span class="hljs-a">'.repeat(70) + 'x' + '</span>'.repeat(70);
  assert.equal(R.hlNodes(deep, 'x', fdoc), null, 'too deep');
  const ok = R.hlNodes('<span class="hljs-title function_">f</span>&lt;&amp;&gt;&quot;&#x27;', 'f<&>"\'', fdoc);
  assert.ok(ok && spansOnly(ok));
  assert.equal(ok.all()[0].className, 'hljs-title function_');
});

test('P57 KaTeX options: trust:false — \\href / \\url / \\includegraphics never become links or images', () => {
  const ctx = { window: {}, document: undefined };
  vm.runInNewContext(read('vendor/katex/katex.min.js'), ctx);
  const katex = ctx.katex || ctx.window.katex;
  assert.ok(katex && katex.renderToString, 'katex UMD loads');
  const src = read('js/render.js');
  assert.match(src, /const KATEX = \{ throwOnError: true, trust: false, strict: 'ignore', maxSize: 20, maxExpand: 200, output: 'htmlAndMathml' \}/);
  const opts = { throwOnError: true, trust: false, strict: 'ignore', maxSize: 20, maxExpand: 200, output: 'htmlAndMathml' };
  for (const tex of ['\\href{javascript:alert(1)}{x}', '\\url{javascript:alert(1)}', '\\includegraphics{https://evil.example/x.png}',
    '\\htmlId{x}{y}', '\\htmlStyle{color:red}{y}', '\\htmlData{a=b}{y}']) {
    let out = '';
    try { out = katex.renderToString(tex, opts); } catch { out = ''; }
    // (the TeX source itself appears only as the text of the MathML <annotation>)
    assert.doesNotMatch(out.replace(/<annotation[^>]*>[^<]*<\/annotation>/g, ''), /<a\b|\shref=|<img\b|\ssrc=|javascript:|evil\.example|color:red|data-a=|id="x"/i, tex);
  }
  // expansion bombs are bounded (maxExpand) instead of hanging
  const t = performance.now();
  try { katex.renderToString('\\def\\a{\\a\\a}\\a', opts); } catch { /* too many expansions */ }
  assert.ok(performance.now() - t < 2000);
});

// ---------------------------------------------------------------- vendored files + loading rules
test('P57 vendor/: every file pinned in VERSIONS.md (sha256), each library with its LICENSE, exact versions', () => {
  const V = read('vendor/VERSIONS.md');
  const listed = new Map([...V.matchAll(/^([0-9a-f]{64})  (\S+)$/gm)].map((m) => [m[2], m[1]]));
  const walk = (d) => readdirSync(d).flatMap((n) => { const p = join(d, n); return statSync(p).isDirectory() ? walk(p) : [p]; });
  const files = walk(join(PUB, 'vendor')).map((p) => relative(join(PUB, 'vendor'), p).split(sep).join('/'))
    .filter((r) => !r.startsWith('jsQR') && r !== 'VERSIONS.md').sort();
  assert.deepEqual([...listed.keys()].sort(), files, 'VERSIONS.md lists exactly the shipped vendor files');
  for (const [r, h] of listed) assert.equal(createHash('sha256').update(readFileSync(join(PUB, 'vendor', r))).digest('hex'), h, r);
  for (const lib of ['hljs', 'katex', 'mermaid']) assert.ok(listed.has(`${lib}/LICENSE.txt`), `${lib} LICENSE`);
  assert.match(V, /\| 11\.12\.0 \|/); assert.match(V, /\| 0\.19\.0 \|/); assert.match(V, /\| 12\.1\.0 \|/);
  // KaTeX: only the woff2 faces ship; the CSS lists woff2 first, so a browser never asks for the missing woff / ttf
  assert.ok([...listed.keys()].filter((r) => r.startsWith('katex/fonts/')).every((r) => r.endsWith('.woff2')));
  const css = read('vendor/katex/katex.min.css');
  for (const m of css.matchAll(/src:url\(([^)]+)\)/g)) assert.match(m[1], /^fonts\/KaTeX_[\w-]+\.woff2$/);
  for (const m of css.matchAll(/url\(fonts\/(KaTeX_[\w-]+)\.woff2\)/g)) assert.ok(listed.has(`katex/fonts/${m[1]}.woff2`), m[1]);
});

test('P57 render.js: loads only our own vendor files, by <script src>, lazily; md.js stays DOM-only; relay hooks it after fill', () => {
  const src = read('js/render.js');
  const paths = [...src.matchAll(/(?:js|css): '([^']+)'/g)].map((m) => m[1]);
  assert.deepEqual(paths, ['vendor/hljs/highlight.min.js', 'vendor/katex/katex.min.js', 'vendor/katex/katex.min.css', 'vendor/mermaid/mermaid.min.js']);
  for (const bad of [/\binnerHTML\b/, /outerHTML/, /insertAdjacentHTML/, /DOMParser/, /createContextualFragment/, /\bimport\s*\(/, /\bfetch\s*\(/, /https?:/, /srcdoc/, /\beval\b/])
    assert.doesNotMatch(src, bad, `render.js uses ${bad}`);
  assert.match(src, /securityLevel: 'strict', htmlLabels: false, flowchart: \{ htmlLabels: false \}/);
  assert.match(src, /new Blob\(\[s\], \{ type: 'image\/svg\+xml' \}\)/, 'a diagram is only ever an <img> of a blob SVG');
  assert.match(src, /document\.createElement\('img'\)/);
  assert.doesNotMatch(src, /appendChild\(svg|insertBefore\(svg/i);
  assert.match(src, /dataset\.pageOpen === '1'/, 'an open (streaming) page is not upgraded');
  const relay = read('js/relay.js');
  assert.match(relay, /import \{ upgrade as upgradeWords, forget as forgetRendered \} from '\.\/render\.js';/);
  assert.match(relay, /w\.replaceChildren\(RelayMD\.toDOM\(RelayMD\.parse\(tx\), document\)\); upgradeWords\(w\);/);
  assert.match(relay, /forgetRendered\(\);/, 'unpair / revoke drops the rendered copies');
  const html = read('index.html');
  assert.doesNotMatch(html, /vendor\/(hljs|katex|mermaid)/, 'nothing new on first paint');
  assert.doesNotMatch(read('sw.js'), /vendor/, 'the service worker caches nothing (and no vendor file)');
  const md = read('js/md.js');
  assert.doesNotMatch(md, /innerHTML|DOMParser|insertAdjacentHTML/);
});
