---
name: agentj-reconnect
description: No reply / "Not yet received" / only a phone, or phone or web page grey / disconnected / "not connected", or the first turn of a new session (also after compaction, /clear or resume) — 「手机灰了」「网页断开了」「手机连不上你」「接上手机」「重连」「手机卡住了」「紧急修复」「找回原来的对话」, "phone is grey", "reconnect my phone", "connect my phone", "bring back my earlier conversation". For no reply, guide Stop → Settings Run doctor --reconnect → Restart service → Reopen AI session (0.25.2a1+) → Father help; grey/offline uses Emergency repair ①; checks the background service, the relay and the Agent in turn with `agentj status --json`, repairs the broken link with `agentj doctor --reconnect`, and tells the owner one plain sentence; `agentj rescue undo` brings back the conversation a phone reset put aside.
---
# Bring a grey phone back (起死回生)

Run doctor resources when busy. Never promise uptime.

## Agent 没回应、只有手机时怎么办 {#phone-only}

消息没回应，或一直显示「尚未接收」？不用到电脑前。每做一步，回聊天发一条简短消息，收到回复就停：

1. 点 **Stop**，打断当前任务。
2. 打开 **设置 → 紧急修复 → Agent J 主机上的命令**，找到 `agentj doctor --reconnect`，点旁边的 **执行**（推荐）。这是在手机上让主机检查并修复连接，不用自己打开终端；按提示用 Face ID 确认。
3. 仍没回应，在 **紧急修复** 点 **重启服务**（推荐），等连接恢复后再试。
4. 仍没回应，点 **紧急修复 → 重开 AI 会话**（主机与网页均需 0.25.2a1 起的版本）。它结束卡住的 AI 会话进程，优先接回同一段对话；无法恢复时开新会话，原对话保留。旧版没有这个按钮，直接下一步。
5. 仍没解决，点 **紧急修复 → ② 请 Agent J Father 远程帮忙**。只有你点了才会发送在主机上脱敏的诊断报告；记下工单号。不发送密码、密钥、聊天或工作文件。

**先分清额度与连接。** 提示 Claude 额度用完时，先在手机的 Claude 设置看 **Usage**；若页面提供 **免费重置**，可按提示使用，否则等它显示的重置时间。免费重置不是每个账号、每次都提供。额度恢复后仍「尚未接收」，再按上面五步试；重启和重开会话不会增加额度。AI 登录过期则用 **AI 登录救援**。

**手机灰了、显示断开是另一条线。** 先检查手机网络、点「立即重试」，再进 **设置 → 紧急修复 → ① 重置连接**，没恢复再点 **② 请 Agent J Father 远程帮忙**。短暂断线先等几秒。命令、重启和会话修复都需要手机能接通主机或救援守护；主机关机、休眠或断网时不能从手机强行执行。一直连不上时，在手机浏览器打开 [在线客服](https://support.agentj.app) 或写信 [feedback@agentj.app](mailto:feedback@agentj.app)，说明症状、版本和发生时间，不贴凭据。无需回电脑前。

Agent J Father 是官方开发者客服平台。聊天恢复后，也可以用 `/feedback-to-agentj-father` 汇报问题；仍没回应时，直接使用上面的手机求助入口。

## Agent not replying and you only have your phone? {#phone-only}

No reply, or stuck on “Not yet received”? You can do this from your phone. After each step, send a short test message in chat and stop when a reply arrives:

1. Tap **Stop** to interrupt the current task.
2. Open **Settings → Emergency repair → Terminal Commands**, find `agentj doctor --reconnect`, and tap **Run** beside it (recommended). Your phone asks the host to check and repair the connection; you do not need to open a terminal. Confirm with Face ID when asked.
3. Still no reply? In **Emergency repair**, tap **Restart service** (recommended), wait for the connection to return, then try again.
4. Still no reply? Tap **Emergency repair → Reopen AI session** (requires host and web versions from 0.25.2a1). It ends the stuck AI session process and tries to resume the same conversation; if that fails, it opens a new session and keeps the old conversation. On an older version without this button, go to the next step.
5. Still stuck? Tap **Emergency repair → ② Ask Agent J Father for remote help**. Only your tap sends a diagnostic report redacted on the host; keep the ticket number. Passwords, keys, chats and work files are not sent.

**Check quota before connection repairs.** For a Claude quota limit, first open Claude settings on your phone and check **Usage**. Use **Free reset** if offered; otherwise wait until the displayed reset time. A free reset is not available for every account or every limit. If quota has recovered but messages are still “Not yet received”, follow the five steps above. Restarting or reopening a session does not add quota. For an expired AI sign-in, use **AI login rescue**.

**A grey, disconnected phone takes a different path.** Check your phone’s network, tap “Try now”, then **Settings → Emergency repair → ① Reset connection**, followed by **② Ask Agent J Father for remote help** if needed. Give a brief drop a few seconds. Commands, restarts and session repair require a connection to the host or rescue helper; a phone cannot force them to run on a powered-off, sleeping or offline host. If it stays unreachable, open [the support chat](https://support.agentj.app) in your phone browser or email [feedback@agentj.app](mailto:feedback@agentj.app) with the symptom, versions and time, never credentials. You do not need to go to the computer.


Agent J Father is the official support platform run by the developers. Once chat works, `/feedback-to-agentj-father` can report the problem too; while there is no reply, use the phone support controls above.

Owner needs their earlier conversation after a reset → run `agentj rescue undo` yourself. Never ask a remote owner to visit the computer or open its terminal. The following commands are for the Agent to run, not homework for the owner.


1. `agentj status --json`
   - Not running (`running: false`) → step 2.
   - `relay_state` is `reconnecting` → step 2; quote `relay_stuck` (where the relay link sticks: phase / reason / close code).
   - `agent_status` is `down` (or anything other than `idle` / `working` / `waiting` / `compacting`) → step 2.
   - Check `harness_readiness.ready`: false/missing means AI unverified even with relay connected. OpenCode startup failure → phone Reopen AI session, then send once. Never auto-resend. Adapter-ready is not a model reply test.
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

Docs: https://agentj.app/docs/reconnect/#phone-only

## Model fallback

After a failed owner turn, label any separate read-only diagnosis “This is a fallback model answer” / 「这是兜底模型的回答」. Order: owner model → freshly listed free DeepSeek Flash → GLM-5.3-Flash Free under current official zero-retention/no-training terms → explicitly installed Ollama qwen3.5:9b. Exclude LongCat, Space Bunny and MiniMax M3.1 Flash. Missing DeepSeek gets one persistent notice; unknown catalog/terms skip cloud. No main harness switch or task replay. Cloud: fixed error signals only, no owner prompt/files/keys. Local: quote the redacted failure notice. Neither acts or claims repairs/completion; guesses are uncertain.

On Apple Silicon 16GB+ with 20GB free, offer the optional ~6.6GB download after explaining ~8GB runtime memory and imperfect accuracy: install/start Ollama, then `agentj local-model install`; `status` reports eligibility/enabled, `off` disables. The installer accepts `--local-model`. Intel/CPU Linux are not offered it by default. It loads on demand and unloads after use. Installing weights requires the owner's choice. These commands do not change pairing, accounts, approvals or the owner's main model.


OpenCode / ACP diagnostics: bare `/model`, `/status`, `/context` and `/cost` can answer during a busy turn; a query times out after 5 s. Mutating slash commands expire after 10 s waiting for the turn; advise Stop then retry. Expired commands never execute later. `agentj devices --json` uses a public-field allowlist; never request devices.json or private pairing/passkey material.
## Background checks (0.25.3)

Status, reconnect and support use the host service, including native Codex isolation. If chat works but the host check is unavailable, say: “切换已成功，聊天正常；安全隔离不让自检读本机钥匙文件，这一步可以忽略，稍后由后台再检查。” / “The switch succeeded and chat works; isolation hides local key files. Skip this check and retry through the background service later.” Do not disable isolation. A ticket ID confirms feedback was sent; otherwise it is unconfirmed. Never invent a successful check.

OpenCode 1.x/2.x: https://agentj.app/docs/install/#opencode-compatibility .
