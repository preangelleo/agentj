---
name: agentj-reconnect
description: Repair no reply / "Not yet received", grey/disconnected phone or web, and check a new session (including compaction, /clear or resume). Triggers: 「手机灰了」「网页断开了」「手机连不上你」「接上手机」「重连」「手机卡住了」「紧急修复」「找回原来的对话」; "phone is grey", "reconnect/connect my phone", "bring back my earlier conversation".
---

默认派 CEO/子 Agent，写验收、复验、汇报；主人明确要求亲做就照办。指纹清单、凭据规则和 harness 硬限制不变。
Delegate by default; follow explicit owner requests to work personally. Verify and report; UV, credential rules and harness limits remain.

# Bring a grey phone back (起死回生)

When busy, run doctor resources. Never promise uptime.

## Agent 没回应、只有手机时怎么办 {#phone-only}

消息没回应，或一直显示「尚未接收」？不用到电脑前。每做一步，回聊天发一条简短消息，收到回复就停：

1. 点 **Stop**，打断当前任务。
2. 打开 **设置 → 紧急修复 → 终端指令**，点 `agentj doctor --reconnect` 旁的 **执行**（推荐），按提示用 Face ID 确认；手机让主机检查修复，无需打开终端。
3. 仍没回应，在 **紧急修复** 点 **重启服务**（推荐），连接恢复后重试。
4. 仍没回应，点 **紧急修复 → 重启 Agent**（主机、网页均需 0.25.2a1+）；结束卡住进程，优先接回原对话，失败则保留原对话、另开会话。旧版无按钮就跳过。
5. 仍没解决，点 **紧急修复 → ② 请 Agent J Father 远程帮忙**，记下工单号。仅在你点后发送主机脱敏诊断，不发密码、密钥、聊天或工作文件。

**先分额度与连接。** Claude 额度用完，先在手机 Claude 设置看 **Usage**；有 **免费重置** 才按提示使用（非每个账号/每次都有），否则等显示的重置时间。额度恢复仍「尚未接收」再走五步；重启/重开不增额度。登录过期用 **AI 登录救援**。

**手机灰了/断开另处理：** 短暂掉线先等几秒，查手机网络、点「立即重试」，再走 **设置 → 紧急修复 → ① 重置连接**，无效再 **② 请 Agent J Father 远程帮忙**。命令/重启/会话修复需接通主机或救援守护，不能强行操作关机、休眠或离线主机。仍不通，用手机 [在线客服](https://support.agentj.app) 或 [feedback@agentj.app](mailto:feedback@agentj.app)，报症状、版本、时间，不贴凭据；无需回电脑。

Agent J Father 是官方开发者客服。聊天恢复可用 `/feedback-to-agentj-father`；无回应时用上述手机求助入口。

## Agent not replying and you only have your phone? {#phone-only}

No reply / “Not yet received”? Use your phone; after each step send a short test message and stop when answered:

1. Tap **Stop** to interrupt the current task.
2. Open **Settings → Emergency repair → Terminal Commands**, tap **Run** beside `agentj doctor --reconnect` (recommended); confirm with Face ID when asked. The phone requests host repair without opening a terminal.
3. Still no reply? In **Emergency repair**, tap **Restart service** (recommended); wait for reconnection and retry.
4. Still no reply? Tap **Emergency repair → Restart Agent** (host and web 0.25.2a1+). It ends the stuck process, tries the same conversation, or keeps it and opens a new one. Skip if an older version lacks the button.
5. Still stuck? Tap **Emergency repair → ② Ask Agent J Father for remote help**; keep the ticket number. Only your tap sends host-redacted diagnostics, never passwords, keys, chats or work files.

**Check quota first.** In phone Claude settings, check **Usage**. Use **Free reset** only if offered (not every account/limit); otherwise wait until the displayed reset time. After quota recovers, persistent “Not yet received” needs the five steps. Restart/reopen adds no quota. Expired sign-in needs **AI login rescue**.

**Grey/disconnected:** wait a few seconds for a brief drop, check phone networking and “Try now”, then **Settings → Emergency repair → ① Reset connection**, followed by **② Ask Agent J Father for remote help** if needed. Commands/restart/session repair need the host or rescue helper online; they cannot force a powered-off, sleeping or offline host. Still unreachable? Use phone [support chat](https://support.agentj.app) or [feedback@agentj.app](mailto:feedback@agentj.app): symptom, versions, time, never credentials. No computer visit needed.


Agent J Father is support from the developers. Once chat works, use `/feedback-to-agentj-father`; otherwise use the phone controls above.

After a reset, restore the requested earlier conversation with `agentj rescue undo`. Run the commands below yourself; never send a remote owner to the computer/terminal.


1. `agentj status --json`
   - `running: false`, `relay_state: reconnecting`, or `agent_status` outside `idle` / `working` / `waiting` / `compacting` (including `down`) → step 2. Quote `relay_stuck` phase/reason/close code for relay failures.
   - Check `harness_readiness.ready`: false/missing means AI unverified even with relay connected. OpenCode startup failure → phone Restart Agent, then send once. Never auto-resend. Adapter-ready is not a model reply test.
2. `agentj doctor --reconnect` — run it **in this session**: it attaches the session that runs it.
   In order, repair only the broken link: **service** (`agentj serve`) unresponsive → start/restart; **relay** (host ↔ relay) disconnected → wait, then one restart; **Agent** (Agent J ↔ AI session) → reattach, listing concurrent sessions in the folder.
   Exit 0 = all three up. Repeat its last line (✓ / ✗ sentence) to the owner word for word.
3. Several sessions (`choices`) → `agentj doctor --reconnect --session <ID>` for the owner's session; inside it, omit `--session`.
4. Still ✗ → `agentj doctor`, then the agentj-manual support steps. Never edit config files by hand, never restart
   other people's processes, never ask the owner for keys.

Shared mode follows one Claude Code session. Reboot/new window/`/clear`/upgrade can replace it. Since 0.18.4 Agent J follows the sole live owner session in the folder or the newly started one, saying 「又接上了，新会话 <id>」; it asks only if two are equally likely.
A grey phone does not mean the relay is broken: service, relay and Agent are separate links (see step 2).

Docs: https://agentj.app/docs/reconnect/#phone-only

## Model fallback

After a failed owner turn, label separate read-only diagnosis “This is a fallback model answer” / 「这是兜底模型的回答」. Order: owner model → freshly listed free DeepSeek Flash → GLM-5.3-Flash Free under current official zero-retention/no-training terms → explicitly installed Ollama qwen3.5:9b. Exclude LongCat, Space Bunny and MiniMax M3.1 Flash. Missing DeepSeek gets one persistent notice; unknown catalog/terms skip cloud. No main harness switch or task replay. Cloud: fixed error signals only, no owner prompt/files/keys. Local: quote the redacted failure notice. Neither acts or claims repairs/completion; guesses are uncertain.

Apple Silicon 16GB+ / 20GB free: offer optional ~6.6GB weights, explaining ~8GB runtime RAM and imperfect accuracy. Owner choice required; install/start Ollama, then `agentj local-model install` (installer: `--local-model`). `status` reports eligibility/enabled; `off` disables. Load on demand, unload after use. No default offer on Intel/CPU Linux; no pairing/account/approval/main-model change.


OpenCode / ACP: bare `/model`, `/status`, `/context`, `/cost` work during busy turns (5 s query timeout). Mutating slash commands expire after 10 s awaiting the turn; advise Stop/retry, never execute expired commands later. `agentj devices --json` is public-field-only; never request devices.json or private pairing/passkey material.
## Background checks (0.25.3)

Status/reconnect/support use the host service, including native Codex isolation. If chat works but the host check is unavailable, say: “切换已成功，聊天正常；安全隔离不让自检读本机钥匙文件，这一步可以忽略，稍后由后台再检查。” / “The switch succeeded and chat works; isolation hides local key files. Skip this check and retry through the background service later.” Do not disable isolation. Only a ticket ID confirms sending; never invent delivery or a successful check.

OpenCode 1.x/2.x: https://agentj.app/docs/install/#opencode-compatibility .
