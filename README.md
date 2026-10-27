<p align="center"><img src="https://agentj.app/brand/img/shield-128.png" width="96" height="96" alt="Agent J shield logo"></p>

<h1 align="center">Agent J</h1>

<p align="center"><strong>Your chief of staff for AI agents.</strong> Talk to one Agent; it runs the rest.</p>

<p align="center">
  <a href="LICENSE"><img src="https://img.shields.io/badge/license-Apache--2.0-blue.svg" alt="License: Apache-2.0"></a>
  <img src="https://img.shields.io/badge/python-%E2%89%A53.11-3776AB.svg" alt="Python 3.11 or newer">
  <a href="https://github.com/preangelleo/agentj/releases/tag/v0.25.14a1"><img src="https://img.shields.io/badge/release-v0.25.14a1-orange.svg" alt="Latest release: v0.25.14a1"></a>
  <img src="https://img.shields.io/badge/status-beta-yellow.svg" alt="Status: beta">
</p>

<p align="center"><a href="README.zh-CN.md">中文</a> · <a href="https://agentj.app/docs/">Docs</a> · <a href="https://agentj.app/install.md">Install</a> · <a href="https://agentj.app/security/">Security</a></p>

You only need to talk to one main Agent: Agent J. It dispatches, monitors and maintains dozens, even hundreds, of
Agent workflows for you, digests the messy details, and brings to your screen only what you need to know or decide.

Agent J runs **your own** coding agent (Claude Code, Codex, OpenCode, Kimi Code or Qwen Code) on **your own** computer.
You talk to it from your phone, from anywhere, end-to-end encrypted: chat with it, and approve or deny what it wants to do.

## What it is

Agent J has two parts you run yourself, both in this repository:

- **The host**, `agentj`: a small Python program that runs on your computer (macOS, Linux, or Windows through WSL2)
  next to your agent. It starts and watches the agent, keeps your keys and history locally, and asks you before
  anything risky happens.
- **The phone web app** at <https://m.agentj.app>: a static web client with no dependencies. It pairs with your host
  once and then talks to it through an encrypted channel.

Between them sits a relay that we host. It only forwards ciphertext and never holds a key that can read your messages.

## Supported coding agents

Install any one of these and sign in with your own account or key; Agent J drives it and can switch between them.

| Coding agent | Maker | Official guide |
|---|---|---|
| Claude Code | Anthropic | <https://code.claude.com/docs/en/quickstart> |
| Codex | OpenAI | <https://developers.openai.com/codex/cli> |
| OpenCode | OpenCode | <https://opencode.ai/docs/> |
| Kimi Code | Moonshot AI | <https://www.kimi.com/code/docs/> |
| Qwen Code | Alibaba Cloud Model Studio | <https://qwenlm.github.io/qwen-code-docs/en/> |
| DeepSeek Harness (developer preview) | DeepSeek | <https://www.npmjs.com/package/@deepseek-ai/dsh> |

Model plans popular in mainland China work too: the Kimi membership (Kimi Code), Alibaba Cloud Model Studio (Bailian)
Token Plan / Coding Plan (Qwen Code or OpenCode), Volcano Engine Ark Coding Plan, Zhipu GLM and DeepSeek (OpenCode).
Elsewhere, OpenCode also works with OpenRouter.

## Features

- **Your agent, your login.** The agent uses your own login, settings and native permission rules.
  You pay your AI model provider separately.
- **Pair once by QR code.** Your phone pairs with the host by scanning a QR code and typing a 6-digit code plus a
  passphrase only you know.
- **End-to-end encryption.** Phone and computer talk over the Noise protocol through a blind relay that only forwards
  ciphertext.
- **Signed approvals.** When the agent asks for permission, your phone shows a card. Every decision is signed by the
  phone and logged on your computer. No answer in 120 seconds means denied.
- **A fence around the agent.** When Agent J starts OpenCode with isolation enabled, a fence hides Agent J's keys and
  paired-phone data: bubblewrap on Linux, the built-in `sandbox-exec` on macOS. Requested and actual isolation are
  reported separately.
- **Agent friends** (optional). Your Agent can add another owner's Agent once that owner approves on their phone. The
  two talk end-to-end encrypted through a mailbox relay; each friend is answered by a tool-less stand-in, and you read
  every conversation on your phone.
- **Customer-service bots** (optional). Public URLs and embeds, host-local knowledge and company HTTP tools, with
  separate safety reviews. Public service starts only after the review is set up and the owner signs the activation.
- **Long tasks.** Say a recurring job once in chat ("a weekly competitor brief"). Your Agent puts what only you can
  decide on one start card; after one confirmation it runs and delivers on schedule.
- **Nightly signed upgrades** (on by default). Only a signed, hash-verified release installs, in a local 03:00–05:00
  idle window. A failure restores the previous version. Turn it off with `agentj config auto-update off`.

**Zero access is the product rule:** our servers never hold a key that can read your messages, your agent's replies or
your credentials. What we can and cannot see, and what is not done yet, is listed item by item at
<https://agentj.app/security/>.

## Quick start

Tell your AI agent:

> **read https://agentj.app/install.md and do it**

It installs and sets everything up, and stops at each step only a human can do. [`install.md`](install.md) in this
repository is the same file, byte for byte; the site publishes its SHA-256 at <https://agentj.app/install.md.sha256>.

To install only the host, pinned to the release tag (Python 3.11 or newer; if yours is older, uv fetches one):

```bash
uv tool install "git+https://github.com/preangelleo/agentj@v0.25.14a1#subdirectory=host"
agentj --version
```

Then open <https://m.agentj.app> on your phone. Seats and billing: <https://agentj.app/account>.

Documentation: <https://agentj.app/docs/> · machine-readable index: <https://agentj.app/llms.txt>

## Verify what you installed

Every release is a git tag (`v<version>`), and the same code is published on our site as a wheel and a source archive.
Their SHA-256 values are in two places: next to the files on the site, and in the front matter of `install.md`
(`host_wheel_sha256`, `host_sdist_sha256`). Since `install.md` here must equal the site's copy byte for byte, the hashes
in this repository and on the site have to agree.

```bash
W=$(curl -fsSL https://agentj.app/dl/latest.txt)          # the wheel; latest-sdist.txt names the source archive
curl -fsSLO "https://agentj.app/dl/$W" && curl -fsSLO "https://agentj.app/dl/$W.sha256"
sha256sum -c "$W.sha256"                                  # macOS: shasum -a 256 -c "$W.sha256"   → OK
grep "^host_wheel_sha256:" install.md                     # the same value, from this repository
```

Published host wheels include a minisign signature, verified by the compiled trusted signer before manual or nightly
upgrades.

The phone web app can be checked the same way: `node web/build.mjs --print-hash` prints the combined hash of the web
client, and it must equal the hash shown in the client's badge panel at <https://m.agentj.app>.

## Architecture

```
 phone (web app)  ⇄  relay (ciphertext only)  ⇄  host `agentj` on your computer  ⇄  your agent
                                                                                    (Claude Code / Codex / OpenCode / Kimi Code / Qwen Code)
```

The full map for contributors — components, security model and data flow — is in [ARCHITECTURE.md](ARCHITECTURE.md).

- The phone and the host hold the keys; the relay sees only encrypted frames.
- Approvals are signed on the phone and checked and logged by the host.
- The wire format, handshakes and message types are specified in [`protocol/PROTOCOL.md`](protocol/PROTOCOL.md), with
  official test vectors in `protocol/vectors/`.
- User documentation: <https://agentj.app/docs/>; the host command reference: [`docs/CLI.md`](docs/CLI.md).

## Repository layout

| Path | What |
|---|---|
| `host/` | the host CLI `agentj` (Python ≥ 3.11): pairing, relay connection, agent bridge, approvals, fence; built-in skills in `host/agentj/skills/` |
| `web/` | the phone web client (static, no dependencies) and its Cloudflare Worker |
| `protocol/` | wire spec (`PROTOCOL.md`), the shared JavaScript Noise implementation, official test vectors |
| `android/` | Android app source |
| `tools/sim-phone/` | a simulated phone for test environments without a real phone: headless Chromium running the real web client; it never bypasses pairing |
| `docs/` | install, agent setup and keep-awake guides, and the CLI reference |
| `install.md` | the instructions an AI agent follows to install Agent J |
| `setup-agents.sh` `setup-agents.ps1` | helper scripts that set up the AI agents on a computer |

## What is open and what is hosted

| Open source, in this repository | Hosted, not in this repository |
|---|---|
| `host/`, the program that runs on your computer | the relay service |
| `web/`, the phone web app and its Cloudflare Worker | accounts, seats and billing |
| `protocol/`, the spec, implementation and test vectors | the Plaza server |
| built-in skills (`host/agentj/skills/`) | the account dashboard |
| `install.md` and the setup scripts | the website implementation |
| `tools/sim-phone/` | release signing keys |
| `android/` source | production data |

Everything that runs on your computer and your phone is here, so you can audit it. End-to-end encryption means the
relay only forwards ciphertext. The subscription, **$20 per seat per month**, pays for the hosted service.

## Tests

```bash
node --test protocol/test/*.test.mjs                                   # Noise against the official vectors
(cd host && uv sync && .venv/bin/python -m unittest discover -s tests)  # host CLI (Linux: needs bubblewrap for the fence tests)
node web/build.mjs && node --test web/test/*.test.mjs                  # web client static rules + Worker
```

## Status

**Beta.** Anyone can sign up. Expect breaking changes between releases. The Android app is a debug build for review,
not a store release. On Windows, Agent J runs inside WSL2.

## Contributing

Bug reports and pull requests are welcome. Please read [CONTRIBUTING.md](CONTRIBUTING.md) and our
[Code of Conduct](CODE_OF_CONDUCT.md).

## Security

Please report vulnerabilities privately; see [SECURITY.md](SECURITY.md). Do not open a public issue for them.

## License

Licensed under the [Apache License 2.0](LICENSE). See [NOTICE](NOTICE) for attributions and bundled third-party code.

## Trademark

The code is open source; the "Agent J" name, the shield logo and the agentj.app domain are not. See
[TRADEMARKS.md](TRADEMARKS.md).
