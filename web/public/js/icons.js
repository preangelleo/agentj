// P183: the file card's and the viewer's icons — 24-unit glyphs drawn like the reply card's own (.rmbar: stroke 1.9, round
// caps), built with createElementNS from the constants below (never markup).
const SVG = 'http://www.w3.org/2000/svg';
export const GLYPH = {
  preview: [['path', { d: 'M2.5 12S6 5.5 12 5.5 21.5 12 21.5 12 18 18.5 12 18.5 2.5 12 2.5 12z' }], ['circle', { cx: 12, cy: 12, r: 3 }]],
  download: [['path', { d: 'M12 4v11' }], ['path', { d: 'm7 10 5 5 5-5' }], ['path', { d: 'M5 19.5h14' }]],
  share: [['path', { d: 'M12 3.5v11' }], ['path', { d: 'm8 7.5 4-4 4 4' }],
    ['path', { d: 'M8.5 10.5h-2a1.5 1.5 0 0 0-1.5 1.5v7a1.5 1.5 0 0 0 1.5 1.5h11a1.5 1.5 0 0 0 1.5-1.5v-7a1.5 1.5 0 0 0-1.5-1.5h-2' }]],
  mail: [['rect', { x: 3.5, y: 5.5, width: 17, height: 13, rx: 2.5 }], ['path', { d: 'm4.5 7.5 7.5 5.5 7.5-5.5' }]],
  ok: [['path', { d: 'm5 12.5 4.5 4.5L19 7.5' }]],
  file: [['path', { d: 'M14 3.5H7.5a2 2 0 0 0-2 2v13a2 2 0 0 0 2 2h9a2 2 0 0 0 2-2V8z' }], ['path', { d: 'M14 3.5V8h4.5' }]],
};
// The Telegram mark (circle + paper plane), one colour, transparent: telegram.org/tour/screenshots lets apps use the logo for
// "forward to Telegram" buttons; the path is Simple Icons' (CC0).
export const TG_PATH = 'M11.944 0A12 12 0 0 0 0 12a12 12 0 0 0 12 12 12 12 0 0 0 12-12A12 12 0 0 0 12 0a12 12 0 0 0-.056 0zm4.962 7.224c.1-.002.321.023.465.14a.506.506 0 0 1 .171.325c.016.093.036.306.02.472-.18 1.898-.962 6.502-1.36 8.627-.168.9-.499 1.201-.82 1.23-.696.065-1.225-.46-1.9-.902-1.056-.693-1.653-1.124-2.678-1.8-1.185-.78-.417-1.21.258-1.91.177-.184 3.247-2.977 3.307-3.23.007-.032.014-.15-.056-.212s-.174-.041-.249-.024c-.106.024-1.793 1.14-5.061 3.345-.48.33-.913.49-1.302.48-.428-.008-1.252-.241-1.865-.44-.752-.245-1.349-.374-1.297-.789.027-.216.325-.437.893-.663 3.498-1.524 5.83-2.529 6.998-3.014 3.332-1.386 4.025-1.627 4.476-1.635z';
/** One 24-unit glyph as DOM (createElementNS from the constants above). */
export function glyph(name, cls = 'g g0') {
  const s = document.createElementNS(SVG, 'svg');
  s.setAttribute('viewBox', '0 0 24 24'); s.setAttribute('class', cls); s.setAttribute('aria-hidden', 'true');
  if (name === 'tg') {
    s.setAttribute('fill', 'currentColor');
    const p = document.createElementNS(SVG, 'path'); p.setAttribute('d', TG_PATH); s.appendChild(p);
    return s;
  }
  s.setAttribute('fill', 'none'); s.setAttribute('stroke', 'currentColor'); s.setAttribute('stroke-width', name === 'ok' ? '2.2' : '1.9');
  s.setAttribute('stroke-linecap', 'round'); s.setAttribute('stroke-linejoin', 'round');
  for (const [tag, at] of GLYPH[name]) {
    const e = document.createElementNS(SVG, tag);
    for (const [k, v] of Object.entries(at)) e.setAttribute(k, String(v));
    s.appendChild(e);
  }
  return s;
}
