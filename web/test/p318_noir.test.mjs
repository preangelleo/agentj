// P318 (2026-10-15: 「浅色背景黑字从上面滑下来」 — never again). Static half of the Noir Notification: every notice /
// toast / snack / flash popup in the page's CSS is the dialog layer (Noir --dlg-bg, Lotion --dlg-fg, opaque) and the format
// table in js/ui.js uses the state token and existing semantic palette accents. The runtime half: notify() builds the right DOM (tested here with a
// tiny DOM stand-in, no browser).
import { test } from 'node:test';
import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import { fileURLToPath } from 'node:url';

const PUB = fileURLToPath(new URL('../public/', import.meta.url));
const read = (f) => readFileSync(PUB + f, 'utf8');
const CSS_FILES = ['app.css', 'menu.css', 'five-pages.css'];
const NOTICE = /(^|[\s,>+~(])\.?[\w-]*(toast|notice|snack|flash|notif)[\w-]*/i;
const LIGHT_BG = /background(-color)?\s*:\s*[^;}]*(--btn-strong-bg|--lotion|--c-lotion|--pg\b|--bg\b|--surface|#fff\b|#ffffff|white\b|#faf9f5|#b7e396)/i;
const TRANSLUCENT = /background(-color)?\s*:\s*[^;}]*(rgba?\([^)]*[,/]\s*0?\.\d|transparent)/i;

/** [selector, body] for every rule (comments dropped, @media unwrapped one level). */
function rules(css) {
  css = css.replace(/\/\*[\s\S]*?\*\//g, '');
  const out = [];
  const re = /([^{}]+)\{([^{}]*)\}/g;
  let m;
  while ((m = re.exec(css))) out.push([m[1].trim().replace(/^@media[^{]*\{/, '').trim(), m[2]]);
  return out;
}
const all = CSS_FILES.flatMap((f) => rules(read(f)).map(([sel, body]) => ({ f, sel, body })));
// .srflash is a search-hit glow (box-shadow only), not a popup; keyframes are not rules
const notices = all.filter((r) => NOTICE.test(r.sel) && !/^(from|to|\d+%)/.test(r.sel) && !/srflash|sr-flash/.test(r.sel));

test('the toast and the share notice are the Noir dialog layer', () => {
  for (const sel of ['.toast', '.share-notice']) {
    const r = all.find((x) => x.f === 'app.css' && x.sel === sel);
    assert.ok(r, sel + ' rule exists');
    assert.match(r.body, /background:var\(--dlg-bg\)/, sel + ' background = --dlg-bg (Noir)');
    assert.match(r.body, /(^|;)color:var\(--dlg-fg\)/, sel + ' text = --dlg-fg (Lotion)');
  }
});

test('no notice / toast / snack / flash rule paints a light or see-through background', () => {
  assert.ok(notices.length >= 5, 'the notice rules were found (' + notices.length + ')');
  for (const r of notices) {
    assert.doesNotMatch(r.body, LIGHT_BG, `${r.f}: ${r.sel} — a light background is back (use --dlg-bg / notify())`);
    // the popup itself is opaque; a chip / button inside it may be a tint of it
    const container = r.sel.split(',').some((s) => NOTICE.test(s.trim().split(/\s+/).at(-1)));
    if (container) assert.doesNotMatch(r.body, TRANSLUCENT, `${r.f}: ${r.sel} — notifications are opaque`);
    // P459m: only the strong rescue action has dark button ink; notice prose stays Lotion.
    const prose = r.sel.split(',').map(s => s.trim()).filter(s => s !== '.reply-notices .rsbanner__go');
    if (prose.length) assert.doesNotMatch(r.body, /color\s*:\s*var\(--(btn-strong-ink|ink|sooty|c-sooty)\)/, `${r.f}: ${prose.join(',')} — dark ink on a notice`);
  }
});

test('reply rescue action keeps strong background and matching strong ink', () => {
  const action = all.find(r => r.f === 'app.css' && r.sel === '.rsbanner__go');
  const replyAction = all.find(r => r.f === 'app.css' && r.sel === '.reply-notices .rsbanner__go');
  assert.ok(action && replyAction, 'base and reply-card rescue action rules exist');
  assert.match(action.body, /background:var\(--btn-strong-bg\)/);
  assert.match(action.body, /(?:^|;)color:var\(--btn-strong-ink\)/);
  assert.match(replyAction.body, /(?:^|;)color:var\(--btn-strong-ink\)/);
  const noticeControls = all.find(r => r.f === 'app.css' && r.sel === '.reply-notices .setup-banner__open,.reply-notices .setup-banner__x');
  assert.ok(noticeControls);
  assert.match(noticeControls.body, /color:var\(--dlg-fg\)/);
});

test('ui.js: notify with six kinds; FORMATS accents use current state or semantic palette tokens', async () => {
  const src = read('js/ui.js');
  assert.match(src, /export function notify\(spec\)/);
  globalThis.window = globalThis.window || {};
  const ui = await import('../public/js/ui.js');
  assert.deepEqual(Object.keys(ui.FORMATS).sort(), ['confirm', 'error', 'info', 'progress', 'success', 'warn']);
  assert.deepEqual([...ui.NOTIFY_ACCENTS].sort(), ['--emerald-oasis', '--magic-carpet', '--peachy-feeling', '--state', '--windstorm']);
  for (const [k, f] of Object.entries(ui.FORMATS)) {
    assert.ok(ui.NOTIFY_ACCENTS.includes(f.accent), k + ' accent from the palette');
    assert.ok(['rect', 'circle'].includes(f.shape), k + ' shape');
  }
  // the accent vars resolve to the palette hexes (leo-palette.json via brand/palette.css + app.css)
  const css = read('app.css') + read('brand/palette.css');
  const hex = { '--emerald-oasis': '#629887', '--windstorm': '#6a9bcc', '--peachy-feeling': '#d97757', '--magic-carpet': '#827dbd' };
  const val = (v) => { const m = css.match(new RegExp(v.replace(/-/g, '\\-') + '\\s*:\\s*([^;]+);')); return m && m[1].trim(); };
  for (const [v, h] of Object.entries(hex)) {
    let x = val(v); for (let i = 0; i < 3 && x && x.startsWith('var('); i++) x = val(x.slice(4, -1).trim());
    assert.equal(x && x.toLowerCase(), h, v + ' = ' + h);
  }
  // the old ad-hoc paths are gone: one component paints the toast
  assert.doesNotMatch(read('js/mail.js') + read('js/elevate.js'), /classList\.add\('mail-saved'\)/);
});

test('notifyFormat: kind → accent / icon / ms / shape', async () => {
  globalThis.window = globalThis.window || {};
  const { notifyFormat, FORMATS } = await import('../public/js/ui.js');
  assert.deepEqual(notifyFormat({ kind: 'success', text: 'ok' }), { kind: 'success', accent: '--emerald-oasis', icon: 'check', shape: 'circle', ms: FORMATS.success.ms });
  assert.equal(notifyFormat({ kind: 'nope' }).kind, 'info', 'an unknown kind is info');
  for (const k of ['success', 'info', 'warn', 'error']) {
    const ms = notifyFormat({ kind: k }).ms; assert.ok(ms >= 1800 && ms <= 3000, k + ' is transient 1.8–3 s (' + ms + ')');
  }
  assert.equal(notifyFormat({ kind: 'progress' }).ms, 0, 'progress holds');
  assert.equal(notifyFormat({ kind: 'confirm' }).ms, 0, 'confirm holds');
  assert.equal(notifyFormat({ kind: 'info', ms: 6000 }).ms, 6000);
  assert.equal(notifyFormat({ kind: 'success', shape: 'circle', text: '已保存' }).shape, 'circle');
  assert.equal(notifyFormat({ kind: 'success', shape: 'circle', text: '已保存到Agent J 主机'.repeat(5) }).shape, 'square', 'long A text → centred square');
  assert.equal(notifyFormat({ kind: 'confirm', shape: 'circle', text: 'x', actions: [{ label: 'a', fn() {} }] }).shape, 'rect', 'buttons → rect');
  assert.equal(notifyFormat({ kind: 'error' }).accent, '--peachy-feeling');
  assert.equal(notifyFormat({ kind: 'progress' }).accent, '--state');
});

// a minimal DOM: enough for notify() (classList, dataset, style, replaceChildren, textContent)
function fakeDom() {
  class N {
    constructor(tag) { this.tagName = tag; this.children = []; this.attrs = {}; this.dataset = {}; this._text = ''; this.listeners = {}; this.hidden = false;
      const cls = new Set(); this.classList = { add: (...a) => a.forEach((c) => cls.add(c)), remove: (...a) => a.forEach((c) => cls.delete(c)),
        contains: (c) => cls.has(c), toggle: (c, on) => { if (on === undefined ? !cls.has(c) : on) cls.add(c); else cls.delete(c); }, get list() { return [...cls]; } };
      Object.defineProperty(this, 'className', { set: (v) => { cls.clear(); String(v).split(/\s+/).filter(Boolean).forEach((c) => cls.add(c)); }, get: () => [...cls].join(' ') });
      const props = {}; this.style = { setProperty: (k, v) => { props[k] = v; }, removeProperty: (k) => { delete props[k]; }, props }; }
    setAttribute(k, v) { this.attrs[k] = v; if (k === 'class') this.className = v; }
    appendChild(c) { this.children.push(c); return c; }
    replaceChildren(...c) { this.children = c; this._text = ''; }
    addEventListener(t, f) { this.listeners[t] = f; }
    getClientRects() { return []; }
    get textContent() { return this._text + this.children.map((c) => c.textContent || '').join(''); }
    set textContent(v) { this._text = String(v); this.children = []; }
  }
  const toastEl = new N('div');
  globalThis.document = { getElementById: (id) => (id === 'toast' ? toastEl : null), querySelector: () => null, querySelectorAll: () => [],
    createElement: (t) => new N(t), createElementNS: (_ns, t) => new N(t), createTextNode: (t) => { const n = new N('#text'); n._text = t; return n; } };
  return toastEl;
}

test('notify(): title + text in #toast, kind / shape classes, action button .undo, held vs timed', async () => {
  globalThis.window = globalThis.window || {};
  const { notify, toast, toastAction, toastOff } = await import('../public/js/ui.js');
  const box = fakeDom();
  try {
    notify({ kind: 'success', title: 'Saved', text: 'to the Agent J host' });
    assert.equal(box.textContent, 'Savedto the Agent J host');
    assert.ok(box.classList.contains('on') && box.classList.contains('rect'));
    assert.equal(box.dataset.kind, 'success');
    assert.equal(box.style.props['--nt-accent'], 'var(--emerald-oasis)');
    assert.ok(!box.dataset.hold, 'success is timed');
    notify({ kind: 'success', shape: 'circle', text: '已保存' });
    assert.ok(box.classList.contains('circle') && !box.classList.contains('rect'));
    let hit = 0; toastAction('Deleted', 'Undo', () => { hit++; });
    assert.ok(box.classList.contains('act'));
    const undo = box.children.at(-1).children[0];
    assert.equal(undo.className, 'undo'); assert.equal(undo.textContent, 'Undo');
    assert.equal(box.textContent, 'DeletedUndo');
    undo.listeners.click(); assert.equal(hit, 1); assert.ok(!box.classList.contains('on'), 'the button dismisses');
    notify({ kind: 'progress', text: 'Uploading…' });
    assert.equal(box.dataset.hold, '1', 'progress holds');
    assert.equal(box.attrs.role, 'status');
    notify({ kind: 'error', text: 'x' }); assert.equal(box.attrs.role, 'alert');
    box.dataset.sharedFollow = '1'; toast('plain'); assert.equal(box.dataset.sharedFollow, undefined);
    assert.equal(box.dataset.kind, 'info'); assert.equal(box.textContent, 'plain');
    toastOff(); assert.ok(!box.classList.contains('on'));
  } finally { clearTimeout(box._t); delete globalThis.document; }
});

// P535 owner continuation: centred notices use the microphone-style plain Noir ground.
test('centred circle/square/saved notices have no border, outline or shadow', () => {
  const r = all.find(x => x.sel === '.toast:is(.circle,.square,.mail-saved)');
  assert.ok(r);
  for (const property of ['border', 'outline']) assert.ok(r.body.includes(property + ':0;'), property);
  assert.ok(r.body.includes('box-shadow:none;'));
});
