/* Agent J — ui.js: tiny progressive enhancements for base.css components. Optional; load with `defer`.
   CSP-safe (no inline, no eval). Everything works without it (the header menu is a plain <details>).
   - header menu  (<details class="aj-menu" data-aj-menu>): open on wide screens, closed on ≤ 720 px;
     closes on Escape, on outside click and after a link inside is followed.
   - theme switch (any element with data-aj-theme-switch): cycles auto → light → dark, stored as localStorage "aj.theme";
     lang.js pre-applies the stored theme in <head>, so there is no flash. */
(function () {
  "use strict";
  var doc = document;
  var wide = window.matchMedia ? window.matchMedia("(min-width: 721px)") : { matches: true, addEventListener: function () {} };

  function menus() { return doc.querySelectorAll("details[data-aj-menu]"); }
  function syncMenus() {
    var list = menus();
    for (var i = 0; i < list.length; i++) list[i].open = wide.matches;
  }
  function closeNarrow(except) {
    if (wide.matches) return;
    var list = menus();
    for (var i = 0; i < list.length; i++) if (list[i] !== except) list[i].open = false;
  }
  if (wide.addEventListener) wide.addEventListener("change", syncMenus);
  else if (wide.addListener) wide.addListener(syncMenus);

  doc.addEventListener("keydown", function (e) {
    if (e.key !== "Escape" || wide.matches) return;
    var list = menus();
    for (var i = 0; i < list.length; i++) {
      if (list[i].open) { list[i].open = false; var s = list[i].querySelector("summary"); if (s) s.focus(); }
    }
  });
  doc.addEventListener("click", function (e) {
    if (wide.matches) return;
    var inMenu = e.target.closest ? e.target.closest("details[data-aj-menu]") : null;
    if (!inMenu) { closeNarrow(null); return; }
    if (e.target.closest("a[href]")) inMenu.open = false;
  });
  // Desktop: a closed menu must not be toggled shut by its (hidden) summary.
  doc.addEventListener("toggle", function (e) {
    var d = e.target;
    if (d && d.matches && d.matches("details[data-aj-menu]") && wide.matches && !d.open) d.open = true;
  }, true);

  // ---- theme switch ----
  var THEME_KEY = "aj.theme";
  var ORDER = ["auto", "light", "dark"];
  var LABEL = {
    zh: { auto: "跟随系统", light: "浅色", dark: "深色" },
    en: { auto: "Auto", light: "Light", dark: "Dark" }
  };
  function getTheme() {
    var t = doc.documentElement.getAttribute("data-theme");
    return t === "light" || t === "dark" ? t : "auto";
  }
  function setTheme(t) {
    if (t === "auto") doc.documentElement.removeAttribute("data-theme");
    else doc.documentElement.setAttribute("data-theme", t);
    try { if (t === "auto") localStorage.removeItem(THEME_KEY); else localStorage.setItem(THEME_KEY, t); } catch (e) { /* blocked storage */ }
    renderTheme();
  }
  function lang() { return window.AJLang ? window.AJLang.get() : "zh"; }
  function renderTheme() {
    var nodes = doc.querySelectorAll("[data-aj-theme-switch]");
    var t = getTheme();
    for (var i = 0; i < nodes.length; i++) {
      nodes[i].textContent = LABEL[lang()][t];
      nodes[i].setAttribute("data-theme-state", t);
    }
  }
  doc.addEventListener("click", function (e) {
    var b = e.target.closest ? e.target.closest("[data-aj-theme-switch]") : null;
    if (!b) return;
    setTheme(ORDER[(ORDER.indexOf(getTheme()) + 1) % ORDER.length]);
  });
  if (window.AJLang) window.AJLang.onChange(renderTheme);

  function boot() { syncMenus(); renderTheme(); }
  if (doc.readyState === "loading") doc.addEventListener("DOMContentLoaded", boot); else boot();
})();
