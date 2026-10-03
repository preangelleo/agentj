/* Agent J — lang.js: the ONE language runtime for every surface (site, dashboard, web client, agentj admin).
   Load synchronously in <head>, after <title>/<meta name=description>, before the stylesheets:
     <script src="/brand/lang.js"></script>
   CSP-safe: no inline script, no eval, no innerHTML. Works with script-src 'self'.

   Resolution: ?lang=zh|en (then persisted) → localStorage "aj.lang" → default zh.
   Sets <html lang> + data-lang immediately (no flash). base.css hides [data-l] elements of the other language.
   Also pre-applies an explicit theme from localStorage "aj.theme" (light|dark) as html[data-theme].

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

  var LABELS = { zh: "中文", en: "EN" };
  var SWITCH_LABEL = { zh: "语言", en: "Language" };

  function renderSwitches(lang) {
    if (!doc || !doc.querySelectorAll) return;
    var nodes = doc.querySelectorAll("[data-aj-lang-switch]");
    for (var i = 0; i < nodes.length; i++) renderSwitch(nodes[i], lang);
  }
  function renderSwitch(node, lang) {
    if (!node.getAttribute("data-aj-ready")) {
      while (node.firstChild) node.removeChild(node.firstChild);
      ["zh", "en"].forEach(function (l, idx) {
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
      if (!node.classList.contains("aj-lang")) node.classList.add("aj-lang");
      node.setAttribute("role", "group");
      node.setAttribute("data-aj-ready", "1");
    }
    node.setAttribute("aria-label", SWITCH_LABEL[lang]);
    var btns = node.querySelectorAll("[data-aj-lang]");
    for (var j = 0; j < btns.length; j++) {
      btns[j].setAttribute("aria-pressed", btns[j].getAttribute("data-aj-lang") === lang ? "true" : "false");
    }
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
  }
})(typeof window !== "undefined" ? window : globalThis);
