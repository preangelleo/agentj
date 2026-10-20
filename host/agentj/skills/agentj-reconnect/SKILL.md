---
name: agentj-reconnect
description: Phone or web page grey / disconnected / "not connected", or the first turn of a new session (also after compaction, /clear or resume) — 「手机灰了」「网页断开了」「手机连不上你」「接上手机」「重连」「手机卡住了」「紧急修复」「找回原来的对话」, "phone is grey", "reconnect my phone", "connect my phone", "bring back my earlier conversation". Points the owner to the phone's Settings → 紧急修复 ①; checks the background service, the relay and the Agent in turn with `agentj status --json`, repairs the broken link with `agentj doctor --reconnect`, and tells the owner one plain sentence; `agentj rescue undo` brings back the conversation a phone reset put aside.
---
# Bring a grey phone back (起死回生)

The owner should never need to know a command. You run these yourself.

**The phone's own repair (P332).** When the owner says the phone / web page is stuck, offline or not replying, first point
them to Settings → 「紧急修复」 / "Emergency repair" → ① 「重置连接」 / "Reset connection" (also ≡ or `/reset`; the offline card's
「紧急修复」 button opens it). It reconnects → finds the conversation → opens a new conversation if needed (the old one is
kept) → restarts Agent J, and stops at the first step that works. ① did not help → ② 「请 Agent J Father 远程帮忙」 (a redacted
check-up the owner starts; support may only pick repairs from a fixed list). ③ 「AI 账号登录失效」 is the AI login rescue.
- 「找回原来的对话」 / "bring back my earlier conversation" after a reset → run `agentj rescue undo` (it pins the conversation
  the reset put aside; run it again to flip back), then tell the owner in one sentence.
- `agentj rescue status` — the rescue watchdog (installed by `agentj service install` and every update; it answers the phone
  when `agentj serve` is frozen). Not installed → `agentj rescue install`.
- Someone at the Agent J host and the phone cannot reach it at all → first use the menu’s Emergency repair. If it fails, share https://agentj.app/docs/reconnect-help/ for OS-specific terminal instructions, rescue commands, privacy explanation and recovery checks. Keep commands on that page, not in the offline dialog.


1. `agentj status --json`
   - Not running (`running: false`) → step 2.
   - `relay_state` is `reconnecting` → step 2; quote `relay_stuck` (where the relay link sticks: phase / reason / close code).
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
that just started) and the phone says 「又接上了，新会话 <id>」. It stops and asks only when two sessions are equally likely.
A grey phone is not a broken relay — the three links are separate:

| Link | Look at | Broken means |
|---|---|---|
| service | `agentj status` answers at all | `agentj serve` not running |
| relay | `relay_state` | the computer cannot reach the Agent J relay (network / account) |
| Agent | `agent_status` | Agent J is not attached to a live AI session (`down`) |

中文：手机/网页变灰，先 `agentj status --json`，不正常就在本会话运行 `agentj doctor --reconnect`（依次查后台服务、中继、
Agent 三段，哪段断了修哪段，并把运行它的会话接上手机），把最后那句结论原话告诉主人；列出多个会话时用
`--session <ID>` 指定；仍失败按 agentj-manual 求助。手机灰 ≠ 中继坏，多半是Agent J 主机上换了新会话。
主人说手机卡住/不回消息：先让他点 设置 →「紧急修复」→ ①「重置连接」，不行再点 ②；主人要「找回原来的对话」就运行
`agentj rescue undo`；`agentj rescue status` 查看救援守护；Agent J 主机旁边有人时先走菜单紧急恢复，仍失败再看 https://agentj.app/docs/reconnect-help/。
Docs: https://agentj.app/docs/reconnect/zh.md · https://agentj.app/docs/reconnect/en.md

## Model fallback

A failed owner turn may receive a separate read-only diagnostic labelled “This is a fallback model answer” / 「这是兜底模型的回答」. Order: the owner's model → freshly listed free OpenCode DeepSeek Flash → currently documented zero-retention/no-training LongCat → explicitly installed Ollama qwen3.5:9b. Space Bunny / MiniMax M3.1 Flash are excluded. Missing DeepSeek triggers one persistent notice; unavailable terms/catalog skip the cloud layer. The host does not change the main harness or resend the task. Cloud sees only fixed error signals, not the owner prompt/files/keys. Local diagnosis sees the redacted failure notice, quotes it, and cannot act. Treat model guesses as uncertain; never claim it repaired or completed the failed task.

On Apple Silicon 16GB+ with 20GB free, offer the optional ~6.6GB download after explaining ~8GB runtime memory and imperfect accuracy: install/start Ollama, then `agentj local-model install`; `status` reports eligibility/enabled, `off` disables. The installer accepts `--local-model`. Intel/CPU Linux are not offered it by default. It loads on demand and unloads after use. Installing weights requires the owner's choice. These commands do not change pairing, accounts, approvals or the owner's main model.
