<p align="center"><img src="https://agentjarvis.net/brand/img/shield-128.png" width="96" height="96" alt="Agent Jarvis shield logo"></p>

# Agent Jarvis

Docs (中文 / English): <https://agentjarvis.net/docs/> · machine-readable index: <https://agentjarvis.net/llms.txt>

Control **your own** Claude Code, Codex or OpenCode from your phone: chat with it, and approve or deny what it wants to do,
from anywhere.

- A small host program, `jarvis`, runs on your computer (macOS or Linux) next to your agent.
- Your phone pairs with it once by scanning a QR code and typing a 6-digit code plus a passphrase only you know.
- Phone and computer talk **end-to-end encrypted** (Noise protocol) through a blind relay that only forwards ciphertext.
- When the agent asks for permission, your phone shows a card; every decision is signed by the phone and logged on your
  computer. No answer in 120 seconds = denied.
- The agent runs with your own login, settings and permission rules; on Linux it runs fenced (bubblewrap) so it cannot
  see or change Jarvis's keys or the list of paired phones.

**Zero access is the product rule:** our servers never hold a key that can read your messages, your agent's replies or
your credentials. What we can and cannot see — and what is not done yet — is listed honestly, item by item, at
<https://agentjarvis.net/security/>.

## Install

Tell your AI agent: **"read https://agentjarvis.net/install.md and do it"** — it installs, sets up and stops
for you at every step only a human can do. [`install.md`](install.md) in this repository is the same file, byte for byte;
the site publishes its SHA-256 at <https://agentjarvis.net/install.md.sha256>.

The host CLI on its own:
```bash
uv tool install "git+https://github.com/preangelleo/agentjarvis#subdirectory=host"
jarvis --version
```

## What is here

| Path | What |
|---|---|
| `protocol/` | wire spec (`PROTOCOL.md`), the shared JavaScript Noise implementation, official test vectors |
| `host/` | the host CLI `jarvis` (Python 3.13): pairing, relay connection, agent bridge, approvals, fence |
| `web/` | the phone web client (static, no dependencies) and its Cloudflare Worker |
| `tools/sim-phone/` | a simulated phone for test environments without a real phone: headless Chromium running the real web client; it never bypasses pairing |
| `install.md` | the instructions an AI agent follows to install Agent Jarvis |

The relay, the Dashboard (accounts and billing) and the website are not in this repository.

## Tests

```bash
node --test protocol/test/*.test.mjs                                   # Noise against the official vectors
(cd host && uv sync && .venv/bin/python -m unittest discover -s tests)  # host CLI (Linux: needs bubblewrap for the fence tests)
node web/build.mjs && node --test web/test/*.test.mjs                  # web client static rules + Worker
```
`node web/build.mjs --print-hash` prints the combined hash of the web client; it must equal the hash shown in the
client's badge panel at <https://alpha-web.agentjarvis.net>.

## Status

**Alpha** — invite-only by word of mouth, not promoted, pages not indexed. Expect breaking changes. Not yet: signed
releases, a release log, native apps, Windows (use WSL2), the macOS fence.

## Security

Please report vulnerabilities privately — see [SECURITY.md](SECURITY.md).

## License

[Apache License 2.0](LICENSE).
