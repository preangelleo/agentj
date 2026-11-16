---
name: agentj-relay-check
description: Agents Relay for background Claude Code / Codex. Hand one task to Claude Code (development) or Codex (writing, content) through the owner's Agents Relay key after the owner's Face ID card, whatever the main Agent is; or check whether locally configured background accounts answer one short request. 「派给 Claude Code」「让 Codex 写」「走中转派活」「检查中转」「check relay」「dispatch via relay」. Never change the main Agent or expose a key.
---

# Agents Relay: dispatch a background job, or check an account

## Dispatch one job (`agentj relay … --full`)

Use this when the owner wants work done by Claude Code or Codex and has saved an Agents Relay key in Agent J (phone: Settings → Models & API keys → Agents Relay). It works under any main Agent (Claude, Codex, OpenCode, Kimi, Ark, Bailian …) and never touches the owner's own Claude Code or Codex sign-in or settings.

- Development (code, tests, refactors, debugging) → `agentj relay claude --full "<task>"`. Writing and content production → `agentj relay codex --full "<task>"`.
- Sensitive work stays off this route: passwords, keys, customer data, financial records, production access. The task, the files it reads and the replies pass through a third party (Agents Relay). Use the owner's official account or do it yourself.
- First run `agentj relay status`: it says which of `AGENTSRELAY_CLAUDE_KEY` / `AGENTSRELAY_OPENAI_KEY` are set (names only). If the needed one is missing, tell the owner where to add it on the phone. Never ask for a key in chat, arguments or files. `agentj relay status --check` sends one read-only request with each stored key.
- Write a self-contained task: the job does not see this conversation. Name the goal, the files, what "done" means and what not to touch. `--cwd <folder>` sets the working folder (default: the current one); `--minutes N` the time limit (1–240, default 60); `-` as the task reads it from stdin.
- Each request raises **one Face ID card** on the owner's phone showing the tool, folder, model, time limit and the whole task. Nothing starts without it. The job then runs headless with **full permissions on this computer** (no sandbox, no step confirmations), so say plainly what it will do before you ask.
- Give the command a long shell timeout (10 minutes) and wait for the card. The first stderr line after approval gives the job id. If your command is cut short, the job keeps running; read the result later with `agentj relay status relay:<id>`. Output comes back redacted; never claim it is complete when it says `withheld`.
- Exit codes: 0 completed · 2 not started (declined, expired, key missing, busy …: `error` says why) · 3 started and failed or timed out (`why`: auth / quota / rate_limit / model / network / exit_nonzero) · 4 outcome unknown. **Never resubmit automatically** after 3 or 4: the job may have changed files. Check the folder, then ask the owner. At most four jobs per tool run at once.
- Without `--full`, `agentj relay codex` keeps its older meaning (the current Codex session's own Relay profile and sandbox ceiling); `agentj relay claude` without `--full` is refused.

## Check a background CLI account (`check.py`)

This part checks only the owner's already-installed background CLIs, not the main Agent. Before running, explain that each installed CLI whose current base URL is the official `agentsrelay.net` or `api.agentsrelay.net` HTTPS domain will make **one short model request** (which may consume plan quota), up to 60 seconds per CLI. Do not run without the owner's instruction to check. No paid/live request is part of installation or automated tests.

Run `python3 -I <this skill directory>/check.py --json` on the host in the same environment that runs the background CLIs. The checker reads only the configured base URL to decide whether to call. It never prints keys, config content, model replies, command output or exception text; output is one fixed status per CLI. Do not paste a package, key, curl command, terminal export, raw diagnostics or CLI config into chat. Never source a key file to perform the check. No account restore, main-Agent switch or configuration write happens here.

Statuses: `ok` = the one call returned output (not proof that every model or plan feature works); `not_installed` = CLI missing; `not_relay` = selected configuration does not point to the official HTTPS domain; `auth` = 401/sign-in issue; `forbidden` = 403/plan or region; `model_or_endpoint` = 404; `quota` = 429/rate or plan; `timeout` = 60-second deadline; `empty_reply` / `call_failed` = no usable response. Do not retry automatically. For an unresolved status, ask the owner to check their plan and the vendor's configuration package; do not request the key in chat.

Use this only as a background account diagnostic; `/switch-harness` is a separate explicit owner decision. This check does not install or fully configure Agents Relay, and cannot verify fleet environments from a single machine.

## 中文

派活：主人在手机「设置 → 模型和 API Key → Agents Relay」存了密钥后，不管主 Agent 用哪家，都可以用 `agentj relay claude --full "<任务>"`（开发）或 `agentj relay codex --full "<任务>"`（写作、内容生产）派一单。先 `agentj relay status` 看密钥是否已设置（只给名字）。每单都会在主人手机上弹一张 Face ID 确认卡，写明工具、目录、模型、时限和完整任务；主人同意后才由 Agent J 主机在后台以**完全权限**运行（无沙箱、不逐步确认）。任务要写成独立可执行的说明。命令给 10 分钟超时；被中断也不影响任务，用 `agentj relay status relay:<编号>` 取结果，输出已脱敏。退出码 3（开始后失败/超时）或 4（结果未知）时**不要自动重派**，先看目录再问主人。含密码、密钥、客户资料、财务记录、生产权限的活不走中转，用主人的官方账号或自己做。不碰主人自己的 Claude Code / Codex 登录和设置；密钥绝不进聊天、命令参数或文件。

检查：只在主人要求时运行 `check.py`，每个指向官方中转域名的后台工具发一次短请求（可能消耗额度），只输出固定状态，不自动重试。
