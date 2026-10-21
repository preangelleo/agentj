#!/usr/bin/env node
// P412 resume (#176 / #177): the cards' own keys (Alt+Shift + a letter of the English title, digits when the letters are
// taken) and the continuous colour picker (saturation / value square, hue bar, the palette as 「推荐」, Sooty words on a light
// fill, a Save that is either live or grey with its reason); the Agent J card without its edge. The real web client against
// fakehost.mjs in its own headless Chromium (never :9222): 1440 × 900 mouse (zh / en), 390 × 844 touch.
//   → reports/qa/p412/cards/<shot>.png
import assert from 'node:assert/strict';
import { mkdirSync, writeFileSync } from 'node:fs';
import { fileURLToPath } from 'node:url';
import { startWebServer } from './serve.mjs';
import { parityRecorder } from '../../parity/lib.mjs';
import { startFakeHost } from './fakehost.mjs';
import { launch, newPage, evaluate, navigate, waitFor, waitState, shoot, sleep, key, rect, swipe } from './browser.mjs';

const OUT = fileURLToPath(new URL('../../../reports/qa/p412/cards/', import.meta.url));
mkdirSync(OUT, { recursive: true });
const web = await startWebServer({ port: 0, relayCsp: 'ws://127.0.0.1:*' });
const fake = await startFakeHost();
const B = await launch();
const ev = (p, s) => evaluate(p, s), q = JSON.stringify;
let checks = 0;
// P417: features.json names these checks by their static words — record the label as written, plus the label without its
// measured tail 「 (…)」 and without a leading issue tag 「#177 ②: 」 (fail sticks; aliases like the node-test path/name pair).
const rec = parityRecorder('web/test/p412_cards.mjs');
const caseNames = (label) => { const a = label.replace(/^#\d+[^:]*:\s*/, ''), cut = (s) => s.endsWith(')') && s.includes(' (') ? s.slice(0, s.indexOf(' (')) : s;
  return [...new Set([label, a, cut(label), cut(a)])]; };
const check = (v, label) => { for (const n of caseNames(label)) rec.record(n, !!v); assert.ok(v, label); checks++; };
const snap = async (p, n) => writeFileSync(`${OUT}/${n}.png`, await shoot(p, ''));
const CARDS = () => [
  { id: 'notes', name: { zh: '记事本', en: 'Notebook' }, desc: { zh: '常用地址、电话和备忘', en: 'Addresses, phone numbers and memos' }, icon: 'note', accent: 'emerald-oasis', on: true, official: true, plus: 'agentj-add-note' },
  { id: 'automation', name: { zh: '自动化', en: 'Automation' }, desc: { zh: '后台运行的自动化', en: 'Scheduled background work' }, icon: 'calendar', accent: 'magic-carpet', on: true, official: true, plus: 'agentj-automation' },
  { id: 'todo', name: { zh: '待办', en: 'To-do' }, desc: { zh: '要做的事', en: 'Things to do' }, icon: 'checklist', accent: 'windstorm', on: true, official: true, plus: 'agentj-add-todo' },
];
// Alt+Shift+<code> as a Mac sends it: keyDown WITH the ⌥⇧ symbol as text — typed into the box unless the page prevents it
const altShift = async (p, code, text = 'ˇ') => {
  const k = code.replace(/^Key|^Digit/, '');
  const base = { key: text, code, modifiers: 1 | 8, windowsVirtualKeyCode: k.charCodeAt(0) };
  await p.send('Input.dispatchKeyEvent', { type: 'keyDown', ...base, text });
  await p.send('Input.dispatchKeyEvent', { type: 'keyUp', ...base });
  await sleep(250);
};
const page = (p) => ev(p, `({hidden: document.getElementById('five-page').hidden, view: document.body.dataset.fivePage, card: document.querySelector('.jc-body')?.dataset.card || ''})`);

async function paired(w, h, { touch = false, lang = 'zh' } = {}) {
  fake.reset();
  fake.st.caps = ['jcards1'];
  fake.st.prefs = { appearance: { language: lang, theme: 'dark' }, voice: { speak_replies: false, wake_enabled: false }, agent: { session_mode: 'independent' } };
  const cards = CARDS(), colors = {}, ops = [];
  fake.st.onApp = async (c, m, send) => {
    const reply = async (t, v) => { await send({ t, r: m.r, ...v }); return true; };
    if (m.t === 'jcard_list') return reply('jcard_items', { cards, colors });
    if (m.t === 'jcard_get') return reply('jcard_get', { ok: true, id: m.id, template: 'notes', entries: [{ id: 'a', text: m.id, editable: false }], text: '# ' + m.id, anchors: [], revision: 'r', actions: [] });
    if (m.t === 'jcard_color') { ops.push(m); if (m.color === null) delete colors[m.id]; else colors[m.id] = m.color; return reply('jcard_color', { ok: true, id: m.id }); }
    return false;
  };
  const p = await newPage(B, w, h, 'dark', { touch, allow: [web.url, fake.relay + '/'] });
  await navigate(p, fake.newPairing(web.url + '?lang=' + lang));
  await waitState(p, 'awaiting-approval'); await fake.approve(); await waitState(p, 'ready');
  await fake.send({ t: 'status', s: 'idle', agent: 'claude', name: 'Agent J' });
  await ev(p, `document.getElementById('phone-env-dismiss')?.click()`);
  await ev(p, `import('./js/jcards.js').then(m => m.refresh())`); await sleep(300);
  p.ops = ops;
  return p;
}
const deck = async (p) => { await ev(p, `import('./js/five-pages.js').then(m => { if (document.getElementById('five-menu').hidden) m.toggleMenu(); })`); await waitFor(p, `!document.getElementById('five-menu').hidden`, 3000); await sleep(200); };

try {
  for (const lang of ['zh', 'en']) {
    const p = await paired(1440, 900, { lang });
    // ---------------------------------------------------------------- #176: who gets which key
    const keys = await ev(p, `import('./js/five-pages.js').then(m => Object.fromEntries(m.cardKeys().map(c => [c.key, c.letter])))`);
    check(keys['card:notes'] === 'N' && keys['card:todo'] === 'T' && keys['card:automation'] === 'I' && keys.skills === 'S', `letters from the English titles (${q(keys)})`);
    check(keys.workflows === '1' && keys.memory === '2' && keys.agentj === '3' && !keys.chat, 'titles whose letters are all taken get digits; 对话 has none (Esc)');
    const taken = await ev(p, `import('./js/cardkeys.js').then(m => [...m.taken()].join(''))`);
    check(Object.values(keys).every((k) => !taken.includes(k)) && taken.includes('A') && taken.includes('M'), `never one of the app's own Alt+Shift keys (${taken})`);
    // the deck shows them: underlined letter in an English title, a chip otherwise
    await deck(p);
    const hint = await ev(p, `({u: document.querySelector('[data-card=notes].fp-menu-card h2 u.fp-key')?.textContent || '', chip: document.querySelector('[data-card=notes].fp-menu-card h2').dataset.key || '', name: document.querySelector('[data-card=notes].fp-menu-card h2').textContent,
      digit: document.querySelector('[data-page=workflows] h2').dataset.key || '', aria: document.querySelector('[data-card=todo].fp-menu-card').getAttribute('aria-keyshortcuts'),
      edge: getComputedStyle(document.querySelector('.fp-dark-4')).boxShadow, fill: getComputedStyle(document.querySelector('.fp-dark-4')).backgroundColor})`);
    check(await ev(p, `getComputedStyle(document.querySelector('[data-page=workflows] h2'), '::after').display !== 'none'`), 'a mouse screen shows the key chips');
    if (lang === 'en') check(hint.u === 'N' && !hint.chip, `English: the N of Notebook is underlined (${q(hint)})`);
    else check(!hint.u && hint.chip === 'Alt+Shift+N', `Chinese title: a key chip (${q(hint)})`);
    check(hint.digit === 'Alt+Shift+1' && hint.aria === 'Alt+Shift+T', 'digit chip; aria-keyshortcuts');
    check(hint.name === (lang === 'zh' ? '记事本' : 'Notebook'), 'the title\'s text stays the name (the chip is drawn by CSS)');
    check(hint.edge === 'none' && hint.fill === 'rgb(44, 43, 38)', `#177: the Agent J card has no edge and keeps its Noir (${hint.edge} / ${hint.fill})`);
    await snap(p, `deck-keys-${lang}`);
    await key(p, 'Escape'); await sleep(200);
    // open from the chat screen, again = back to the chat; from inside the message box, nothing typed; Esc closes
    await altShift(p, 'KeyN');
    let r = await page(p);
    check(!r.hidden && r.view === 'card' && r.card === 'notes', `Alt+Shift+N opens Notebook (${q(r)})`);
    await snap(p, `card-open-${lang}`);
    await altShift(p, 'KeyN');
    check((await page(p)).hidden, 'the same key again goes back to the chat');
    await ev(p, `(() => { const i = document.getElementById('input'); i.focus(); i.value = '草稿'; i.dispatchEvent(new Event('input')); })()`);
    await altShift(p, 'KeyT', 'ˇ');
    r = await page(p);
    check(!r.hidden && r.card === 'todo' && await ev(p, `document.getElementById('input').value`) === '草稿', `while typing: To-do opens and no ⌥⇧ symbol is typed (${q(r)})`);
    await altShift(p, 'KeyI');
    check((await page(p)).card === 'automation', 'from one card straight to another');
    await key(p, 'Escape'); await sleep(250);
    check((await page(p)).hidden, 'Esc closes the card');
    await altShift(p, 'Digit1', '⁄');
    r = await page(p);
    check(!r.hidden && r.view === 'workflows', `Alt+Shift+1 opens Workflows (${q(r)})`);
    await key(p, 'Escape'); await sleep(250);
    // a letter nobody has does nothing (and the app's own keys stay theirs: Alt+Shift+B is free, nothing opens)
    await altShift(p, 'KeyB');
    check((await page(p)).hidden, 'a free letter opens nothing');
    // the shortcut sheet lists the cards' keys as they are now
    await ev(p, `document.getElementById('input').blur()`);
    await altShift(p, 'KeyH'); await waitFor(p, `!document.getElementById('keys').hidden`, 3000);
    r = await ev(p, `(() => { const s = document.querySelector('#keysCols section[data-group=cards]'); return s && {title: s.querySelector('h3').textContent, rows: [...s.querySelectorAll('dt')].map(d => d.dataset.card + '=' + d.textContent), first: s.querySelector('dd').textContent, at: [...document.querySelectorAll('#keysCols section')].indexOf(s)}; })()`);
    check(r && r.rows.includes('card:notes=Alt+Shift+N') && r.rows.includes('workflows=Alt+Shift+1') && r.rows.length === 7 && r.at === 1, `the shortcut sheet's Cards section (${q(r)})`);
    check(r.first.includes(lang === 'zh' ? '记事本' : 'Notebook'), 'each row names its card');
    await ev(p, `document.querySelector('#keysCols section[data-group=cards]').scrollIntoView()`);
    await snap(p, `keys-sheet-cards-${lang}`);
    await key(p, 'Escape'); await sleep(200);
    // ---------------------------------------------------------------- #177: the continuous picker
    await deck(p);
    const pt = await ev(p, `(() => { const r = document.querySelector('[data-card=notes].fp-menu-card').getBoundingClientRect(); return {x: r.right - 24, y: r.top + 24}; })()`);
    await p.send('Input.dispatchMouseEvent', { type: 'mousePressed', x: pt.x, y: pt.y, button: 'left', buttons: 1, clickCount: 1 });
    await waitFor(p, `document.querySelector('.jc-color-dialog[open]')`, 3000);
    await p.send('Input.dispatchMouseEvent', { type: 'mouseReleased', x: pt.x, y: pt.y, button: 'left', clickCount: 1 });
    await sleep(200);
    const S = `document.querySelector('.jc-color-dialog')`;
    const state = () => ev(p, `(() => { const d = ${S}, s = d.querySelector('.jc-dialog-actions .fp-action'); return {hex: d.querySelector('input[aria-label=HEX]').value, rgb: ['R','G','B'].map(k => d.querySelector('input[aria-label=' + k + ']').value).join(','),
      ink: d.querySelector('.jc-color-preview').style.color, dis: s.disabled, why: d.querySelector('.jc-color-why').textContent, whyShown: getComputedStyle(d.querySelector('.jc-color-why')).display !== 'none', contrast: d.querySelector('.jc-color-contrast').textContent,
      hue: d.querySelector('.jc-hue').value, pressed: d.querySelector('.jc-color-rec [aria-pressed=true]')?.dataset.color || ''}; })()`);
    check(await ev(p, `${S}.querySelectorAll('.jc-color-rec .jc-tone').length === 18 && !${S}.querySelector('.jc-ring-svg')`), 'square + hue bar + 18 suggestions (no fixed ring)');
    // a point on the square: near the top-left = a very light colour → Sooty words, Save live
    const sv = await rect(p, '.jc-color-dialog .jc-sv');
    await p.send('Input.dispatchMouseEvent', { type: 'mousePressed', x: sv.left + sv.w * 0.1, y: sv.top + sv.h * 0.05, button: 'left', buttons: 1, clickCount: 1 });
    await p.send('Input.dispatchMouseEvent', { type: 'mouseMoved', x: sv.left + sv.w * 0.15, y: sv.top + sv.h * 0.06, button: 'left', buttons: 1 });
    await p.send('Input.dispatchMouseEvent', { type: 'mouseReleased', x: sv.left + sv.w * 0.15, y: sv.top + sv.h * 0.06, button: 'left', clickCount: 1 });
    let st = await state();
    check(/^#[0-9A-F]{6}$/.test(st.hex) && st.ink === 'rgb(20, 20, 20)' && !st.dis && !st.whyShown, `a light point: Sooty words, Save live (${q(st)})`);
    check(st.contrast.includes(lang === 'zh' ? '浅色底' : 'light fill'), 'the contrast line says the words turned dark');
    // the hue bar turns the colour, HEX / RGB follow
    await ev(p, `(() => { const h = ${S}.querySelector('.jc-hue'); h.value = '210'; h.dispatchEvent(new Event('input')); })()`);
    const st2 = await state();
    check(st2.hex !== st.hex && st2.hue === '210' && st2.rgb.split(',').map(Number).every((x) => x >= 0 && x <= 255), `hue → HEX / RGB (${st.hex} → ${st2.hex})`);
    // arrow keys on the square: darker
    await ev(p, `${S}.querySelector('.jc-sv').focus()`);
    for (let i = 0; i < 6; i++) await key(p, 'ArrowDown', { shift: true });
    st = await state();
    check(st.ink === 'rgb(250, 249, 245)', `Shift+↓ on the square: darker → Lotion words (${st.hex})`);
    // RGB → HEX; a suggestion; a broken value greys Save with the reason; a good one brings it back
    await ev(p, `(() => { for (const [k, v] of [['R', 255], ['G', 255], ['B', 255]]) { const i = ${S}.querySelector('input[aria-label=' + k + ']'); i.value = String(v); i.dispatchEvent(new Event('input')); } })()`);
    st = await state();
    check(st.hex === '#FFFFFF' && st.ink === 'rgb(20, 20, 20)' && !st.dis, 'RGB 255,255,255 → #FFFFFF, Sooty words, saveable');
    await ev(p, `${S}.querySelector('.jc-tone[data-color="#ffe292"]').click()`);
    st = await state();
    check(st.hex === '#FFE292' && st.pressed === '#ffe292' && st.hue !== '0', `a suggestion fills everything (${q(st)})`);
    await snap(p, `picker-${lang}`);
    await ev(p, `(() => { const i = ${S}.querySelector('input[aria-label=HEX]'); i.focus(); i.value = '#FFE2'; i.dispatchEvent(new Event('input')); })()`);
    st = await state();
    const paint = await ev(p, `(()=>{const s=getComputedStyle(${S}.querySelector('.jc-dialog-actions .fp-action'));return {opacity:s.opacity,bg:s.backgroundColor,fg:s.color}})()`);
    check(st.dis && st.whyShown && st.why.length > 3 && Number(paint.opacity) === 1 && paint.bg === 'rgb(44, 43, 38)' && paint.fg === 'rgb(250, 249, 245)', `a broken value: Save grey, reason shown (${st.why}, P448 readable disabled pair ${q(paint)})`);
    await snap(p, `picker-blocked-${lang}`);
    await ev(p, `(() => { const i = ${S}.querySelector('input[aria-label=HEX]'); i.value = 'ffe292'; i.dispatchEvent(new Event('input')); })()`);
    st = await state();
    check(!st.dis && !st.whyShown, 'a good value (without #) brings Save back');
    await ev(p, `${S}.querySelector('.jc-dialog-actions .fp-action').click()`);
    await waitFor(p, `!document.querySelector('.jc-color-dialog')`, 3000);
    const focused = await ev(p, `(() => { const c=document.querySelector('[data-card=notes].fp-menu-card'),s=getComputedStyle(c);return {focus:c.matches(':focus'),bg:s.backgroundColor,ink:s.color}; })()`);
    check(focused.focus && focused.bg === 'rgb(44, 43, 38)' && focused.ink === 'rgb(250, 249, 245)', `P448 saved card retains focus with readable Noir/Lotion (${q(focused)})`);
    await ev(p, `document.querySelector('[data-card=notes].fp-menu-card').blur()`);
    r = await ev(p, `(() => { const c = document.querySelector('[data-card=notes].fp-menu-card'); return {bg: c.style.backgroundColor, ink: getComputedStyle(c).color}; })()`);
    check(p.ops.at(-1)?.color === '#ffe292' && r.bg === 'rgb(255, 226, 146)' && r.ink === 'rgb(20, 20, 20)', `#177 ②: the light fill saves and the card's words turn Sooty (${q(r)})`);
    await snap(p, `deck-light-card-${lang}`);
    await p.close?.();
  }
  // ---------------------------------------------------------------- phone 390 × 844 (touch): no key hints; the square follows a finger
  const p = await paired(390, 844, { touch: true });
  await deck(p);
  const h = await ev(p, `({chip: getComputedStyle(document.querySelector('[data-page=workflows] h2'), '::after').display})`);
  check(h.chip === 'none', 'touch-only screen: no key chips');
  const pt = await ev(p, `(() => { const r = document.querySelector('[data-card=notes].fp-menu-card').getBoundingClientRect(); return {x: r.right - 24, y: r.top + 24}; })()`);
  await p.send('Input.dispatchTouchEvent', { type: 'touchStart', touchPoints: [{ x: pt.x, y: pt.y }] });
  await waitFor(p, `document.querySelector('.jc-color-dialog[open]')`, 3000);
  await p.send('Input.dispatchTouchEvent', { type: 'touchEnd', touchPoints: [] });
  await sleep(200);
  const sv = await rect(p, '.jc-color-dialog .jc-sv');
  const hex0 = await ev(p, `document.querySelector('.jc-color-dialog input[aria-label=HEX]').value`);
  await swipe(p, sv.left + sv.w * 0.9, sv.top + sv.h * 0.9, sv.left + sv.w * 0.2, sv.top + sv.h * 0.2, 8, 160);
  const hex1 = await ev(p, `document.querySelector('.jc-color-dialog input[aria-label=HEX]').value`);
  check(hex1 !== hex0 && await ev(p, `document.querySelector('.jc-color-dialog[open]') !== null`), `a finger on the square picks (${hex0} → ${hex1})`);
  await snap(p, 'phone-picker');
  await p.close?.();
  console.log(`p412-cards: ${checks} checks ok → ${OUT}`);
} finally {
  rec.write();
  await B.close?.();
  await fake.close?.();
  await web.close?.();
}
process.exit(0);
