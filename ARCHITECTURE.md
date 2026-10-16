# Architecture

This is the high-level map of Agent J for contributors. The normative wire specification is
[`protocol/PROTOCOL.md`](protocol/PROTOCOL.md); when this page and the spec disagree, the spec wins.

## The pieces

```
 phone (web app)  ⇄  relay (ciphertext only)  ⇄  host `agentj` on your computer  ⇄  your coding agent
   web/                 hosted service               host/                         Claude Code · Codex · OpenCode
                                                                                     Kimi Code · Qwen Code
```

| Piece | Where | What it does |
|---|---|---|
| **Host** (`agentj`) | `host/` (Python ≥ 3.11) | A daemon (`agentj serve`) next to your coding agent. It dials the relay, runs the end-to-end handshake with each paired device, starts and watches the agent, answers its permission requests with your signed approvals, and keeps keys, history and logs on your computer. |
| **Agent bridge** | `host/agentj/agent*.py`, `host/agentj/acp.py` | One adapter per coding agent. Claude Code, Codex and OpenCode use their own interfaces; Kimi Code and Qwen Code speak the Agent Client Protocol (ACP) through the shared `acp.py` adapter. Every agent runs with your own login, settings and native permission rules. |
| **Fence** | `host/agentj/fence.py` | Optional isolation for the agent process: bubblewrap on Linux, `sandbox-exec` on macOS. It hides Agent J's own keys and paired-phone data from the agent. Requested and actual isolation are reported separately. |
| **Phone web app** | `web/` | A static client with no runtime dependencies, served by a small Cloudflare Worker (`web/worker.ts`) at <https://m.agentj.app>. It pairs once, then talks to the host through the encrypted channel. |
| **Protocol** | `protocol/` | The wire specification, the shared JavaScript Noise implementation used by the web app, and the official test vectors. |
| **Skills** | `host/agentj/skills/` | The built-in skills the host installs for your agent (configuration, upgrades, handoff, and more). |
| **Relay** | hosted, not in this repository | Forwards opaque frames between a host and its devices. It never holds a key that can read them. |

## Security model in one screen

- **Pairing.** The phone scans a QR code shown by `agentj pair` in a terminal, and the owner types a 6-digit code plus a
  passphrase. First pairing uses `Noise_IKpsk2_25519_AESGCM_SHA256`; later connections use `Noise_IK_25519_AESGCM_SHA256`.
  Pairings are approved only on the host, never by the relay.
- **End-to-end encryption.** Every message between phone and host is encrypted inside relay payloads. What the relay can
  and cannot see (connection metadata and padded frame sizes, never keys or text) is listed in section 5 of the spec.
- **Approvals.** When the agent asks for permission, the phone shows a card; the decision is signed by the phone,
  checked and logged by the host. No answer in 120 seconds means denied.
- **The bridge never outranks the session.** The host passes the agent's own permission requests to you; it does not
  grant the agent more than its native configuration allows.
- **Credentials stay local.** Keys are entered through the phone's key card and written only to the host; they never
  travel through chat, logs or the relay in plain text.
- **Signed upgrades.** Releases are published as a git tag plus a wheel and source archive whose SHA-256 values appear
  in `install.md`; host wheels carry a minisign signature that the host verifies before installing an upgrade.

## Data flow of one message

1. You type on the phone. The web app encrypts the message for the paired host and sends it to the relay.
2. The relay forwards the opaque frame to the host's socket.
3. The host decrypts it and hands the text to the active coding agent through its adapter.
4. The agent works. Permission requests come back to the phone as approval cards; replies, files and status updates
   are encrypted by the host and travel back the same way.

## Where to read next

- Wire format, handshakes and message types: [`protocol/PROTOCOL.md`](protocol/PROTOCOL.md)
- Host command reference: [`docs/CLI.md`](docs/CLI.md)
- What is open and what is hosted: [README](README.md#what-is-open-and-what-is-hosted)
- User documentation: <https://agentj.app/docs/>
- Reporting a vulnerability: [SECURITY.md](SECURITY.md)
