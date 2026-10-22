// P459: black-translucent standalone paints the document, not a theme-color refresh.
// The probe resolves current page / reader / custom-theme colours without feeding back the chrome token.
let diagnostic = null, wired = false, waterWatch = null;
function diagnose() {
  if (!diagnostic) return;
  const html = document.documentElement, cs = getComputedStyle(html);
  diagnostic.textContent = '--ios-chrome: ' + cs.getPropertyValue('--ios-chrome').trim()
    + '\nhtml: ' + cs.backgroundColor
    + '\ntheme-color: ' + [...document.querySelectorAll('meta[name="theme-color"]')].map(m => m.content).join(', ')
    + '\nsafe top/bottom: ' + cs.getPropertyValue('--ios-safe-top').trim() + ' / ' + cs.getPropertyValue('--ios-safe-bottom').trim()
    + '\nvv: ' + Math.round(window.visualViewport?.height || innerHeight) + ' @ ' + (window.visualViewport?.offsetTop || 0)
    + '\nstandalone: ' + (html.dataset.standalone === '1');
}
export function paintIOSChrome() {
  const html = document.documentElement;
  if (html.dataset.ios !== '1') return;
  // Only iPhone Safari tabs expose the root canvas behind their toolbar/keyboard.
  html.dataset.iosSafari = html.dataset.standalone !== '1' && /iPhone/.test(navigator.userAgent)
    && /Version\/.*Safari\//.test(navigator.userAgent)
    && !/CriOS|FxiOS|EdgiOS|OPiOS/.test(navigator.userAgent) ? '1' : '0';
  // Status chrome is fixed Noir in Safari, CriOS and Home Screen, including overlays.
  const noir = '#2c2b26';
  const color = noir;
  const water = document.getElementById('water');
  let bottom = noir;
  if (document.body.dataset.fs !== '1' && document.body.dataset.reader !== '1') {
    // Match the layer actually touching the bottom, including zero/unknown context.
    const ws = water && getComputedStyle(water), rect = water?.getBoundingClientRect();
    const edge = document.body.getBoundingClientRect().bottom;
    const visible = ws && ws.display !== 'none' && ws.visibility !== 'hidden' && Number(ws.opacity) > 0
      && rect.height > 0 && rect.top < edge && rect.bottom >= edge - 1;
    const page = document.getElementById('chromeProbe');
    const followsLayer = html.dataset.iosSafari === '1' || html.dataset.standalone === '1';
    const candidate = (!followsLayer || visible) && water ? getComputedStyle(water).backgroundColor
      : followsLayer && page && getComputedStyle(page).backgroundColor;
    if (/^(?:rgb\([^)]+\)|color\([^)]+\))$/.test(candidate)) bottom = candidate;
  }
  if (html.style.getPropertyValue('--ios-bottom') !== bottom) html.style.setProperty('--ios-bottom', bottom);
  if (!waterWatch && water) {
    waterWatch = new MutationObserver(() => paintIOSChrome());
    waterWatch.observe(water, {attributes:true, attributeFilter:['style','class','data-known']});
    waterWatch.observe(document.body, {attributes:true, attributeFilter:['style','class']});
  }
  if (!wired && new URLSearchParams(location.search).get('iosdiag') === '1') {
    wired = true; diagnostic = document.createElement('div'); diagnostic.id = 'iosdiag';
    document.body.append(diagnostic);
    for (const ev of ['resize', 'scroll']) window.visualViewport?.addEventListener(ev, diagnose);
    window.addEventListener('resize', diagnose);
  }
  diagnose();
  return color;
}
