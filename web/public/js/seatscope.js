/* P545 (FEEDBACK #309, PROTOCOL §40): m.agentj.app/<seat id>/ is one seat's own page. Runs first in <head>, before
   brand/lang.js, so every later read of localStorage on a seat's page — this page's own modules and the brand scripts —
   reads that seat's keys ("aj.lang@<seat id>"): two seats opened in one browser never share a setting, a pairing marker
   or an install id. IndexedDB is split the same way by name (js/store.js: "agentjarvis@<seat id>"). The root page
   (m.agentj.app/) is unchanged: its keys and its database keep their old names, so a phone paired there before keeps
   working. A seat's first visit starts from the root page's display preferences (copied, never moved). */
(function () {
  "use strict";
  var m = /^\/([a-z2-7]{8})\//.exec(location.pathname);
  var seat = m ? m[1] : "";
  var suffix = "@" + seat;
  var raw = null;
  try { raw = window.localStorage; } catch (e) { raw = null; }   // blocked storage: every read keeps failing as before
  var PREFS = ["aj.lang", "aj.theme", "aj.fontScale", "aj.readerFs", "aj.toolOrder", "aj.sfxVolume", "aj.shotClip", "aj.pageFz"];
  function scoped(store) {
    function own(k) { return String(k) + suffix; }
    function names() {
      var out = [];
      for (var i = 0; i < store.length; i++) {
        var k = store.key(i);
        if (k && k.length > suffix.length && k.slice(-suffix.length) === suffix) out.push(k.slice(0, -suffix.length));
      }
      return out;
    }
    var api = {
      getItem: function (k) { return store.getItem(own(k)); },
      setItem: function (k, v) { store.setItem(own(k), String(v)); },
      removeItem: function (k) { store.removeItem(own(k)); },
      key: function (i) { var n = names(); return i >= 0 && i < n.length ? n[i] : null; },
      clear: function () { names().forEach(function (k) { store.removeItem(own(k)); }); }
    };
    Object.defineProperty(api, "length", { get: function () { return names().length; } });
    return Object.freeze(api);
  }
  if (seat && raw) {
    try {
      if (raw.getItem("aj.seatInit" + suffix) === null) {
        PREFS.forEach(function (k) { var v = raw.getItem(k); if (v !== null && raw.getItem(k + suffix) === null) raw.setItem(k + suffix, v); });
        raw.setItem("aj.seatInit" + suffix, "1");
      }
    } catch (e) { /* quota / blocked: the seat starts from the defaults */ }
    var view = scoped(raw);
    try { Object.defineProperty(window, "localStorage", { configurable: true, enumerable: true, get: function () { return view; } }); } catch (e) { /* not replaceable: see AJSeat.scoped */ }
  }
  window.AJSeat = Object.freeze({
    id: seat,                                    // "" on the root page
    base: seat ? "/" + seat + "/" : "/",
    db: function (name) { return seat ? name + suffix : name; },
    root: raw,                                   // the origin's own localStorage: only js/seats.js reads it (the seat list)
    scoped: !seat || !raw || window.localStorage !== raw,
    /** The seat's Home Screen name and passkey hint for its manifest URL (worker.ts seatManifest): "?n=…&c=…" or "". */
    query: function () {
      if (!seat || !raw) return "";
      var q = [], n = null, c = null;
      try { n = raw.getItem("aj.seatName" + suffix); c = raw.getItem("aj.seatPk" + suffix); } catch (e) { /* blocked */ }
      if (n) q.push("n=" + encodeURIComponent(n));
      if (c && /^[A-Za-z0-9_-]{16,512}$/.test(c)) q.push("c=" + c);
      return q.length ? "?" + q.join("&") : "";
    },
    name: function () { try { return (seat && raw && raw.getItem("aj.seatName" + suffix)) || ""; } catch (e) { return ""; } }
  });
})();
