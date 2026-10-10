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
| `public/js/*.js` | `relay.js` relay's page script (one module: its sections share state) · `api.js` the adapter (relay call → §10 message) · `snap.js` messages → relay's snapshot + history view · `session.js` Noise, frag, padding, pacing · `blobs.js` uploads · `wav.js` 16 kHz WAV · `speak.js` on-device read aloud · `store.js` IndexedDB + sealed drafts / input history · `controls.js` · `push.js` · `settings.js` (F13 Settings panel: pref_set, reply text size, update app, Add to Home Screen, read-only safety rows) · `md.js` (relay's Markdown, DOM only) · `t.js` · `ui.js` · `boot.js` (head, launch colour) · `friends.js` (0.16, PROTOCOL §17.7: the read-only `/friends` page — list, conversation, details / usage, my card, add by ID, policy groups — and the friend-request / friend-question cards in the sheet; no field sends text to a friend) · `qr.js` (QR encoder for the friend share link, byte mode level M, no library) |
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
| `worker.ts` + `wrangler.toml` | Worker `agentjarvis-web` (no imports) on `WEB_HOST` + `LEGACY_WEB_HOST`; page routes `SPA_PATHS` (`/friends`, exact) are served as `index.html`; any other host / non-GET/HEAD / malformed relay URL → 404; CSP `connect-src` = `RELAY_URL` + `LEGACY_RELAY_URL` exactly, security headers (`webHeaders()`), `noindex` |
| `test/serve.mjs` | `startWebServer({port:0, relayCsp:'ws://127.0.0.1:*'})` → `{url, port, stop}`, same headers as the Worker |
| `test/fakehost.mjs` | JS fake relay+host (responder, real noise.js) speaking §8 + §10 (say, blobs + WAV check, hist_* + frag, question, meter, models, menu, say_cancel, slash); verifies answer signatures with wire.js; `p33:false` = an older host; not a relay implementation |
| `test/browser.mjs` | own headless Chromium (never :9222), fake camera + microphone, page helpers |
| `test/parity_cases.mjs` | one named case per `../parity/features.json` id (pwa + agentj-only), driven by `screens.mjs` |
| `test/sidebyside.mjs` | relay's `pwa/index.html` (stub endpoints) next to this client, 10 states → `reports/qa/parity/shots/` |
| `test/settings.mjs` | F13 Settings + density cases (parity format) against `fakehost.mjs` (answers `pref_set` per C6, `prefSet:false` = a host before 0.15) → `reports/qa/release-0.15/settings-*.png` |
| `test/p71_friends.test.mjs` · `test/p71_friends.mjs` | 0.16 friends: Agent ID / mbox vs `protocol/vectors/peer-id.json`, `friendAnswerMessage`, the six control objects, QR → jsQR, `/friends` route · the page + both cards in Chromium against `fakehost.mjs` (`fake.friends(sampleFriends())`, signatures verified), audited shots → `reports/qa/p71/web/` |
| `public/js/scan.js` · `test/p73_scan.test.mjs` · `test/p73_scan_page.mjs` · `test/p73_scan_evidence.mjs` | F29 (ADR-A177) QR scanner: 1080p request, centre-square / smoothed / inverted / whole-frame tries, 10 s hint · compact pairing link + jsQR on simulated screen photos (`p73_scan_lib.mjs`) and the real iPhone screenshot (`fixtures/p73/`) · real page in Chromium with a fake 1080p camera → scan → pair, 10 s hint · old-vs-new grid → `reports/qa/p73/f29/` |
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
Friends (0.16): `/friends` (`#add=AJ-…` prefills and is stripped) → `#friends-view[data-fv=list|chat|detail|card|add|groups|group]` (`#fr-back #fr-title #fr-status`, `#fr-open-card #fr-open-add #fr-open-groups`, `#fr-list .fr-item[data-id]`, `#fr-pending`, `#fr-msgs .fr-msg--in|--out`, `#fr-open-detail #fr-group #fr-usage #fr-block #fr-delete`, `#fr-my-id #fr-qr #fr-discoverable #fr-owner #fr-intro #fr-card-save`, `#fr-add-id #fr-add-note #fr-add-go #fr-sent`, `#fr-groups .fr-gitem[data-id] #fr-group-new #fr-g-* #fr-g-save #fr-g-delete`); menu `#open-friends`; sheet `#frAsk` (`#frAskGroup #frAskAllow #frAskDeny` · `#frQSend #frQSkip #frQTell`).
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


## P102 history corners (phone web only)

The source-card header owns two 44px shortcuts: double-click / double-tap `#pgLatest` returns to the newest page (g); `#omFirst` returns to the first visible page, fetching older history (G). Without unread messages, a single click stays inert; Enter / Space provides keyboard and assistive activation. `history-corners.js` handles primary short tap pairs, rejects holds/movement/cancel/multiple pointers, and ignores compatibility dblclick after touch. Selection and double-tap zoom are suppressed only on the corners; message text stays selectable.

P102 supersedes P87’s floating new-reply button with the existing right-header page number: its fixed-size transparent capsule becomes a 1.2s smooth Sage Sensation (#b7e396) / Buttery (#fcbe7c) background pulse on unread replies, with dark digits and a 1px dark border. Reduced motion uses static sage. Unread accepts single or double click/tap; newest clears unread and restores transparent styling. Both normal corners remain inert on single click. The latest corner is also a no-op when already at latest, preserving expanded input and reading state. Double-click or a short primary touch/pen pair maps right to existing `toNewest`, left to existing `goEdge(false)` (including older-batch fetch); Enter/Space is the accessible equivalent. The leaf module rejects motion, holds, cancel and multi-pointer taps and deduplicates compatibility dblclick; post-touch pointerleave preserves a completed tap. The 44px targets and transparent border/padding reserve identical geometry. Only these regions disable selection/double-tap zoom; message text remains selectable. Labels/title are bilingual. Leo selected B: A image, review switch and image-specific tests/styles are removed. No protocol, host, storage, identity, permission or release-version boundary moves. Evidence: web/test/p102.test.mjs and p102.mjs, included in screens.mjs; 390px zh/en × light/dark. Physical iOS/Android and screen-reader acceptance remain human checks. Use the command below to reproduce the previews.

`P102_PREVIEW_DIR=<output> node web/test/p102.mjs --preview` writes 390px final previews and the readiness marker. Full behavior screenshots run in `test/screens.mjs`.


## P110 screenshot sharing (0.17.3 integration candidate)

The installed Android PWA registers a POST multipart `share_target` at `/share-target`. Its active service worker consumes the form entirely inside the browser, redirects with a random one-time handle, and hands files to `js/share.js` using a local MessageChannel. No network fetch, content log, Cache Storage or IndexedDB write is added. Intake is memory-only, up to two pending shares, ten images / 25 MiB total and 16,000 text characters; handles expire after five minutes or worker termination. The network Worker continues rejecting POST without reading the body. Installation / active SW is required. A paired page waits for the Noise session; an unpaired page discards intake and asks to pair and share again. Unpair / revoke clears pending page intake. All ordinary page assets still come from the network.

`?from=share` shows a 56px Paste screenshot action. Clipboard reading runs inside its click through relay's existing `pasteClipImages`; failures retain the action and existing manual paste / photo fallback. No Clipboard permission is requested at boot. Safari and the Home Screen app retain separate pairing. The source Shortcut copies its first input image locally, then opens the web page in the browser chosen by iOS (Safari recommended); no upload, token or query content. Apple signing/iCloud publication and physical iOS permission prompts remain pending. Public documentation truthfully labels the `.unsigned.shortcut` source.

Tests: `node --test web/test/p110.test.mjs`; `node web/test/p110.mjs` launches isolated Chromium and a real Noise fake host, tests multipart navigation / byte equality / no server POST or auto-send, actual clipboard (CDP browser permission) and explicit denied/empty fixtures. Set `P110_DOC_SHOTS=1` only to deliberately refresh public fixture screenshots, then rebuild site; normal/parity runs never rewrite source illustrations. Neither is Android system-share-sheet nor physical iPhone acceptance. Full qualification uses `flock /tmp/agentj-full-gates.lock`.
