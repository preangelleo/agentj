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
A turn that ends on a tool call (ScheduleWakeup, Monitor, …) sends no Stop hook; Claude
Code still records its end (`system/turn_duration`). With no Stop within 1 s that recorded
end commits the turn with its last assistant text (P151); owner turns whose answer sits only
in the final message's visible thinking use that. No text at all → an honest 「已结束，但没有
写文字回复」, not 「未确认」. A Stop later than the recorded end is not delivered twice.
After a compaction (or a /clear with `~/.local/state/agentj/handoff/clear.md`) and 1.5 s with no new turn, Agent J sends
one 〔Agent J 系统提示〕 message itself — read the handoff (`…/handoff/compact.md`), then carry on — and the phone shows
「压缩完成，Agent 正在恢复上下文」. A phone /compact first asks for the handoff when it is missing or stale (P151).
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
and changing credentials or showing them on screen (P128 `agent.approvals=standard`; a program reading `.env` by name
or a note that only mentions it does not ask; `strict` = any credential path asks, the 0.13 rule). Local file work, scripts, dependency installation
and git commit use native permissions with no extra card. Same-category grants
expire at the next turn, revocation, signing-key change or emergency stop; audit
entries bind later inputs to the original signed approval; a "show" grant never covers a "change". Cards are in one language
(`appearance.language`): 查看密码或密钥 / Shows passwords or keys vs 改密码或密钥 / Changes passwords or keys. Credential cards omit
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

When the phone channel is detached (host gone, socket error/timeout), hooks never
return allow (ADR-A146). P139 (0.18.3): Claude PreToolUse for the high-risk
categories and for hook-file writes returns `ask` (the native dialog, even over an
owner allow rule) instead of handing the call to native rules; PermissionRequest
keeps retrying the host for up to 150 s while the native dialog is up, so a
restarting host still puts the card on the phone, and whatever the dialog handled
meanwhile is reported to the phone afterwards. Routine tools keep native rules.
Codex (PreToolUse `ask` is unsupported) and OpenCode (its before-hook cannot ask)
keep native rules on the detached path. Another PermissionRequest hook never makes
Agent J yield: observers are ignored and answerers race the phone card.
P307: AskUserQuestion is shown on the phone and in the terminal at once. A phone
answer is returned as PermissionRequest `allow` + `updatedInput` carrying
`answers` (probed with Claude Code 2.1.296: a bare `allow` leaves the terminal
dialog open and loses the answer); phone cancel / timeout is `deny` with the
host's message, which also closes the dialog. A terminal answer (PostToolUse with
`answers`/`annotations`, ignored by the card key) or a terminal Esc (only the
transcript's tool_result; no PostToolUse, no Stop) withdraws the phone card. A
phone answer the session has not taken within 20 s, or at a phone-turn stall, is
sent to the session as a message and the phone is told to press Esc there.
Native CLI resume also verifies a real routine Read after the host exits.

A same-category rejection blocks retries without another phone card until a new
prompt. Both approvals and rejections are scoped to the current turn epoch.
