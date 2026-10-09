<p align="center"><img src="https://agentj.app/brand/img/shield-128.png" width="96" height="96" alt="Agent J shield logo"></p>

# Agent J

This 0.17.1a1 candidate tree is for review. The published installation remains 0.17.0a1 until the public mirror/tag and all release gates pass. Android is a debug review APK, with offline model provenance and physical-device acceptance still pending; it is not a store release.

Docs (中文 / English): <https://agentj.app/docs/> · machine-readable index: <https://agentj.app/llms.txt>

Control **your own** Claude Code, Codex or OpenCode from your phone: chat with it, and approve or deny what it wants to do,
from anywhere.

- A small host program, `agentj`, runs on your computer (macOS or Linux) next to your agent.
- Your phone pairs with it once by scanning a QR code and typing a 6-digit code plus a passphrase only you know.
- Phone and computer talk **end-to-end encrypted** (Noise protocol) through a blind relay that only forwards ciphertext.
- When the agent asks for permission, your phone shows a card; every decision is signed by the phone and logged on your
  computer. No answer in 120 seconds = denied.
- The agent uses your own login, settings and native permission rules. When Agent J starts OpenCode with isolation
  enabled, a fence hides Agent J's keys and paired-phone data: bubblewrap on Linux, built-in `sandbox-exec` on macOS.
  An OpenCode server you started yourself is not wrapped or restarted by Agent J; requested and actual isolation are
  reported separately.
- Optional Agent friends (0.16): your Agent can add another owner's Agent once that owner approves on their phone; the two
  talk end-to-end encrypted through a mailbox relay that forwards ciphertext only, each friend is answered by a tool-less
  stand-in, and you read every conversation on the phone.

- Optional customer-service bots (0.17 candidate): free public URLs and embeds, host-local knowledge and company HTTP tools, the owner model plan and separate owner-funded OpenRouter safety reviews. Public service requires successful review setup and signed owner activation.

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
uv tool install "git+https://github.com/preangelleo/agentj@v0.17.1a1#subdirectory=host"
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
Published host wheels include a minisign signature, verified by the compiled trusted signer before manual or nightly upgrades.

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

Account-bound online hosts support Add a remote on their account seat card with owner passkey confirmation, including another computer. No terminal passphrase setup is required. `agentj remote-pair off` opts out; local/offline pairing retains a local passphrase. See install.md and docs/account for the trust boundary.

Shared Codex phone turns use the owner's explicit model and reasoning effort; desktop turns keep their own selections. Resuming an existing thread applies the current native provider. Read-only banners identify the actual writer; quit that program before retrying. On older hosts, start a new Codex conversation with current defaults or use an independent Agent J session.

Shared Claude enables unset phone ingress automatically (installation, switching and 0.16.4 upgrade), backing up settings.json and notifying the phone once. Explicit off/hold or refuse remains unchanged. To disable: `agentj config claude-inbound off`. Only Claude Code is affected; repository/managed policies remain in force.

Nightly host upgrades are enabled by default. Use `agentj config auto-update on|off|status`. Only a signed/hash-verified latest wheel installs during the local 03:00–05:00 idle window; busy or stopped hosts defer until tomorrow. Failures restore the locally cached previous wheel; doctor and the next phone opening report the outcome. Shared native sessions are never forcibly cleared.

The 0.17.1 hotfix keeps phone key-card writes responsive and applies the owner-signed website allowlist to both inner frames. Deploy Dashboard app metadata code, then front Worker/site and phone web; no new database migration or relay implementation. Production Turnstile and full bot reply/review/limit/embed acceptance remain release-operator steps.
