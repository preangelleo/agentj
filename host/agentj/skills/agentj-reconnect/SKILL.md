---
name: agentj-reconnect
description: Phone or web page grey / disconnected / "not connected", or the first turn of a new session (also after compaction, /clear or resume) — 「手机灰了」「网页断开了」「手机连不上你」「接上手机」「重连」, "phone is grey", "reconnect my phone", "connect my phone". Checks the background service, the relay and the Agent in turn with `agentj status --json`, repairs the broken link with `agentj doctor --reconnect`, and tells the owner one plain sentence.
---
# Bring a grey phone back (起死回生)

The owner should never need to know a command. You run these yourself.

1. `agentj status --json`
   - Not running (`running: false`) → step 2.
   - `relay_state` is `reconnecting` → step 2.
   - `agent_status` is `down` (or anything other than `idle` / `working` / `waiting` / `compacting`) → step 2.
   - Everything up → tell the owner the computer side is fine; a grey page usually comes back with pull-to-refresh.
2. `agentj doctor --reconnect` — run it **in this session**: it attaches the session that runs it.
   It checks, in order, and repairs only the broken one:
   - **service** (`agentj serve`): not answering → started / restarted;
   - **relay** (computer ↔ Agent J relay): not connected → waits, then one restart;
   - **Agent** (Agent J ↔ this AI session): re-attached; with several sessions open in the folder it names them.
   Exit 0 = all three up. Repeat its last line (✓ / ✗ sentence) to the owner word for word.
3. Several sessions listed (`choices`) → `agentj doctor --reconnect --session <ID>` with the one the owner uses
   (from inside that session plain `agentj doctor --reconnect` is enough).
4. Still ✗ → `agentj doctor`, then the agentj-manual support steps. Never edit config files by hand, never restart
   other people's processes, never ask the owner for keys.

Why it happens: shared mode follows one Claude Code session. A reboot, a new window, `/clear` or an upgrade gives the
desktop a new session; since 0.18.4 Agent J follows it by itself (the only live owner session in the folder, or the one
that just started) and the phone says 「已接上新会话 <id>」. It stops and asks only when two sessions are equally likely.
A grey phone is not a broken relay — the three links are separate:

| Link | Look at | Broken means |
|---|---|---|
| service | `agentj status` answers at all | `agentj serve` not running |
| relay | `relay_state` | the computer cannot reach the Agent J relay (network / account) |
| Agent | `agent_status` | Agent J is not attached to a live AI session (`down`) |

中文：手机/网页变灰，先 `agentj status --json`，不正常就在本会话运行 `agentj doctor --reconnect`（依次查后台服务、中继、
Agent 三段，哪段断了修哪段，并把运行它的会话接上手机），把最后那句结论原话告诉主人；列出多个会话时用
`--session <ID>` 指定；仍失败按 agentj-manual 求助。手机灰 ≠ 中继坏，多半是电脑上换了新会话。
Docs: https://agentj.app/docs/reconnect/zh.md · https://agentj.app/docs/reconnect/en.md
