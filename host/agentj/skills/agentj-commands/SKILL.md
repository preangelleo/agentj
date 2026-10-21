---
name: agentj-commands
description: Add, change or remove the owner's own slash commands — the list that opens when the owner types 「/」 on the phone (and the ≡ menu). Use for 「定制命令」「我想加一个 / 命令」「把 /xxx 删掉」「改一下 /xxx 的说明」「做一个每天发周报的命令」, "customise my commands", "add a slash command", "remove /xxx", the phone's /customize or a long press on its ≡ button. Asks first, then writes a document command (a skill that is only instructions) or a command skill (instructions + a script it runs), puts it in the phone's list with `agentj menu`, checks it, and tells the owner how to see it. Also answers Agent J's 「同类事已做 3 次」 offer (save it as a skill, with a self-check) and skill health (`agentj skills`).
---

The phone's 「/」 list has four parts, top to bottom: Agent J's own one-tap commands (fixed: /compact /clear /model …,
/customize, 全部停下), **the owner's menu items**, **the harness's skills**, then the harness's own built-in commands — each
tagged 「直接执行」 (sent to the harness as typed) or 「交给 Agent 代办」 (you get 「〔菜单命令 /x〕…」 and do it through your
normal path), or greyed with the reason when no web page can do it. You change only the owner's items and skills.
`/switch-harness` (one tap, among the one-tap commands) and `/goal` are always there, whatever the owner's items are — never
add, remove or re-describe them.

If `/compact` or `/clear` is waiting behind a scheduled workflow, explain that task and that the command has not been sent. Only the requesting remote can cancel the wait. Cancellation never stops the workflow. Once command execution starts, do not promise withdrawal; shared Claude follows its native-control waiting card. An unsupported adapter still reports its usual refusal after the wait.

## 1. Ask first (always)
Show what is there now, then ask ONE short question set:
- `agentj menu list` (the owner's items, as JSON) and the skills you can see (Claude Code: `~/.claude/skills/`, the
  project's `.claude/skills/`; Codex: `~/.agents/skills/`; OpenCode: `~/.config/opencode/skills/`). Never show or edit
  the `agentj-*` ones — they come with Agent J.
- Ask: add, change or remove? Which name (lower-case letters, digits, `-`; short; not a built-in one)? What should it
  do? **Document command** (the instructions are the whole thing — 「按这个格式写周报」) or **command skill** (you also
  run a script / CLI — 「把下载文件夹里的发票整理成表」)? Wait for the answers before writing anything.

## 2. Write it
- **Document command** → a skill folder with one file: `<skills dir>/<name>/SKILL.md` — front matter `name: <name>` and
  a `description:` that says when to use it (the owner's own words, zh and en), then the instructions.
- **Command skill** → the same SKILL.md (say exactly which command to run and what to do with its output) plus the
  script next to it (`<skills dir>/<name>/run.sh` or `.py`); no secrets inside — read keys by NAME from the owner's
  existing env / config. Run it once on a harmless input before calling it done.
- Write it for the harness you are (the folders above). One skill = one folder; editing = edit that folder; removing =
  delete that folder (say its path first and ask yes / no).

## 3. Put it in the phone's list — and check
- Claude Code shows its skills in the list by itself (after its next start or `/clear`).
- Codex / OpenCode (and any command you want at the top): `agentj menu add <name> --label "<一句话说明 / one-line description>"`.
  Remove: `agentj menu remove <name>`. These go through Agent J's settings writer, which checks every entry (name shape,
  length, no duplicates, ≤ 60) and refuses a bad one — fix what it says and run it again. Never edit the settings file
  or `.agentj/menu.json` by hand.
- Check: `agentj menu list` shows it; for a skill, read the SKILL.md back once.

## 4. Tell the owner (one short message)
What you made, its name, and: 「在手机输入框里敲 `/`，就能看到 `/<name>`；选它会把命令放进输入框，补一句再发。」 / "Type `/`
in the phone's field to see `/<name>`; picking it puts it in the field — add a few words and send." Picking a command
never sends it by itself: the owner always presses Send.

## 5. Agent J's offer: 「同类事已做 3 次」 → save it as a skill (with a self-check)
An 〔Agent J 系统提示〕 says the same kind of task came up 3 times and gives an id. Finish the current task first, then ask the
owner ONE short question: 「这件事你已经让我做了 3 次，要不要存成技能？以后一句 /<名字> 就行。」 / "You've asked me for this three
times — save it as a skill, so next time it's just /<name>?" Never ask twice.
- **No** → `agentj skills suggest no <id>` (never offered again). **Yes** → `agentj skills suggest yes <id>`, then §1–§4 above
  (ask the name; document command or command skill), built from what you actually did these three times.
- Every skill made this way carries a runnable self-check: `<skills dir>/<name>/tests/check.sh` (or `check.py`) that runs the
  skill's script on a harmless sample — or, for a document command, checks the SKILL.md has `name`, `description` and the
  steps — and exits 0 only when it passes. Run it once and show the owner the result before saying it is done.
- `agentj skills suggest` lists the current offers (`--all` also the answered ones).

## 6. Skill health
The phone's 技能 page shows each skill's 「最近使用：x 天前 · y 次」 (「—」 when Agent J saw none) and marks long-unused ones;
`agentj skills health` prints the same. Archive only when the owner asks: `agentj skills archive <name>` moves the folder to
the archive (never deleted) and `agentj skills restore <name>` puts it back; Agent J's own skills are never archived.

Never: change Agent J's built-in commands, remove a skill the owner did not name, put a credential in a skill, or claim
it is in the list without `agentj menu list` (or the skill folder) showing it.
