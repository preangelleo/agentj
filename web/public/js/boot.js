/* Runs synchronously in <head>, before first paint (relay's STATUSBAR "match" mode, its ADR-022 / 044). An iPhone
   home-screen app has been seen keeping the theme-color it read at launch, so there the launch value is the last state
   colour the page actually painted (relay.js saves it as localStorage "aj.chrome" — a colour, nothing else). Android and
   Safari follow the live updates the page makes later. CSP-safe: a same-origin file, no inline script. */
(function () {
  "use strict";
  window.__ajLaunchChrome = "";
  if (navigator.standalone !== true) return;
  try {
    var c = localStorage.getItem("aj.chrome");
    if (!/^#[0-9a-f]{6}$/.test(c || "")) return;
    window.__ajLaunchChrome = c;
    var ms = document.querySelectorAll('meta[name="theme-color"]');
    for (var i = 0; i < ms.length; i++) ms[i].setAttribute("content", c);
  } catch (e) { /* blocked storage: the static colour stays */ }
})();
