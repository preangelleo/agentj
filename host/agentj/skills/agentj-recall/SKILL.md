---
name: agentj-recall
description: Continue earlier work without a conversation list — 「接着昨天那件事」「上次那个」「继续刚才的」「那个 xx 项目」「重来」「换个说法」, "continue yesterday's thing", "that project", "redo", "say it differently". Finds the thread with `agentj recall`, the harness's own history and the working root, says in one line what it found, and carries on.
---

The phone has no conversation list, project picker or regenerate button on purpose: the user just says it, you do it.

## 「接着昨天那件事」「上次那个」「继续刚才的」 / "continue", "that thing from yesterday", "where were we"
1. Search: `agentj recall <2–4 keywords from the user's words> [--days 2 | --date YYYY-MM-DD] --json`
   (no keywords: `agentj recall --days 2` lists the newest pages). It searches the phone's pages on this computer —
   the current conversation and the ones `/clear` put aside — and works inside the fence (serve answers on its socket).
   Hits are `{id, time, who, said, reply, archived}`; excerpts are redacted. Native sandboxes that block the socket use
   the current serve's bounded local index (`via: index`, latest 50 excerpts); this never opens the state directory or
   relaxes permissions. If the index has no hit, widen the date and read handovers/reports; it cannot search every old page.
   Actually run the command and read the whole result; never hide stderr or pipe to head. Read both the owner's request
   and the reply's decision/stopping point. A vague "yesterday" first uses `--days 2`, then `--days 7` if empty; do not
   hard-code a date from memory. Never claim you dispatched a CEO merely by displaying a command: invoke its roster
   entry point and read back the report.
2. Also look where the work itself lives: a handover in `.agentj/handover/` of the working root, the workflow folders
   under the working root (their NEXT_SESSION / reports), your harness's own session history if you can read it.
3. Pick the most likely thread (most recent + best keyword match). Say it in ONE line — 「接着 10/04 那件：给官网加使用说明，
   上次停在截图。」 — and continue. Ask only when two candidates are equally likely, as one short question with both.
4. Nothing found: say so in one line and ask what it was about.

## 「那个 xx 项目」 / "that xx project"
Locate it under the working root (your session cwd): list the direct child folders, read the CEO roster
(`documentation/ROLES.md` or the root entry file), match the name loosely (pinyin, English, abbreviations, part of the
name). Confirm in one line — 「是 ~/coding/agentj-site 吧，开始。」 — and go. Ask only when two folders fit equally.

## 「重来」「再来一次」「换个说法」 / "redo", "try again", "say it differently"
- About your previous answer: answer the same question again, differently (other structure, other wording, shorter) —
  no questions, no apology paragraph.
- About the last task: run it again from scratch (fresh look, not a patch on the previous attempt), then report the result.

## Rules
- Read-only: `agentj recall` never changes anything. Never paste credentials from old pages into the reply.
- One line of "what I found", then the work. No tool streams, no lists of every candidate.
