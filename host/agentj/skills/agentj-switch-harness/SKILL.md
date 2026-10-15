---
name: agentj-switch-harness
description: Switch the AI harness under Agent J — the phone menu's `/switch-harness` (with or without words), 「切换 harness」「换底层」「切换到 Codex / OpenCode / Claude Code」「切回 Claude Code」「能换成别的 AI 吗」, "switch harness", "switch to Codex / OpenCode / Claude Code", "use another agent". Explains what a harness is, scans which harnesses are installed, probes each one headless with the owner's own login (≤ 60 s, nothing written), reports what can be switched to, helps install and guides the owner through login, and switches only through the `agentj agent switch` gate (auto switch-back on failure).
---
# Switch the harness — carefully (切换底层 harness)

A switch restarts Agent J and replaces **you**. If the other side has no working login, the owner — away from the computer,
maybe unable to use a terminal — is left with nobody to talk to. So: scan, probe, report, and switch only through the gate.

**What a harness is (say it in plain words when asked, or when `/switch-harness` came with no words):** the AI program on this
computer that actually thinks and does the work — Claude Code, Codex or OpenCode. Agent J is the phone line to it. Switching
changes which AI answers; the folder, phone, approvals and safety rules stay the same. Each harness keeps its own conversation;
switching back resumes that one. To switch, the owner just replies 「切换到 Claude Code / Codex / OpenCode」.

1. **Scan + probe** (read-only; one tiny tool-free model call per harness with the owner's own login, ≤ 60 s each):
   `agentj agent switch --scan` (`--json` for data) — a job outside Agent J probes Claude Code, Codex and OpenCode in Agent
   J's SERVICE environment (the one a switched-to harness will run with) and prints per harness: not installed / works now /
   why not. Use it even inside the fence: your own environment hides harnesses whose path or key lives only in the service
   environment (`agentj agent detect --probe --json` is the fallback when Agent J is not a service). Nothing is written, no
   key is read or printed.
2. **Report** in one short message: which one runs now, and 「现在能切到：X、Y」 (only `callable: true` ones) — or
   「这台电脑只有现在这一个能用，没法切换；要切换得先装一个别的」. For each installed-but-not-callable one say why
   (not signed in / token expired / timed out — network or proxy).
3. **Not installed / not signed in** — offer, never push:
   - Install only after the owner says yes: follow https://agentj.app/docs/setup-agents/en.md (one-command `setup-agents.sh`
     with `--cli <name>`, or that page's per-OS steps). Needs admin rights → `agentj sudo --why '…' -- <cmd>`.
   - Login is the owner's own act. Guide them step by step: Claude Code `claude auth login` (or `claude` then `/login`), Codex
     `codex login`, OpenCode `opencode auth login <provider>` — in **their own terminal**; tell them what they will see and
     what to click. A model API key never goes through chat: `agentj secret request --name <VAR> --purpose '…' --dest …`
     (masked phone card; see agentj-config). Never ask for or repeat passwords, tokens, callback URLs or codes; never click
     through an unverified-app warning for them.
   - Then scan again (step 1). Only a fresh "works now" counts.
4. **Switch** — only on the owner's clear choice of a target that just passed step 1. This is their instruction; no extra
   approval card. First tell them: 「Agent J 会重启，手机断开几秒后自动重连，然后 X 会先跟你打招呼；如果 X 没起来，会自动切回
   我这里并说明原因」. Then run:
   `agentj agent switch <claude|codex|opencode>` (OpenCode may add `--model provider/model`).
   It starts a job outside Agent J that probes the target again in the service's environment (the gate: exit 3 + reason =
   nothing changed — tell the owner the reason and go back to step 3; exit 4 = still checking, look with `--result` in a
   minute). On ✓ the job waits for this turn to end, switches, restarts, checks that the new harness is up, answers its
   self-introduction and is idle again — and on any failure restores the previous harness exactly and makes it explain.
   Finish your turn right after it prints ✓; do not run anything else.
5. **Afterwards** (either side): `agentj agent switch --result` shows the last outcome (✓ / rolled back + reason).

Shared Codex whose conversation is read-only refuses `agentj …` (outside its sandbox): tell the owner Codex's refusal sentence
— that conversation needs Full access (see agentj-config, `agentj codex-sandbox`); never work around the sandbox.

Never switch by `agentj agent <kind>` + `agentj service restart`, by editing `~/.config/agentj/` or by restarting the service:
that skips the gate and the automatic switch-back. Never switch to a harness whose probe failed, "to see if it works".
Isolation, session mode (shared / independent), the folder and approvals are unchanged by a switch.

中文：主人发 `/switch-harness`（没带话就先解释什么是 harness），或说「切换到 X」时：① 扫描 + 无头探测（`agentj agent switch --scan`，用服务的环境，每个 ≤ 60 秒、只读、不打印凭据）；② 告诉主人现在能切到哪些，只有一个就说没法切、要先装别的；③ 没装的经主人
同意按 setup-agents 文档帮装，登录由主人在自己的终端完成（你一步步指导；密钥走 `agentj secret request` 手机卡片，绝不进聊天），
装好登好再回到 ①；④ 目标探测通过、主人明确要切，先说「会断开几秒自动重连，新 harness 会先自报家门，不通会自动切回」，再运行
`agentj agent switch <目标>`——电脑上的独立任务用服务的环境再探测一次（闸门；`--dry-run` 只探测不切），切换、重启、自检
都由它完成，失败自动切回原 harness 并说明；
⑤ `agentj agent switch --result` 看结果。绝不绕过闸门直接改配置或重启服务。
Docs: https://agentj.app/docs/switch-harness/zh.md · https://agentj.app/docs/switch-harness/en.md
