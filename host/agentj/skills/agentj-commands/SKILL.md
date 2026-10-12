---
name: agentj-commands
description: Add, change or remove the owner's own slash commands — the list that opens when the owner types 「/」 on the phone (and the ≡ menu). Use for 「定制命令」「我想加一个 / 命令」「把 /xxx 删掉」「改一下 /xxx 的说明」「做一个每天发周报的命令」, "customise my commands", "add a slash command", "remove /xxx", the phone's /customize or a long press on its ≡ button. Asks first, then writes a document command (a skill that is only instructions) or a command skill (instructions + a script it runs), puts it in the phone's list with `agentj menu`, checks it, and tells the owner how to see it.
---

The phone's 「/」 list has three parts, top to bottom: Agent J's own one-tap commands (fixed: /compact /clear /model …,
/customize, 全部停下), **the owner's menu items**, then **the harness's skills**. You change only the last two.

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

Never: change Agent J's built-in commands, remove a skill the owner did not name, put a credential in a skill, or claim
it is in the list without `agentj menu list` (or the skill folder) showing it.
