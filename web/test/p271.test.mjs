import test from 'node:test';
import assert from 'node:assert/strict';
import { readFileSync, existsSync, readdirSync } from 'node:fs';
const read = p => readFileSync(new URL(p, import.meta.url), 'utf8');
test('P271: both phone pages paint a non-interactive bottom safe area and reserve composer space', () => {
  const pages = [['../public/index.html', '../public/app.css', '--pg'], ['../../../pwa/index.html', '../../../pwa/index.html', '--bg']]
    .filter(([html]) => existsSync(new URL(html, import.meta.url)));   // the older relay PWA is private (not in the public export)
  for (const [html, css, colour] of pages) {
    const h = read(html), c = read(css);
    assert.match(h, /viewport-fit=cover/);
    assert.match(h, /id="sabband" aria-hidden="true"/);
    assert.match(c, /\.sabband\{[^}]*bottom:0;[^}]*height:env\(safe-area-inset-bottom,0px\);[^}]*pointer-events:none;[^}]*background:var\(--(?:pg|bg)\)/);
    assert.match(c, /\.composer\{[^}]*padding:[^;]*env\(safe-area-inset-bottom\)/);
    assert.match(c, /body\[data-kb="1"\] \.sabband\{display:none\}/);
    assert.ok(c.match(/\.sabband\{[^}]+/)[0].includes(`background:var(${colour})`), `${html}: use this page's actual colour variable`);
  }
});
test('P271: both Android shells expose their existing native background through the system navigation bar', () => {
  // the Agent J shell, plus the older private relay shell when this tree has it (found by walking, not by its package path)
  const legacy = new URL('../../../android/app/src/main/kotlin/', import.meta.url);
  const walkKt = (u) => existsSync(u) ? readdirSync(u, { withFileTypes: true }).flatMap((e) => e.isDirectory() ? walkKt(new URL(e.name + '/', u)) : e.name === 'MainActivity.kt' ? [u.href + e.name] : []) : [];
  const shells = ['../../android/app/src/main/kotlin/app/agentj/android/MainActivity.kt', ...walkKt(legacy).map((h) => new URL(h).pathname)];
  for (const p of shells) {
    const s = read(p);
    assert.match(s, /window\.setDecorFitsSystemWindows\(false\)/);
    assert.match(s, /window\.navigationBarColor = Color\.TRANSPARENT/);
    assert.match(s, /window\.isNavigationBarContrastEnforced = false/);
    assert.match(s, /v\.setPadding\(bars.left, bars.top, bars.right, bottom\)/);
  }
});
