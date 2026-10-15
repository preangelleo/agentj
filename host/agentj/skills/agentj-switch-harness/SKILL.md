---
name: agentj-switch-harness
description: Switch the AI harness under Agent J — the phone menu's `/switch-harness` (with or without words), 「切换 harness」「换底层」「切换到 Codex / OpenCode / Claude Code / Kimi Code / Qwen Code」「切回 Claude Code」「能换成别的 AI 吗」, "switch harness", "switch to Codex / OpenCode / Claude Code / Kimi Code / Qwen Code", "use another agent". Explains what a harness is, scans which harnesses are installed, probes each one headless with the owner's own login (≤ 60 s, nothing written), reports what can be switched to, helps install and guides the owner through login, and switches only through the `agentj agent switch` gate (auto switch-back on failure).
---
# Switch the harness — carefully (切换底层 harness)

A switch restarts Agent J and replaces **you**. If the other side has no working login, the owner — away from the computer,
maybe unable to use a terminal — is left with nobody to talk to. So: scan, probe, report, and switch only through the gate.

**What a harness is (say it in plain words when asked, or when `/switch-harness` came with no words):** the AI program on this
computer that actually thinks and does the work — Claude Code, Codex, OpenCode, Kimi Code or Qwen Code. Agent J is the phone line to it. Switching
changes which AI answers; the folder, phone, approvals and safety rules stay the same. Each harness keeps its own conversation;
switching back resumes that one. To switch, the owner just replies 「切换到 Claude Code / Codex / OpenCode / Kimi Code / Qwen Code」.

1. **Scan + probe** (read-only; one tiny tool-free model call per harness with the owner's own login, ≤ 60 s each):
   `agentj agent switch --scan` (`--json` for data) — a job outside Agent J probes Claude Code, Codex, OpenCode, Kimi Code and Qwen Code in Agent
   J's SERVICE environment (the one a switched-to harness will run with) and prints per harness: not installed / works now /
   why not. Use it even inside the fence: your own environment hides harnesses whose path or key lives only in the service
   environment (`agentj agent detect --probe --json` is the fallback when Agent J is not a service). Nothing is written, no
   key is read or printed.
   It takes up to about 5 minutes. Say so in one short sentence **before** you run it (「我先扫描一下，大约 1–3 分钟」) — the
   phone also shows Agent J's own line under the dancing robot (「正在探测 Codex（2/3）…」, then 「正在切换 / 已切回」 during a
   switch); never leave the page empty and never put a placeholder character or a lone emoji first.
2. **Report** in one short message: which one runs now, and 「现在能切到：X、Y」 (only `callable: true` ones) — or
   「这台电脑只有现在这一个能用，没法切换；要切换得先装一个别的」. For each installed-but-not-callable one say why
   (not signed in / token expired / timed out — network or proxy). `not signed in` here means the SERVICE sees no login
   (e.g. Claude Code's token only in the owner's `~/.zshenv`): the scan line already says the one step — repeat it in plain
   words (`claude` → `/login` once, the login goes to the Keychain / credentials file the service can read; or
   `agentj service import-key CLAUDE_CODE_OAUTH_TOKEN` + `agentj service restart` in a terminal where claude works). It
   works in YOUR session is not proof — only the scan counts. A line saying an old saved path was replaced (a harness that
   updated itself) needs nothing; another install listed as unused may be removed if the owner wants.
   Claude Code / Codex fail with "region not supported", or the owner is in Hong Kong / Macau / mainland China: run
   `agentj agent route` — where they are not offered it recommends a Kimi membership Plus or higher in OpenCode (Go has no
   coding quota) or in Kimi's own Kimi Code CLI (`kimi`) — the owner chooses. Give its link with the sentence right after it, never apart: 「这是 Agent J 官方的邀请链接，您和我们各得一次抽奖」
   (en: "This is Agent J's official invite link: you and we each get one prize draw"), plus its alternatives; the key only
   via the phone key card it prints (`KIMI_API_KEY`), then `agentj service restart` and scan again. Side by side with Kimi
   it recommends an Alibaba Cloud Bailian Token Plan, personal (¥39–499 a month; Qwen 3.8 Max / Flash read images):
   `agentj provider bailian` writes OpenCode's config without touching what is there and prints the key card
   (`BAILIAN_TOKEN_PLAN_API_KEY`); then `agentj agent switch opencode --model bailian-token-plan-personal/qwen3.8-max` (or
   `agentj agent switch qwen`). Always tell the owner its terms in plain words: only their own interactive use, never
   unattended scripts / bots (Agent J never gives this key to tasks or bots), and inputs / outputs are used by Alibaba Cloud
   to improve its models.
3. **Not installed / not signed in** — offer, never push:
   - Install only after the owner says yes: follow https://agentj.app/docs/setup-agents/en.md (one-command `setup-agents.sh`
     with `--cli <name>`, or that page's per-OS steps). Needs admin rights → `agentj sudo --why '…' -- <cmd>`.
   - Login is the owner's own act. Guide them step by step: Claude Code `claude auth login` (or `claude` then `/login`), Codex
     `codex login`, OpenCode `opencode auth login <provider>`, Kimi Code `kimi login --region mainland-cn` (kimi.ai:
     `--region global`; or the phone's Settings → AI login rescue device code) — in **their own terminal**; tell them what they will see and
     what to click. A model API key never goes through chat: `agentj secret request --name <VAR> --purpose '…' --dest …`
     (masked phone card; see agentj-config). Never ask for or repeat passwords, tokens, callback URLs or codes; never click
     through an unverified-app warning for them.
   - **OpenCode with no key (P298):** its own free models (`opencode/…-free`, list with `opencode models opencode`; it changes
     daily) need no login — offer `agentj agent switch opencode --model opencode/<free model>` as the no-key way. The check
     of a free model may wait ~1 min (`free_tier` = its free channel refused for now: try later or another free model).
   - **Kimi Code (P305):** install `curl -fsSL https://code.kimi.com/kimi-code/install.sh | bash` (2.2.0+). It runs in an
     independent session only, over ACP: every approval is a phone card answered "this once", its own auto / YOLO modes are
     switched back, its hooks are not relied on. kimi.com and kimi.ai are separate accounts — a kimi.ai member switches with
     `--region global`. A Kimi Code console key goes only through the key card (`KIMI_API_KEY`).
   - **Qwen Code (P310):** `npm install -g @qwen-code/qwen-code` (0.25+). Independent sessions only, over the same ACP
     adapter: its auto / auto-edit / YOLO modes are switched back to "default", every approval is a phone card. It has no
     login of its own (the free one ended 2026-04-15): an owner's own `~/.qwen/settings.json` (e.g. from `bl config agent`)
     is used as it is; otherwise a Bailian key from the key card (`BAILIAN_TOKEN_PLAN_API_KEY`) — Agent J passes it to Qwen
     in memory only. `/model` lists the plan's models (those marked 看图 read screenshots).
   - Then scan again (step 1). Only a fresh "works now" counts.
4. **Switch** — only on the owner's clear choice of a target that just passed step 1. This is their instruction; no extra
   approval card. First tell them: 「Agent J 会重启，手机断开几秒后自动重连，然后 X 会先跟你打招呼；如果 X 没起来，会自动切回
   我这里并说明原因」. Then run:
   `agentj agent switch <claude|codex|opencode|kimi|qwen>` (OpenCode may add `--model provider/model`; Kimi `--region global`).
   It starts a job outside Agent J that probes the target again in the service's environment (the gate: exit 3 + reason =
   nothing changed — tell the owner the reason and go back to step 3; exit 4 = still checking, look with `--result` in a
   minute). On ✓ the job waits for this turn to end, switches, restarts, checks that the new harness is up, answers its
   self-introduction and is idle again — and on any failure restores the previous harness exactly and makes it explain.
   Finish your turn right after it prints ✓; do not run anything else. If the ✓ printout has a 「提示：…免费模型…」 / "Note: …
   free model" line (its provider logs or trains on the conversation), pass that sentence on to the owner word for word.
   **Handoff (P289):** right before the switch Agent J sends you an 〔Agent J 系统提示〕 asking for the handoff — write
   `switch.md` at exactly the path it names (agentj-handoff format, the essentials only, ≤ 150 lines, no credentials), reply
   one line and stop. The new harness reads it before greeting the owner, then carries on from its next step. Not written
   within 3 minutes → the switch goes ahead and the owner is told there was no handoff.
   The gate also tries the way back — the harness running now. If that fails (`no_way_back`), nothing changed: tell the
   owner the reason and the fix (step 3) first; only if the owner still wants the one-way switch, run it again with
   `--force`.
5. **Afterwards** (either side): `agentj agent switch --result` shows the last outcome (✓ / rolled back + reason and the next
   step — relay that step in plain words).
6. **Going back** — 「切回 X」 where X is the harness used before the last switch: try `agentj agent switch X` first; if its
   check fails, the printout offers `agentj agent switch --undo` — that restores the previous harness exactly, with no
   pre-check (the restart check and automatic switch-back still apply). Tell the owner first, like step 4.

Shared Codex whose conversation is read-only refuses `agentj …` (outside its sandbox): tell the owner Codex's refusal sentence
— that conversation needs Full access (see agentj-config, `agentj codex-sandbox`); never work around the sandbox.

Never switch by `agentj agent <kind>` + `agentj service restart`, by editing `~/.config/agentj/` or by restarting the service:
that skips the gate and the automatic switch-back. Never switch to a harness whose probe failed, "to see if it works".
Isolation, session mode (shared / independent), the folder and approvals are unchanged by a switch.

**Just switched to you** — the 「自报家门」 note names a handoff: read that file completely before you greet the owner, say in
one sentence what you picked up, and use no other tool in that turn; the next 〔Agent J 系统提示〕 says carry on — do the
handoff's next step (nothing in progress → wait for the owner).

中文：主人发 `/switch-harness`（没带话就先解释什么是 harness），或说「切换到 X」时：① 先说一句「我先扫描一下，大约 1–3 分钟」，再扫描 + 无头探测（`agentj agent switch --scan`，用服务的环境，每个 ≤ 60 秒、只读、不打印凭据；手机上会自己显示「正在探测 / 正在切换 / 已切回」）；② 告诉主人现在能切到哪些，只有一个就说没法切、要先装别的；③ 没装的经主人
同意按 setup-agents 文档帮装，登录由主人在自己的终端完成（你一步步指导；密钥走 `agentj secret request` 手机卡片，绝不进聊天），
Kimi Code 用 `kimi login --region mainland-cn`（海外站 `global`）或手机 设置 →「AI 登录救援」设备码，只支持独立会话，审批全走手机；Qwen Code（`qwen`）没有自己的登录，用手机密钥卡存阿里百炼 Token Plan 的 key（`BAILIAN_TOKEN_PLAN_API_KEY`），同样只支持独立会话；装好登好再回到 ①；④ 目标探测通过、主人明确要切，先说「会断开几秒自动重连，新 harness 会先自报家门，不通会自动切回」，再运行
`agentj agent switch <目标>`——电脑上的独立任务用服务的环境再探测一次（闸门；`--dry-run` 只探测不切），切换、重启、自检
都由它完成，失败自动切回原 harness 并说明；闸门还会试「回头路」（现在这个），回不来就不切（`no_way_back`），先帮主人修好，
主人坚持才加 `--force`；切换前 Agent J 会发〔Agent J 系统提示〕让你按 agentj-handoff 格式把交接写进它点名的 `switch.md`（只留要点、≤ 150 行、
不写凭据），写完回一句就停；新 harness 先读交接再打招呼，随后接着干，3 分钟没写成也照切并告诉主人；⑤ `agentj agent switch --result` 看结果（带原因和下一步，照着转告主人）；⑥ 切回上一个却过不了检查时，用 `agentj agent switch --undo`
不经预检原样切回（起不来自动回到现在这个）。「没登录」指后台服务看不到登录：照扫描结果那句一步做法讲给主人（`claude` 里 `/login`
一次，或 `agentj service import-key CLAUDE_CODE_OAUTH_TOKEN` 再 `agentj service restart`）；你自己会话里能用不算数。OpenCode 没 key 时可以先用它的免费模型（`--model opencode/<免费模型>`），切换结果里那句数据提示原样转告主人。绝不绕过闸门直接改配置或重启服务。
Docs: https://agentj.app/docs/switch-harness/zh.md · https://agentj.app/docs/switch-harness/en.md
