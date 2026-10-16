# Security policy

## Supported versions

Only the latest release receives security fixes. Releases are the `v<version>` tags of this repository; the current
one is named in the front matter of [`install.md`](install.md) (`source_tag`). Nightly upgrades, on by default, keep
a host on the latest signed release.

## Reporting a vulnerability

Email **feedback@agentj.app** with "security" in the subject. Please do not open a public issue for a vulnerability.

Include what you found, how to reproduce it, and what an attacker could do with it. We answer within a few days and
tell you what we will fix and when.

Please do not test against other people's accounts, hosts or devices, do not run denial-of-service tests against our
services, and do not access data that is not yours.

## Scope

- The host CLI (`host/`): pairing, approvals, the approval passphrase, the agent fence, local files and sockets,
  the loopback admin page (`agentj admin`).
- The protocol and its implementations (`protocol/`, `host/agentj/noise.py`): anything that lets the relay,
  a network attacker or an unpaired device read, inject or approve.
- The web client (`web/`): anything that leaks keys or plaintext, or lets a page other than the client act as a device.
- `install.md`: instructions that could make an agent do something unsafe.
- Our hosted services (`*.agentj.app`) where a finding breaks the promises on
  <https://agentj.app/security/>, for example our servers being able to read message content.

## Out of scope

- Findings that need a compromised OS account on the host.
- Social engineering.
- Issues in third-party agents (Claude Code, Codex, OpenCode) themselves.

## Account pairing trust

Hosts bound to an online account can add a remote device from the account's seat card after the owner confirms with a
passkey. This route deliberately trusts the signing control plane for adding or approving a remote, resuming, and
enabling a task after that confirmation. The host checks the signature, a one-use nonce, a five-minute lifetime, and
the account, host, channel and target, and honours the owner's opt-out. Pairing material is returned encrypted to a
short-lived browser key; the backend does not keep the plaintext.

A compromise of the signer could therefore authorize adding a device. `agentj remote-pair off` turns this route off.
Local and unbound approvals keep their local-only passphrase hash.

## Disclosure

We credit you when the fix ships, unless you prefer not to be named. Please give us a reasonable time to ship a fix
before you disclose publicly.

## Bounty

There is no bug bounty yet. This is a beta.
