// P569 (#370, chairman 10-28): one left-bar design everywhere — the card's own border-left, curving through the card's
// top-left and bottom-left corners (border-radius), in the bar's old colour. No square strips: no inset x-only box-shadow,
// no thin absolutely-positioned ::before / ::after strip on the left, no 3 px linear-gradient stripe, no bar on a square box.
// Static half; the measured half (Chromium + WebKit, zh / en, 390 light / dark, 1440) is test/p569_bars.mjs.
// brand/base.css is the shared brand sheet (also shipped by the site and the host admin); the phone app uses none of its
// barred classes (.aj-callout, .aj-nav), so it is not scanned here.
import test from 'node:test';
import assert from 'node:assert/strict';
import { readFileSync, existsSync } from 'node:fs';

const read = (rel) => readFileSync(new URL(rel, import.meta.url), 'utf8').replace(/\/\*[\s\S]*?\*\//g, '');
const FILES = {
  'app.css': '../public/app.css', 'menu.css': '../public/menu.css', 'cards.css': '../public/cards.css',
  'five-pages.css': '../public/five-pages.css', 'controls.css': '../public/controls.css', 'terminal.css': '../public/terminal.css',
  'dashboard/app.css': '../../dashboard/public/app.css',
};
// The account Dashboard is private (not in the public export): its sheet is checked wherever it exists (the private tree's
// web-unit); the exported tree checks the phone sheets only.
if (!existsSync(new URL(FILES['dashboard/app.css'], import.meta.url))) delete FILES['dashboard/app.css'];
const CSS = Object.fromEntries(Object.entries(FILES).map(([k, v]) => [k, read(v)]));

// every innermost rule: { sel, body } (selectors inside @media come out bare, which is what we want)
function rules(css) {
  const out = [];
  for (const m of css.matchAll(/([^{}]+)\{([^{}]*)\}/g)) out.push({ sel: m[1].trim().replace(/\s+/g, ' '), body: m[2] });
  return out;
}
const R = Object.fromEntries(Object.entries(CSS).map(([k, v]) => [k, rules(v)]));
const decls = (body) => Object.fromEntries(body.split(';').map((d) => d.split(/:(.*)/s)).filter((p) => p.length > 1)
  .map(([k, v]) => [k.trim(), v.trim()]));
// all declarations of the rules whose selector list contains `sel` exactly (later rules win)
function props(file, sel) {
  const want = sel.replace(/\s+/g, ' ');
  const hit = R[file].filter((r) => r.sel.split(/,(?![^(]*\))/).map((s) => s.trim()).includes(want));
  assert.ok(hit.length, `${file}: no rule for ${sel}`);
  return Object.assign({}, ...hit.map((r) => decls(r.body)));
}
const px = (v) => /^0(px)?$/.test(v) ? 0 : 1;   // non-zero length (tokens / calc count as non-zero)
function leftRadii(v) {
  const p = v.split('/')[0].trim().split(/\s+(?![^(]*\))/);
  return [p[0], p[3] ?? p[1] ?? p[0]];
}

// the inventory: surface → the rule that draws the bar, and the rule(s) that give its radius
const BARS = [
  ['app.css', '.toast', null, 'notification card (rect notice)'],
  ['app.css', '#toast.rect', '.toast', 'reply-bottom notice: no stripe of its own'],
  ['app.css', '.share-notice', null, 'share hand-off notice'],
  ['app.css', '.elev-grant', null, '10-minute approval bar'],
  ['app.css', '.elev-run', null, 'admin command run card'],
  ['app.css', '.omq', null, 'source card quote block'],
  ['app.css', '.qbar', null, 'composer quote bar'],
  ['app.css', '.qc', null, 'composer quote chip'],
  ['app.css', '.words blockquote', null, 'reply markdown quote'],
  ['app.css', '.setup-act', null, 'first-run setup action note'],
  ['app.css', '.sheet.danger .cmd', '.cmd', 'danger approval command'],
  ['app.css', '.slashmenu.sug button[aria-selected="true"]', null, 'type-ahead selected row'],
  ['app.css', '.fr-gmentions .fp-mention[aria-selected=true]', '.fr-gmentions .fp-mention', 'group mention selected row'],
  ['app.css', '.setside button[aria-current="true"]', '.setside button', 'settings side nav current'],
  ['app.css', '.ct-field.fresh input', '.ct-dlg input', 'contact field just filled'],
  ['five-pages.css', '.fp-prose blockquote', null, 'five-pages prose quote'],
  ['five-pages.css', '.fp-mention[aria-selected=true]', null, 'five-pages mention selected row'],
  ['cards.css', '.jc-item[data-mark]', null, 'marked to-do row'],
  ['terminal.css', '.term-origin', null, 'terminal sheet origin'],
  ['terminal.css', '.term-sheet .term-warn', null, 'terminal sheet warning'],
  ['menu.css', '#settings .rsmode', null, 'settings rescue mode note'],
  ['dashboard/app.css', '.msg', null, 'dashboard status toast'],
  ['dashboard/app.css', '.warn', null, 'dashboard callout'],
  ['dashboard/app.css', '.sentence', null, 'dashboard sentence callout'],
  ['dashboard/app.css', '.plaza-reply.admin', null, 'dashboard plaza admin reply'],
  ['dashboard/app.css', '.plaza-reply.pinned', null, 'dashboard plaza pinned reply'],
  ['dashboard/app.css', '.side-link[aria-current="true"]', '.side-link', 'dashboard side nav current'],
  ['dashboard/app.css', '.approve-note', null, 'dashboard approve note'],
];

test('P569: no square left-bar technique in any phone / dashboard sheet', () => {
  for (const [file, rs] of Object.entries(R)) {
    for (const { sel, body } of rs) {
      const d = decls(body);
      // an inset shadow offset only along x is a painted stripe on the left
      for (const sh of (d['box-shadow'] || '').split(/,(?![^(]*\))/)) {
        assert.ok(!/\binset\s+-?[\d.]+px\s+0(px)?\s+0(px)?\b/.test(sh.trim()), `${file} ${sel}: inset x-only box-shadow «${sh.trim()}»`);
      }
      // a hard-stop gradient stripe on the left edge
      const bg = (d['background-image'] || '') + (d.background || '');
      assert.ok(!/linear-gradient\(\s*(90deg|to right)\s*,[^,]+\s[\d.]+px\s*,\s*transparent\s+[\d.]+px/.test(bg), `${file} ${sel}: gradient stripe`);
      // a thin, absolutely placed strip at the left edge drawn by a pseudo element
      if (/::?(before|after)/.test(sel) && /position:\s*absolute/.test(body) && 'left' in d && !('right' in d)
        && /^[1-6]px$/.test(d.width || '') && (('top' in d && 'bottom' in d) || parseFloat(d.height) >= 12) && !/border/.test(body)) {
        assert.fail(`${file} ${sel}: pseudo-element left strip`);
      }
    }
  }
});

test('P569: no pseudo-element bar left behind on the old strip owners', () => {
  for (const sel of ['.omq::before', '.qbar::before', '.qbar[data-n]::before']) {
    assert.ok(!R['app.css'].some((r) => r.sel.split(',').map((s) => s.trim()).includes(sel)), sel);
  }
  if (R['dashboard/app.css']) assert.ok(!R['dashboard/app.css'].some((r) => /\.msg(\[[^\]]*\])?::before/.test(r.sel)), 'dashboard .msg::before');
});

test('P569: every inventoried bar is border-left on a box with rounded left corners', () => {
  for (const [file, sel, radiusSel, what] of BARS) {
    if (!(file in R)) continue;   // private Dashboard sheet absent (public export)
    const p = props(file, sel);
    const own = props(file, radiusSel || sel);
    if (sel === '#toast.rect') {          // P458's rule keeps only "no cast shadow"; the bar is .toast's
      assert.ok(!('background-image' in p), what);
      continue;
    }
    const bl = p['border-left'] || '';
    const width = /^([\d.]+)px\b/.exec(bl)?.[1] || /^([\d.]+)px$/.exec(p['border-left-width'] || '')?.[1];
    assert.ok(width && +width > 0, `${what} (${sel}): border-left width, got «${bl}»`);
    assert.ok(bl ? /solid/.test(bl) : true, `${what}: solid`);
    const rad = p['border-radius'] || own['border-radius'];
    assert.ok(rad, `${what} (${sel}): border-radius`);
    for (const r of leftRadii(rad)) assert.equal(px(r), 1, `${what} (${sel}): left corner radius «${rad}»`);
  }
});

test('P569: the bars keep their colours', () => {
  const c = (file, sel) => { const p = props(file, sel); return p['border-left-color'] || (p['border-left'] || '').replace(/^[\d.]+px\s+solid\s+/, ''); };
  const want = [
    ['app.css', '.toast', 'var(--nt-accent,var(--state))'],
    ['app.css', '.share-notice', 'var(--state)'],
    ['app.css', '.elev-grant', 'var(--state)'],
    ['app.css', '.elev-run', 'var(--state)'],
    ['app.css', '.elev-run[data-state="ask"]', 'var(--c-peachy-feeling)'],
    ['app.css', '.elev-run[data-state="done"][data-ok="1"]', 'var(--emerald-oasis)'],
    ['app.css', '.elev-run[data-state="done"][data-ok="0"]', 'var(--c-danger,#c0392b)'],
    ['app.css', '.omq', 'rgba(20,20,20,.55)'],
    ['app.css', '.qbar', 'var(--qc,var(--src-leo))'],
    ['app.css', '.qc', 'var(--qc,var(--src-leo))'],
    ['app.css', '.words blockquote', 'var(--md-mark)'],
    ['app.css', '.setup-act', 'var(--state)'],
    ['app.css', '.sheet.danger .cmd', 'var(--lotion)'],
    ['app.css', '.slashmenu.sug button[aria-selected="true"]', 'var(--ink)'],
    ['app.css', '.fr-gmentions .fp-mention[aria-selected=true]', 'var(--text)'],
    ['app.css', '.setside button[aria-current="true"]', 'var(--accent)'],
    ['menu.css', '#settings .setside button[aria-current="true"]', 'var(--menu-accent)'],
    ['menu.css', '#sug.slashmenu button[aria-selected="true"]', 'var(--menu-accent)'],
    ['five-pages.css', '.fp-prose blockquote', 'var(--menu-accent)'],
    ['five-pages.css', '.fp-mention[aria-selected=true]', 'var(--menu-accent)'],
    ['cards.css', '.jc-item[data-mark]', 'var(--c-peachy-feeling,#d97757)'],
    ['dashboard/app.css', '.msg', 'var(--accent)'],
    ['dashboard/app.css', '.warn', 'var(--callout-bar)'],
    ['dashboard/app.css', '.sentence', 'var(--callout-bar)'],
    ['dashboard/app.css', '.plaza-reply.pinned', 'var(--accent)'],
    ['dashboard/app.css', '.side-link[aria-current="true"]', 'var(--accent)'],
  ];
  for (const [file, sel, colour] of want) if (file in R) assert.equal(c(file, sel).replace(/\s+/g, ''), colour, `${file} ${sel}`);
  // the field keeps its idle colour on the whole border, now 4.5 px on the left (1.5 px border + the old 3 px stripe)
  const f = props('app.css', '.ct-field.fresh input');
  assert.equal(f['border-color'], 'var(--c-idle)'); assert.equal(f['border-left-width'], '4.5px');
});

test('P569: notice stacks keep the left border (they reset only top / right / bottom)', () => {
  const p = props('app.css', '.reply-notices>:is(.toast,.elev-run,.elev-grant,.setup-banner,.estop-banner,.share-notice,.shared-status,.ver-later,.grant-bar)');
  assert.ok(!('border' in p) && !('border-left' in p), 'no full border reset');
  assert.equal(p['border-block'], '0'); assert.equal(p['border-right'], '0');
  const fold = props('app.css', '.omqfold');
  assert.ok(!('border' in fold) && !('border-left' in fold), '.omqfold keeps .omq\'s bar');
  // the round ✓ notice has no bar
  assert.equal(props('app.css', '.toast:is(.circle,.square,.mail-saved)').border, '0');
});
