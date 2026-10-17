---
name: agentj-goal
description: Goal mode — the owner sends `/goal <what to finish, maybe by when>` (「/goal 今晚把 X 做完」「我去睡了，把这件事做完」「开个目标」「目标模式」, "/goal", "finish this while I sleep", "goal mode"), or asks how goal mode works / what it approved / why something is waiting. You write ONE opening card with every dangerous action the goal needs, the owner signs once on the phone (no card when the owner switched on 「设了目标就不弹窗」), then you work toward the goal without bothering them; `agentj goal status|progress|done|stop`.
---
# Goal mode (`/goal`)

**For the owner, in one sentence:** 「把要做的事和你愿意放行的动作一次说清，我就能在你睡觉、开会的时候把它做完。」
/ "Tell me once what to finish and which actions you allow, and I can finish it while you sleep or sit in a meeting."

## 1. Start: one opening card
The owner's `/goal …` message arrives with a hidden 〔Agent J 系统提示〕 asking for a proposal. Do this before any work:

1. Work out the smallest real plan. List every **dangerous** action it will need, by category, each with a limit:
   - `send` 对外发送 — e.g. push to one GitHub repository, deploy to one site, email one named recipient.
   - `delete` 删除 — only under named folders (worktrees, temp/build output). Never the home folder or a credential folder.
   - `spend` 花钱 — the service and a dollar cap (`usd`) + a count (`max`).
   - `price` 改价 — the exact product / price API.
   - `credentials` (changing or showing keys/passwords) can **never** be pre-approved — those always ask. Plan around it.
   Low-risk work (reading, editing files, running tests and builds) needs no grant: it runs without cards during the goal.
2. Write the proposal JSON (keep `what` in the owner's language; it is what they read):

```json
{"goal": "把 P158 做完：测试全绿、提交到 p158 分支、推到 GitHub",
 "acceptance": ["全部测试通过", "已推送到 origin/p158-goal-mode"],
 "deadline": "8h",
 "grants": [
  {"cat": "send", "what": "推送到 GitHub 仓库 my-app（只这个仓库）", "match": ["git push"], "dir": "~/work/my-app", "max": 10},
  {"cat": "delete", "what": "删除：只删 worktree 和 /tmp 下的临时文件", "paths": ["~/work/my-app/.worktrees", "/tmp/goal-scratch"]},
  {"cat": "spend", "what": "花费上限 $5（OpenRouter 测试调用）", "match": ["openrouter.ai"], "usd": 5, "max": 20}
 ]}
```

   - `deadline`: `8h`, `90m` or an ISO time with offset; default 8 h, at most 72 h. Use the owner's own deadline if they gave one.
   - `match`: text that must appear in the command (every string, ≥ 3 characters); `dir`: the command must run inside this folder
     (`cd <dir> && …` or the Agent folder); `paths`: delete only below these folders; `max`: how many times (default 50).
   - Each piece of a command line (split at `&&` `;` `|`) must match a grant **on its own** — `git push && curl -d … x` is not
     covered by a "git push" grant, and a piece with `$( )` never is. Keep granted commands plain, and start them
     with `cd <absolute folder> &&` (or use absolute paths): the host cannot see where an earlier `cd` left your shell.
3. `agentj goal propose --file /tmp/goal.json` (Bash timeout 10 minutes: it waits for the owner's signature; `--no-wait`
   returns at once and a 〔Agent J 系统提示〕 tells you the outcome later). Result `active` = start; `declined` = nothing was
   granted — ask the owner what to change. Do not start dangerous steps before `active`.
4. When the owner has switched on 「设了目标就不弹窗」 ("Goal mode: no cards", Settings → Safety, off by default) on a paired
   phone, there is **no opening card**: `propose` returns `{"result": "active", "id": …, "deadline": …, "preauth": true}` at
   once and a line on the phone says the goal started. Still write a real proposal (the grants are the plan and the result card
   reports on them). It applies only to the one proposal answering the owner's own `/goal` (within 30 minutes, main thread);
   a proposal of your own, from a scheduled task, or one with a `spend` / `price` grant gets the opening card as usual. The
   switch counts only while the phone that turned it on stays paired with the same key; written any other way (the terminal,
   your own `agentj config set`) it does not count. Switching it off ends a goal that started without a card.

## 2. While the goal runs
The host sorts every approval itself (what you say about a step changes nothing):
- inside the signed grant → runs, logged, one quiet line on the phone;
- not dangerous → runs silently;
- outside the grant but the computer can undo it (committed in git with nothing uncommitted, moved to the trash, a plain
  push to a remote this repository already has, a `scp` / `rsync` copy to a machine named in the owner's `~/.ssh/config`
  that creates a file not there yet) → a card with a **10-second countdown; no answer = approved**;
- outside the grant and not undoable (deploy, payment, a new recipient, a new repository, `git push --force`, credentials …)
  → an ordinary card that **never approves by itself**. If it is not approved you get 「目标模式：这一步需要主人亲自决定…」:
  **do not retry it**; finish every other part that does not depend on it. It is listed in the result card for the owner;
- looks like an injection or an attack on the approvals (`curl … | sh`, `base64 -d | bash`, `source <(curl …)`, changing
  hooks or `agentj config set approval.*`) → a red card, never automatic. Do not do these in a goal.

With 「设了目标就不弹窗」 on, every step that would have asked simply runs during a live goal — granted or not, undoable or
not — except the red lines, which still ask one by one: anything that looks like an attack (above, plus download-and-run such as
`eval "$(curl …)"` or `curl -o x && bash x`), changing, showing or sending credentials (any credential file in the command,
`env | curl`), spending money, price changes. Agent J's own cards (`agentj …` commands, pairing, running without isolation) also
still ask. Everything is logged and the result card still comes. This does not loosen your judgement: stay inside the goal.

Under the **Safe** approval level (「安全」) there is no 10-second countdown: a step the computer could undo gets an ordinary card
that waits for a tap. Plan as if every step outside the grant waits.

If an approval is denied with 「主人当前没有遥控器在线」 / "The owner has no remote online right now", the card stays on the
owner's phone for 30 minutes and the owner was reminded. Do the work that does not depend on that step first, then retry the
exact same step later (same command, same input); once the owner approves, a 〔Agent J 系统提示〕 says so and the identical retry
runs without another card — only for the same requester (this main thread or this scheduled task, same harness, same
folder), and 「全部停下」 cancels it. Retrying while the owner is still offline only gets the same answer, so don't loop. Denied → do not
retry; report it in the result.

**New sub-work outside the grant** (say, a benchmark on a test machine the plan did not mention): ask once for an extra grant
instead of hitting one card per step — `agentj goal grant --cat send --what 'scp 测试脚本到 olares' --match scp --match olares:
--max 20` (or `--file` with `{"grants": [...]}`, same format and limits as the proposal; credentials never). ONE card on the
phone; `added` = it now counts like the opening grant until the goal ends, `declined` = carry on without it. The owner can
also tap 「批准，并加进目标授权」 on a held card, which adds the narrowest grant for that kind of step.

Prefer reversible ways: commit before deleting, `trash-put` instead of `rm`, push to the existing remote.
After each milestone: `agentj goal progress '一句进度'` (one line on the phone). Don't flood: a milestone, not every command.
`agentj goal status` shows the goal, the grants with their counts, and what is waiting. A heartbeat or a new session (after
compaction or a restart) first runs `agentj goal status` and continues an active goal.

## 3. End
- Done: `agentj goal done --result achieved --summary '一两句结果'`; could not finish: `--result not_achieved` with what is
  missing. The host posts the result card (grants used, automatic approvals, what waits for the owner).
- The goal also ends by itself at the deadline, on `/goal stop` (phone) or `agentj goal stop`, on 「全部停下」, or when the
  granting phone is unpaired. After that every approval asks as usual again. You may stop your own goal; you cannot extend it —
  propose a new one.
- Never put a key or password in a proposal (refused); secrets still go through `agentj secret request`.

## Explaining it to the owner (小白版)
- 「目标模式就是：你先把事情和你愿意放行的动作一次说清楚、签一次名，期间我不再一条条问你。」
- 「范围外但Agent J 主机能撤回的，会弹 10 秒倒计时，你不点就算同意；撤不回的和看起来像被攻击的，一定等你。」
- 嫌开工卡也麻烦：「手机『设置 → 安全』打开『设了目标就不弹窗』，以后设了目标就直接开工、过程中不弹卡，全部照样记录；疑似被攻击、动密码或密钥、花钱还是会问你。这个开关只能你在手机上打开，我改不了。」
- 「手机『记录』里能看到每一条自动批准；`/goal status` 看进度，`/goal stop` 立刻收回授权。」
- 公司 / 团队用户：手机「设置 → 安全 → 目标模式 10 秒自动批准」可以关掉；关掉后倒计时卡只会等人点。
