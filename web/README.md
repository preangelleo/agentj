# web — the Agent Jarvis web client (`alpha-web.agentjarvis.net`, public but not indexed)

Device side of `../protocol/PROTOCOL.md` §2–§4 and §8–§9: pair with a QR / link (IKpsk2 + 6-digit code typed on the host), resume (IK),
chat with the host's Agent (status pill, approval cards signed with a non-extractable Ed25519 key), content-free Web Push.
One host only (N=1). Tier: **"tampering would be detectable"**, not zero-access (badge on every screen; see
https://alpha.agentjarvis.net/security/).

| Path | What |
|---|---|
| `public/index.html` `app.js` `app.css` | the client (ES module, no deps, textContent only, no third-party resources) |
| `public/sw.js` | service worker: push display only (「有新回复」/「有一个请求等你批准」), no fetch handler, no cache |
| `public/manifest.webmanifest` + `icon-*.png` `apple-touch-icon.png` | install to home screen (iOS needs it for push); icons from `tools/icons.py` (deterministic) |
| `public/proto/{noise,wire}.js` | **build copies** of `../protocol/*.js`; never edit here |
| `public/version.json` / `version.js` | SHA-256 per shipped file + `combined`; `version.js` is the same manifest as a module (CSP `connect-src` allows only the relay, so the page cannot fetch JSON) |
| `build.mjs` | copies proto, writes the manifest; `--print-hash` prints `combined` from source without writing |
| `worker.ts` + `wrangler.toml` | Worker `agentjarvis-web` (no imports): wrong host / non-GET/HEAD / malformed relay URL → 404; CSP pinned to the relay + security headers (`webHeaders()`), `noindex` |
| `test/serve.mjs` | `startWebServer({port:0, relayCsp:'ws://127.0.0.1:*'})` → `{url, port, stop}`, same headers as the Worker |
| `test/fakehost.mjs` | JS fake relay+host (responder, real noise.js) used by `screens.mjs`; not a relay implementation |

## Build & test
```bash
node web/build.mjs                    # after any change in public/ or protocol/
node --test web/test/*.test.mjs       # static rules, proto byte-identity, manifest, Worker headers/404
node web/test/screens.mjs             # own headless Chromium (never :9222); shots → /tmp/aj-a2/web-shots/
```

## Driving it (e2e)
States in `#status[data-state]` (mirror `window.__ajState`): `idle connecting waiting-host pairing awaiting-approval ready revoked error`.
Pair: open `<url>#p=…` (or fill `#pair-link`, click `#pair-go`) → `awaiting-approval`, 6 digits in `#sas` → host approves → `ready`.
Chat: `#msg-input` + Enter or `#send`; `#messages li[data-dir=in|out][data-from=agent|host|you|device|notice]`.
Agent: `#agent-status[data-s=idle|working|waiting|down]`; cards `#messages li.ask` (`.ask-summary`, `.ask-allow`, `.ask-deny`,
`[data-result=allow|deny|timeout|gone]`); push `#push-row` / `#push-on`. Revoked: `#repair` (clears host, keeps device key).
Error: `#retry`. Local relays must be `ws://127.0.0.1:<port>` (what `parsePairing` accepts besides `wss://`).

## Verify the deployed page (anyone)
`node web/build.mjs --print-hash` on the public source must equal the hash in the badge panel; stricter: download each
served file and compare with `version.json`.

## Deploy (CEO only)
`node build.mjs`, the tests, then `wrangler deploy` from this directory with your own account (`account_id` in `wrangler.toml`).
Verify: `curl -sI https://alpha-web.agentjarvis.net/` → 200 with `x-robots-tag: noindex, nofollow, noarchive`, and the CSP's
`connect-src` naming only the relay.
