---
name: agentj-first-run
description: First-run "at your computer" checklist — prepare everything remote work will need while the owner is still at the computer. Use after the first installation's welcome, or when the owner says "电脑前一次做完", "继续首次准备", "离开电脑前检查", "prepare everything while I am at my computer", "continue first-run setup", "check before I leave".
---
You are the one main Agent; only you talk to the owner. The host owns the checklist journal and every check; you guide.

1. Read first: `agentj setup checklist --json` (read-only; `needs_setup` = never started). Do not ask what the host can see.
2. Start or continue only with the owner's go-ahead: `agentj setup checklist --resume --json`. The host probes everything and shows the
   checklist card on the paired phone AND this computer's browser (same revision). Tell the owner where the card is.
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
7. Websites: the checklist reads the dedicated browser’s real sign-in evidence. A stopped browser or a result older than15 minutes
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

中文要点：先 `agentj setup checklist --json` 只读；主人同意才 `--resume`，卡片同时出现在手机和电脑网页。一次讲清六组全貌，再按卡片顺序一项一项做。
「要用 / 我不用 / 稍后」只能主人在卡片上点；你不能替主人标记、不能写就绪、必需项不能「我不用」。主人说「做完了」只运行 `--check`。
失败只暂停相关项。网站登录检查与 Google API（0.18.1）没接上时如实说「当前版本还不支持」。最后的离机检查：主人点「开始离机检查」，
电脑熄屏、手机切蜂窝发一句话；清单显示就绪才说「可以离开电脑」，并列出未启用的事项和仍可能要回电脑的情况。密码和密钥只走手机卡。
