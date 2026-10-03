# sim-phone — a simulated phone for tests

**For test environments without a real phone only.** It is a real browser running our real web client; it never
bypasses pairing — a human (or the test) still types the 6 digits and the passphrase into the host.

`sim-phone.mjs` starts a headless Chromium (its own profile and debugging port, never your normal browser), opens the
Agent J web client, takes the pairing link the way the phone's 扫码 does (the client's own `startPairing`, via its
"paste the pairing link" box), prints the 6-digit safety code the page shows, waits until you approve it on the host,
and can then send one message, print the replies, and answer the next approval card.

Requirements: Node ≥ 22 (no npm packages) and Chromium or Google Chrome. It looks for `$CHROMIUM`, then
`/usr/bin/chromium`, then `chromium`, `chromium-browser`, `google-chrome`, `google-chrome-stable` on `PATH`, then
`/Applications/Google Chrome.app/Contents/MacOS/Google Chrome` (macOS).

## Use

```bash
agentj pair --no-qr                 # terminal 1: prints the pairing link, then asks for the 6-digit code
node tools/sim-phone/sim-phone.mjs --link '<the link>' --profile ./phone --send "hello"
#   CODE 123456        ← type these 6 digits (then your passphrase) into terminal 1
#   PAIRED
#   SENT hello
#   REPLY …            ← the Agent's reply, one line each
node tools/sim-phone/sim-phone.mjs --profile ./phone --send "delete the build folder" --deny
#   RESUMED · SENT … · ASK Bash: rm -rf build · ANSWERED deny · ASK-RESULT deny · REPLY …
```

| Option | Meaning |
|---|---|
| `--link <url>` | the pairing link from `agentj pair --no-qr`, or from 显示链接 under the QR on the `agentj admin` page |
| `--web <url>` | the web client to open; default: the link's own origin, else `https://m.agentj.app` |
| `--profile <dir>` | keep the phone (device key + pairing) in `<dir>`; later runs without `--link` resume as the same phone |
| `--send <text>` | send one message after pairing / resuming and print the replies |
| `--approve` / `--deny` | answer the next approval card with 批准 / 拒绝 |
| `--wait <s>` | how long to wait for the reply / the card (default 60; the pairing itself waits up to 180 s) |

Output lines: `CODE <6 digits>`, `PAIRED` / `RESUMED`, `SENT <text>`, `REPLY <line>`, `ASK <tool>: <first line>`,
`ANSWERED allow|deny`, `ASK-RESULT allow|deny|timeout|gone`, `AGENT idle|working|waiting|down`. Errors: one line on
stderr starting with `sim-phone:`.

Exit codes: `0` done · `1` usage · `2` no Chromium / it did not start · `3` pairing refused or failed (wrong code,
expired link, revoked) · `4` the host did not approve in time · `5` no reply within `--wait` · `6` no approval card
within `--wait` · `7` not paired (no `--link` and the profile holds no pairing, or it could not reconnect).

## What it does not do

- It does not read QR images: pass the link text (`agentj pair --no-qr` prints it).
- It cannot approve a pairing: only the host can, after a person (or a test playing that person) types the code and the
  passphrase there. Remove the simulated phone like any other device: `agentj devices`, then `agentj revoke <id>`.
- It is not a push-notification client; it sees replies only while it runs.
