---
name: agentj-switch-harness
description: Switch the AI harness under Agent J — the phone menu's `/switch-harness` (with or without words), 「切换 harness」「换底层」「切换到 Codex / OpenCode / Claude Code / Kimi Code / Qwen Code / DeepSeek Harness」「切换到 OpenCode · 火山方舟 / 豆包」「切回 Claude Code」「能换成别的 AI 吗」, "switch harness", "switch to Codex / OpenCode / Claude Code / Kimi Code / Qwen Code / DeepSeek Harness", "use another agent". Explains what a harness is, scans which harnesses are installed, probes each one headless with the owner's own login (≤ 60 s, nothing written), reports what can be switched to, helps install and guides the owner through login, and switches only through the `agentj agent switch` gate (auto switch-back on failure).
---
# Switch the harness — carefully (切换底层 harness)

A switch restarts Agent J and replaces **you**. If the other side has no working login, the owner — away from the computer,
maybe unable to use a terminal — is left with nobody to talk to. So: scan, probe, report, and switch only through the gate.

**What a harness is (say it in plain words when asked, or when `/switch-harness` came with no words):** the AI program on this
computer that actually thinks and does the work — Claude Code, Codex, OpenCode, Kimi Code, Qwen Code or DeepSeek Harness. Agent J is the phone line to it. Switching
changes which AI answers; the folder, phone, approvals and safety rules stay the same. Each harness keeps its own conversation;
switching back resumes that one. To switch, the owner just replies 「切换到 Claude Code / Codex / OpenCode / Kimi Code / Qwen Code / DeepSeek Harness」.

1. **Scan + probe** (read-only; one tiny tool-free model call per harness with the owner's own login, ≤ 60 s each):
   `agentj agent switch --scan` (`--json` for data) — a job outside Agent J probes Claude Code, Codex, OpenCode, Kimi Code, Qwen Code and DeepSeek Harness in Agent
   J's SERVICE environment (the one a switched-to harness will run with) and prints per harness: not installed / works now /
   why not. Use it even inside the fence: your own environment hides harnesses whose path or key lives only in the service
   environment (`agentj agent detect --probe --json` is the fallback when Agent J is not a service). Nothing is written, no
   key is read or printed.
   It takes up to about 5 minutes. Say so in one short sentence **before** you run it (「我先扫描一下，大约 1–3 分钟」) — the
   phone also shows Agent J's own line under the dancing robot (「正在探测 Codex（2/3）…」, then 「正在切换 / 已切回」 during a
   switch); never leave the page empty and never put a placeholder character or a lone emoji first.
2. **Report** in one short message: which one runs now, and 「现在能切到：X、Y」 (only `callable: true` ones) — or
   「这台Agent J 主机只有现在这一个能用，没法切换；要切换得先装一个别的」. For each installed-but-not-callable one say why
   (not signed in / token expired / timed out — network or proxy). `not signed in` here means the SERVICE sees no login
   (e.g. Claude Code's token only in the owner's `~/.zshenv`): the scan line already says the one step — repeat it in plain
   words (`claude` → `/login` once, the login goes to the Keychain / credentials file the service can read; or
   `agentj service import-key CLAUDE_CODE_OAUTH_TOKEN` + `agentj service restart` in a terminal where claude works). It
   works in YOUR session is not proof — only the scan counts. A line saying an old saved path was replaced (a harness that
   updated itself) needs nothing; another install listed as unused may be removed if the owner wants.
   Claude Code / Codex fail with "region not supported", or the owner is in Hong Kong / Macau / mainland China: run
   `agentj agent route` — where they are not offered: mainland China from zero → Volcano Ark Agent Plan, Medium first (Kimi
   K3 from Medium; `agentj provider ark`; an existing Coding Plan: `--plan coding`); Hong Kong / overseas → Kimi membership Plus or higher (Go has no
   coding quota) in OpenCode or Kimi Code CLI (`kimi`); already on one → keep it. The owner decides. Give Kimi's link with the sentence right after it, never apart: 「这是 Agent J 官方的邀请链接，您和我们各得一次抽奖」
   (en: "This is Agent J's official invite link: you and we each get one prize draw"), plus its alternatives; the key only
   via the phone key card it prints (`KIMI_API_KEY` / `ARK_AGENT_PLAN_API_KEY` — the Agent Plan's own key from 订阅 → Agent Plan → 使用配置, not API Key management), then `agentj service restart` and scan again. Also
   possible on the mainland: an Alibaba Cloud Bailian Token Plan, personal (¥39–499 a month; Qwen 3.8 Max / Flash read images):
   `agentj provider bailian` writes OpenCode's config without touching what is there and prints the key card
   (`BAILIAN_TOKEN_PLAN_API_KEY`); then `agentj agent switch opencode --model bailian-token-plan-personal/qwen3.8-max` (or
   `agentj agent switch qwen`). Always tell the owner its terms in plain words: their own scheduled tasks may use it (long continuous background
   runs may be rate-limited — then a pay-as-you-go key), support bots never get it, and inputs / outputs are used by
   Alibaba Cloud to improve its models.
   **OpenCode · Volcano Ark (P371)** in one step: `agentj agent switch opencode --preset ark --plan agent|coding|payg [--model …]`.
   Ask first: Agent Plan (default, …/api/plan/v3, `ARK_AGENT_PLAN_API_KEY`), Coding Plan (…/api/coding/v3 — both plans are for coding Agents only; other programs using them can get the account banned) or
   pay-as-you-go (…/api/v3, per token, `ARK_API_KEY`, checked against Ark's model list). It finds OpenCode (arkcli only
   detected), prints the key card when the key is missing (run it, then the same command again), writes the provider and
   switches only after one real call; on failure it switches back and removes the provider it added. A failed check (any
   harness) may end with OpenCode's own one-line error — read it to the owner as it is (credentials are already redacted).
3. **Not installed / not signed in** — offer, never push:
   - Install only after the owner says yes: follow https://agentj.app/docs/setup-agents/en.md (one-command `setup-agents.sh`
     with `--cli <name>`, or that page's per-OS steps). Needs admin rights → `agentj sudo --why '…' -- <cmd>`.
   - Login is the owner's own act. Guide them step by step: Claude Code `claude auth login` (or `claude` then `/login`), Codex
     `codex login`, OpenCode `opencode auth login <provider>`, Kimi Code `kimi login --region mainland-cn` (kimi.ai:
     `--region global`; or the phone's Settings → Emergency repair → ③ → AI login rescue device code) — in **their own terminal**; tell them what they will see and
     what to click. A model API key never goes through chat: `agentj secret request --name <VAR> --purpose '…' --dest …`
     (masked phone card; see agentj-config). Never ask for or repeat passwords, tokens, callback URLs or codes; never click
     through an unverified-app warning for them.
   - **OpenCode with no key (P298):** its own free models (`opencode/…-free`, list with `opencode models opencode`; it changes
     daily) need no login — offer `agentj agent switch opencode --model opencode/<free model>` as the no-key way. The check
     of a free model may wait ~1 min (`free_tier` = its free channel refused for now: try later or another free model).
   - **Kimi Code (P305):** install `curl -fsSL https://code.kimi.com/kimi-code/install.sh | bash` (2.2.0+). It runs in an
     independent session only, over ACP: every approval is a phone card answered "this once", its own auto / YOLO modes are
     switched back, its hooks are not relied on. A kimi.com member's key works on api.kimi.com and api.kimi.ai; a kimi.ai member
     switches with `--region global`. A Kimi Code console key goes only through the key card (`KIMI_API_KEY`).
   - **Qwen Code (P310):** `npm install -g @qwen-code/qwen-code` (0.25+). Independent sessions only, over the same ACP
     adapter: its auto / auto-edit / YOLO modes are switched back to "default", every approval is a phone card. It has no
     login of its own (the free one ended 2026-04-15): an owner's own `~/.qwen/settings.json` (e.g. from `bl config agent`)
     is used as it is; otherwise a Bailian key from the key card (`BAILIAN_TOKEN_PLAN_API_KEY`) — Agent J passes it to Qwen
     in memory only. `/model` lists the plan's models (those marked 看图 read screenshots).
   - **DeepSeek Harness (P329):** `npm install -g @deepseek-ai/dsh@latest` (0.2.0-rc.2+, preview, Node 22.19+; Olares' Lares
     engine). Independent sessions only, same ACP adapter, read-only preset + "ask": every write / command is a phone card.
     Model: the owner's own dsh setup, or `agentj agent dsh --base-url https://<server>/v1 --model <id>` (Olares Router or any
     OpenAI-compatible server; key card `DSH_ROUTER_API_KEY` if it needs one), or DeepSeek's API (`DEEPSEEK_API_KEY`). Text only.
   - Then scan again (step 1). Only a fresh "works now" counts.
4. **Switch** — only on the owner's clear choice of a target that just passed step 1. This is their instruction; no extra
   approval card. First tell them: 「Agent J 会重启，手机断开几秒后自动重连，然后 X 会先跟你打招呼；如果 X 没起来，会自动切回
   我这里并说明原因」. Then run:
   `agentj agent switch <claude|codex|opencode|kimi|qwen|dsh>` (OpenCode may add `--model provider/model`; Kimi `--region global`).
   It starts a job outside Agent J that probes the target again in the service's environment (the gate: exit 3 + reason =
   nothing changed — tell the owner the reason and go back to step 3; exit 4 = still checking, look with `--result` in a
   minute). On ✓ the job waits for this turn to end, switches, restarts, checks that the new harness is up, answers its
   self-introduction and is idle again — and on any failure restores the previous harness exactly and makes it explain.
   A passed preflight / `waiting`, `switching`, `switched` progress is **not success**. The CLI returns 4 while pending.
   From an outside terminal, poll `agentj agent switch --result --wait 900` until final (0 = `ok` or dry-run `probe_ok`,
   3 = `rolled_back` / `failed`, 4 = still pending). Report the receipt's outcome and reason verbatim in plain language.
   From the very Agent J turn being switched, end this turn with “已安排，尚未完成 / scheduled, not complete” so the job
   can run; do not block that turn waiting for itself. The host posts the final receipt to the phone. On the next turn,
   read `--result` before claiming success. Success requires the new harness to receive the host's phone greeting and reply;
   a process running or `idle` alone never counts. If the ✓ printout has a 「提示：…免费模型…」 / "Note: …
   free model" line (its provider logs or trains on the conversation), pass that sentence on to the owner word for word.
   **Handoff (P289):** right before the switch Agent J sends you an 〔Agent J 系统提示〕 asking for the handoff — write
   `switch.md` at exactly the path it names (agentj-handoff format, the essentials only, ≤ 150 lines, no credentials), reply
   one line and stop. The new harness reads it before greeting the owner, then carries on from its next step. Not written
   within 3 minutes (or a finished turn leaves no file) → the switch goes ahead with a local redacted excerpt of recent owner conversation. It may be incomplete; never invent progress.
   The gate also tries the way back — the harness running now. If that fails (`no_way_back`), nothing changed: tell the
   owner the reason and the fix (step 3) first; only if the owner still wants the one-way switch, run it again with
   `--force`.
5. **Required final verification** (either side): `agentj agent switch --result` shows the last outcome (✓ / rolled back + reason and the next
   step — relay that step in plain words).
6. **Going back** — 「切回 X」 where X is the harness used before the last switch: try `agentj agent switch X` first; if its
   check fails, the printout offers `agentj agent switch --undo` — that restores the previous harness exactly, with no
   pre-check (the restart check and automatic switch-back still apply). Tell the owner first, like step 4.

Shared Codex whose conversation is read-only refuses `agentj …` (outside its sandbox): tell the owner Codex's refusal sentence
— that conversation needs Full access (see agentj-config, `agentj codex-sandbox`); never work around the sandbox.

Never switch by `agentj agent <kind>` + `agentj service restart`, by editing `~/.config/agentj/` or by restarting the service:
that skips the gate and the automatic switch-back. Never switch to a harness whose probe failed, "to see if it works".
Isolation, session mode (shared / independent), the folder and approvals are unchanged by a switch.

**Switching, scans and `--undo` never need sudo or the owner's terminal**: the command goes through Agent J's own socket,
also inside the isolation (where launchd / systemd are invisible). Never `agentj sudo`; never ask the owner to run `agentj
service start` or launchctl. `agentj service status` says serve answers → the service IS running. On failure tell the owner
only the printed reason (and `--result`).

**Just switched to you** — the 「自报家门」 note names a handoff: read that file completely before you greet the owner, say in
one sentence what you picked up, and use no other tool in that turn; the next 〔Agent J 系统提示〕 says carry on — do the
handoff's next step (nothing in progress → wait for the owner).

中文：主人发 `/switch-harness`（没带话就先解释什么是 harness），或说「切换到 X」时：① 先说一句「我先扫描一下，大约 1–3 分钟」，再扫描 + 无头探测（`agentj agent switch --scan`，用服务的环境，每个 ≤ 60 秒、只读、不打印凭据；手机上会自己显示「正在探测 / 正在切换 / 已切回」）；② 告诉主人现在能切到哪些，只有一个就说没法切、要先装别的；③ 没装的经主人
同意按 setup-agents 文档帮装，登录由主人在自己的终端完成（你一步步指导；密钥走 `agentj secret request` 手机卡片，绝不进聊天），
Kimi Code 用 `kimi login --region mainland-cn`（海外站 `global`）或手机 设置 →「紧急修复」→ ③ →「AI 登录救援」设备码，只支持独立会话，审批全走手机；Qwen Code（`qwen`）没有自己的登录，用手机密钥卡存阿里百炼 Token Plan 的 key（`BAILIAN_TOKEN_PLAN_API_KEY`），同样只支持独立会话；DeepSeek Harness（`dsh`）用主人自己的 dsh 设置，或 `agentj agent dsh --base-url https://<服务>/v1 --model <模型>` 接 Olares Router 等 OpenAI 兼容服务（要 key 走密钥卡 `DSH_ROUTER_API_KEY`），只支持独立会话；装好登好再回到 ①；④ 目标探测通过、主人明确要切，先说「会断开几秒自动重连，新 harness 会先自报家门，不通会自动切回」，再运行
`agentj agent switch <目标>`——Agent J 主机上的独立任务用服务的环境再探测一次（闸门；`--dry-run` 只探测不切），切换、重启、自检
都由它完成，失败自动切回原 harness 并说明；闸门还会试「回头路」（现在这个），回不来就不切（`no_way_back`），先帮主人修好，
主人坚持才加 `--force`；切换前 Agent J 会发〔Agent J 系统提示〕让你按 agentj-handoff 格式把交接写进它点名的 `switch.md`（只留要点、≤ 150 行、
不写凭据），写完回一句就停；新 harness 先读交接再打招呼，随后接着干，3 分钟没写成也照切并告诉主人；⑤ `agentj agent switch --result` 看结果（带原因和下一步，照着转告主人）；⑥ 切回上一个却过不了检查时，用 `agentj agent switch --undo`
不经预检原样切回（起不来自动回到现在这个）。「没登录」指后台服务看不到登录：照扫描结果那句一步做法讲给主人（`claude` 里 `/login`
一次，或 `agentj service import-key CLAUDE_CODE_OAUTH_TOKEN` 再 `agentj service restart`）；你自己会话里能用不算数。「OpenCode · 火山方舟」一步到位：先问主人用 Coding Plan 套餐（只给编程 Agent 用，别的程序调用会被判滥用封号）还是按量付费（…/api/v3），再运行 `agentj agent switch opencode --preset ark --plan coding|payg`，缺 key 它会给密钥卡命令，实测一次才切、不通自动切回。OpenCode 没 key 时可以先用它的免费模型（`--model opencode/<免费模型>`），切换结果里那句数据提示原样转告主人。绝不绕过闸门直接改配置或重启服务。
切换、扫描、`--undo` 不需要管理员权限，也不需要主人开终端（经 Agent J 自己的通道，隔离里也能用）：绝不为此用 `agentj sudo`，不让主人跑 `agentj service start` 或 launchctl；`agentj service status` 说 serve 在应答就是在运行；失败只转告打印的原因。
Ark401: Agent Plan and Coding Plan keys are not interchangeable. A confirmed mismatch exposes a signed one-use phone button in the failure banner and Emergency repair: Use Agent Plan/Coding Plan and retry. Direct the owner to tap it; never request the same key again, copy it yourself, bypass the probe, or replay the failed message. The host reuses the exact local key, configures the matching provider and verifies the same model with native OpenCode before switching. Existing different target credentials/providers are retained. Show the redacted upstream error line from the switch result.

方舟套餐误配时，把主人引导到失败提示或「紧急修复」的「改用 Agent Plan / Coding Plan 并重试」按钮；不用再贴 key，验证成功才切。

Docs: https://agentj.app/docs/switch-harness/zh.md · https://agentj.app/docs/switch-harness/en.md

## Background connection checks and support (0.25.3)

After switching the AI, connection checks and support feedback run in the computer’s background service. The Agent reads the results without opening local key files. If chat works and the check has not returned, keep chatting; this does not mean the switch failed. Feedback is confirmed only when a ticket ID is returned; otherwise try later.

切换 AI 后，连接自检和客服反馈由电脑后台完成，Agent 只读取结果，不需要读取本机钥匙文件。若聊天已正常、自检暂时没返回，可以继续聊天；这不表示切换失败。反馈收到编号才算已提交，没收到时稍后重试。

Only after `agentj agent switch --result` is final `ok`, when the switched session is replying and a separate background check is unavailable, say: “切换已成功，聊天正常；自检这一步在 Codex 的安全隔离里不能读本机钥匙文件，可以忽略，稍后由后台再检查。” / “The switch succeeded and chat works. Codex isolation hides the local key files; this check can be skipped and retried by the background service later.” For Claude or OpenCode, name the actual harness. Never claim a check completed or feedback was sent without its result. Do not ask the owner to disable isolation, reveal keys or grant broad filesystem access.

P481: Claude via mise/asdf with multiple installed executables reports `ambiguous_install`, not `not_installed`. Without an explicit selection, the highest executable numeric version is used (2.1.308 > 2.1.97). Keep an explicit `AGENTJ_CLAUDE_BIN` or mise/asdf selection; if it cannot be found, ask the owner to select an installed version. / Claude 多版本会标明 ambiguous_install，默认用最高可执行版本；可用 AGENTJ_CLAUDE_BIN 或 mise/asdf 选定。

P529 / 切换结果：只能根据 `agentj agent switch --result` 的最终收据报告。必须轮询到 `ok`、`rolled_back` 或 `failed`；
`probe_ok` 只是演练。`waiting`、`switching`、已启动、idle、退出码 4 都不能说切换成功。发起切换的 Agent J 回合
要先以「已安排，尚未完成」结束以让后台继续，下一回合必查收据；外部终端用 `--result --wait 900` 等待。
回退如实说「已回退到 Codex（或实际原 harness），原因：…」。交接失败会提供本地脱敏摘录，不增加工具权限。
