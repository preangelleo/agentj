// Customer observer: local xterm renderer only. No onData, input callback, protocol reply, resize sender or clipboard access.
import { Terminal } from '../vendor/xterm/xterm.mjs';
import { linkHandler } from './termlink.js';
export function mount(host, grid = {cols:80,rows:24}) {
  if (!document.getElementById('ast-xterm-css')) { const l = document.createElement('link'); l.id = 'ast-xterm-css'; l.rel = 'stylesheet'; l.href = new URL('../vendor/xterm/xterm.css', import.meta.url).href; document.head.append(l); }
  const term = new Terminal({ disableStdin: true, cursorBlink: false, scrollback: 3000, cols: grid.cols, rows: grid.rows, fontSize: 13,
    theme: { background: '#2c2b26', foreground: '#faf9f5' }, minimumContrastRatio: 4.5, allowProposedApi: false, linkHandler: linkHandler() });
  term.parser.registerOscHandler(52, () => true); // Clipboard writes and queries are never part of observation.
  term.open(host); term.textarea?.setAttribute('tabindex', '-1'); term.textarea?.setAttribute('aria-hidden', 'true');
  return { write: d => term.write(d), resize:(cols,rows)=>term.resize(cols,rows), dispose: () => { term.dispose(); host.replaceChildren(); } };
}
