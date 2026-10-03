# web — the Agent J web client (`m.agentj.app`, public but not indexed; legacy `alpha-web.agentjarvis.net`)

Device side of `../protocol/PROTOCOL.md` §2–§4 and §8–§9: pair with a QR / link (IKpsk2 + 6-digit code typed on the host), resume (IK),
chat with the host's Agent (status pill, approval cards signed with a non-extractable Ed25519 key), content-free Web Push.
One host only (N=1). Tier: **"tampering would be detectable"**, not zero-access (badge on every screen; see
https://agentj.app/security/).

| Path | What |
|---|---|
| `public/index.html` `app.js` `app.css` | the client (ES module, no deps, textContent only, no third-party resources); layout only in `app.css`, everything else from the design system |
| `public/brand/…` + `public/favicon.ico` `apple-touch-icon.png` | **build copies** of `../brand/` (set `web`: tokens, base.css, `lang.js`, `ui.js`, self-hosted fonts, shield + PWA + status logos); never edit here |
| `i18n/web.zh.src.json` → `web.zh.json` | Chinese UI text: write the source, run `python3 agentjarvis/i18n/polish.py --surface web web/i18n/` (sinify; cache `../i18n/cache/web.json`, log `../i18n/log/web.md`, commit both); `web.zh.override.json` = human fixes that win over the polish |
| `i18n/web.en.json` | English UI text, written on its own (same keys and `{placeholders}`); wording rules in `../i18n/TERMS.md` |
| `public/i18n.js` | **build output**: both dictionaries as an ES module (CSP: the page cannot fetch JSON). Runtime = `window.AJLang` (`brand/lang.js`): 中文 default, `?lang=en` or the switch (pairing screen + menu), saved in `localStorage["aj.lang"]`, `<html lang>` follows; `` `code` `` in dictionary text renders as `<code>` |
| `public/sw.js` | service worker: push display only (「有新回复」/「有一个请求等你批准」, English when the page registered `sw.js?lang=en`), no fetch handler, no cache (every load comes from the network, so the version hash stays the whole story); `SW_VERSION` is bumped with each shell redesign so phones take the new worker at once |
| `public/manifest.webmanifest` | install to home screen (iOS needs it for push): name "Agent J", icons `brand/img/icon-{192,512}.png` + `icon-512-maskable.png`, colours = tokens (Lotion; `theme-color` meta is Sooty in dark) |
| `public/proto/{noise,wire}.js` | **build copies** of `../protocol/*.js`; never edit here |
| `public/version.json` / `version.js` | SHA-256 per shipped file + `combined`; `version.js` is the same manifest as a module (CSP `connect-src` allows only the relay, so the page cannot fetch JSON) |
| `build.mjs` | copies proto + brand, writes `i18n.js`, syncs the Chinese text inside `[data-i18n]` / `[data-i18n-attr]` of `index.html`, writes the manifest; `--print-hash` prints `combined` from the sources without writing. In the public export (web/ without `../brand`) the committed `public/brand/` copy is the source — same bytes, same hash |
| `worker.ts` + `wrangler.toml` | Worker `agentjarvis-web` (no imports) on `WEB_HOST` + `LEGACY_WEB_HOST`: any other host / non-GET/HEAD / malformed relay URL → 404; CSP `connect-src` = `RELAY_URL` + `LEGACY_RELAY_URL` exactly, security headers (`webHeaders()`), `noindex` |
| `test/serve.mjs` | `startWebServer({port:0, relayCsp:'ws://127.0.0.1:*'})` → `{url, port, stop}`, same headers as the Worker |
| `test/fakehost.mjs` | JS fake relay+host (responder, real noise.js) used by `screens.mjs`: `send(appMsg)`, `answer(t, fn)` canned replies for memory / activity / tasks / stop (signatures NOT checked — the real host does that); not a relay implementation |

## Build & test
```bash
python3 agentjarvis/i18n/polish.py --surface web web/i18n/   # after changing web.zh.src.json (needs sinify)
node web/build.mjs                    # after any change in public/, i18n/, brand/ or protocol/
node --test web/test/*.test.mjs       # static rules, proto + brand byte-identity, i18n (keys, placeholders,
                                                  # polish --check, no internal terms from i18n/glossary.json), manifest, Worker
node web/test/screens.mjs             # own headless Chromium (never :9222): flow checks + every screen at 360×800 /
                                                  # 1440×900 × light/dark × 中文/English, audited (overflow, tap ≥ 44 px, console
                                                  # errors, off-origin requests) + language persistence; shots → /tmp/aj-web-shots/
                                                  # (AJ_QUICK=1: two combinations only)
```

Design: phone first (one centred column, `--col`, wider on desktop), header = status shield (`brand/img/status/*`: idle green,
working blue, waiting-for-you orange, pairing purple, offline/stopped grey) + Agent name/state + the security badge + menu
(language, theme, 「这个网页版有多安全」, links to agentj.app docs / security / privacy / terms). The whole page is washed
lightly with the Agent state (`body[data-agent]`, `body[data-conn]`). Primary = Sooty; shield green only as an accent.
「添加到主屏幕」 hint (`#a2hs`): phone browsers only, not when installed, dismissed once (`localStorage["aj.a2hs"]`). iPhone Safari
has no in-page QR scanner: there the scan button is hidden and the pairing steps say to add the page to the Home Screen first
and paste the link from `agentj pair --link` (a Camera-app scan would pair the Safari tab, which has separate storage).

## Driving it (e2e)
States in `#status[data-state]` (mirror `window.__ajState`): `idle connecting waiting-host pairing awaiting-approval ready revoked error`.
Pair: open `<url>#p=…` (or fill `#pair-link`, click `#pair-go`) → `awaiting-approval`, 6 digits in `#sas` → host approves → `ready`.
Chat: `#msg-input` + Enter or `#send`; `#messages li[data-dir=in|out][data-from=agent|host|you|device|notice]`.
Agent: `#agent-status[data-s=idle|working|compacting|waiting|down|stopped]` (text 「<Agent> · <state>」 while connected); cards `#messages li.ask` (`.ask-summary`, `.ask-allow`, `.ask-deny`,
`[data-result=allow|deny|timeout|gone]`); push `#push-row` / `#push-on`. Revoked: `#repair` (clears host, keeps device key).
Error: `#retry`. Local relays must be `ws://127.0.0.1:<port>` (what `parsePairing` accepts besides `wss://`).
Agent name: `#brand-name` (line 1) + `document.title` = the `name` of the host's `{"t":"status"}` (cleaned, ≤ 32 code points,
textContent), `Agent J` when null / absent; cached as `name` in the IndexedDB `host` record for cold starts.

## Hosts (rename to agentj.app, one version cycle of overlap)
- Relays: `allowRelay()` accepts exactly `wss://relay.agentj.app` and `wss://alpha-relay.agentjarvis.net` (pairing links from
  hosts not yet updated carry the old one; the same relay Worker answers on both and the channel meets either way).
- `alpha-web.agentjarvis.net` serves the same page. Before anything else `main()` checks that origin's IndexedDB
  (`agentjarvis`, name unchanged on purpose): no approved `host` record → `location.replace('https://m.agentj.app' + path +
  query + fragment)` (a `#p=` link arrives whole); a pairing → the phone keeps working there. Tested in `screens.mjs` §5
  through CDP Fetch interception (no network).

## Verify the deployed page (anyone)
`node web/build.mjs --print-hash` on the public source must equal the hash in the badge panel; stricter: download each
served file and compare with `version.json`.

## Deploy (CEO only)
`node build.mjs`, the tests, then `wrangler deploy` from this directory with your own account (`account_id` in `wrangler.toml`).
Verify: `curl -sI https://m.agentj.app/` → 200 with `x-robots-tag: noindex, nofollow, noarchive`, and the CSP's
`connect-src` naming only the two relays; the same on `https://alpha-web.agentjarvis.net/`.
