// 0.26.9 #463 (PROTOCOL §38 PTY additions, ADR-0269-w-term): the full web terminal — xterm.js on a real PTY on the host.
// Loaded by js/terminal.js as a module script next to it, only when the host's `terminal.modes` has "pty" and only on the
// first open (never in the first-screen bundle). xterm.js + its fit addon are OUR copies (vendor/xterm/, pinned by sha256 in
// vendor/VERSIONS.md, MIT); nothing is fetched from anywhere else.
// What this module owns: the xterm instance (theme = the closed palette, following light/dark canvas), fitting it to the page
// (ResizeObserver, the visual viewport for the soft keyboard, orientation), the extra-keys bar for a touch screen (Esc, Tab,
// sticky Ctrl / Alt, arrows, Ctrl+C / Ctrl+D / Ctrl+Z, | ~ /) and Esc / Tab inside the terminal going to the program (not to
// the page's layers). terminal.js owns the wire: every byte xterm hands us goes out as `term_in`, sizes as `term_resize`,
// and `term_out` comes back through write(). Output lives in xterm's memory only and is disposed when the terminal closes.
import { Terminal } from '../vendor/xterm/xterm.mjs';
import { FitAddon } from '../vendor/xterm/addon-fit.mjs';
import { registerPty } from './terminal.js';
import { t, onLang } from './t.js';
import { mk } from './ui.js';
import { linkHandler } from './termlink.js';   // 0.26.9 L-2: https-only links, no confirm() + window.open of any scheme

/** The 16 ANSI colours from the closed palette (leo-palette.json); every foreground ≥ 4.5:1 on Noir except black / sooty,
 *  which programs use as a ground. */
export const THEME = {
  background: '#2c2b26', foreground: '#faf9f5', cursor: '#71bbde', cursorAccent: '#2c2b26',
  selectionBackground: 'rgba(113,187,222,0.35)', selectionInactiveBackground: 'rgba(113,187,222,0.2)',
  scrollbarSliderBackground: 'rgba(250,249,245,0)', scrollbarSliderHoverBackground: 'rgba(250,249,245,0)',
  scrollbarSliderActiveBackground: 'rgba(250,249,245,0)', overviewRulerBorder: 'rgba(250,249,245,0)',
  black: '#141414', red: '#f7827e', green: '#b7e396', yellow: '#f7e17c', blue: '#71bbde', magenta: '#d1a7e8', cyan: '#78dedc', white: '#e3dacc',
  brightBlack: '#acacac', brightRed: '#fbc8c6', brightGreen: '#dff3d1', brightYellow: '#ffe292', brightBlue: '#c1e1f0',
  brightMagenta: '#ebd8f5', brightCyan: '#c4f0f0', brightWhite: '#faf9f5',
};
/** A light canvas uses the same closed palette; OSC responses come from xterm's active ThemeService, not this map. */
export const LIGHT_THEME = { ...THEME, background: '#faf9f5', foreground: '#141414', cursor: '#2c2b26', cursorAccent: '#faf9f5',
  black: '#141414', white: '#858585', brightBlack: '#2c2b26', brightWhite: '#faf9f5' };
export function terminalScheme(choice, systemDark) {
  return choice === 'dark' || choice === 'light' ? choice : systemDark ? 'dark' : 'light';
}
/** xterm already implements OSC 4/10/11/12. Distinguish its finite protocol replies from owner keys/paste. */
export function isPtyReply(d) {
  if (typeof d !== 'string' || d.length > 128) return false;
  if (d === '\x1b[0n') return true; // DSR status barrier after Nvim's OSC11 startup query.
  if (/^\x1b\[\??[1-9]\d{0,3};[1-9]\d{0,3}R$/.test(d)) return true;
  const m = /^\x1b\](10|11|12|4;(\d{1,3}));rgb:[\da-f]{1,4}\/[\da-f]{1,4}\/[\da-f]{1,4}(?:\x07|\x1b\\)$/i.exec(d);
  return !!m && (!m[2] || Number(m[2]) <= 255);
}
export function routePtyData(d, cb, take) {
  if (isPtyReply(d)) cb.reply(d);
  else cb.data(withMods(d, take()));
}
/** The bar, left to right. `seq` = the bytes; `mod` = a sticky modifier; `ins` = a character typed as if by the keyboard. */
export const BAR = [
  { id: 'paste', label: '', paste: true }, { id: 'enter', label: 'Enter', seq: '\r' },
  { id: 'esc', label: 'Esc', seq: '\x1b' }, { id: 'tab', label: 'Tab', seq: '\t' },
  { id: 'ctrl', label: 'Ctrl', mod: 'ctrl' }, { id: 'alt', label: 'Alt', mod: 'alt' },
  { id: 'up', label: '↑', arrow: 'A' }, { id: 'down', label: '↓', arrow: 'B' }, { id: 'left', label: '←', arrow: 'D' }, { id: 'right', label: '→', arrow: 'C' },
  { id: 'intr', label: 'Ctrl+C', seq: '\x03' }, { id: 'eof', label: 'Ctrl+D', seq: '\x04' }, { id: 'susp', label: 'Ctrl+Z', seq: '\x1a' },
  { id: 'pipe', label: '|', ins: '|' }, { id: 'tilde', label: '~', ins: '~' }, { id: 'slash', label: '/', ins: '/' },
];
/** An arrow key with the sticky modifiers: plain = CSI / SS3 (application cursor mode), modified = CSI 1;m X (xterm). */
export function arrowSeq(x, app, { ctrl = false, alt = false } = {}) {
  const m = 1 + (alt ? 2 : 0) + (ctrl ? 4 : 0);
  if (m > 1) return `\x1b[1;${m}${x}`;
  return (app ? '\x1bO' : '\x1b[') + x;
}
/** One keystroke from the keyboard (or a bar key) with the sticky Ctrl / Alt applied: Ctrl turns @ A–Z [ \ ] ^ _ (either
 *  case) into C0, Ctrl+Space = NUL, Ctrl+? = DEL; Alt = an ESC prefix (meta). Longer data (a paste, an IME word) passes as is. */
export function withMods(d, { ctrl = false, alt = false } = {}) {
  let s = String(d);
  if (ctrl && [...s].length === 1) {
    const c = s.toUpperCase().charCodeAt(0);
    if (c >= 64 && c <= 95) s = String.fromCharCode(c - 64);
    else if (s === ' ') s = '\x00';
    else if (s === '?') s = '\x7f';
  }
  if (alt && [...s].length === 1) s = '\x1b' + s;
  return s;
}
/** The terminal's text: every line of the buffer (or only the rows on screen), trailing blanks trimmed. */
function lines(term, view) {
  const b = term.buffer.active, from = view ? b.viewportY : 0, to = view ? Math.min(b.length, b.viewportY + term.rows) : b.length;
  const out = [];
  for (let y = from; y < to; y++) out.push(b.getLine(y)?.translateToString(true) ?? '');
  return out;
}
const coarse = () => { try { return matchMedia('(pointer: coarse)').matches; } catch { return false; } };

let cssOnce = false, live = null;   // live = the mounted terminal's labels() (onLang has no unsubscribe)
onLang(() => live?.());
function css() {
  if (cssOnce || document.getElementById('term-xterm-css')) { cssOnce = true; return; }
  const l = document.createElement('link');
  l.id = 'term-xterm-css'; l.rel = 'stylesheet'; l.href = new URL('../vendor/xterm/xterm.css', import.meta.url).href;
  document.head.appendChild(l);
  cssOnce = true;
}

/** Put a terminal into `host`. cb.data(bytes) = what the owner typed / pasted; cb.reply(bytes) = native finite terminal responses;
 *  cb.resize(cols, rows) = the grid changed. → the handle terminal.js drives. */
function mount(host, cb) {
  css();
  const fontFamily = (getComputedStyle(document.documentElement).getPropertyValue('--font-mono') || '').trim() || 'monospace';
  const narrow = Math.min(innerWidth, screen?.width || innerWidth) < 500;
  const systemScheme = matchMedia('(prefers-color-scheme: dark)');
  const activeTheme = () => terminalScheme(document.documentElement.getAttribute('data-theme'), systemScheme.matches) === 'dark' ? THEME : LIGHT_THEME;
  const term = new Terminal({ fontFamily, fontSize: narrow ? 13 : 14, lineHeight: 1.2, scrollback: 5000, cursorBlink: true, theme: activeTheme(),
    minimumContrastRatio: 4.5, macOptionIsMeta: true, allowProposedApi: false, convertEol: false, drawBoldTextInBrightColors: false,
    screenReaderMode: false, rightClickSelectsWord: true, overviewRuler: { width: 0 }, linkHandler: linkHandler() });
  const fit = new FitAddon();
  term.loadAddon(fit);
  term.open(host);
  const paintTheme = () => {
    const theme = activeTheme();
    term.options.theme = theme;
    host.style.setProperty('--term-canvas', theme.background);
  };
  const themeObserver = new MutationObserver(paintTheme);
  themeObserver.observe(document.documentElement, { attributes: true, attributeFilter: ['data-theme'] });
  systemScheme.addEventListener('change', paintTheme);
  paintTheme();
  // A short tap anywhere on the screen focuses xterm's own textarea during the
  // touch gesture. Keep scrolling and long-press selection native.
  let tap = null;
  const touchStart = e => { tap = e.pointerType === 'touch' ? {x:e.clientX,y:e.clientY,at:performance.now()} : null; };
  const touchEnd = e => {
    if (tap && Math.hypot(e.clientX-tap.x,e.clientY-tap.y) < 10 && performance.now()-tap.at < 400) term.focus();
    tap = null;
  };
  host.addEventListener('pointerdown', touchStart);
  host.addEventListener('pointerup', touchEnd);
  term.textarea?.setAttribute('aria-label', t('pty.aria'));
  for (const [k, v] of [['autocapitalize', 'off'], ['autocorrect', 'off'], ['autocomplete', 'off'], ['enterkeyhint', 'enter']]) term.textarea?.setAttribute(k, v);
  const mods = { ctrl: false, alt: false };
  let bar = null;
  const paintMods = () => {
    if (!bar) return;
    for (const m of ['ctrl', 'alt']) { const b = bar.querySelector(`[data-mod="${m}"]`); b.setAttribute('aria-pressed', String(mods[m])); b.classList.toggle('on', mods[m]); }
  };
  const take = () => { const m = { ...mods }; if (mods.ctrl || mods.alt) { mods.ctrl = mods.alt = false; paintMods(); } return m; };
  const emit = (d) => { if (d) cb.data(d); };
  const subs = [term.onData((d) => routePtyData(d, cb, take)), term.onResize(({ cols, rows }) => cb.resize(cols, rows))];
  // Esc and Tab inside the terminal belong to the program (vim, the shell's completion) — the page's layers listen on the
  // document in the capture phase, so we take them one step earlier (window, capture) and type them ourselves.
  const keys = (e) => {
    if (e.isComposing || e.ctrlKey || e.metaKey || !host.contains(e.target)) return;
    if (e.key !== 'Escape' && e.key !== 'Tab') return;
    e.preventDefault(); e.stopImmediatePropagation();
    if (e.key === 'Escape') emit(withMods('\x1b', take()));
    else emit(e.shiftKey ? '\x1b[Z' : (e.altKey ? '\x1b\t' : withMods('\t', take())));
  };
  addEventListener('keydown', keys, true);
  let raf = 0;
  const refit = () => {
    cancelAnimationFrame(raf);
    raf = requestAnimationFrame(() => { if (host.isConnected && host.clientWidth > 0 && host.clientHeight > 0) { try { fit.fit(); } catch { /* not laid out */ } } });
  };
  const ro = typeof ResizeObserver === 'function' ? new ResizeObserver(refit) : null;
  ro?.observe(host);
  const vv = globalThis.visualViewport;
  vv?.addEventListener('resize', refit);
  addEventListener('orientationchange', refit);
  addEventListener('resize', refit);

  /** The extra-keys bar: touch screens and narrow windows (the page's CSS decides); every key ≥ 44 px. */
  bar = mk('div', 'term-xkeys'); bar.id = 'term-xkeys'; bar.setAttribute('role', 'group');
  for (const k of BAR) {
    const b = mk('button', 'term-key term-xkey', k.label, 'aj-terminal'); b.type = 'button'; b.id = 'term-x-' + k.id; b.dataset.k = k.id;
    if (k.mod) { b.dataset.mod = k.mod; b.setAttribute('aria-pressed', 'false'); }
    b.addEventListener('pointerdown', (e) => { if (e.pointerType !== 'mouse') e.preventDefault(); });   // keep the soft keyboard where it is
    b.addEventListener('click', async (e) => {
      if (k.paste) {
        try { const text = await navigator.clipboard.readText(); if (text) term.paste(text); }
        catch { cb.pasteBlocked?.(); }
      }
      else if (k.mod) { mods[k.mod] = !mods[k.mod]; paintMods(); }
      else if (k.arrow) emit(arrowSeq(k.arrow, term.modes.applicationCursorKeysMode, take()));
      else if (k.ins) emit(withMods(k.ins, take()));
      else emit(k.id === 'esc' || k.id === 'tab' ? withMods(k.seq, take()) : (take(), k.seq));
      if (e.pointerType === 'mouse' || !e.pointerType) term.focus();
    });
    bar.append(b);
  }
  const labels = () => {
    bar.setAttribute('aria-label', t('pty.keys'));
    for (const b of bar.querySelectorAll('button')) { if (b.dataset.k === 'paste') b.textContent = t('terminal.paste'); b.setAttribute('aria-label', t('pty.k.' + b.dataset.k, { c: b.textContent })); }
    term.textarea?.setAttribute('aria-label', t('pty.aria'));
  };
  live = labels;
  labels();
  bar.dataset.touch = coarse() ? '1' : '0';
  refit();

  return {
    term, bar,
    write: (s) => term.write(s),
    size: () => ({ cols: term.cols, rows: term.rows }),
    fit: () => { try { fit.fit(); } catch { /* not laid out */ } return { cols: term.cols, rows: term.rows }; },
    refit,
    focus: () => term.focus(),
    /** A command typed in for the owner (never with Enter): a bracketed paste when the program asked for it. */
    type: (s) => term.paste(String(s)),
    text: () => lines(term, false).join('\n').replace(/\n+$/, ''),
    viewRows: () => lines(term, true),
    mods: () => ({ ...mods }),
    dispose() {
      host.removeEventListener('pointerdown', touchStart);
      host.removeEventListener('pointerup', touchEnd);
      removeEventListener('keydown', keys, true);
      vv?.removeEventListener('resize', refit);
      removeEventListener('orientationchange', refit);
      removeEventListener('resize', refit);
      ro?.disconnect(); themeObserver.disconnect(); systemScheme.removeEventListener('change', paintTheme);
      host.style.removeProperty('--term-canvas'); cancelAnimationFrame(raf);
      if (live === labels) live = null;
      for (const s of subs) s.dispose();
      try { term.clear(); term.reset(); } catch { /* disposed */ }
      term.dispose();
      bar.remove();
    },
  };
}

/** A size guess before the terminal exists (term_open carries one; the real fit follows right after term_ready). */
export function guess(w = innerWidth, h = globalThis.visualViewport?.height || innerHeight) {
  const fs = Math.min(w, screen?.width || w) < 500 ? 13 : 14;
  return { cols: Math.max(20, Math.min(400, Math.floor((w - 24) / (fs * 0.6)))), rows: Math.max(5, Math.min(200, Math.floor((h - 200) / (fs * 1.2)))) };
}

registerPty({ mount, guess });
