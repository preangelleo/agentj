// P183: the file card's and the viewer's icons — 24-unit glyphs drawn like the reply card's own (.rmbar: stroke 1.9, round
// caps), built with createElementNS from the constants below (never markup).
export const SVG = 'http://www.w3.org/2000/svg';   // P318: js/ui.js's notice icons use it too
export const GLYPH = {
  wechat: [["path", {"d": "M8 14.8c-.7 0-1.4-.1-2-.3L3.5 16l.7-3A5.1 5.1 0 0 1 2.5 9.3C2.5 6.1 5.7 3.8 9.6 3.8c3.4 0 6.2 1.8 6.8 4.3"}], ["path", {"d": "M21.5 14.5c0-2.8-2.7-5-6-5s-6 2.2-6 5 2.7 5 6 5c.8 0 1.6-.1 2.3-.4l2.7 1.1-.6-2.5a4.6 4.6 0 0 0 1.6-3.2Z"}], ["path", {"d": "M7 8.5h.01M11.5 8.5h.01M13.5 14h.01M17.5 14h.01"}]],
  preview: [['path', { d: 'M2.5 12S6 5.5 12 5.5 21.5 12 21.5 12 18 18.5 12 18.5 2.5 12 2.5 12z' }], ['circle', { cx: 12, cy: 12, r: 3 }]],
  download: [['path', { d: 'M12 4v11' }], ['path', { d: 'm7 10 5 5 5-5' }], ['path', { d: 'M5 19.5h14' }]],
  share: [['path', { d: 'M12 3.5v11' }], ['path', { d: 'm8 7.5 4-4 4 4' }],
    ['path', { d: 'M8.5 10.5h-2a1.5 1.5 0 0 0-1.5 1.5v7a1.5 1.5 0 0 0 1.5 1.5h11a1.5 1.5 0 0 0 1.5-1.5v-7a1.5 1.5 0 0 0-1.5-1.5h-2' }]],
  mail: [['rect', { x: 3.5, y: 5.5, width: 17, height: 13, rx: 2.5 }], ['path', { d: 'm4.5 7.5 7.5 5.5 7.5-5.5' }]],
  ok: [['path', { d: 'm5 12.5 4.5 4.5L19 7.5' }]],
  file: [['path', { d: 'M14 3.5H7.5a2 2 0 0 0-2 2v13a2 2 0 0 0 2 2h9a2 2 0 0 0 2-2V8z' }], ['path', { d: 'M14 3.5V8h4.5' }]],
};
// P201: 转发到 Telegram is a solid paper plane (Leo's pick, re-drawn here as a plain path from measured vertices — no third-party
// artwork), one colour = currentColor, so it takes the same theme colour as the stroked glyphs above.
export const TG_PATH = 'M20.69 3.91Q20.79 3.14 20.1 3.49L3.69 11.93Q3 12.28 3.71 12.59L8.03 14.5L18.12 5.85L10.31 15.27L18.22 17.6Q18.89 17.8 18.98 17.1zM9.59 16.43Q9.57 16.22 9.77 16.29L12.4 17.18Q12.56 17.24 12.45 17.37L10.26 19.87Q9.89 20.3 9.85 19.74z';
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
