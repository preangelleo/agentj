---
type: Implementation Status
status: draft
generated: { by: Relay/codex, at: 2026-10-04T14:20:00Z }
verified: []
---
# Native shared sessions — 0.13

`shared` is the default. An upgrade from 0.12 that has no session-mode override
uses shared without replacing device keys or its pairing allowlist. Explicit
`independent` retains the fenced, separate conversation and its original danger
list. Never change native permission settings to suppress phone cards.

Claude uses the production relay's authenticated local socket transport and its
foreground/background-aware committed Stop state machine. Registry idle cannot
complete a turn; Stop's final payload owns completion even before transcript flush.
New desktop input and final reply mirror to encrypted phone history. Native
PermissionRequest decisions come from signed paired-phone approval and are logged.
Project hooks merge with existing hooks and permission settings. Hook-disabled
owner settings refuse attachment. Without a selected live session, Agent J starts
ordinary native Claude outside the fence; the computer can `claude --resume` its
saved ID. Ordinary workspace trust remains the owner's native choice.

Mobile stop sends one Esc in an owned ordinary PTY; for an existing terminal it
uses relay's exact-session Herdr route. It refuses Esc while a permission dialog
is open. Unsupported terminal routing reports a notice instead of guessing a pane
or terminating the owner's session. Detach terminates only a native actor launched
by this host, never an existing owner TUI.

OpenCode attaches to the exact loopback server/session and project. It preserves
native permissions, native approvals, and desktop decisions. A local native plugin
calls the shared classifier in `tool.execute.before`: native allowed tools otherwise
emit no permission event. Installing this plugin reloads the native project
instance only when all sessions are idle and requires a callback handshake before
phone delivery. No PATCH permissions. Without a selector it starts an owned native
server/session; stale explicit selectors fail. The owner's server survives detach.

Codex continues an exact existing thread using alternating native owner/app-server
and phone actors. The owned app-server exits after each phone turn. This is not
simultaneous live TUI attachment. The original approval policy, reviewer and
verified permission profile are retained. Unknown/custom profile shapes fail
closed; the built-in read-only profile and measured disabled/full-access profile
are supported. A stored session ID is retained on upgrade. PreToolUse is installed
additively; before the first resume only Agent J's exact command hashes receive
thread-scoped trust. Other owner hooks retain their own trust and enabled state.

With `agent.high_risk_warnings=true` (default), four classes trigger an additional
signed phone card: spending, deleting public content, sending/publishing externally,
and reading/writing credentials. Local file work, scripts, dependency installation
and git commit use native permissions with no extra card. Same-category grants
expire at the next turn, revocation, signing-key change or emergency stop; audit
entries bind later inputs to the original signed approval. Credential cards omit
values and executable input; exact operation details stay on the computer. Native
asks remain separate: a risk grant never permits a different native ask.

Actual native + Noise evidence: `reports/qa/release-0.13d/{claude-shared-noise,
claude-ordinary-noise,opencode-tui-noise,codex-shared-noise}.json`.
All three routine tasks executed files/scripts/offline dependencies/commit with
zero cards; each high-risk task had one card with reminders enabled. Claude's
risk task was initiated by the native desktop owner; the native model refused a
phone peer credential request before calling any tool, which was not counted as
high-risk acceptance. A separate phone-origin native permission request passed.
Publication remains governed by the complete strict/export/secret gates and ready.json.

When the phone channel is detached, routine desktop tools retain native rules and
permission UI. Only the same four high-risk categories fail closed; hooks never
return allow while disconnected. Native CLI resume also verifies a real routine
Read after the host exits.

A same-category rejection blocks retries without another phone card until a new
prompt. Both approvals and rejections are scoped to the current turn epoch.
