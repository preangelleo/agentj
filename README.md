<p align="center"><img src="https://agentj.app/brand/img/shield-128.png" width="96" height="96" alt="Agent J shield logo"></p>

# Agent J

This 0.12 candidate tree is for review. The published installation remains the complete 0.11 path until the public mirror/tag and all release gates pass. Android is a debug review APK, with offline model provenance and physical-device acceptance still pending; it is not a store release.

Docs (中文 / English): <https://agentj.app/docs/> · machine-readable index: <https://agentj.app/llms.txt>

Control **your own** Claude Code, Codex or OpenCode from your phone: chat with it, and approve or deny what it wants to do,
from anywhere.

- A small host program, `agentj`, runs on your computer (macOS or Linux) next to your agent.
- Your phone pairs with it once by scanning a QR code and typing a 6-digit code plus a passphrase only you know.
- Phone and computer talk **end-to-end encrypted** (Noise protocol) through a blind relay that only forwards ciphertext.
- When the agent asks for permission, your phone shows a card; every decision is signed by the phone and logged on your
  computer. No answer in 120 seconds = denied.
- The agent runs with your own login, settings and permission rules, inside a fence so it cannot see or change Agent J's
  keys or the list of paired phones: bubblewrap on Linux, the built-in `sandbox-exec` on macOS.

**Zero access is the product rule:** our servers never hold a key that can read your messages, your agent's replies or
your credentials. What we can and cannot see, and what is not done yet, is listed item by item at
<https://agentj.app/security/>.

The service is **$20 per seat per month**. You pay your AI model provider separately.

## Install

The real guide is <https://agentj.app/install.md>. Tell your AI agent: **"read https://agentj.app/install.md and do it"**.
It installs, sets up and stops for you at every step only a human can do. [`install.md`](install.md) in this repository
is the same file, byte for byte; the site publishes its SHA-256 at <https://agentj.app/install.md.sha256>.

The host program on its own, pinned to the release tag (Python 3.11 or newer; if yours is older, uv fetches one):
```bash
uv tool install "git+https://github.com/preangelleo/agentj@v0.15.1a1#subdirectory=host"
agentj --version
```

Phone: <https://m.agentj.app> · your account (seats, billing): <https://agentj.app/account>

## Check what you installed

Every release is a git tag (`v<version>`), and the same code is published on our site as a wheel and a source archive.
Their SHA-256 values are in two places: next to the files on the site, and in the front matter of `install.md`
(`host_wheel_sha256`, `host_sdist_sha256`). Since `install.md` here must equal the site's copy byte for byte, the hashes in
this repository and on the site have to agree.
```bash
W=$(curl -fsSL https://agentj.app/dl/latest.txt)          # the wheel; latest-sdist.txt names the source archive
curl -fsSLO "https://agentj.app/dl/$W" && curl -fsSLO "https://agentj.app/dl/$W.sha256"
sha256sum -c "$W.sha256"                                  # macOS: shasum -a 256 -c "$W.sha256"   → OK
grep "^host_wheel_sha256:" install.md                     # the same value, from this repository
```
There are no publisher signatures yet.

## What is here

| Path | What |
|---|---|
| `protocol/` | wire spec (`PROTOCOL.md`), the shared JavaScript Noise implementation, official test vectors |
| `host/` | the host CLI `agentj` (Python ≥ 3.11): pairing, relay connection, agent bridge, approvals, fence |
| `web/` | the phone web client (static, no dependencies) and its Cloudflare Worker |
| `tools/sim-phone/` | a simulated phone for test environments without a real phone: headless Chromium running the real web client; it never bypasses pairing |
| `install.md` | the instructions an AI agent follows to install Agent J |

The relay, the account dashboard and the website are not in this repository.

## Tests

```bash
node --test protocol/test/*.test.mjs                                   # Noise against the official vectors
(cd host && uv sync && .venv/bin/python -m unittest discover -s tests)  # host CLI (Linux: needs bubblewrap for the fence tests)
node web/build.mjs && node --test web/test/*.test.mjs                  # web client static rules + Worker
```
`node web/build.mjs --print-hash` prints the combined hash of the web client; it must equal the hash shown in the
client's badge panel at <https://m.agentj.app>.

## Status

**Beta.** Anyone can sign up; not advertised yet, pages not indexed. Expect breaking changes. Not yet: signed releases, a release
log, native apps, Windows (use WSL2).

## Security

Please report vulnerabilities privately; see [SECURITY.md](SECURITY.md).

## License

[Apache License 2.0](LICENSE).
