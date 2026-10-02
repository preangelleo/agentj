# Security policy

## Reporting a vulnerability

Email **me@agentjarvis.net** with "security" in the subject. Please do not open a public issue for a vulnerability.
Include what you found, how to reproduce it, and what an attacker could do with it. We answer within a few days and
tell you what we will fix and when; we credit you when the fix ships, unless you prefer not to be named.

Please do not test against other people's accounts, hosts or devices, do not run denial-of-service tests against our
services, and do not access data that is not yours.

## In scope

- The host CLI (`host/`): pairing, approvals, the approval passphrase, the agent fence, local files and sockets,
  the loopback admin page (`jarvis admin`).
- The protocol and its implementations (`protocol/`, `host/jarvis_host/noise.py`): anything that lets the relay,
  a network attacker or an unpaired device read, inject or approve.
- The web client (`web/`): anything that leaks keys or plaintext, or lets a page other than the client act as a device.
- `install.md`: instructions that could make an agent do something unsafe.
- Our hosted services (`*.agentjarvis.net`) where a finding breaks the promises on
  <https://alpha.agentjarvis.net/security/> — for example, our servers being able to read message content.

Out of scope: findings that need a compromised OS account on the host, social engineering, and issues in
third-party agents (Claude Code, Codex) themselves.

## Bounty

There is no bug bounty yet. This is an alpha.
