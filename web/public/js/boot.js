/* P422: synchronous first-paint chrome. Never restore a cached state colour. */
(function () {
  "use strict";
  var ios = /iPad|iPhone|iPod/.test(navigator.userAgent) || (navigator.platform === "MacIntel" && navigator.maxTouchPoints > 1);
  if (ios) document.documentElement.dataset.ios = "1";
  if (navigator.standalone === true || matchMedia('(display-mode: standalone)').matches) document.documentElement.dataset.standalone = '1';
  window.__ajLaunchChrome = ios ? "#2c2b26" : "#141414";
  document.documentElement.style.backgroundColor = window.__ajLaunchChrome;
  // P500: iOS keeps the original manifest identity and dark backing at installation.
  // Theme-specific splash manifests belong to Android; never snapshot Lotion into a new iOS shortcut.
  var scheme = matchMedia('(prefers-color-scheme: dark)');
  function manifest() {
    var link = document.querySelector('link[rel="manifest"]');
    if (link) link.setAttribute('href', ios ? 'manifest.webmanifest' : 'manifest-' + (scheme.matches ? 'dark' : 'light') + '.webmanifest');
  }
  manifest();
  scheme.addEventListener('change', manifest);
  var bars = document.querySelectorAll('meta[name="theme-color"]');
  for (var i = 0; i < bars.length; i++) bars[i].setAttribute("content", window.__ajLaunchChrome);
})();
