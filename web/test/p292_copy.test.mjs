// P292 (Leo 10-15: 「但凡是这个地方都应该有 copy 按钮，或者点击就能 copy……在弹窗里边的这些结构，我们迷失掉了，我们忘掉了」):
// the static half of "never again". Every place in the shipped page that MAKES a <code> or <pre> — createElement('code'|'pre'),
// mk('code'|'pre', …), an HTML literal `<code` / `<pre` in a script, a <code> / <pre> tag in index.html — must hand it to the
// copy component (js/copycode.js: `copyable(` within the statement or the next three lines, or a `data-cc` mark that the
// component's delegated click reads). Something that truly must not be copyable says why on the line above or the same line:
// `copy-exempt: <reason>`; at most EXEMPT_MAX of those in the whole page. md.js makes its elements from a tag variable (no
// literal to find), so its two marks are checked by name. The runtime half (every visible code / pre on the real screens is
// wired, and one real click lands on the clipboard) is web/test/p292.mjs.
import { test } from 'node:test';
import assert from 'node:assert/strict';
import { readFileSync, readdirSync, statSync } from 'node:fs';
import { join, relative } from 'node:path';
import { fileURLToPath } from 'node:url';

const PUB = fileURLToPath(new URL('../public/', import.meta.url));
const BUILT = /^(brand|proto|vendor)\//;                                // build copies (never edited here)
const OUTPUT = new Set(['i18n.js', 'version.js', 'js/interactions.gen.js']);   // generated
export const EXEMPT_MAX = 3;

const CREATE = [
  /createElement(?:NS)?\(\s*(?:[^,()]*,\s*)?['"`](code|pre)['"`]\s*\)/,
  /\bmk\(\s*['"`](code|pre)['"`]/,
  /<(code|pre)[\s>/]/,
];
const WIRED = /copyable\(|data-cc\b|\[ATTR\]|setAttribute\(\s*ATTR/;
const EXEMPT = /copy-exempt:\s*\S/;

/** Drop comments but keep the line numbers (block comments → their newlines; `//` not after a `:` as in https://). */
function stripJs(src) {
  return src.replace(/\/\*[\s\S]*?\*\//g, (m) => m.replace(/[^\n]/g, ' '))
    .split('\n').map((l) => l.replace(/(^|[^:\\'"`])\/\/.*$/, '$1')).join('\n');
}

/** → { bad: [{ line, text }], exempt: [{ line, text }] } for one script. */
export function scanJs(src) {
  const code = stripJs(src).split('\n'), raw = src.split('\n');
  const bad = [], exempt = [];
  for (let i = 0; i < code.length; i++) {
    if (!CREATE.some((re) => re.test(code[i]))) continue;
    const near = raw.slice(i, i + 4).join('\n');
    if (EXEMPT.test(raw[i]) || (i > 0 && EXEMPT.test(raw[i - 1]))) { exempt.push({ line: i + 1, text: raw[i].trim().slice(0, 160) }); continue; }
    if (!WIRED.test(near)) bad.push({ line: i + 1, text: raw[i].trim().slice(0, 160) });
  }
  return { bad, exempt };
}

/** → { bad, exempt } for markup: every <code> / <pre> tag carries data-cc (copycode.upgrade / the delegated click wire it). */
export function scanHtml(src) {
  const bad = [], exempt = [];
  const lines = src.split('\n');
  const body = src.replace(/<!--(?![^>]*copy-exempt)[\s\S]*?-->/g, (m) => m.replace(/[^\n]/g, ' '));
  for (const m of body.matchAll(/<(code|pre)\b[^>]*>/g)) {
    const line = body.slice(0, m.index).split('\n').length;
    if (/\sdata-cc=/.test(m[0])) continue;
    if (EXEMPT.test(lines[line - 1]) || (line > 1 && EXEMPT.test(lines[line - 2]))) { exempt.push({ line, text: m[0] }); continue; }
    bad.push({ line, text: m[0].slice(0, 160) });
  }
  return { bad, exempt };
}

function shipped() {
  const out = [];
  const walk = (d) => {
    for (const n of readdirSync(d)) {
      const f = join(d, n), r = relative(PUB, f).split('\\').join('/');
      if (BUILT.test(r + '/') || BUILT.test(r)) continue;
      if (statSync(f).isDirectory()) walk(f);
      else if ((/\.js$/.test(r) && !OUTPUT.has(r)) || r === 'index.html') out.push(r);
    }
  };
  walk(PUB);
  return out;
}

test('P292: every code / pre the page makes goes through the copy component (or says copy-exempt: why)', () => {
  const files = shipped();
  assert.ok(files.includes('index.html') && files.includes('js/t.js') && files.includes('js/copycode.js') && files.length > 40, 'the scan sees the page');
  const bad = [], exempt = [];
  for (const r of files) {
    const s = readFileSync(join(PUB, r), 'utf8');
    const res = r.endsWith('.html') ? scanHtml(s) : scanJs(s);
    for (const b of res.bad) bad.push(`${r}:${b.line}  ${b.text}`);
    for (const e of res.exempt) exempt.push(`${r}:${e.line}  ${e.text}`);
  }
  assert.deepEqual(bad, [], 'code / pre made without copycode.js — wrap it in copyable(…) or mark it data-cc (or `copy-exempt: <why>`):\n' + bad.join('\n'));
  assert.ok(exempt.length <= EXEMPT_MAX, `at most ${EXEMPT_MAX} copy-exempt, found ${exempt.length}:\n${exempt.join('\n')}`);
});

test('P292: md.js (elements from a tag variable) marks the reply\'s inline code and the code block\'s code', () => {
  const md = readFileSync(join(PUB, 'js/md.js'), 'utf8');
  assert.match(md, /if \(tag === "code"\) \{[^\n]*setAttribute\("data-cc", "bare"\)/, 'inline code → data-cc="bare" (P154 one click)');
  assert.match(md, /const code = doc\.createElement\("code"\);\s*\n\s*code\.setAttribute\("data-cc", "ext"\)/, 'code block → data-cc="ext" (presses 拷贝)');
  const t = readFileSync(join(PUB, 'js/t.js'), 'utf8');
  assert.match(t, /return copyable\(c,/, 'dictionary `code` (fillText) → copyable');
  const ui = readFileSync(join(PUB, 'js/ui.js'), 'utf8');
  assert.match(ui, /fillText\(el\('confirm-text'\), text\)/, 'the confirm dialog renders `code` (agentj pair) through fillText');
  assert.match(ui, /if \(cmd\) \{ fillText\(n, t\)/, 'a toast naming a `command` renders it through fillText');
});

test('P292: positive control — the checker goes red on an unwired code / pre', () => {
  const js = [
    "const c = document.createElement('code'); c.textContent = cmd; box.append(c);",
    "box.append(mk('pre', 'raw', text));",
    "p.append(mk(\"code\", '', id));",
    "const html = '<code>' + cmd + '</code>';",
    "const e = doc.createElementNS(NS, 'pre');",
  ];
  for (const line of js) assert.equal(scanJs(line).bad.length, 1, 'red: ' + line);
  assert.equal(scanJs("box.append(copyable(mk('pre', 'raw', text)));").bad.length, 0, 'green: wrapped');
  assert.equal(scanJs("const c = document.createElement('code');\nc.textContent = x;\nbox.append(copyable(c));").bad.length, 0, 'green: wrapped two lines below');
  assert.equal(scanJs("const c = document.createElement('code');\na();\nb();\nd();\nbox.append(copyable(c));").bad.length, 1, 'red: too far from its copyable');
  assert.equal(scanJs("// a <code> in a comment\n/* mk('pre') */").bad.length, 0, 'comments are not code');
  const ex = scanJs("// copy-exempt: a decorative glyph\nconst c = document.createElement('code');");
  assert.equal(ex.bad.length, 0); assert.equal(ex.exempt.length, 1, 'copy-exempt is counted');
  assert.equal(scanHtml('<p>run <code>agentj pair</code></p>').bad.length, 1, 'red: html code without data-cc');
  assert.equal(scanHtml('<pre id="x"></pre>').bad.length, 1, 'red: html pre without data-cc');
  assert.equal(scanHtml('<p>run <code data-cc="inline">agentj pair</code></p>').bad.length, 0, 'green: html data-cc');
  assert.equal(scanHtml('<!-- a <code> in a comment -->').bad.length, 0, 'html comments are not markup');
});
