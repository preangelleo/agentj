# web — the Agent J web client (`m.agentj.app`, public but not indexed; legacy `alpha-web.agentjarvis.net`)

Device side of `../protocol/PROTOCOL.md` §2–§4 and §8–§10: pair with a QR / link (IKpsk2 + 6-digit code typed on the host), resume (IK),
and — since PROMPT-33 — **relay's phone page** on that session (`../parity/DESIGN.md` §a): the whole screen is the Agent's state
colour, history pages (source card + reply card), reader, quote / excerpt, attachments and voice end to end (§10.3 / §10.9),
approval sheet (hold to approve, Ed25519-signed) and question cards, meters, model · effort pill, ≡ menu, keyboard shortcuts;
plus Agent J's pairing, security badge, language, theme, memory / activity / tasks, 全部停下, content-free Web Push.
One host only (N=1). Tier: **"tampering would be detectable"**, not zero-access (badge on every screen; see
https://agentj.app/security/).

| Path | What |
|---|---|
| `public/index.html` `app.css` | relay's markup + CSS (variables renamed where the design system already has the name; palette names mapped to tokens) + Agent J's screens; no inline style / script |
| `public/app.js` | boot: language, legacy-host redirect, pairing / SAS / removed / error views, Agent name, session hooks, the app-message switch |
| `public/js/*.js` | `relay.js` relay's page script (one module: its sections share state) · `api.js` the adapter (relay call → §10 message) · `snap.js` messages → relay's snapshot + history view · `session.js` Noise, frag, padding, pacing · `blobs.js` uploads · `wav.js` 16 kHz WAV · `speak.js` on-device read aloud · `store.js` IndexedDB + sealed drafts / input history · `controls.js` · `push.js` · `settings.js` (F13 Settings panel: pref_set, reply text size, update app, Add to Home Screen, read-only safety rows) · `md.js` (relay's Markdown, DOM only) · `t.js` · `ui.js` · `boot.js` (head, launch colour) |
| `public/assets/` | relay's rocket masks + whoosh (copied from `pwa/assets/`) |
| `public/brand/…` + `public/favicon.ico` `apple-touch-icon.png` | **build copies** of `../brand/` (set `web`: tokens, base.css, `lang.js`, `ui.js`, self-hosted fonts, shield + PWA + status logos); never edit here |
| `i18n/web.zh.src.json` → `web.zh.json` | Chinese UI text: write the source, run `python3 agentjarvis/i18n/polish.py --surface web web/i18n/ --model qwen/qwen3.6-flash` (sinify; since 0.15 the whole web dictionary is polished with this model and `static.test.mjs` checks with it; cache `../i18n/cache/web.json`, log `../i18n/log/web.md`, commit both); `web.zh.override.json` = human fixes that win over the polish |
| `i18n/web.en.json` | English UI text, written on its own (same keys and `{placeholders}`); wording rules in `../i18n/TERMS.md` |
| `public/i18n.js` | **build output**: both dictionaries as an ES module (CSP: the page cannot fetch JSON). Runtime = `window.AJLang` (`brand/lang.js`): 中文 default, `?lang=en` or the switch (pairing screen + menu), saved in `localStorage["aj.lang"]`, `<html lang>` follows; `` `code` `` in dictionary text renders as `<code>` |
| `public/sw.js` | service worker: push display only (「有新回复」/「有一个请求等你批准」, English when the page registered `sw.js?lang=en`), no fetch handler, no cache (every load comes from the network, so the version hash stays the whole story); `SW_VERSION` is bumped with each shell redesign so phones take the new worker at once |
| `public/manifest.webmanifest` | install to home screen (iOS needs it for push): name "Agent J", icons `brand/img/icon-{192,512}.png` + `icon-512-maskable.png`, colours = tokens (Lotion; `theme-color` meta is Sooty in dark) |
| `public/proto/{noise,wire}.js` | **build copies** of `../protocol/*.js`; never edit here |
| `public/version.json` / `version.js` | SHA-256 per shipped file + `combined`; `version.js` is the same manifest as a module (CSP `connect-src` allows only the relay, so the page cannot fetch JSON) |
| `build.mjs` | copies proto + brand, writes `i18n.js`, syncs the Chinese text inside `[data-i18n]` / `[data-i18n-attr]` of `index.html`, writes the manifest; `--print-hash` prints `combined` from the sources without writing. In the public export (web/ without `../brand`) the committed `public/brand/` copy is the source — same bytes, same hash |
| `worker.ts` + `wrangler.toml` | Worker `agentjarvis-web` (no imports) on `WEB_HOST` + `LEGACY_WEB_HOST`: any other host / non-GET/HEAD / malformed relay URL → 404; CSP `connect-src` = `RELAY_URL` + `LEGACY_RELAY_URL` exactly, security headers (`webHeaders()`), `noindex` |
| `test/serve.mjs` | `startWebServer({port:0, relayCsp:'ws://127.0.0.1:*'})` → `{url, port, stop}`, same headers as the Worker |
| `test/fakehost.mjs` | JS fake relay+host (responder, real noise.js) speaking §8 + §10 (say, blobs + WAV check, hist_* + frag, question, meter, models, menu, say_cancel, slash); verifies answer signatures with wire.js; `p33:false` = an older host; not a relay implementation |
| `test/browser.mjs` | own headless Chromium (never :9222), fake camera + microphone, page helpers |
| `test/parity_cases.mjs` | one named case per `../parity/features.json` id (pwa + agentj-only), driven by `screens.mjs` |
| `test/sidebyside.mjs` | relay's `pwa/index.html` (stub endpoints) next to this client, 10 states → `reports/qa/parity/shots/` |
| `test/settings.mjs` | F13 Settings + density cases (parity format) against `fakehost.mjs` (answers `pref_set` per C6, `prefSet:false` = a host before 0.15) → `reports/qa/release-0.15/settings-*.png` |
| `test/density_shots.mjs` | `node … density_shots.mjs <tag>` → `reports/qa/release-0.15/density-<tag>.{desktop,mobile}.png` + the measured geometry (JSON) |

## Build & test
```bash
python3 agentjarvis/i18n/polish.py --surface web web/i18n/ --model qwen/qwen3.6-flash   # after changing web.zh.src.json
node web/build.mjs                    # after any change in public/, i18n/, brand/ or protocol/
node --test web/test/*.test.mjs       # static rules, proto + brand byte-identity, i18n (keys, placeholders,
                                                  # polish --check, no internal terms from i18n/glossary.json), manifest, Worker
node web/test/screens.mjs             # own headless Chromium (never :9222): §0 security regressions (P33-X01 / X02;
                                                  # AJ_SECURITY_ONLY=1 alone) + flow checks + every screen at 360×800 /
                                                  # 1440×900 × light/dark × 中文/English, audited (overflow, tap ≥ 44 px, console
                                                  # errors, off-origin requests) + language persistence + 160 parity cases;
                                                  # shots → /tmp/aj-web-shots/, results → reports/qa/parity/web-web_test_screens.mjs.json (parity format)
                                                  # (AJ_QUICK=1 two gallery combos · AJ_PARITY_ONLY=id,… · AJ_NO_PARITY=1)
node web/test/sidebyside.mjs          # relay vs Agent J screenshots (reports/qa/parity/shots/*-pair.png)
node web/test/settings.mjs            # F13 Settings + density (AJ_SETTINGS_ONLY=case,… runs a subset)
```

Design: relay's phone page (its ADR-033 … 052): the page IS the state colour (idle green, working blue with a light running along
the top, waiting orange with breathing edges, question purple, offline / stopped grey; dark theme = the same hues deepened), the
water level = context window, the two grey rules = weekly / 5-hour quota. Agent J's pages (pairing, …) are the brand canvas.
Storage: localStorage holds only `aj.a2hs`, `aj.readerFs`, `aj.chrome`, `aj.fontScale` (Settings → reply text size, 0.8 … 1.6) (+ lang / theme from brand/lang.js); drafts and the ↑↓
history are AES-GCM sealed in IndexedDB (`draft`, `ihist`; key `local`, non-extractable, in the same IndexedDB — it stops a
casual read of the stored files, not code running in this origin or a copied browser profile) and wiped, with the key, by
重新配对 / 解除配对 / a new pairing and as soon as the computer revokes the phone.
Sends are bound to one session generation and one computer (P33-X01 / X02): nothing queued before a reconnect is sent after
it (unsent words stay in the field; uploads resume after `ready` to the same computer only); unpair / re-pair / revoke drop
every upload, queued send, the tray and the field. Markdown (`md.js`) is bounded: tables ≤ 32 × 300 / 4 000 cells, ≤ 20 000
elements; links https / http / mailto only, with the real host shown when the words differ.

## Driving it (e2e) — relay's ids
States: `window.__ajState` / `#status[data-state]` = `idle connecting waiting-host pairing awaiting-approval ready revoked error`;
`body[data-view]` = `pair sas chat mem act tasks revoked error`; `body[data-status]` = relay's `idle working waiting unknown`
(+ `data-kind=question`), `body[data-agent]` = the host's status, `body[data-conn]` = `on|off`, `body[data-sheet]` = `1` when the
sheet is up.
Pair: open `<url>#p=…` (or `#pair-link` + `#pair-go`) → 6 digits in `#sas` → host approves → `ready`. Removed: `#repair`. Error: `#retry`.
Pages: `#deck` · source card `#om[data-src=leo|dev|host|agent|sys|task|cmd]` (`#omLabel`, `#omText`, `#omQuote`, `#omMore`,
`#omAtt`) · reply card `#rm` (`#words`, `#agent` = the Agent name, `#rmTime`, `#pg` "n / total", `#cmdx`, `#localNote`) · actions
`#readBtn #speakBtn #fwdBtn #copyReply #replyBtn` · landscape `#pgPrev #pgNext` · reader `#rd` (`#rdSlider #rdPlus #rdMinus #rdClose`).
Composer: `#input` + Enter or `#send`; `#sayCancel`; `#clr`; tools `#tDoc #tPhoto #tCamera #mic` (inputs `#fDoc #fPhoto #fCamera`);
tray `#tray .chip[data-st=up|ready|held|failed]`; quote strip `#qbar` (`#qbJump #qbX`); grants `#grant-bar` / `#grant-off`;
≡ `#slashBtn` → `#menu` (`[data-run]` one-tap commands, `[data-estop]`, `[data-slash]` insert); `/` list `#sug`; voice bubble `#ptt`.
Sheet: `#sheet` (`#sheetTitle #tool #cmd #why #askTags #askLeft #apprDeny #apprAllow #apprBatch #qs .opt #askSend #askCancel
#sheetClose`), `#pendTag`. Header: `#orb` (type size), `#meta` (`#metaModel #metaEffort`), `#mWeek #m5h #water`, `#badge`,
`#setBtn` → `#settings` (F13: `#set-close #set-back`, `[data-set-lang]`, `[data-set-theme]`, switches `#set-speak #set-wake`, `#set-font-minus #set-font-plus #set-font-val #set-font-reset`, `#set-push`, `#set-refresh`, `#set-a2hs` / iPhone guide `#set-ios`, `#keysBtn` → `#keys`, `#set-computer #set-phones #set-unpair`, read-only `#set-risk(-cmd) #set-mode(-cmd)`, `#set-status`; keys `s`, ⌘, / Ctrl+,), `#aj-menu` (`#brand-name #open-mem #open-act #open-tasks #push-row #push-on #menu-about #unpair`).
Agent J: `#estop-banner` / `#resume`, `#confirm` (`#confirm-yes #confirm-no`), `#badge-panel` / `#version-hash`.
Local relays must be `ws://127.0.0.1:<port>` (what `parsePairing` accepts besides `wss://`).

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
