---
name: agentj-handoff
description: Write a handoff file before the context is compacted or cleared, and read it back right after — 「要压缩了」「上下文过半了」「先写个交接」「compact 之前存一下」「我要 /clear 了」, "write a handoff", "context is past half", "about to compact", an Agent J message 〔Agent J 系统提示〕 asking for the handoff, or a compaction / clear that just finished. Keeps the owner's own words, progress, decisions, absolute paths and pitfalls on disk so nothing is lost.
---

A compaction turns the conversation into a summary and drops details: exact numbers, IDs, absolute paths, ideas the owner
rejected, attempts that failed. A file on disk is not compacted. Write it before, read it after.

## Where (fixed, human-readable — the owner copies these from the phone)
| Situation | File |
|---|---|
| Before a compaction (shared session: your own Claude Code) | `~/.local/state/agentj/handoff/compact.md` |
| Before a `/clear` | `~/.local/state/agentj/handoff/clear.md` (used once: Agent J renames it to `clear-used.md` when it hands it back) |
| Agent J's own main conversation (it names the path in its message) | `<work folder>/.agentj/handover/compact.md` |

Never put a session id, hash or random string in a handoff path or file name: the phone masks id-like strings and the
owner can no longer copy the path. Create the folder if missing (`mkdir -p`).

## When to write
- the owner says the context is past half, asks to compact / clear, or asks for a handoff;
- an 〔Agent J 系统提示〕 message asks for it (the phone's /compact does this first when the file is missing or older than
  the current turn);
- you notice the context is getting long in a long task — write it early, update it later.
Write the file, then stop: you never run the compaction yourself unless the owner asked for it.

## How
1. The file already exists from this conversation → read it and **merge**: one complete new version, overwrite. Do not
   only append, and do not drop the pitfalls of earlier rounds.
2. Absolute paths only. The owner's requirements and rejections in their own words, quoted.
3. **No credentials**: never a key, token or password — only "in file X, variable Y".
4. Sections 1, 2, 5 and 6 are required; drop the others when empty:

```markdown
# Handoff
- Written: <YYYY-MM-DD HH:MM> (round N)
- Working folder: <absolute path>

## 1. The task (owner's own words)
> <original request and later constraints, quoted>

## 2. Progress
- Done: <items, with absolute file paths>
- In progress: <where exactly it stands>
- Not started: <the rest>

## 3. Decisions
- <decision — who decided — why>

## 4. Paths and commands
- <absolute path → what it is>; <commands to rerun as-is>

## 5. Pitfalls and failed attempts
- <tried → why it did not work>

## 6. First thing after the compaction
1. <the very next concrete action>

## 7. Open questions for the owner
- <or "none">
```

5. End your reply with the path on its own line in a code block (the phone has a copy button), then one sentence:
   「交接写好了，可以压缩了。」/ "Handoff written; ready to compact."

## After a compaction or /clear
Agent J sends 〔Agent J 系统提示〕「压缩完成。先读交接文件 …，再继续刚才的任务」 by itself when no new message comes (the phone
shows 「压缩完成，Agent 正在恢复上下文」). Before anything else, read that file completely, then carry on with the task it
names — do not wait for the owner to ask. If the note says there was no handoff this time, continue from the compaction
summary and write a fresh handoff soon.
