// P340 C (owner 10-16: 「logo 和底色相同……在比较 heavy 的颜色上，字体不能用黑色，要用我们指定的白色」). Static half of
// the colour audit (web-unit, no browser); the runtime half is test/p340_contrast.mjs (gate p340-contrast).
//  1. No CSS rule paints a heavy fill (the Dark Cluster / state colours / Black Mana / Noir / Sooty) and writes dark text
//     (Sooty, Noir, black) on it — 深底必白字.
//  2. Text on a heavy fill is the palette's Lotion, not an off-palette #fff / white.
//  3. The action fill and its text are ONE token pair (menu.css --ui-action-bg / --ui-action-fg) and the fg is Lotion.
//  4. The robot mark (brand/img/logo-mark.png, body = the logo green) never stands bare on a green fill: every element whose
//     class paints the logo green and that holds the mark has a `<class> img` rule giving the mark its Lotion disc.
import { test } from 'node:test';
import assert from 'node:assert/strict';
import { readFileSync, readdirSync } from 'node:fs';
import { fileURLToPath } from 'node:url';

const PUB = fileURLToPath(new URL('../public/', import.meta.url));
const read = (f) => readFileSync(PUB + f, 'utf8');
const CSS_FILES = ['app.css', 'menu.css', 'five-pages.css'];
// heavy fills: palette names (exact — `--c-shield` is not `--c-shield-soft`) and their hex
const tok = (names) => names.map((n) => n.startsWith('#') ? n + '(?![0-9a-f])' : n + '(?![\\w-])').join('|');
const HEAVY = tok(['--c-shield', '--accent', '--c-idle', '--emerald-oasis', '--c-emerald-oasis', '--windstorm', '--c-windstorm', '--c-working',
  '--peachy-feeling', '--c-peachy-feeling', '--c-waiting', '--magic-carpet', '--c-magic-carpet', '--c-question', '--black-mana',
  '--c-black-mana', '--c-offline', '--ui-action-bg', '--c-noir', '--noir', '--c-sooty', '--sooty',
  '#629887', '#6a9bcc', '#d97757', '#827dbd', '#858585', '#2c2b26', '#141414']);
const GREEN = tok(['--c-shield', '--accent', '--c-idle', '--emerald-oasis', '--c-emerald-oasis', '--ui-action-bg', '#629887']);
const BG_HEAVY = new RegExp(String.raw`(^|;)\s*background(-color)?\s*:\s*(var\((${HEAVY})[^;]*|(${HEAVY}))\s*(!important)?\s*(;|$)`, 'i');
const BG_GREEN = new RegExp(String.raw`(^|;)\s*background(-color)?\s*:\s*(var\((${GREEN})[^;]*|(${GREEN}))\s*(!important)?\s*(;|$)`, 'i');
const DARK_FG = /(^|;)\s*color\s*:\s*(var\((--c-sooty|--sooty|--c-noir|--noir|--menu-on-accent)(?![\w-])[^;]*|#141414|#2c2b26|#111\b|#111111|#000\b|#000000|black)\s*(!important)?\s*(;|$)/i;
const WHITE_FG = /(^|;)\s*color\s*:\s*(#fff\b|#ffffff|white)\s*(!important)?\s*(;|$)/i;

/** [selector, body] for every rule (comments dropped, @media / @supports unwrapped). */
function rules(css) {
  css = css.replace(/\/\*[\s\S]*?\*\//g, '');
  const out = [];
  const re = /([^{}]+)\{([^{}]*)\}/g;
  let m;
  while ((m = re.exec(css))) out.push([m[1].trim().replace(/^@(media|supports)[^{]*\{/, '').trim(), m[2].trim()]);
  return out.filter(([sel]) => !/^(from|to|\d+%)/.test(sel) && !sel.startsWith('@'));
}
const all = CSS_FILES.flatMap((f) => rules(read(f)).map(([sel, body]) => ({ f, sel, body })));
const where = (r) => `${r.f}: ${r.sel.slice(0, 90)} { ${r.body.slice(0, 120)} }`;

test('the CSS was read', () => assert.ok(all.length > 1500, 'rules: ' + all.length));

test('no rule paints a heavy fill with dark text on it (深底必白字)', () => {
  const bad = all.filter((r) => BG_HEAVY.test(r.body) && DARK_FG.test(r.body));
  assert.deepEqual(bad.map(where), []);
});

test('text on a heavy fill is Lotion, never an off-palette #fff / white', () => {
  const bad = all.filter((r) => BG_HEAVY.test(r.body) && WHITE_FG.test(r.body));
  assert.deepEqual(bad.map(where), []);
});

test('the action fill is one token pair: logo green + Lotion', () => {
  const root = all.find((r) => r.f === 'menu.css' && r.sel === ':root' && /--ui-action-bg/.test(r.body));
  assert.ok(root, 'menu.css :root defines --ui-action-bg');
  assert.match(root.body, /--ui-action-bg:var\(--c-shield\)/);
  assert.match(root.body, /--ui-action-fg:var\(--c-lotion\)/);
  assert.match(root.body, /--ui-mark-disc:var\(--c-lotion\)/);
  const assist = all.find((r) => r.sel === '.setup-assist');
  assert.ok(assist, '.setup-assist rule');
  assert.match(assist.body, /background:var\(--ui-action-bg\)/);
  assert.match(assist.body, /(^|;)color:var\(--ui-action-fg\)/);
});

test('the robot mark never stands bare on the logo green', () => {
  const html = read('index.html');
  const js = readdirSync(PUB + 'js').filter((f) => f.endsWith('.js')).map((f) => read('js/' + f)).join('\n') + read('app.js');
  const greenClasses = new Set();
  for (const r of all) if (BG_GREEN.test(r.body)) for (const part of r.sel.split(',')) {
    const last = part.trim().split(/[\s>+~]+/).pop() || '';
    for (const m of last.matchAll(/\.([\w-]+)/g)) greenClasses.add(m[1]);
  }
  assert.ok(greenClasses.has('setup-assist'), 'the green action class is found: ' + [...greenClasses].join(' '));
  const bad = [];
  for (const cls of greenClasses) {
    const holds = new RegExp(String.raw`class="[^"]*\b${cls}\b[^"]*"[^>]*>\s*<img[^>]*logo-mark`).test(html)
      || new RegExp(String.raw`['"\s.]${cls}\b[\s\S]{0,240}logo-mark`).test(js);
    if (!holds) continue;
    const disc = all.some((r) => r.sel.split(',').some((s) => new RegExp(String.raw`\.${cls}\b[^,]*\bimg\b`).test(s))
      && /(^|;)\s*background(-color)?\s*:\s*var\(--ui-mark-disc\)/.test(r.body) && /border-radius:50%/.test(r.body));
    if (!disc) bad.push(cls);
  }
  assert.deepEqual(bad, [], 'classes that put the green robot on a green fill without its Lotion disc');
});
