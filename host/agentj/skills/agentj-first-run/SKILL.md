---
name: agentj-first-run
description: First-run "at your computer" checklist — prepare everything remote work will need while the owner is still at the computer. Use after the first installation's welcome, or when the owner says "电脑前一次做完", "继续首次准备", "离开电脑前检查", "prepare everything while I am at my computer", "continue first-run setup", "check before I leave".
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
5. Do the technical work yourself with the existing commands: `agentj keep-awake on` (system change = a phone password card),
   `agentj service install`, `agentj admin` for pairing, `agentj secret request` for keys. When the owner says "done", run
   `agentj setup checklist --check --json` (or `--item <id>`): their word is a request to check, not proof.
6. A failed item pauses only what depends on it (the card shows it); keep going with independent items. Never a global lock.
7. Websites: the checklist reads the real sign-in evidence of the browser Agent J uses — its own, or one the owner already runs that
   they chose to use (adopted, e.g. a CDP Chromium on 127.0.0.1:9222). Each row on the page says which browser was checked and
   what to tap next: install Agent J's browser / use my existing browser / open it on the computer and sign in (Agent J's window
   comes to the front, titled 「Agent J 专用浏览器」) / I won't use this. The owner's everyday browser is never read. If the owner
   says "I already have a browser that is signed in", run `agentj browser adopt --endpoint http://127.0.0.1:<port> --json`
   (loopback only) — it only proposes; the owner confirms with 「用我已有的浏览器」 on the page. A stopped browser or a result older than15 minutes
   cannot pass; use `agentj browser check --if-stale` to refresh when needed. WeChat requires both Official Accounts and Channels checks. Google API: see agentj-google (full guided authorization is 0.18.1). Mac privacy permissions (Full Disk Access,
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

中文要点：先 `agentj setup checklist --json` 只读；主人同意才 `--resume`，手机和电脑网页的聊天上方出现一条细横幅，点开是独立的清单页，
绝不挡住聊天。告诉主人：步骤都在电脑上做、手机看进度、卡住了直接在聊天里说。主人点 ✕ 或说「关掉清单」就 `--close`，之后
`--resume`/`--check`/重启都不会再弹；只有主人要看时才 `--open` 或让他点菜单重开。网站项会写明查的是哪个浏览器；主人已有登录好的本机浏览器时用 `agentj browser adopt --endpoint http://127.0.0.1:<端口>`
提议，主人在页面上点「用我已有的浏览器」才生效。一次讲清六组全貌，再按卡片顺序一项一项做。
「要用 / 我不用 / 稍后」只能主人在卡片上点；你不能替主人标记、不能写就绪、必需项不能「我不用」。主人说「做完了」只运行 `--check`。
失败只暂停相关项。网站登录检查与 Google API（0.18.1）没接上时如实说「当前版本还不支持」。最后的离机检查：主人点「开始离机检查」，
电脑熄屏、手机切蜂窝发一句话；清单显示就绪才说「可以离开电脑」，并列出未启用的事项和仍可能要回电脑的情况。密码和密钥只走手机卡。
