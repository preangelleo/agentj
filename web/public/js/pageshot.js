// P264: local DOM → PNG. No screen sharing, clipboard, third-party code or upload.
// Render the viewport, including the current scroll positions. Secrets are removed BEFORE SVG serialization.
export const PRIVATE = 'input[type="password"], [data-secret], [data-screenshot-private], #secret-out, #cred-card, #so, #sout, #elev, #rm-qr, #rm-code, #rvCode, #rvQR, #pair-link, [autocomplete="one-time-code"], [autocomplete*="password"]';
const XHTML = 'http://www.w3.org/1999/xhtml';
const safeURL = (raw) => { try { const u = new URL(raw, location.href); return u.protocol === 'data:' || u.protocol === 'blob:' || u.origin === location.origin; } catch { return false; } };
function imageData(node) {
  if (!safeURL(node.currentSrc || node.src) || !node.complete || !node.naturalWidth) return '';
  // Reuse the already decoded first-party image. No fetch and no CSP relaxation.
  try { const c = document.createElement('canvas'); c.width = node.naturalWidth; c.height = node.naturalHeight;
    c.getContext('2d').drawImage(node, 0, 0); return c.toDataURL('image/png'); } catch { return ''; }
}
// P349: a native player is drawn by the browser, not the DOM, so the SVG copy cannot paint it — the owner's screenshot had an
// empty block where the file card's audio player was. Its box gets a stand-in: a pill (audio) or a dark frame (video) with
// a play mark and the position / length. No source, poster or frame is copied.
const clock = (s) => { s = Number.isFinite(s) && s > 0 ? Math.floor(s) : 0; return `${Math.floor(s / 60)}:${String(s % 60).padStart(2, '0')}`; };
function player(node, style, rect) {
  if (!node.controls && node.tagName === 'AUDIO') return null;
  const video = node.tagName === 'VIDEO', dark = video || /dark/.test(style.colorScheme), fg = dark ? '#fff' : '#202124';
  const el = (css, text) => { const e = document.createElementNS(XHTML, 'div'); e.style.cssText = css + `;color:${fg}`; if (text) e.textContent = text; return e; };
  const out = el(`display:${style.display === 'inline' ? 'inline-flex' : 'flex'};align-items:${video ? 'flex-end' : 'center'};gap:10px;box-sizing:border-box;width:${rect.width}px;height:${rect.height}px;`
    + `margin:${style.margin};flex:none;padding:0 14px ${video ? '10px' : '0'};border-radius:${video ? style.borderRadius : rect.height / 2 + 'px'};overflow:hidden;`
    + `background:${video ? '#000' : dark ? '#3b3b3b' : '#f1f3f4'};font:500 13px/1 ${style.fontFamily};white-space:nowrap`);
  out.setAttribute('data-pageshot', video ? 'video' : 'audio');
  out.append(el('flex:none;width:0;height:0;border-style:solid;border-width:6px 0 6px 10px;border-color:transparent transparent transparent currentColor'),
    el('flex:none', `${clock(node.currentTime)} / ${clock(node.duration)}`),
    el(`flex:1;height:4px;border-radius:2px;background:${dark ? 'rgba(255,255,255,.5)' : 'rgba(32,33,36,.3)'}`));
  return out;
}
function copy(node, parentRect = {left: 0, top: 0}) {
  if (node.nodeType === 3) return document.createTextNode(node.textContent);
  if (node.nodeType !== 1 || /^(SCRIPT|STYLE|LINK|META|IFRAME)$/.test(node.tagName)) return null;
  const style = getComputedStyle(node), rect = node.getBoundingClientRect();
  if (style.display === 'none' || style.visibility === 'hidden' || node.hidden) return null;
  if (/^(VIDEO|AUDIO)$/.test(node.tagName)) return player(node, style, rect);
  const privateNode = node.matches(PRIVATE);
  // No attributes from live nodes: value, title, event handlers, URLs and data-* never enter the SVG.
  const tag = node.namespaceURI === 'http://www.w3.org/2000/svg' ? node.localName : (/^(INPUT|TEXTAREA|SELECT|CANVAS|IMG)$/.test(node.tagName) || privateNode ? 'div' : node.localName);
  const out = document.createElementNS(node.namespaceURI === 'http://www.w3.org/2000/svg' && !privateNode ? node.namespaceURI : XHTML, tag);
  for (const prop of style) {
    const value = style.getPropertyValue(prop);
    // External URLs are never fetched by the generated SVG. Images are explicitly embedded below.
    if (!/url\(/i.test(value)) out.style.setProperty(prop, value);
  }
  out.style.animation = 'none'; out.style.transition = 'none';
  if (node.namespaceURI === 'http://www.w3.org/2000/svg') {
    for (const attr of ['viewBox','d','cx','cy','r','x','y','x1','y1','x2','y2','width','height','points','fill','stroke','stroke-width','stroke-linecap','stroke-linejoin']) if (node.hasAttribute(attr)) out.setAttribute(attr, node.getAttribute(attr));
  }
  if (/(auto|scroll)/.test(style.overflowY + style.overflowX)) out.style.overflow = 'hidden';
  if (privateNode) {
    out.style.width = rect.width + 'px'; out.style.height = rect.height + 'px'; out.style.minWidth = rect.width + 'px'; out.style.flexShrink = '0';
    out.textContent = '••••••'; out.style.background = '#141414'; out.style.color = '#faf9f5'; out.style.overflow = 'hidden';
    return out;
  }
  if (/^(INPUT|TEXTAREA|SELECT)$/.test(node.tagName)) out.textContent = node.tagName === 'SELECT' ? node.selectedOptions[0]?.textContent || '' : node.value;
  else if (node.tagName === 'IMG' || node.tagName === 'CANVAS') {
    const src = node.tagName === 'IMG' ? imageData(node) : (() => { try { return node.toDataURL(); } catch { return ''; } })();
    // P340: the copy keeps the image's own box, not the computed background geometry. The header orb outside chat draws a
    // dance sprite as its background (background-size 384px, an animated background-position) — copying those onto the
    // embedded logo slid it out of the disc, and the owner's screenshot had an empty orb.
    if (src) Object.assign(out.style, {backgroundImage: `url("${src}")`, backgroundRepeat: 'no-repeat', backgroundOrigin: 'content-box', backgroundClip: 'border-box',
      backgroundPosition: 'center', backgroundSize: /^(contain|cover)$/.test(style.objectFit) ? style.objectFit : '100% 100%'});
  } else {
    const pseudo = which => { const st = getComputedStyle(node, which); if (st.content === 'none' || st.content === 'normal' || /url\(/i.test(st.content)) return null; const c = document.createElementNS(XHTML, 'span'); for (const prop of st) { const v = st.getPropertyValue(prop); if (!/url\(/i.test(v)) c.style.setProperty(prop, v); } c.textContent = st.content.replace(/^['"]|['"]$/g, ''); return c; };
    const before = pseudo('::before'); if (before) out.append(before);
    for (const child of node.childNodes) { const c = copy(child, rect); if (c) { if (c.style && (node.scrollTop || node.scrollLeft)) c.style.transform = `translate(${-node.scrollLeft}px, ${-node.scrollTop}px) ` + (c.style.transform === 'none' ? '' : c.style.transform); out.append(c); } }
    const after = pseudo('::after'); if (after) out.append(after);
  }
  return out;
}
export async function snapshotSVG(root = document.body) {
  const width = document.documentElement.clientWidth, height = document.documentElement.clientHeight;
  const tree = copy(root);
  Object.assign(tree.style, {position: 'relative', margin: '0', left: '0', top: '0', width: width + 'px', height: height + 'px', overflow: 'hidden'});
  const svg = `<svg xmlns="http://www.w3.org/2000/svg" width="${width}" height="${height}"><foreignObject width="100%" height="100%">${new XMLSerializer().serializeToString(tree)}</foreignObject></svg>`;
  return { svg, width, height };
}
export async function capturePage() {
  const {svg, width, height} = await snapshotSVG();
  const img = new Image();
  await new Promise((resolve, reject) => { img.onload = resolve; img.onerror = () => reject(new Error('render')); img.src = 'data:image/svg+xml;charset=utf-8,' + encodeURIComponent(svg); });
  const scale = Math.min(devicePixelRatio || 1, 2, Math.sqrt(4_000_000 / (width * height)));
  const canvas = document.createElement('canvas'); canvas.width = Math.round(width * scale); canvas.height = Math.round(height * scale);
  const ctx = canvas.getContext('2d'); ctx.scale(scale, scale); ctx.drawImage(img, 0, 0);
  const blob = await new Promise(resolve => canvas.toBlob(resolve, 'image/png'));
  if (!blob) throw new Error('png');
  return new File([blob], 'agentj-page.png', {type: 'image/png'});
}
