/* Agent J — lang.js: the ONE language runtime for every surface (site, dashboard, web client, agentj admin).
   Load synchronously in <head>, after <title>/<meta name=description>, before the stylesheets:
     <script src="/brand/lang.js"></script>
   CSP-safe: no inline script, no eval, no innerHTML. Works with script-src 'self'.

   Resolution: ?lang=zh|en (then persisted) → localStorage "aj.lang" → default zh.
   Sets <html lang> + data-lang immediately (no flash). base.css hides [data-l] elements of the other language.
   Also pre-applies an explicit theme from localStorage "aj.theme" (light|dark) as html[data-theme].

   Switch: [data-aj-lang-switch] in a page header → a round globe button + menu (中文 / English, current one ticked;
   arrows / Home / End / Esc, closes on outside press); elsewhere (footers, pairing page) → the 「中文 / EN」 pair.
   Force either with data-aj-lang-switch="globe" | "pair".

   API: window.AJLang = { get(), set(lang), t(dict, key, vars), onChange(cb) → unsubscribe, link(url) → url }
*/
(function (root) {
  "use strict";
  var KEY = "aj.lang";
  var THEME_KEY = "aj.theme";
  var LANGS = { zh: "zh-CN", en: "en" };
  var DEFAULT = "zh";
  // Our hosts: localStorage is per origin, so links between them carry ?lang=.
  var OUR_HOST = /(^|\.)(agentj\.app|agentjarvis\.net)$/i;   // the second domain = the legacy hosts (one version cycle)

  var doc = root.document;
  var listeners = [];
  var current = DEFAULT;

  function norm(v) {
    if (typeof v !== "string") return null;
    v = v.trim().toLowerCase();
    if (v === "zh" || v === "zh-cn" || v === "zh-hans" || v === "cn") return "zh";
    if (v === "en" || v.indexOf("en-") === 0) return "en";
    return null;
  }

  function storageGet(k) {
    try { return root.localStorage ? root.localStorage.getItem(k) : null; } catch (e) { return null; }
  }
  function storageSet(k, v) {
    try { if (!root.localStorage) return false; root.localStorage.setItem(k, v); return true; } catch (e) { return false; }
  }

  function urlParam() {
    try {
      var s = root.location && root.location.search;
      if (!s) return null;
      var m = /[?&]lang=([^&#]*)/.exec(s);
      return m ? norm(decodeURIComponent(m[1])) : null;
    } catch (e) { return null; }
  }

  function resolve() {
    var fromUrl = urlParam();
    if (fromUrl) { storageSet(KEY, fromUrl); return fromUrl; }
    var stored = norm(storageGet(KEY));
    return stored || DEFAULT;
  }

  // Keep ?lang= in the address bar in sync when it is present, or when storage is unavailable
  // (so a reload keeps the choice). Never adds history entries.
  function syncUrl(lang, storageOk) {
    try {
      if (!root.history || !root.history.replaceState || !root.location) return;
      var has = /[?&]lang=/.test(root.location.search || "");
      if (!has && storageOk) return;
      var u = new root.URL(root.location.href);
      u.searchParams.set("lang", lang);
      root.history.replaceState(root.history.state, "", u.pathname + u.search + u.hash);
    } catch (e) { /* ignore */ }
  }

  function applyRoot(lang) {
    var html = doc && doc.documentElement;
    if (!html) return;
    html.setAttribute("lang", LANGS[lang]);
    html.setAttribute("data-lang", lang);
  }

  function applyTheme() {
    var t = storageGet(THEME_KEY);
    if (t === "light" || t === "dark") doc.documentElement.setAttribute("data-theme", t);
  }

  // <title data-zh data-en>, <meta name=description data-zh data-en>,
  // and any element with data-aj-attrs="placeholder aria-label" + data-zh-placeholder / data-en-placeholder …
  function applyText(lang) {
    if (!doc || !doc.querySelector) return;
    var title = doc.querySelector("title[data-" + lang + "]");
    if (title) doc.title = title.getAttribute("data-" + lang);
    var desc = doc.querySelectorAll('meta[name="description"][data-' + lang + "], meta[property][data-" + lang + "]");
    for (var i = 0; i < desc.length; i++) desc[i].setAttribute("content", desc[i].getAttribute("data-" + lang));
    var els = doc.querySelectorAll("[data-aj-attrs]");
    for (var j = 0; j < els.length; j++) {
      var names = els[j].getAttribute("data-aj-attrs").split(/[\s,]+/);
      for (var k = 0; k < names.length; k++) {
        if (!names[k]) continue;
        var v = els[j].getAttribute("data-" + lang + "-" + names[k]);
        if (v !== null) els[j].setAttribute(names[k], v);
      }
    }
  }

  var LABELS = { zh: "中文", en: "EN" };             // the pair's short labels (footers, the phone's pairing page)
  var NAMES = { zh: "中文", en: "English" };        // the globe menu: each language in its own script (add 日本語 here)
  var SWITCH_LABEL = { zh: "语言", en: "Language" };
  var MENU_LABEL = "语言 · Language";               // the globe button: readable whichever language is on screen
  var SVG_NS = "http://www.w3.org/2000/svg";
  var menuSeq = 0;
  var openMenu = null;                              // the one open globe menu (its switch node), or null

  // Style: data-aj-lang-switch="globe" | "pair"; empty = globe inside a page header, pair elsewhere.
  function styleOf(node) {
    var v = node.getAttribute("data-aj-lang-switch");
    if (v === "globe" || v === "pair") return v;
    return node.closest && node.closest(".aj-header, header") ? "globe" : "pair";
  }

  function renderSwitches(lang) {
    if (!doc || !doc.querySelectorAll) return;
    var nodes = doc.querySelectorAll("[data-aj-lang-switch]");
    for (var i = 0; i < nodes.length; i++) {
      if (styleOf(nodes[i]) === "globe") renderGlobe(nodes[i], lang); else renderSwitch(nodes[i], lang);
    }
  }
  function renderSwitch(node, lang) {
    if (!node.getAttribute("data-aj-ready")) {
      while (node.firstChild) node.removeChild(node.firstChild);
      api.langs.forEach(function (l, idx) {
        if (idx) {
          var sep = doc.createElement("span");
          sep.className = "aj-lang__sep"; sep.setAttribute("aria-hidden", "true"); sep.textContent = "/";
          node.appendChild(sep);
        }
        var b = doc.createElement("button");
        b.type = "button";
        b.setAttribute("data-aj-lang", l);
        b.setAttribute("lang", LANGS[l]);
        b.textContent = LABELS[l];
        b.addEventListener("click", function () { api.set(l); });
        node.appendChild(b);
      });
      node.classList.add("aj-lang", "aj-lang--pair");
      node.setAttribute("role", "group");
      node.setAttribute("data-aj-ready", "1");
    }
    node.setAttribute("aria-label", SWITCH_LABEL[lang]);
    var btns = node.querySelectorAll("[data-aj-lang]");
    for (var j = 0; j < btns.length; j++) {
      btns[j].setAttribute("aria-pressed", btns[j].getAttribute("data-aj-lang") === lang ? "true" : "false");
    }
  }

  function svg(cls, paths) {
    var el = doc.createElementNS(SVG_NS, "svg");
    el.setAttribute("class", cls); el.setAttribute("viewBox", "0 0 24 24");
    el.setAttribute("aria-hidden", "true"); el.setAttribute("focusable", "false");
    paths.forEach(function (d) {
      var p = doc.createElementNS(SVG_NS, d[0]);
      for (var k in d[1]) if (Object.prototype.hasOwnProperty.call(d[1], k)) p.setAttribute(k, d[1][k]);
      el.appendChild(p);
    });
    return el;
  }
  var GLOBE = [["circle", { cx: "12", cy: "12", r: "9" }], ["path", { d: "M3 12h18" }],
    ["path", { d: "M12 3c2.5 2.6 3.8 5.6 3.8 9s-1.3 6.4-3.8 9c-2.5-2.6-3.8-5.6-3.8-9S9.5 5.6 12 3z" }]];
  var CHECK = [["path", { d: "M5 12.5l4.5 4.5L19 7.5" }]];

  function menuItems(node) { return node.querySelectorAll('[role="menuitemradio"]'); }
  function setOpen(node, open, focusTo) {
    var btn = node.querySelector(".aj-lang__button"), menu = node.querySelector(".aj-lang__menu");
    if (!btn || !menu) return;
    if (open && openMenu && openMenu !== node) setOpen(openMenu, false);
    menu.hidden = !open;
    btn.setAttribute("aria-expanded", open ? "true" : "false");
    openMenu = open ? node : (openMenu === node ? null : openMenu);
    if (open) {
      var items = menuItems(node), pick = items[0];
      for (var i = 0; i < items.length; i++) if (items[i].getAttribute("aria-checked") === "true") pick = items[i];
      if (focusTo === "last") pick = items[items.length - 1];
      if (pick) pick.focus();
    } else if (focusTo === "button") btn.focus();
  }
  function onMenuKey(node, ev) {
    var items = Array.prototype.slice.call(menuItems(node));
    var at = items.indexOf(doc.activeElement);
    var k = ev.key;
    if (k === "Escape") { ev.preventDefault(); setOpen(node, false, "button"); return; }
    if (k === "Tab") { setOpen(node, false); return; }
    var next = k === "ArrowDown" ? at + 1 : k === "ArrowUp" ? at - 1 : k === "Home" ? 0 : k === "End" ? items.length - 1 : null;
    if (next === null) return;
    ev.preventDefault();
    items[(next + items.length) % items.length].focus();
  }

  function renderGlobe(node, lang) {
    if (!node.getAttribute("data-aj-ready")) {
      while (node.firstChild) node.removeChild(node.firstChild);
      var id = "aj-lang-menu-" + (++menuSeq);
      var btn = doc.createElement("button");
      btn.type = "button";
      btn.className = "aj-lang__button";
      btn.setAttribute("aria-haspopup", "menu");
      btn.setAttribute("aria-expanded", "false");
      btn.setAttribute("aria-controls", id);
      btn.setAttribute("aria-label", MENU_LABEL);
      btn.setAttribute("title", MENU_LABEL);
      btn.appendChild(svg("aj-lang__globe", GLOBE));
      var menu = doc.createElement("div");
      menu.className = "aj-lang__menu";
      menu.id = id;
      menu.setAttribute("role", "menu");
      menu.setAttribute("aria-label", MENU_LABEL);
      menu.hidden = true;
      api.langs.forEach(function (l) {
        var it = doc.createElement("button");
        it.type = "button";
        it.className = "aj-lang__item";
        it.setAttribute("role", "menuitemradio");
        it.setAttribute("data-aj-lang", l);
        it.setAttribute("lang", LANGS[l]);
        var name = doc.createElement("span");
        name.textContent = NAMES[l];
        it.appendChild(name);
        it.appendChild(svg("aj-lang__check", CHECK));
        it.addEventListener("click", function () { setOpen(node, false, "button"); api.set(l); });
        menu.appendChild(it);
      });
      btn.addEventListener("click", function () { setOpen(node, menu.hidden); });
      btn.addEventListener("keydown", function (ev) {
        if (ev.key === "ArrowDown" || ev.key === "ArrowUp") { ev.preventDefault(); setOpen(node, true, ev.key === "ArrowUp" ? "last" : null); }
        else if (ev.key === "Escape" && !menu.hidden) { ev.preventDefault(); setOpen(node, false, "button"); }
      });
      menu.addEventListener("keydown", function (ev) { onMenuKey(node, ev); });
      node.addEventListener("focusout", function (ev) {
        if (!menu.hidden && ev.relatedTarget && !node.contains(ev.relatedTarget)) setOpen(node, false);
      });
      node.appendChild(btn);
      node.appendChild(menu);
      node.classList.add("aj-lang", "aj-lang--globe");
      node.removeAttribute("role");
      node.setAttribute("data-aj-ready", "1");
    }
    var items = menuItems(node);
    for (var j = 0; j < items.length; j++) {
      items[j].setAttribute("aria-checked", items[j].getAttribute("data-aj-lang") === lang ? "true" : "false");
    }
  }

  // A press anywhere outside the open globe menu closes it.
  function onOutside(ev) {
    if (openMenu && ev.target && !openMenu.contains(ev.target)) setOpen(openMenu, false);
  }

  function isOurCrossOrigin(u) {
    try {
      if (!OUR_HOST.test(u.hostname)) return false;
      return !root.location || u.host !== root.location.host;
    } catch (e) { return false; }
  }

  function link(url, lang) {
    lang = lang || current;
    try {
      var base = root.location && root.location.href ? root.location.href : "https://agentj.app/";
      var u = new root.URL(String(url), base);
      if (!isOurCrossOrigin(u)) return String(url);
      u.searchParams.set("lang", lang);
      return u.toString();
    } catch (e) { return String(url); }
  }

  // Cross-origin links to our hosts get ?lang= at click time (works for app-rendered links too).
  function onNavigate(ev) {
    var a = ev.target && ev.target.closest ? ev.target.closest("a[href]") : null;
    if (!a) return;
    var href = a.getAttribute("href");
    var next = link(href);
    if (next !== href) a.setAttribute("href", next);
  }

  function apply(lang, fire) {
    current = lang;
    applyRoot(lang);
    applyText(lang);
    renderSwitches(lang);
    if (fire) {
      for (var i = 0; i < listeners.length; i++) {
        try { listeners[i](lang); } catch (e) { if (root.console) root.console.error(e); }
      }
      try { doc.dispatchEvent(new root.CustomEvent("aj:langchange", { detail: { lang: lang } })); } catch (e) { /* old browsers */ }
    }
  }

  function fmt(s, vars) {
    if (!vars) return s;
    return String(s).replace(/\{(\w+)\}/g, function (m, k) {
      return Object.prototype.hasOwnProperty.call(vars, k) ? String(vars[k]) : m;
    });
  }

  var api = {
    langs: ["zh", "en"],
    get: function () { return current; },
    set: function (lang) {
      var l = norm(lang);
      if (!l) return current;
      var ok = storageSet(KEY, l);
      syncUrl(l, ok);
      if (l !== current) apply(l, true); else apply(l, false);
      return l;
    },
    // dict = { zh: { key: "…{n}…" }, en: { … } }  (en falls back to zh, then to the key)
    t: function (dict, key, vars) {
      var d = dict && dict[current];
      var s = d && Object.prototype.hasOwnProperty.call(d, key) ? d[key] : null;
      if (s === null && dict && dict.zh && Object.prototype.hasOwnProperty.call(dict.zh, key)) s = dict.zh[key];
      return fmt(s === null ? key : s, vars);
    },
    onChange: function (cb) {
      if (typeof cb !== "function") return function () {};
      listeners.push(cb);
      return function () { var i = listeners.indexOf(cb); if (i >= 0) listeners.splice(i, 1); };
    },
    link: function (url) { return link(url); }
  };

  // ---- boot (synchronous part: root attributes before first paint) ----
  current = resolve();
  if (doc && doc.documentElement) {
    applyRoot(current);
    try { applyTheme(); } catch (e) { /* ignore */ }
    try { applyText(current); } catch (e) { /* <title> above the script is swapped now, the rest on DOMContentLoaded */ }
  }
  root.AJLang = api;

  if (doc && doc.addEventListener) {
    var ready = function () { apply(current, false); };
    if (doc.readyState === "loading") doc.addEventListener("DOMContentLoaded", ready);
    else ready();
    doc.addEventListener("click", onNavigate, true);
    doc.addEventListener("auxclick", onNavigate, true);
    doc.addEventListener("contextmenu", onNavigate, true);
    doc.addEventListener("pointerdown", onOutside, true);
  }
})(typeof window !== "undefined" ? window : globalThis);
