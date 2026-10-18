/* Runs synchronously in <head>, before first paint. P384: iOS Safari / Home Screen always launches on Sooty,
   including iPadOS desktop mode. Other platforms retain relay's STATUSBAR match mode (ADR-022 / 044).
   CSP-safe: a same-origin file, no inline script. */
(function () {
  "use strict";
  window.__ajLaunchChrome = "";
  // P384 #86: iPadOS can request the Mac desktop site; touch distinguishes it from a Mac.
  var ios = /iPad|iPhone|iPod/.test(navigator.userAgent) || (navigator.platform === "MacIntel" && navigator.maxTouchPoints > 1);
  if (ios) {
    document.documentElement.dataset.ios = "1";
    document.documentElement.style.backgroundColor = "#141414";
    window.__ajLaunchChrome = "#141414";
    var bars = document.querySelectorAll('meta[name="theme-color"]');
    for (var k = 0; k < bars.length; k++) bars[k].setAttribute("content", "#141414");
    var apple = document.querySelector('meta[name="apple-mobile-web-app-status-bar-style"]');
    if (apple) apple.setAttribute("content", "black-translucent");
    return;
  }
  if (navigator.standalone !== true) {
    // P364: an installed Android app shows Chrome's splash (manifest background_color, Sooty) until the page paints, and
    // the status bar icons follow the page's theme-color meanwhile — a light-scheme #faf9f5 drew dark icons on Sooty.
    // Launch on the splash colour; relay.js paintChrome() writes the real page colour on its first paint.
    try {
      if (window.matchMedia && matchMedia("(display-mode: standalone)").matches) {
        var am = document.querySelectorAll('meta[name="theme-color"]');
        for (var j = 0; j < am.length; j++) am[j].setAttribute("content", "#141414");
      }
    } catch (e) { /* no matchMedia: the static colour stays */ }
    return;
  }
  try {
    var c = localStorage.getItem("aj.chrome");
    if (!/^#[0-9a-f]{6}$/.test(c || "")) return;
    window.__ajLaunchChrome = c;
    var ms = document.querySelectorAll('meta[name="theme-color"]');
    for (var i = 0; i < ms.length; i++) ms[i].setAttribute("content", c);
  } catch (e) { /* blocked storage: the static colour stays */ }
})();
