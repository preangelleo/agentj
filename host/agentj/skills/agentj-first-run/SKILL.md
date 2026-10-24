---
name: agentj-first-run
description: First-run "at your computer" checklist — prepare everything remote work will need while the owner is still at the computer. Use after the first installation's welcome, or when the owner says "Agent J 主机前一次做完", "继续首次准备", "离开Agent J 主机前检查", "prepare everything while I am at my computer", "continue first-run setup", "check before I leave".
---
You are the one main Agent; only you talk to the owner. The host owns the checklist journal and every check; you guide.

1. Read first: `agentj setup checklist --json` (read-only; `needs_setup` = never started). Do not ask what the host can see.
2. Start or continue only with the owner's go-ahead: `agentj setup checklist --resume --json`. The host probes everything; the paired
   phone AND this computer's browser show a thin banner above the chat (「首次准备 3/12 · 继续」) that opens the checklist as its own
   page (same revision). It never covers the chat. Tell the owner: the steps are done ON THE COMPUTER, the phone shows progress,
   and if anything is stuck they just tell you in the chat. The banner's ✕ (or the page's 「收起清单」) closes it at once and it no
   longer comes back by itself; the menu 「首次准备清单」 reopens it. If the owner says "close it / not now / 关掉清单", run
   `agentj setup checklist --close --json` — never insist, never re-open it on your own. Closed is permanent on that device:
   `--resume`, `--check`, new revisions and restarts never bring it back. Only when the owner asks to see it again run
   `agentj setup checklist --open --json` (or point them to the menu).
3. Explain the whole outline once, in six groups: your connection · power and services · your websites · Google tools · permissions
   you need · optional connections. Each item is "I do it", "needs you at the computer", "can be done on the phone" or "not in this
   version yet". Recommend Gmail website sign-in first if they use Google. Chat-only use is a valid choice.
4. Then one item at a time, in the card's order. The owner taps Use / I won't use this / Later on the card. You never mark a choice,
   never write "ready", never skip a required item. Required items (seat, AI login, both remotes, passkey, approval key, service,
   keep-awake, network, the final phone check) cannot be "I won't use this". "Later" keeps it as unfinished, not unused.
5. Do the technical work yourself with the existing commands: `agentj keep-awake setup --phone` (or `--yes` after the owner's yes
   in chat; system change = a phone password card; a Mac's lid only after a second yes and the reminder to shut it down
   before travel or idle time),
   `agentj service install`, `agentj admin` for pairing, `agentj secret request` for keys. When the owner says "done", run
   `agentj setup checklist --check --json` (or `--item <id>`): their word is a request to check, not proof.
   Main AI row (`harness`): where Claude Code / Codex are not offered (`agentj agent route` says `kimi`), follow the rule
   the command prints: mainland China from zero → Volcano Ark Coding Plan, Lite first (same models as Pro, Pro = 5x quota;
   `agentj provider ark`); Hong Kong / overseas → Kimi membership Plus or higher (Go has no coding quota); already on one
   (Ark, Kimi, Bailian) → keep it. Kimi's link keeps the disclosure 「这是 Agent J 官方的邀请链接，您和我们各得一次抽奖」
   right after it, never apart. Keys only through the phone key card the command opens. Bailian Token Plan
   (`agentj provider bailian`) with its terms (the owner's own scheduled tasks OK, may be rate-limited; never support bots; data used for training).
   An owner with their own model server (Olares Router, LiteLLM, Ollama …) can use DeepSeek Harness:
   `agentj agent dsh --dir <dir> --base-url <url>/v1 --model <id>`.
6. A failed item pauses only what depends on it (the card shows it); keep going with independent items. Never a global lock.
7. Websites: the checklist reads the real sign-in evidence of the browser Agent J uses — its own, or one the owner already runs that
   they chose to use (adopted, e.g. a CDP Chromium on 127.0.0.1:9222). Each row on the page says which browser was checked and
   what to tap next: install Agent J's browser / use my existing browser / open it on the computer and sign in (Agent J's window
   comes to the front, titled 「Agent J 专用浏览器」) / I won't use this. The owner's everyday browser is never read. If the owner
   says "I already have a browser that is signed in", run `agentj browser adopt --endpoint http://127.0.0.1:<port> --json`
   (loopback only) — it only proposes; the owner confirms with 「用我已有的浏览器」 on the page. A stopped browser or a result older than15 minutes
   cannot pass; use `agentj browser check --if-stale` to refresh when needed. WeChat requires both Official Accounts and Channels checks. Google API: see agentj-google (guided: Calendar only). Mac privacy permissions (Full Disk Access,
   Accessibility, Automation, Screen Recording): ask only for a selected task that truly needs one, name the exact program and why.
   Touch ID / PAM is diagnosis only; never disable it.
8. Final check (item `exit`): when everything else selected is verified, the owner taps "Start the leaving check" on the card,
   lets the computer's screen turn off, switches the phone to mobile data and sends any message. Reply normally; the host records
   the round trip. Only when the checklist says ready, say: "You can now leave your computer and continue the selected, verified
   tasks from your phone", with the check time, the unused items, and that power loss, a restart that needs unlocking, a new
   system permission or an account security check may still need them at the computer. Otherwise say what already works and
   how many items are left. Never claim "everything is remote forever".
9. Secrets never enter chat: passwords and keys only through the phone cards; QR codes, callback URLs and admin links never in
   ordinary chat or Telegram. Existing approvals (send, pay, delete, deploy, credentials) stay exactly as they are.
10. **The first workflow** (P215). A fresh computer has no workflows yet, and an assistant with nothing to run looks lost.
   When the checklist is ready, closed or declined — or right away if the owner only wants to chat for now — ask one question
   in the owner's language: 「你每周重复做的三件事是什么？」 / "What are three things you do again and again every week?"
   Then follow documentation/WORKFLOW.md in the working root ("A new computer with no workflows yet" / 「新Agent J 主机、还没有工作流时
   怎么起步」): pick the one that repeats most and is easiest to check, propose it in one line as a workflow (folder + code
   name, its own CEO and documents, run once by hand, scheduled only after a yes), and on a yes build it with the
   agentj-capability skill (the workflow wizard's goal mode, built on the Workflow Design Bible). Record who the owner is in
   documentation/MEMORY.md and rewrite documentation/NEXT_SESSION.md. Ask once; if they say not now, drop it.

中文要点：先 `agentj setup checklist --json` 只读；主人同意才 `--resume`，手机和Agent J 主机网页的聊天上方出现一条细横幅，点开是独立的清单页，
绝不挡住聊天。告诉主人：步骤都在Agent J 主机上做、手机看进度、卡住了直接在聊天里说。主人点 ✕ 或说「关掉清单」就 `--close`，之后
`--resume`/`--check`/重启都不会再弹；只有主人要看时才 `--open` 或让他点菜单重开。网站项会写明查的是哪个浏览器；主人已有登录好的本机浏览器时用 `agentj browser adopt --endpoint http://127.0.0.1:<端口>`
提议，主人在页面上点「用我已有的浏览器」才生效。一次讲清六组全貌，再按卡片顺序一项一项做。
「要用 / 我不用 / 稍后」只能主人在卡片上点；你不能替主人标记、不能写就绪、必需项不能「我不用」。主人说「做完了」只运行 `--check`。
主 AI 一项：用不了 Claude Code / Codex 的地区（`agentj agent route` 结果为 kimi）按命令打印的规则：中国大陆从零开始首推火山方舟 Coding Plan、先买 Lite（和 Pro 能力一样，只差 5 倍用量）；香港 / 海外推荐 Kimi 会员 Plus 及以上（Go 档没有编程额度）；已有某一家就继续用；主人有自己的模型服务（Olares Router 等）可用 DeepSeek Harness（`agentj agent dsh --base-url … --model …`）。百炼 Token Plan 要讲清自己的定时任务可用但长时间后台任务可能限流、客服 bot 不能用、数据会被用于模型优化。
Kimi 的邀请链接后面紧跟「这是 Agent J 官方的邀请链接，您和我们各得一次抽奖」，同时列出备选；key 只走手机密钥卡。失败只暂停相关项。网站登录检查与 Google API（日历以外）没接上时如实说「当前版本还不支持」。最后的离机检查：主人点「开始离机检查」，
Agent J 主机熄屏、手机切蜂窝发一句话；清单显示就绪才说「可以离开Agent J 主机」，并列出未启用的事项和仍可能要回Agent J 主机的情况。密码和密钥只走手机卡。
清单就绪、关掉或主人不做时，问一句「你每周重复做的三件事是什么？」，按工作根目录 documentation/WORKFLOW.md「新Agent J 主机、还没有工作流时怎么起步」
提议第一个工作流（一句话：文件夹 + 代号、自带 CEO 和文档、先手动跑一次、定时等点头），主人点头才用 agentj-capability 搭；主人说先不用就不再提。


macOS startup/control check: run `agentj doctor --offline` yourself. `launchd-environment` compares configured PATH/HOME/proxy
names with the terminal and never shows values; inherited runtime environment is unverified. Do not infer a network cause
from a timeout. Keep the `AJ_START_*` code; in mainland China offer Volcano Ark (`agentj provider ark`), Alibaba Bailian
(`agentj provider bailian`) or Kimi (`agentj agent switch kimi`). Provider commands open the phone key card directly;
`--dry-run` never opens one, and saved keys are reused. Wait for its system result before switching; never request keys in chat.
For shared Claude, `shared-controls` checks the exact Herdr route after installation/autostart. No safe route → the owner can
run `/compact` or press Esc in that Claude terminal, or choose Settings → Session mode → Use a separate session for a new managed session.
Do not edit global autostart scripts or send keys to a guessed pane. Agent J remains the owner's single main assistant.

## Local file access

During the first Mac installation, while the owner is at the computer, proactively complete the Mac folders and Finder access checklist item. Have the owner run `agentj files prepare --at-computer` in their own terminal after the Agent J service starts. The service requests Downloads, Desktop, Documents and Finder Automation access; the owner confirms macOS prompts. Finder Automation allows Agent J to ask Finder to copy selected files and is broader than the phone's folder allowlist; approve it only for remote importing. Full Disk Access is optional: grant it to the actual program shown in System Settings → Privacy & Security only when a task needs more protected data. Never enable it by default, click system prompts or edit TCC. A terminal and background service may have different TCC attribution; use the service's observations. Settings and doctor show the last check, not a permanent OS grant.

On a folder permission prompt, Permission denied or Operation not permitted, first run `agentj files fetch '<original path>'`, with at least a six-minute command timeout. Serve asks the paired phone: Allow once / Always allow folder / Deny. It tries a read-only copy, then Finder only after a macOS permission refusal and an already-authorized Automation preflight. Keep the source unchanged and use only the successful receipt's new path. Denial, timeout, offline, emergency stop, unverifiable metadata, symbolic links and sensitive paths fail closed. An interrupted command is not a success; retry fetch if no successful receipt exists, never guess a destination.

Missing Finder Automation still requires the owner's first local macOS consent. Explain that the phone cannot grant macOS permissions and preparation can wait until they are next at the computer; continue independent work. Do not send an outdoor owner back to the computer now, loop cp/sudo or click system prompts through computer use. Settings → Local file access supports signed folder additions and removal; a temporary grant lasts one hour. Removal affects future imports; existing copies remain. Grants change neither fence/harness permissions nor TCC. This check does not prove external-volume or Full Disk Access permission.

## 本机文件访问

Mac 首次安装、主人还在电脑前时，主动完成「Mac 文件夹与 Finder 权限」清单项：主人在自己的终端运行 `agentj files prepare --at-computer`（Agent J 服务须已启动）。服务依次请求下载、桌面、文稿的目录访问，以及 Finder 自动化；请主人按系统提示确认。Finder 自动化能让 Agent J 经 Finder 复制指定文件，作用范围比手机目录白名单更广，只有确实需要远程导入时才同意。完全磁盘访问可选，仅需要更多受保护数据时到「系统设置 → 隐私与安全 → 完全磁盘访问」授予系统列出的实际程序；不默认开、不自动点击、不用 tccutil 改权限。安装终端和后台服务可能有不同的 TCC 归属，服务检查结果才是本功能的依据；设置和 doctor 显示上次观察，不声称永久授权。

遇到「允许访问…文件夹」或 Permission denied / Operation not permitted：先 `agentj files fetch '<原路径>'`，给命令至少 6 分钟；服务发手机卡「本次允许 / 以后这个文件夹都允许 / 拒绝」。批准后先尝试只读复制；仅 macOS 权限拒绝时走 Finder，且自动化预检必须已授权。原文件不删不改，只使用成功回执的新路径。拒绝、超时、离线、急停、元数据不可核实、符号链接、敏感目录均不导入。命令被中断不能视为成功，核对回执；没有成功回执时重新 fetch，不猜路径。

如果 Finder Automation 尚未授权，系统仍可能需要本人在本机点一次。说明「这是 macOS 首次系统授权，手机卡无法替代；下次在电脑前准备即可」，继续其它独立工作，不让户外主人立刻回电脑前，不反复 cp/sudo，不用 computer use 代点。设置 → 本机文件访问可添加（签名审批）/撤销导入文件夹，临时授权一小时后到期；直到撤销只影响以后导入，已复制文件保留。这些授权不修改 fence、harness 权限或 TCC。外置盘与完全磁盘访问状态不能由本检查证明。
Optional channels: for mainland-network or Chinese-speaking owners, offer WeChat ClawBot first (agentj-wechat);
for overseas or English-speaking owners, offer Telegram first. Ask once; both can be enabled. Never infer that a
ClawBot can be shared with another WeChat account. The owner scans their own code in the paired Web App or terminal.
中文：大陆网络／中文用户优先推荐微信 ClawBot，海外／英文用户优先 Telegram；两者都可开。微信仅服务扫码者本人。

Owner WeChat/mail/Telegram now enter the external avatar first; the paired Web App stays the main window.
Keep the main harness/plan by default. Multiple available plans: ask once and recommend Codex if present; see agentj-wechat
for signed avatar-only switching and the free-model / already-bound OpenRouter rules.
